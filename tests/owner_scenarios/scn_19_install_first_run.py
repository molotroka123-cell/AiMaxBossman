"""Владельческие сценарии 81–85: установка, первый запуск и обновление продукта.

Пять вопросов, которые владелец задаёт ДО того, как продукт что-нибудь сделает:

* 81 — продукт ставится из СОБРАННОГО пакета и запускается отдельным процессом,
  без репозитория на пути импорта; интерфейс приезжает внутри пакета, а данные
  владельца ложатся ВНЕ пакета;
* 82 — «доктор» не говорит «всё хорошо», когда чего-то нет: испорченный каталог
  состояния, отсутствующий инструмент, занятый порт и конфигурация без версии —
  ЧЕТЫРЕ разных имени с четырьмя разными лекарствами, а не один общий отказ;
* 83 — первый запуск без единой настройки не падает трассой: продукт заводит
  недостающее сам, а о том, чего завести не может, говорит словами;
* 84 — повторная установка (пакет пересобран, старый каталог снесён) не трогает
  данные владельца: токен, ключ шифрования и записи на месте;
* 85 — версия, которую показывает продукт, — личность ТОГО САМОГО установленного
  кода: отметка сборки, а не строка из исходника и не sha соседнего репозитория.

КАК ЗДЕСЬ ПОНИМАЕТСЯ «УСТАНОВЛЕННЫЙ». Владельческая установка
(`scripts/installed_product_install.py`) собирает колёса и ставит их в venv —
ей нужны сеть и `pip`, которых в этом прогоне нет. Поэтому пакет собирается тем
же кодом упаковки, что идёт в колесо (`command-center/setup.py`, класс
`BuildWithUI`: он кладёт в пакет `bcc/_ui`, `bcc/_build.json`, каталог моделей
студии и манифесты интеграций), и продукт запускается ИЗ ЭТОГО дерева отдельным
процессом с `PYTHONPATH`, где репозитория `command-center` нет. Всё, что ниже
этой строки, измерено на том, что получилось, а не на рабочем чекауте: `bcc`
проверяется на принадлежность собранному каталогу отдельной пробой, потому что
в этой среде `bcc` установлен editable из ДРУГОГО рабочего каталога и подменил
бы измерение молча.

ЖИВОГО ШАГА МОДЕЛИ НЕТ: ни один из пяти вопросов модели не задаёт, все объявлены
`model_step: "none"`.

Зависимости модуля — стандартная библиотека и каркас. Всё, что тянет
sqlalchemy/fastapi (`bcc.*`), импортируется ВНУТРИ функций и объявлено в реестре
как `command_center`: BL-085 — корневой CI ставит только
pytest/pytest-timeout/psutil/httpx/pyyaml, и без способности раннер отдаёт
честный вердикт вместо исполнения.
"""
from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "command-center"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario_runner import INSTALLED_PRODUCT, PRODUCT_CONTRACTS, scenario  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_SOURCE = REPO_ROOT / "command-center"
DOCTOR = REPO_ROOT / "scripts" / "bossman_doctor.py"
HEX40 = re.compile(r"[0-9a-f]{40}")
BOOT_TIMEOUT = 90.0

#: Переменные, которые в установленный процесс НЕ передаются: любая из них
#: превращает «установку» обратно в рабочий чекаут или уводит данные в чужой
#: каталог, и измерение перестаёт быть измерением установки.
STRIP_ENV = ("PYTHONPATH", "BCC_UI_DIR", "BCC_DATA_DIR", "BCC_HOST", "BCC_PORT",
             "DATABASE_URL", "BOSSMAN_DATABASE_URL", "BOSSMAN_VAULT_KEY",
             "BOSSMAN_TESTING_PERIOD", "BOSSMAN_DIAG_BUNDLE_ENABLED", "BCC_BUILD_SHA")


