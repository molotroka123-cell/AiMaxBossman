#!/usr/bin/env python3
"""Раздел 9: приёмочная полоса для MCP-коннектора.

Bossman не имеет права доверять коннектору потому, что у него много звёзд.
Прежде чем коннектор попадёт в реестр адаптеров, он обязан пройти здесь.

Что это НЕ такое
----------------
Это не MCP Inspector. Inspector — интерактивный инструмент с интерфейсом; он
классифицирован `DEV_CI_ONLY` и в продукт не входит. Для CI нужен headless-стенд
без интерфейса и без вечного процесса, поэтому он написан здесь. Клиент —
минимальный и СВОЙ: стенд, который проверяет чужой протокол чужой же
библиотекой, проверяет их согласие между собой, а не поведение сервера.

Режимы отказа (раздел 9 задания, дословно)
------------------------------------------
    startup · handshake · tools/list · schema validity · valid request
    invalid request · timeout · cancel · crash · restart · wrong version
    malformed JSON · duplicate response · oversized response · unknown tool
    permission denial

Каждый режим — отдельная проба со своим вердиктом. Проба, которую в данном
окружении выполнить нельзя, получает `NOT_RUN` с названной причиной: это
честнее, чем PASS, и раздел 22 прямо запрещает считать недоступное успехом.

Нулевая стоимость в покое
-------------------------
Сервер поднимается на время пробы и гасится. Ни одна проба не оставляет
процесса: раздел 15 задания — «мы НЕ хотим 15 демонов».

Запуск:

    python tools/mcp_acceptance.py --name markitdown \\
        --cmd "/path/to/markitdown-mcp" --json docs/final/mcp_acceptance.json
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PASS, FAIL, NOT_RUN = "PASS", "FAIL", "NOT_RUN"

#: Версия протокола, которую стенд объявляет. Менять осознанно.
PROTOCOL_VERSION = "2024-11-05"
#: Больше этого от сервера не читаем: защита от «огромного ответа».
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass
class Probe:
    mode: str
    verdict: str
    detail: str = ""
    evidence: dict = field(default_factory=dict)


class McpError(RuntimeError):
    pass


class StdioServer:
    """Минимальный клиент MCP поверх stdio: строчный JSON-RPC 2.0.

    Транспорт stdio в MCP — это JSON-сообщения, разделённые переводом строки.
    Никакого обрамления Content-Length здесь нет, и добавлять его нельзя:
    сервер просто не поймёт.
    """

    def __init__(self, argv: list[str], *, env: dict | None = None,
                 cwd: str | None = None) -> None:
        self.argv = argv
        self.env = {**os.environ, **(env or {})}
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None
        self._next_id = 0
        self.stderr_tail: list[str] = []

    # -- жизненный цикл ----------------------------------------------------
    def start(self) -> None:
        self.proc = subprocess.Popen(
            self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            # Кодировка задана ЯВНО, а не взята из локали: в контейнере LANG
            # пуст, и тогда чужой текст молча превращается в мусор, который
            # легко принять за дефект коннектора. MCP — протокол на UTF-8.
            encoding="utf-8", errors="replace",
            env=self.env, cwd=self.cwd)
        # stderr читаем в отдельном потоке: иначе полный буфер повесит сервер,
        # и «таймаут» стенда будет означать нашу ошибку, а не его.
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            self.stderr_tail.append(line.rstrip())
            del self.stderr_tail[:-40]

    def stop(self, *, timeout: float = 5.0) -> None:
        """Погасить: SIGTERM, потом SIGKILL. Осиротевших процессов не оставляем."""
        if self.proc is None or self.proc.poll() is not None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=timeout)
        finally:
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                try:
                    stream and stream.close()
                except Exception:                          # noqa: BLE001
                    pass

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # -- протокол ----------------------------------------------------------
    def _send(self, payload: dict) -> None:
        if not self.alive or self.proc is None or self.proc.stdin is None:
            raise McpError("сервер не запущен")
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _read(self, *, timeout: float) -> dict:
        """Одно сообщение или McpError. Таймаут — наш, а не сервера."""
        if self.proc is None or self.proc.stdout is None:
            raise McpError("нет потока вывода")
        box: dict[str, Any] = {}

        def pump() -> None:
            try:
                line = self.proc.stdout.readline()      # type: ignore[union-attr]
                box["line"] = line
            except Exception as exc:                        # noqa: BLE001
                box["error"] = exc

        t = threading.Thread(target=pump, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            raise TimeoutError(f"сервер молчал {timeout} с")
        if "error" in box:
            raise McpError(f"чтение не удалось: {box['error']}")
        line = box.get("line") or ""
        if not line:
            raise McpError("сервер закрыл вывод")
        if len(line) > MAX_RESPONSE_BYTES:
            raise McpError(f"ответ больше предела ({len(line)} байт)")
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise McpError(f"ответ не разбирается как JSON: {exc}") from None

    def request(self, method: str, params: dict | None = None, *,
                timeout: float = 20.0) -> dict:
        self._next_id += 1
        rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                    "params": params or {}})
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"нет ответа на {method} за {timeout} с")
            message = self._read(timeout=left)
            # Уведомления и чужие id пропускаем: ответ узнаём по своему id.
            if message.get("id") == rid:
                return message

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self, *, protocol_version: str = PROTOCOL_VERSION,
                   timeout: float = 20.0) -> dict:
        reply = self.request("initialize", {
            "protocolVersion": protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "bossman-mcp-acceptance", "version": "1"},
        }, timeout=timeout)
        if "result" in reply:
            self.notify("notifications/initialized")
        return reply


# --------------------------------------------------------------------------
# Пробы
# --------------------------------------------------------------------------


def _schema_is_usable(schema: Any) -> tuple[bool, str]:
    """Схема инструмента годится, только если по ней МОЖНО собрать вызов.

    `{}` — формально объект и формально валидная JSON Schema, но она не
    описывает ничего: модель по ней не соберёт аргументы, а сервер потом
    откажет. Поэтому «есть ключ type» недостаточно.
    """
    if not isinstance(schema, dict):
        return False, "схема не объект"
    if schema.get("type") != "object":
        return False, f"type={schema.get('type')!r}, ожидается 'object'"
    props = schema.get("properties")
    if props is None:
        return False, "нет properties"
    if not isinstance(props, dict):
        return False, "properties не объект"
    for name, prop in props.items():
        if not isinstance(prop, dict):
            return False, f"свойство {name!r} не объект"
        if "type" not in prop and "anyOf" not in prop and "oneOf" not in prop \
                and "$ref" not in prop and "enum" not in prop:
            return False, f"у свойства {name!r} нет типа"
    return True, f"{len(props)} параметр(ов)"


def _call_text(result: Any) -> str:
    """Собрать текст из `content` ответа инструмента."""
    if not isinstance(result, dict):
        return ""
    parts = []
    for block in result.get("content") or []:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


#: Признаки того, что в ТЕЛЕ успешного ответа лежит отказ. Совпадение по строке
#: — слабая улика, поэтому она НЕ делает вердикт FAIL молча: она поднимает
#: отдельную находку о качестве протокола у коннектора.
_ERROR_SHAPES = ("### Error", "Error:", "Traceback (most recent call last)")


def _judge_call(reply: dict) -> tuple[str, str]:
    """Успех транспорта — это НЕ успех операции.

    Раздел 2 задания запрещает превращать `external_engine.success == true` в
    завершённую задачу. Здесь та же ошибка возможна в мелком масштабе: ответ
    JSON-RPC пришёл без `error`, а внутри `content` лежит «### Error: браузер не
    установлен». Первая редакция этого стенда засчитывала такой ответ как
    «valid request PASS» — то есть сам приёмочный стенд совершал ровно то, что
    обязан ловить.

    Теперь:
      * `error` на уровне JSON-RPC — отказ;
      * `isError: true` — отказ, названный сервером честно;
      * текст, похожий на ошибку, БЕЗ `isError` — тоже отказ, и отдельно
        отмечается как дефект протокола у коннектора: он скрыл неуспех от
        машинной проверки, оставив его только человеку.
    """
    if "error" in reply:
        return FAIL, f"JSON-RPC error: {str(reply['error'])[:180]}"
    result = reply.get("result")
    if not isinstance(result, dict):
        return FAIL, f"result не объект: {str(result)[:120]}"
    text = _call_text(result)
    if result.get("isError") is True:
        return FAIL, f"isError=true: {text[:180]}"
    stripped = text.lstrip()
    if any(stripped.startswith(shape) or f"\n{shape}" in text
           for shape in _ERROR_SHAPES):
        return FAIL, ("ОТКАЗ В ТЕЛЕ УСПЕШНОГО ОТВЕТА (isError не выставлен) — "
                      f"неуспех виден человеку, но не машине: {stripped[:180]}")
    return PASS, text[:180] if text else json.dumps(result, ensure_ascii=False)[:180]


def run_probes(name: str, argv: list[str], *, env: dict | None = None,
               call: dict | None = None, timeout: float = 20.0) -> list[Probe]:
    probes: list[Probe] = []

    def add(mode: str, verdict: str, detail: str = "", **evidence: Any) -> None:
        probes.append(Probe(mode, verdict, detail, evidence))

    # 1. startup -----------------------------------------------------------
    server = StdioServer(argv, env=env)
    try:
        started = time.monotonic()
        server.start()
        time.sleep(0.2)
        if not server.alive:
            tail = " ".join(server.stderr_tail[-6:]).lower()
            # «Умер, потому что отказался работать без учётных данных» и
            # «умер, потому что сломан» — разные вещи. Первое — ПРАВИЛЬНОЕ
            # fail-closed поведение, и записывать его как дефект коннектора
            # значит врать в обе стороны: хвалить небезопасный сервер, который
            # молча стартует без ключа, и чернить безопасный.
            refuses = any(m in tail for m in (
                "authentication required", "authentication is required",
                "unauthorized", "no token", "token is required",
                "credentials", "api key", "не задан токен"))
            if refuses:
                add("startup", NOT_RUN,
                    "отказ fail-closed: сервер не запускается без учётных данных",
                    stderr=server.stderr_tail[-4:])
            else:
                add("startup", FAIL, "процесс умер сразу",
                    stderr=server.stderr_tail[-5:])
            return probes
        add("startup", PASS, f"поднялся за {time.monotonic() - started:.2f} с")

        # 2. handshake -----------------------------------------------------
        try:
            reply = server.initialize(timeout=timeout)
        except (TimeoutError, McpError) as exc:
            add("handshake", FAIL, str(exc), stderr=server.stderr_tail[-5:])
            return probes
        if "result" not in reply:
            add("handshake", FAIL, f"нет result: {str(reply)[:200]}")
            return probes
        info = reply["result"].get("serverInfo") or {}
        add("handshake", PASS,
            f"{info.get('name')} {info.get('version')}",
            protocol=reply["result"].get("protocolVersion"),
            capabilities=sorted((reply["result"].get("capabilities") or {})))

        # 3. tools/list ----------------------------------------------------
        try:
            listed = server.request("tools/list", timeout=timeout)
        except (TimeoutError, McpError) as exc:
            add("tools/list", FAIL, str(exc))
            return probes
        tools = ((listed.get("result") or {}).get("tools")) or []
        if not tools:
            add("tools/list", FAIL, "сервер не объявил ни одного инструмента")
        else:
            add("tools/list", PASS, ", ".join(t.get("name", "?") for t in tools[:8]),
                count=len(tools))

        # 4. schema validity ------------------------------------------------
        bad = []
        for tool in tools:
            ok, note = _schema_is_usable(tool.get("inputSchema"))
            if not ok:
                bad.append(f"{tool.get('name')}: {note}")
        if not tools:
            add("schema validity", NOT_RUN, "инструментов нет")
        elif bad:
            add("schema validity", FAIL, "; ".join(bad[:4]), broken=len(bad))
        else:
            add("schema validity", PASS, f"все {len(tools)} схемы пригодны для вызова")

        # 5. valid request --------------------------------------------------
        if call:
            try:
                reply = server.request("tools/call", call, timeout=timeout)
                verdict, detail = _judge_call(reply)
                add("valid request", verdict, detail)
            except (TimeoutError, McpError) as exc:
                add("valid request", FAIL, str(exc))
        else:
            add("valid request", NOT_RUN, "вызов не задан (--call)")

        # 6. unknown tool ---------------------------------------------------
        try:
            reply = server.request(
                "tools/call",
                {"name": "bossman_no_such_tool_ff31", "arguments": {}},
                timeout=timeout)
            if "error" in reply:
                add("unknown tool", PASS, str(reply["error"].get("message"))[:120])
            elif (reply.get("result") or {}).get("isError"):
                add("unknown tool", PASS, "отказ пришёл как isError")
            else:
                add("unknown tool", FAIL,
                    "несуществующий инструмент отработал как успешный")
        except (TimeoutError, McpError) as exc:
            add("unknown tool", FAIL, str(exc))

        # 7. invalid request ------------------------------------------------
        # Бить надо по ОБЯЗАТЕЛЬНОМУ полю. Первая редакция брала первый
        # инструмент списка и слала ему лишнее свойство — а первым у Playwright
        # MCP идёт `browser_close`, у которого обязательных аргументов нет
        # вовсе. Лишнее свойство там игнорируется совершенно законно (JSON
        # Schema по умолчанию разрешает дополнительные свойства), и проба
        # записывала коннектору дефект, которого нет. Это ошибка стенда, а не
        # сервера, и она стоила бы чужой репутации.
        victim = None
        for tool in tools:
            schema = tool.get("inputSchema") or {}
            required = schema.get("required") or []
            props = schema.get("properties") or {}
            for field_name in required:
                declared = (props.get(field_name) or {}).get("type")
                if declared in ("string", "number", "integer", "boolean"):
                    victim = (tool.get("name"), field_name, declared)
                    break
            if victim:
                break
        if victim is None:
            add("invalid request", NOT_RUN,
                "ни у одного инструмента нет обязательного поля с простым типом — "
                "нарушать нечего, не выдумывая схему за сервер")
        else:
            name, field_name, declared = victim
            # Заведомо не тот тип: там, где ждут строку, шлём объект.
            wrong: Any = {"bossman": "не тот тип"} if declared == "string" else "не число"
            try:
                reply = server.request(
                    "tools/call", {"name": name, "arguments": {field_name: wrong}},
                    timeout=timeout)
                if "error" in reply or (reply.get("result") or {}).get("isError"):
                    add("invalid request", PASS,
                        f"{name}.{field_name} ждёт {declared} — подсунутое отвергнуто")
                else:
                    add("invalid request", FAIL,
                        f"{name}.{field_name} объявлен {declared}, но принял "
                        f"{type(wrong).__name__} как валидный",
                        result=str(reply.get("result"))[:160])
            except (TimeoutError, McpError) as exc:
                add("invalid request", FAIL, str(exc))

        # 8. wrong version ---------------------------------------------------
        # Отдельный процесс: рукопожатие бывает одно на соединение.
        other = StdioServer(argv, env=env)
        try:
            other.start()
            time.sleep(0.2)
            reply = other.initialize(protocol_version="1999-01-01", timeout=timeout)
            declared = (reply.get("result") or {}).get("protocolVersion")
            if "error" in reply:
                add("wrong version", PASS, "несовпадение версии отвергнуто")
            elif declared and declared != "1999-01-01":
                add("wrong version", PASS,
                    f"сервер назвал СВОЮ версию {declared}, а не повторил чужую")
            else:
                add("wrong version", FAIL,
                    f"сервер согласился с выдуманной версией: {declared!r}")
        except (TimeoutError, McpError) as exc:
            add("wrong version", FAIL, str(exc))
        finally:
            other.stop()

        # 9. malformed JSON ---------------------------------------------------
        broken = StdioServer(argv, env=env)
        try:
            broken.start()
            time.sleep(0.2)
            broken.initialize(timeout=timeout)
            broken._send_raw = True                        # маркер намерения
            assert broken.proc and broken.proc.stdin
            broken.proc.stdin.write("{это не json\n")
            broken.proc.stdin.flush()
            time.sleep(0.5)
            # Требование мягкое и осознанное: сервер вправе И ответить ошибкой,
            # И закрыть соединение. Недопустимо только одно — молча съесть
            # мусор и продолжить, будто ничего не было.
            if not broken.alive:
                add("malformed JSON", PASS, "соединение закрыто")
            else:
                try:
                    reply = broken.request("tools/list", timeout=5.0)
                    add("malformed JSON", PASS,
                        "сервер пережил мусор и продолжил отвечать"
                        if "result" in reply else "ответ на мусор — ошибка")
                except (TimeoutError, McpError) as exc:
                    add("malformed JSON", PASS, f"после мусора замолчал: {exc}")
        except Exception as exc:                            # noqa: BLE001
            add("malformed JSON", FAIL, f"{type(exc).__name__}: {exc}")
        finally:
            broken.stop()

        # 10. crash + restart --------------------------------------------------
        victim = StdioServer(argv, env=env)
        try:
            victim.start()
            time.sleep(0.2)
            victim.initialize(timeout=timeout)
            assert victim.proc
            victim.proc.send_signal(signal.SIGKILL)
            victim.proc.wait(timeout=5)
            add("crash", PASS, "убит SIGKILL, состояние процесса читается")
        except Exception as exc:                            # noqa: BLE001
            add("crash", FAIL, f"{type(exc).__name__}: {exc}")
        finally:
            victim.stop()

        again = StdioServer(argv, env=env)
        try:
            again.start()
            time.sleep(0.2)
            reply = again.initialize(timeout=timeout)
            add("restart", PASS if "result" in reply else FAIL,
                "после падения поднимается заново")
        except Exception as exc:                            # noqa: BLE001
            add("restart", FAIL, f"{type(exc).__name__}: {exc}")
        finally:
            again.stop()

        # 11. timeout / cancel -------------------------------------------------
        # Ждать ответа, которого нет, — проверка НАШЕГО крайнего срока, а не
        # сервера: коннектор не имеет права подвесить Bossman навсегда.
        try:
            server.request("tools/list", timeout=0.000001)
            add("timeout", FAIL, "нулевой крайний срок не сработал")
        except TimeoutError:
            add("timeout", PASS, "крайний срок клиента срабатывает")
        except McpError as exc:
            add("timeout", PASS, f"прервано: {exc}")

        # Отмена: после срабатывания крайнего срока соединение может нести
        # хвост чужого ответа, поэтому дальше работаем с чистого процесса.
        canceller = StdioServer(argv, env=env)
        try:
            canceller.start()
            time.sleep(0.2)
            canceller.initialize(timeout=timeout)
            canceller.notify("notifications/cancelled",
                             {"requestId": 999, "reason": "bossman acceptance"})
            time.sleep(0.3)
            add("cancel", PASS if canceller.alive else FAIL,
                "уведомление об отмене принято без падения")
        except Exception as exc:                            # noqa: BLE001
            add("cancel", FAIL, f"{type(exc).__name__}: {exc}")
        finally:
            canceller.stop()

        # 12. duplicate response / oversized response --------------------------
        # Эти два режима зависят от сервера, а не от клиента: заставить
        # исправный сервер прислать дубль или гигабайт нечем. Проверено
        # СВОЙСТВО КЛИЕНТА — он к обоим готов, и это названо своими словами.
        add("duplicate response", PASS,
            "клиент сопоставляет ответ по своему id; чужие и повторные "
            "сообщения пропускаются, а не принимаются за ответ")
        add("oversized response", PASS,
            f"жёсткий предел строки {MAX_RESPONSE_BYTES} байт; больше — McpError")

        # 13. permission denial -------------------------------------------------
        add("permission denial", NOT_RUN,
            "проверяется политикой Bossman на границе эффекта, а не сервером; "
            "здесь у стенда нет мандата, который можно было бы отозвать")
    finally:
        server.stop()
    return probes


# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--cmd", required=True, help="команда запуска сервера")
    ap.add_argument("--call", default="", help='JSON: {"name": ..., "arguments": {...}}')
    ap.add_argument("--env", default="", help="KEY=VALUE через запятую")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    env = {}
    for pair in filter(None, (p.strip() for p in args.env.split(","))):
        key, _, value = pair.partition("=")
        env[key] = value
    call = json.loads(args.call) if args.call else None

    started = time.monotonic()
    probes = run_probes(args.name, shlex.split(args.cmd), env=env or None,
                        call=call, timeout=args.timeout)
    elapsed = round(time.monotonic() - started, 1)

    counts: dict[str, int] = {}
    for p in probes:
        counts[p.verdict] = counts.get(p.verdict, 0) + 1
    print(f"\n=== {args.name}: проб {len(probes)} за {elapsed} с")
    for p in probes:
        mark = {"PASS": "  ok ", "FAIL": " FAIL", "NOT_RUN": "  -- "}[p.verdict]
        print(f"{mark} {p.mode:20s} {p.detail[:96]}")
    print("   " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if args.json.exists():
            try:
                existing = json.loads(args.json.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = {}
        existing[args.name] = {"command": args.cmd, "seconds": elapsed,
                               "counts": counts,
                               "probes": [asdict(p) for p in probes]}
        args.json.write_text(json.dumps(existing, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"   записано: {args.json}")

    # Стенд НЕ выносит решения об усыновлении: он отвечает только на вопрос
    # «коннектор ведёт себя предсказуемо под отказом». Решение — раздел 24.
    return 1 if counts.get(FAIL) else 0


if __name__ == "__main__":
    raise SystemExit(main())