# ------------------------------------------------------------------ оснастка
def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _installed_env(pkg: Path, data: Path, **extra: str) -> dict[str, str]:
    """Окружение установленного продукта: пакет первым, репозиторий не первым.

    `REPO_ROOT` остаётся на пути только ради `bossman_shared` — отдельной
    поставки, которую настоящая установка получает из site-packages. Каталога
    `command-center` на пути нет вовсе, поэтому `bcc` взять неоткуда, кроме
    собранного пакета.
    """
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env.update(PYTHONPATH=os.pathsep.join([str(pkg), str(REPO_ROOT)]),
               BCC_DATA_DIR=str(data), BCC_TOKEN_STDOUT="0", PYTHONUNBUFFERED="1",
               BOSSMAN_TESTING_PERIOD="0")
    env.update(extra)
    return env


def _build_package(ctx, tag: str):
    """Собрать пакет тем же кодом упаковки, что идёт в колесо владельца."""
    out = ctx.path("сборка", tag)
    out.mkdir(parents=True, exist_ok=True)
    try:
        done = subprocess.run([sys.executable, "setup.py", "-q", "build_py",
                               "--build-lib", str(out)], cwd=str(PKG_SOURCE),
                              capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        ctx.not_proven(f"пакет продукта нечем собрать в этой среде: {type(exc).__name__}: {exc}")
    if done.returncode != 0 or not (out / "bcc" / "__init__.py").is_file():
        tail = ((done.stderr or done.stdout or "").strip().splitlines() or ["без вывода"])[-1]
        ctx.not_proven(f"пакет продукта не собирается в этой среде (код {done.returncode}): {tail}")
    return out


def _module_origin(pkg: Path, data: Path, module: str) -> str:
    """Откуда установленный процесс возьмёт модуль. Пустая строка — не взял."""
    probe = f"import {module} as m; print(getattr(m, '__file__', '') or '')"
    done = subprocess.run([sys.executable, "-c", probe], env=_installed_env(pkg, data),
                          cwd=str(data.parent), capture_output=True, text=True, timeout=120)
    return (done.stdout or "").strip()


def _identity_of(pkg: Path, data: Path, **extra: str) -> dict:
    """`bcc.build_identity.source_identity()` глазами установленного процесса."""
    probe = ("import json;from bcc.build_identity import source_identity;"
             "print(json.dumps(source_identity(fresh=True)))")
    done = subprocess.run([sys.executable, "-c", probe],
                          env=_installed_env(pkg, data, **extra),
                          cwd=str(data.parent), capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        return {"_error": (done.stderr or "").strip()[-300:]}
    try:
        return json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"_error": (done.stdout or "")[-300:]}


class _Answer:
    __slots__ = ("status", "body", "text")

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        self.text = body.decode("utf-8", "replace")

    def json(self) -> dict:
        return json.loads(self.body)


@contextlib.contextmanager
def _installed_server(ctx, pkg: Path, data: Path, *, tag: str, **extra: str):
    """Поднять УСТАНОВЛЕННЫЙ продукт отдельным процессом и говорить с ним по HTTP.

    Прокси окружения здесь недопустим (`http.client` ходит прямо в сокет): для
    127.0.0.1 он только соврал бы своим отказом, и мёртвый сервер стал бы
    неотличим от живого.
    """
    port = _free_port()
    data.mkdir(parents=True, exist_ok=True)
    log_path = ctx.path("журналы", f"{tag}.log")
    log = log_path.open("ab")
    process = subprocess.Popen(
        [sys.executable, "-m", "bcc", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(data.parent), env=_installed_env(pkg, data, **extra),
        stdout=log, stderr=subprocess.STDOUT)

    def call(path: str, *, token: str = "", expect: int | None = None) -> _Answer:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        try:
            conn.request("GET", path, headers={"X-BCC-Token": token} if token else {})
            response = conn.getresponse()
            answer = _Answer(response.status, response.read())
        finally:
            conn.close()
        if expect is not None and answer.status != expect:
            raise AssertionError(f"{path}: ожидался {expect}, получен {answer.status}")
        return answer

    try:
        deadline = time.monotonic() + BOOT_TIMEOUT
        while True:
            if process.poll() is not None:
                raise AssertionError("установленный продукт не поднялся: "
                                     + log_path.read_text(encoding="utf-8", errors="replace")[-1500:])
            try:
                call("/api/identity", expect=200)
                break
            except (OSError, AssertionError, http.client.HTTPException):
                if time.monotonic() > deadline:
                    raise AssertionError(
                        f"установленный продукт не ответил за {BOOT_TIMEOUT:.0f} с: "
                        + log_path.read_text(encoding="utf-8", errors="replace")[-1000:])
                time.sleep(0.2)
        yield call, log_path
    finally:
        # Windows venv python.exe can be a launcher whose child owns the log.
        # Stop the complete installed-product process tree before closing it.
        with contextlib.suppress(psutil.NoSuchProcess):
            root = psutil.Process(process.pid)
            descendants = root.children(recursive=True)
            def depth(item):
                with contextlib.suppress(psutil.NoSuchProcess):
                    return len(item.parents())
                return -1
            descendants.sort(key=depth, reverse=True)
            for child in descendants:
                with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                    child.kill()
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                root.kill()
            psutil.wait_procs(descendants + [root], timeout=20)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=20)
        log.close()


def _token(data: Path) -> str:
    return (data / "token").read_text(encoding="utf-8").strip()


# ------------------------------------------------------------------ 81
@scenario(id="OS-81", depth=INSTALLED_PRODUCT)
def os81_product_installs_and_starts(ctx) -> None:
    """Продукт ставится из собранного пакета и запускается, а не «только из репозитория».

    Проверяется не факт сборки, а то, ЧТО получилось: отдельный процесс, поднятый
    с путём, на котором `command-center` отсутствует, отдаёт владельцу страницу и
    называет себя установленной сборкой. Отрицательная половина отвечает на
    вопрос «а не подсунулся ли всё-таки чекаут»: у интерфейса, приехавшего в
    пакете, отбирается `index.html`, и продукт обязан НАЗВАТЬ его отсутствие, а
    не продолжить отдавать страницу из рабочего дерева.
    """
    pkg = _build_package(ctx, "первая")
    data = ctx.path("установка-1", "data")

    ui_index = pkg / "bcc" / "_ui" / "index.html"
    manifest = pkg / "bcc" / "_build.json"
    ctx.positive("в пакет попал интерфейс владельца",
                 ui_index.is_file() and ui_index.stat().st_size > 0,
                 f"{ui_index.relative_to(pkg)} = {ui_index.stat().st_size if ui_index.is_file() else 0} байт")
    stamp = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
    ctx.positive("в пакет попала отметка сборки с настоящим SHA",
                 bool(HEX40.fullmatch(str(stamp.get("source_sha") or ""))),
                 f"source_dirty={stamp.get('source_dirty')}")

    origin = _module_origin(pkg, data, "bcc")
    ctx.positive("установленный процесс берёт продукт ИЗ ПАКЕТА",
                 bool(origin) and Path(origin).is_relative_to(pkg),
                 origin or "модуль не импортировался")

    with _installed_server(ctx, pkg, data, tag="os81") as (call, log_path):
        identity = call("/api/identity", expect=200).json()
        ctx.positive("установленный продукт поднялся и назвал себя",
                     identity.get("app") == "bossman-command-center"
                     and identity.get("source") == "installed_build",
                     f"{identity.get('app')} / {identity.get('source')} / {identity.get('version')}")
        page = call("/", expect=200)
        ctx.positive("интерфейс владельца отдаётся самим продуктом",
                     b"<html" in page.body.lower() and len(page.body) > 500,
                     f"{len(page.body)} байт")
        health = call("/health")
        ui_state = health.json()["components"].get("ui", {}).get("status")
        ctx.positive("продукт видит свой интерфейс на месте", ui_state == "HEALTHY", str(ui_state))
        ctx.reached_installed_product(f"собранный пакет {pkg.name} + отдельный процесс python -m bcc")

    ctx.positive("данные владельца появились отдельным хозяйством",
                 (data / "token").is_file() and (data / "bcc.db").is_file(),
                 ", ".join(sorted(p.name for p in data.iterdir())))

    # --- отрицательные контроли
    ctx.negative("данные владельца лежат НЕ внутри пакета: снос пакета их не унесёт",
                 not data.is_relative_to(pkg), f"{data} vs {pkg}")
    ctx.negative("продукт не притянул себя из соседнего рабочего каталога",
                 bool(origin) and not Path(origin).is_relative_to(PKG_SOURCE),
                 origin or "нет модуля")
    ctx.negative("репозитория продукта на пути установленного процесса нет",
                 str(PKG_SOURCE) not in _installed_env(pkg, data)["PYTHONPATH"],
                 _installed_env(pkg, data)["PYTHONPATH"])

    hidden = ui_index.with_name("index.html.negative-control")
    ui_index.rename(hidden)
    try:
        with _installed_server(ctx, pkg, ctx.path("установка-1б", "data"),
                               tag="os81-без-ui") as (call, _):
            ui_state = call("/health").json()["components"].get("ui", {}).get("status")
            page = call("/")
            ctx.negative("пакет БЕЗ интерфейса не выдаёт себя за рабочий",
                         ui_state == "NOT_CONFIGURED", str(ui_state))
            ctx.negative("страница владельца из рабочего дерева не подставляется",
                         page.status == 404, f"HTTP {page.status}")
    finally:
        hidden.rename(ui_index)


# ------------------------------------------------------------------ 82
@scenario(id="OS-82", depth=PRODUCT_CONTRACTS)
def os82_doctor_names_each_missing_thing(ctx) -> None:
    """Доктор не говорит «всё хорошо», когда чего-то нет: у каждой нехватки своё имя.

    Один общий отказ («что-то не так») стоит владельцу того же часа, что и
    молчание: по нему нечего делать. Поэтому меряются ЧЕТЫРЕ разные поломки
    подряд, и проверяется не только статус, но и то, что имена проверок и
    лекарства РАЗНЫЕ, а исправный прогон не объявляет блокером то, что в порядке.

    Измеряется та же команда, которую запускает владелец, —
    `scripts/bossman_doctor.py`, отдельным процессом и в машиночитаемом виде.
    """
    if not DOCTOR.is_file():
        ctx.not_proven(f"доктор не поставляется этой веткой: нет {DOCTOR}")

    def doctor(extra_env: dict[str, str], argv: tuple[str, ...] = ()) -> tuple[int, dict]:
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BCC_DATA_DIR")}
        env["PYTHONPATH"] = os.pathsep.join([str(PKG_SOURCE), str(REPO_ROOT / "bossman-core"),
                                             str(REPO_ROOT)])
        env.update(extra_env)
        done = subprocess.run([sys.executable, str(DOCTOR), "--json", *argv],
                              cwd=str(REPO_ROOT), env=env, capture_output=True,
                              text=True, timeout=600)
        try:
            return done.returncode, json.loads(done.stdout)
        except ValueError:
            raise AssertionError(f"доктор ответил не отчётом: {(done.stdout or done.stderr)[-400:]}")

    def named(report: dict, name: str) -> dict:
        for check in report["checks"]:
            if check["name"] == name:
                return check
        raise AssertionError(f"доктор не сказал про «{name}» вообще: {[c['name'] for c in report['checks']]}")

    _healthy_code, healthy = doctor({})

    # 1. испорченный каталог состояния: на месте каталога лежит файл
    broken_dir = ctx.path("сломано", "не-каталог")
    broken_dir.write_text("здесь файл, а не каталог состояния", encoding="utf-8")
    code_dir, report_dir = doctor({"BCC_DATA_DIR": str(broken_dir)})
    state = named(report_dir, "state-dir")
    ctx.positive("испорченный каталог состояния назван BLOCKED и лечится названной переменной",
                 state["status"] == "BLOCKED" and "BCC_DATA_DIR" in state["remedy"],
                 f"{state['status']}: {state['detail'][:90]}")

    # 2. отсутствующий инструмент: PATH без ffmpeg
    empty_path = ctx.path("сломано", "пустой-path")
    empty_path.mkdir(parents=True, exist_ok=True)
    code_tool, report_tool = doctor({"PATH": str(empty_path)})
    tool = named(report_tool, "ffmpeg")
    ctx.positive("отсутствующий инструмент назван по имени, с последствием и лекарством",
                 tool["status"] == "WARN" and "ffprobe" in tool["detail"] and bool(tool["remedy"]),
                 f"{tool['status']}: {tool['detail'][:90]}")

    # 3. занятый порт
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    busy = holder.getsockname()[1]
    try:
        code_port, report_port = doctor({}, ("--port", str(busy)))
    finally:
        holder.close()
    port_check = named(report_port, "port")
    ctx.positive("занятый порт назван занятым, а не «сервер не открылся»",
                 port_check["status"] == "WARN" and str(busy) in port_check["detail"],
                 f"{port_check['status']}: {port_check['detail'][:90]}")

    # 4. конфигурация, которая «есть», но работать не будет
    code_cfg, report_cfg = doctor({"BOSSMAN_GATEWAY_URL": "http://127.0.0.1:8765"})
    cfg = named(report_cfg, "gateway-url")
    ctx.positive("конфигурация без версии названа, и назван ФАКТИЧЕСКИЙ адрес",
                 cfg["status"] == "WARN" and "/v1" in cfg["detail"] and "/v1" in cfg["remedy"],
                 f"{cfg['status']}: {cfg['detail'][:110]}")

    outcomes = {("state-dir", state["status"]), ("ffmpeg", tool["status"]),
                ("port", port_check["status"]), ("gateway-url", cfg["status"])}
    ctx.positive("четыре нехватки — четыре РАЗНЫХ имени, а не один общий отказ",
                 len({name for name, _ in outcomes}) == 4
                 and len({state["remedy"], tool["remedy"], port_check["remedy"], cfg["remedy"]}) == 4,
                 ", ".join(sorted(f"{n}={s}" for n, s in outcomes)))

    # --- отрицательные контроли
    ctx.negative("исправное доктор блокером НЕ объявляет (иначе «всё красное» его удовлетворяет)",
                 named(healthy, "state-dir")["status"] != "BLOCKED"
                 and named(healthy, "port")["status"] != "BLOCKED",
                 f"state-dir={named(healthy, 'state-dir')['status']}, "
                 f"port={named(healthy, 'port')['status']}")
    ctx.negative("блокер не заканчивается нулевым кодом возврата",
                 code_dir == 1 and report_dir["blocked"] >= 1,
                 f"код {code_dir}, блокеров {report_dir['blocked']}")
    # На машине владельца другие проверки могут быть BLOCKED одновременно.
    # Код процесса обязан отражать весь отчёт, а три проверки выше отдельно
    # доказывают, что инструмент, порт и URL классифицированы как WARN.
    ctx.negative("код доктора соответствует всем блокерам, без ложного нуля",
                 all(code == (1 if report["blocked"] else 0)
                     for code, report in ((code_tool, report_tool),
                                          (code_port, report_port),
                                          (code_cfg, report_cfg))),
                 f"инструмент={code_tool}/{report_tool['blocked']}, "
                 f"порт={code_port}/{report_port['blocked']}, "
                 f"конфигурация={code_cfg}/{report_cfg['blocked']}")

    # Ключ владельца: доктор обязан называть ПЕРЕМЕННУЮ, а не её значение.
    secret = "sk-" + "or-" + "v1-" + "0" * 24
    _code_key, report_key = doctor({"OPENROUTER_API_KEY": secret,
                                    "BOSSMAN_OPENROUTER_API_KEY": secret[:-1] + "1"})
    clouds = named(report_key, "cloud-providers")
    ctx.positive("расхождение имён одной переменной названо раньше первой задачи",
                 clouds["status"] == "WARN" and "OPENROUTER_API_KEY" in clouds["detail"]
                 and "BOSSMAN_OPENROUTER_API_KEY" in clouds["detail"], clouds["status"])
    ctx.negative("значение ключа не попадает в отчёт доктора ни в каком виде",
                 secret not in json.dumps(report_key, ensure_ascii=False),
                 "значение не найдено в отчёте")

    # Упавшая проверка — это BLOCKED, а не тихий PASS.
    import importlib.util  # noqa: PLC0415

    probe_name = "bossman_doctor_probe"
    spec = importlib.util.spec_from_file_location(probe_name, DOCTOR)
    module = importlib.util.module_from_spec(spec)
    # `@dataclass` на Python 3.11 ищет свой модуль в sys.modules: без записи
    # доктор не загрузится вовсе, и «упавшая проверка» осталась бы непроверенной.
    sys.modules[probe_name] = module
    try:
        spec.loader.exec_module(module)

        def explodes():
            raise RuntimeError("проба недоступна")

        module.CHECKS.append(explodes)
        try:
            results = module.run_checks(_free_port())
        finally:
            module.CHECKS.remove(explodes)
    finally:
        sys.modules.pop(probe_name, None)
    crashed = [r for r in results if r.name == "explodes"]
    ctx.negative("упавшая проверка становится BLOCKED, а не тихим PASS",
                 len(crashed) == 1 and crashed[0].status == module.BLOCKED,
                 crashed[0].detail[:110] if crashed else "проверки нет в результатах")
    ctx.negative("вердикт с блокером не зовёт владельца начинать",
                 "начинать НЕЛЬЗЯ" in module.render(results),
                 module.render(results).strip().splitlines()[-1][:110])


# ------------------------------------------------------------------ 83
@scenario(id="OS-83", depth=INSTALLED_PRODUCT)
def os83_first_run_without_configuration(ctx) -> None:
    """Первый запуск без единой настройки не падает трассой, а называет нехватку.

    Установка чистая: пустой каталог данных и ни одной переменной настройки,
    кроме пути к этому каталогу. Продукт обязан завести недостающее сам (токен,
    база) и НАЗВАТЬ то, чего завести не может (провайдеры, модели, интерфейс),
    вместо «всё хорошо» и вместо трассы в консоль.
    """
    pkg = _build_package(ctx, "чистая")
    data = ctx.path("первый-запуск", "data")

    with _installed_server(ctx, pkg, data, tag="os83") as (call, log_path):
        identity = call("/api/identity", expect=200).json()
        ctx.positive("первый запуск дошёл до ответа, а не до трассы",
                     identity.get("app") == "bossman-command-center", str(identity.get("app")))

        health = call("/health")
        components = health.json()["components"]
        missing = {name: entry["status"] for name, entry in components.items()
                   if entry["status"] == "NOT_CONFIGURED"}
        ctx.positive("ненастроенное НАЗВАНО ненастроенным и перечислено поимённо",
                     health.status == 503 and "models" in missing and "providers" in missing,
                     f"HTTP {health.status}, не настроено: {sorted(missing)}")

        closed = call("/api/system")
        body = closed.json().get("error", {})
        ctx.positive("закрытая дверь объясняет, чего не хватает, и где это взять",
                     closed.status == 401 and bool(body.get("message")) and bool(body.get("hint")),
                     f"HTTP {closed.status}: {body.get('message', '')} / {body.get('hint', '')}")

        token = _token(data)
        system = call("/api/system", token=token, expect=200).json()
        ctx.positive("с токеном первого запуска продукт отвечает по существу",
                     "health" in system and "metrics" in system, sorted(system)[:6])

        unknown = call("/api/no-such-door", token=token)
        ctx.positive("несуществующая дверь отвечает сообщением, а не трассой",
                     unknown.status == 404 and "Traceback" not in unknown.text,
                     f"HTTP {unknown.status}: {unknown.text[:80]}")
        ctx.reached_installed_product("собранный пакет + первый запуск на пустом каталоге данных")

        # --- отрицательные контроли
        ctx.negative("продукт НЕ объявляет себя готовым, пока не настроен",
                     health.json()["ready"] is False and health.json()["status"] != "HEALTHY",
                     f"ready={health.json()['ready']}, status={health.json()['status']}")
        ctx.negative("отказ доступа не превращается в пятисотку и не несёт токена",
                     closed.status == 401 and token not in closed.text,
                     f"HTTP {closed.status}")

    printed = log_path.read_text(encoding="utf-8", errors="replace")
    ctx.positive("продукт завёл недостающее сам: токен и база появились",
                 (data / "token").is_file() and (data / "bcc.db").is_file()
                 and len(token) >= 16, ", ".join(sorted(p.name for p in data.iterdir())))
    ctx.negative("в журнале первого запуска нет ни одной трассы",
                 "Traceback (most recent call last)" not in printed,
                 f"{len(printed)} байт журнала")
    ctx.negative("токен первого запуска не печатается в консоль без явного разрешения",
                 token not in printed, "значение токена в журнале не найдено")


# ------------------------------------------------------------------ 84
@scenario(id="OS-84", depth=INSTALLED_PRODUCT)
def os84_reinstall_keeps_owner_data(ctx) -> None:
    """Повторная установка не затирает данные владельца.

    Обновление здесь настоящее: пакет собирается ВТОРОЙ раз в другой каталог,
    первый каталог пакета сносится целиком — ровно то, что делает установщик с
    site-packages, — и продукт поднимается снова на ТОМ ЖЕ каталоге данных.

    Проверяется не только «файлы на месте». Перевыписанный токен запирает
    владельца снаружи, а перевыписанный `secret.key` превращает все сохранённые
    ключи провайдеров в нечитаемый мусор — обе потери молчаливые, и обе
    измеряются побайтно.
    """
    first = _build_package(ctx, "до-обновления")
    data = ctx.path("общие-данные", "data")

    with _installed_server(ctx, first, data, tag="os84-до") as (call, _):
        token_before = _token(data)
        identity_before = call("/api/identity", expect=200).json()
        ctx.positive("до обновления продукт работает и знает свою сборку",
                     bool(identity_before.get("build_sha")), str(identity_before.get("source")))
    key_before = (data / "secret.key").read_bytes() if (data / "secret.key").is_file() else b""
    db_before = (data / "bcc.db").read_bytes()
    ctx.positive("после первой установки у владельца есть собственное хозяйство",
                 len(token_before) >= 16 and len(db_before) > 0 and len(key_before) > 0,
                 f"токен {len(token_before)} симв., база {len(db_before)} байт, "
                 f"ключ {len(key_before)} байт")

    # --- обновление: пакет пересобран, старый снесён целиком
    second = _build_package(ctx, "после-обновления")
    import shutil  # noqa: PLC0415

    shutil.rmtree(first)
    ctx.positive("старый каталог пакета снесён целиком, как при обновлении",
                 not first.exists() and second.exists(), f"{first.name} → {second.name}")

    with _installed_server(ctx, second, data, tag="os84-после") as (call, _):
        token_after = _token(data)
        system = call("/api/system", token=token_after, expect=200).json()
        ctx.positive("обновлённый продукт поднялся на ТОМ ЖЕ каталоге данных",
                     "health" in system, sorted(system)[:4])

    key_after = (data / "secret.key").read_bytes()
    ctx.positive("токен владельца не перевыписан обновлением",
                 token_after == token_before, "байты токена совпадают")
    ctx.positive("ключ шифрования не перевыписан: сохранённые ключи провайдеров остались читаемыми",
                 key_after == key_before, f"{len(key_after)} байт, совпадает")
    ctx.positive("база владельца не обнулена обновлением",
                 (data / "bcc.db").stat().st_size >= len(db_before),
                 f"{(data / 'bcc.db').stat().st_size} байт против {len(db_before)}")

    # --- отрицательные контроли
    ctx.negative("данные не лежали внутри снесённого пакета",
                 (data / "token").is_file() and (data / "bcc.db").is_file(),
                 ", ".join(sorted(p.name for p in data.iterdir())))

    from bcc.auth import TokenAuth  # noqa: PLC0415
    from bcc.secrets import Vault  # noqa: PLC0415

    fresh = ctx.path("чужая-установка", "data")
    fresh_token = TokenAuth(fresh, announce=False).token
    Vault(fresh)                                   # ключ заводится так же, как на первом старте
    fresh_key = (fresh / "secret.key").read_bytes()
    ctx.negative("на ПУСТОМ каталоге токен другой — совпадение выше не константа",
                 fresh_token != token_before and len(fresh_token) >= 16,
                 "токены разных установок различаются")
    ctx.negative("ключ шифрования новой установки тоже другой",
                 len(fresh_key) > 0 and fresh_key != key_before,
                 "ключи разных установок различаются")
    ctx.negative("повторный первый запуск не считает себя первым: токен не пересоздаётся",
                 TokenAuth(fresh, announce=False).token == fresh_token,
                 "второе открытие того же каталога вернуло тот же токен")


# ------------------------------------------------------------------ 85
@scenario(id="OS-85", depth=INSTALLED_PRODUCT)
def os85_version_names_the_installed_code(ctx) -> None:
    """Версия, которую показывает продукт, — личность ТОГО САМОГО кода.

    Строка `__version__` одинакова у всех сборок и на вопрос «какой код у меня
    работает» не отвечает. Отвечает отметка сборки: `bcc/_build.json` приезжает
    внутри пакета, и установленный продукт называет её на `/api/identity` и
    `/health`.

    Отрицательная половина закрывает три способа соврать: подставить SHA
    окружением, испортить отметку и позаимствовать личность соседнего
    репозитория. Последнее здесь не абстракция — репозиторий физически лежит на
    пути установленного процесса (ради `bossman_shared`), и `git` рядом работает.
    """
    pkg = _build_package(ctx, "личность")
    data = ctx.path("личность", "data")
    manifest = pkg / "bcc" / "_build.json"
    stamped = json.loads(manifest.read_text(encoding="utf-8"))["source_sha"]

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                          capture_output=True, text=True, timeout=60).stdout.strip()
    ctx.positive("отметка сборки указывает на ТО ДЕРЕВО, из которого собран пакет",
                 bool(HEX40.fullmatch(stamped or "")) and stamped == head,
                 f"{(stamped or '')[:12]} = HEAD {head[:12]}")

    with _installed_server(ctx, pkg, data, tag="os85") as (call, _):
        identity = call("/api/identity", expect=200).json()
        ctx.positive("продукт называет личность установленного кода, а не «checkout»",
                     identity.get("build_sha") == stamped
                     and identity.get("source") == "installed_build"
                     and identity.get("source_identity") == "PASS",
                     f"{identity.get('source')} / {(identity.get('build_sha') or '')[:12]}")
        live = call("/health/live", expect=200).json()
        health = call("/health").json()
        ctx.positive("здоровье несёт ТУ ЖЕ личность: «что-то живо» — не ответ",
                     live.get("build_sha") == stamped and health.get("build_sha") == stamped,
                     f"live={(live.get('build_sha') or '')[:12]}, health={(health.get('build_sha') or '')[:12]}")
        ctx.reached_installed_product("собранный пакет + /api/identity установленного процесса")

    # --- отрицательные контроли
    forced = _identity_of(pkg, data, BCC_BUILD_SHA="0" * 40)
    ctx.negative("личность нельзя назначить переменной окружения",
                 forced.get("build_sha") == stamped, str(forced.get("build_sha"))[:16])

    original = manifest.read_text(encoding="utf-8")
    try:
        manifest.write_text(json.dumps({"source_sha": "z" * 40, "source_dirty": False}) + "\n",
                            encoding="utf-8")
        broken = _identity_of(pkg, data)
        ctx.negative("испорченная отметка не становится версией",
                     broken.get("source_identity") == "SOURCE_IDENTITY_UNKNOWN"
                     and broken.get("build_sha") is None,
                     f"{broken.get('source_identity')} / {broken.get('build_sha')}")
        ctx.negative("недоказанная личность — НАЗВАННОЕ состояние с причиной, а не пустое поле",
                     bool(broken.get("detail")) and broken.get("source") == "installed_build",
                     str(broken.get("detail"))[:110])

        manifest.unlink()
        orphan = _identity_of(pkg, data)
        ctx.negative("без отметки пакет НЕ занимает личность соседнего репозитория",
                     orphan.get("source_identity") == "SOURCE_IDENTITY_UNKNOWN"
                     and orphan.get("build_sha") is None
                     and head not in json.dumps(orphan, ensure_ascii=False),
                     f"{orphan.get('source_identity')}, sha репозитория в ответе не найден")
    finally:
        manifest.write_text(original, encoding="utf-8")

    restored = _identity_of(pkg, data)
    ctx.positive("вернули отметку — вернулась и личность (измерение не одностороннее)",
                 restored.get("build_sha") == stamped, (restored.get("build_sha") or "")[:12])
