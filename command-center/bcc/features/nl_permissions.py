"""Feature — компилятор прав из естественного языка (V2.1, фаза L).

Вход: «Coder может править D:/Projects, запускать тесты автоматически, спрашивать
перед установками и git push, никогда не трогать кошельки».
Выход: структурная политика + `tool_rules` в том самом формате, который читает
`bcc.tools.decide_effect` — иначе компиляция была бы косметикой.

Правила модуля (нарушать нельзя):
  * разбор ДЕТЕРМИНИРОВАННЫЙ, русский и английский, без вызова модели —
    как в bcc/features/nl_orchestra.py;
  * расширение доступа НИКОГДА не применяется молча: сначала показывается точная
    дельта, затем нужна строка в канонической очереди approvals и её одобрение;
  * сужение (deny/ask там, где было свободнее) применяется сразу — безопасность
    не должна ждать;
  * нераспознанная фраза попадает в `unrecognised`, а не выбрасывается тихо;
  * компилятор НЕ выдаёт агенту права (`agents.permissions[...] = true`) — только
    правила по конкретным ресурсам. Фраза «может править D:/Projects» не должна
    превращаться в бланкетное `filesystem.write` на всю машину.
"""
from __future__ import annotations

import fnmatch
import json
import re
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import agents as agents_t, approvals as approvals_t, utcnow
from ..tools import args_hash
from . import Feature

router = APIRouter()

STRICTNESS = {"auto": 0, "ask": 1, "deny": 2}
BASELINE = "ask"          # чего стоит ждать там, где правил ещё нет


# ------------------------------------------------------------------ эффекты

#: Порядок важен: «без подтверждения» — это AUTO, а не ASK, поэтому отрицание
#: проверяется раньше самих ключевых слов.
NO_ASK_RE = re.compile(
    r"(?iu)без\s+(?:спрос|подтвержд|вопрос|согласов)|"
    r"without\s+(?:asking|confirmation|approval|permission)")

EFFECT_RES: list[tuple[str, re.Pattern[str]]] = [
    ("deny", re.compile(
        r"(?iu)никогда|ни\s+при\s+каких|не\s+давай|не\s+разреш|не\s+должен|запрет|запрещ|"
        r"нельзя|исключ(?:и|ено)\b|"
        r"\bnever\b|\bdeny\b|\bforbid\w*|\bblock\b|\bprohibit\w*|\bno\s+access\b|"
        r"\bnot\s+allowed\b|\bdon'?t\s+(?:let|allow|touch)\b")),
    ("ask", re.compile(
        r"(?iu)спрашива|спроси|подтвержд|согласов|уточня|с\s+разрешения|только\s+с\s+моего|"
        r"\bask\b|\bconfirm\w*|\bapprov\w*|\bpermission\s+first\b|\bcheck\s+with\s+me\b")),
    ("auto", re.compile(
        r"(?iu)автоматическ|автомат\b|разреш|может|можно|умеет|вправе|самостоятельн|"
        r"\bautomatic\w*|\bauto\b|\bcan\b|\bmay\b|\ballow\w*|\bfreely\b|\bok\s+to\b")),
]


def detect_effect(clause: str) -> str | None:
    if NO_ASK_RE.search(clause):
        return "auto"
    for effect, pattern in EFFECT_RES:
        if pattern.search(clause):
            return effect
    return None


# ------------------------------------------------------------------ объекты

#: (имя, регэксп, раздел политики, [(инструмент, ресурс)…])
SUBJECTS: list[tuple[str, re.Pattern[str], str, list[tuple[str, str]]]] = [
    ("wallets", re.compile(r"(?iu)\bwallets?\b|кошель|кошелёк|кошелек|"
                           r"seed[\s_-]?phrase|сид[\s-]?фраз|private[\s_-]?key|приватн\w*\s+ключ"),
     "secrets", [("*", "*wallet*"), ("*", "*keystore*"),
                 ("*", "*seed_phrase*"), ("*", "*private_key*")]),
    ("secrets", re.compile(r"(?iu)\bsecrets?\b|\.env\b|credential\w*|id_rsa|"
                           r"секрет\w*|учётн\w*\s+данн|парол\w*|\btokens?\b|\bkeys?\b"),
     "secrets", [("*", "*.env*"), ("*", "*id_rsa*"), ("*", "*credentials*"),
                 ("*", "*secret*")]),
    ("tests", re.compile(r"(?iu)\btests?\b|\btesting\b|\bpytest\b|тест\w*|автотест\w*"),
     "terminal", [("terminal.run", "pytest*"), ("terminal.run", "npm test*"),
                  ("terminal.run", "pnpm test*"), ("terminal.run", "go test*")]),
    ("installs", re.compile(r"(?iu)\binstall\w*\b|\bdependenc\w+\b|\bpackages?\b|"
                            r"установ\w*|инстал\w*|зависимост\w*|пакет\w*"),
     "terminal", [("terminal.run", "npm install*"), ("terminal.run", "pip install*"),
                  ("terminal.run", "yarn add*"), ("terminal.run", "pnpm add*"),
                  ("terminal.run", "apt-get install*")]),
    ("git_push", re.compile(r"(?iu)git\s+push|\bpush\w*\b|пуш\w*|публикац\w*\s+измен"),
     "terminal", [("terminal.run", "git push*")]),
    ("git_read", re.compile(r"(?iu)git\s+(?:status|log|diff)|статус\w*\s+git|истори\w*\s+git"),
     "terminal", [("terminal.run", "git status*"), ("terminal.run", "git log*"),
                  ("terminal.run", "git diff*")]),
    ("lint", re.compile(r"(?iu)\blint\w*\b|\bruff\b|\beslint\b|\bflake8\b|линт\w*"),
     "terminal", [("terminal.run", "ruff*"), ("terminal.run", "eslint*"),
                  ("terminal.run", "flake8*")]),
    ("build", re.compile(r"(?iu)\bbuild\w*\b|\bcompile\w*\b|сборк\w*|собира\w*|компилир\w*"),
     "terminal", [("terminal.run", "npm run build*"), ("terminal.run", "make*")]),
    ("docker", re.compile(r"(?iu)\bdocker\b|\bcompose\b|докер\w*|контейнер\w*"),
     "terminal", [("terminal.run", "docker*")]),
    ("sudo", re.compile(r"(?iu)\bsudo\b|\broot\b|повышен\w*\s+прав|админск\w*\s+прав"),
     "terminal", [("terminal.run", "sudo*")]),
    ("destructive", re.compile(r"(?iu)\brm\s+-rf\b|\bdelete\s+files?\b|\bwipe\b|"
                               r"удал\w*\s+файл|снос\w*|форматир\w*"),
     "terminal", [("terminal.run", "rm -rf*"), ("terminal.run", "mkfs*")]),
    ("browser", re.compile(r"(?iu)\bbrowser\b|\bweb\s?sites?\b|\binternet\b|"
                           r"браузер\w*|интернет\w*|сайт\w*"),
     "browser", [("browser.*", "*")]),
    ("payments", re.compile(r"(?iu)\bpayments?\b|\binvoices?\b|\bbilling\b|"
                            r"платеж\w*|оплат\w*|счет\w*\s+на\s+оплату"),
     "secrets", [("*", "*payment*"), ("*", "*invoice*")]),
]

#: Путь: Windows-диск, абсолютный POSIX, ~/, ./ или ../ . URL не путь.
PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s,;\"'«»]*"
    r"|~/[^\s,;\"'«»]+"
    r"|\.{1,2}/[^\s,;\"'«»]+"
    r"|(?<![:\w./])/[A-Za-z0-9_.\-][^\s,;\"'«»]*)")

WRITE_RE = re.compile(r"(?iu)\bedit\w*\b|\bwrite\b|\bmodif\w+|\bchange\b|\bcreate\b|"
                      r"редактир\w*|правит|править|изменя\w*|измен\w*|пис\w*|запис\w*|"
                      r"менять|создава\w*")
READ_RE = re.compile(r"(?iu)\bread\w*\b|\bview\w*\b|\bbrowse\b|\baccess\b|\bopen\b|"
                     r"чита\w*|чтени\w*|смотр\w*|просмотр\w*|доступ\w*|открыва\w*")

CLAUSE_SPLIT = re.compile(r"[,.;\n·]|\bно\b|\bа\s+также\b|\bthen\b")
WORD_RE = re.compile(r"(?u)\w+")


def _globify(path: str) -> str:
    p = path.strip().rstrip("/\\")
    if any(ch in p for ch in "*?"):
        return p
    return f"{p}/**"


def _paths(clause: str) -> list[str]:
    out: list[str] = []
    for m in PATH_RE.finditer(clause):
        raw = m.group(0).strip().rstrip(".,;")
        if raw.lower().startswith(("http://", "https://")) or len(raw) < 2:
            continue
        g = _globify(raw)
        if g not in out:
            out.append(g)
    return out


def compile_policy(text: str) -> dict[str, Any]:
    """NL → {policy, tool_rules, unrecognised}. Ничего не сохраняет."""
    rules: list[dict] = []
    sections: dict[tuple[str, str], str] = {}
    unrecognised: list[dict] = []
    clauses_out: list[dict] = []
    carry: str | None = None

    def add(tool: str, resource: str, effect: str, reason: str, section: str) -> None:
        rules.append({"tool": tool, "resource": resource, "effect": effect, "reason": reason})
        sections[(tool, resource)] = section

    for raw in CLAUSE_SPLIT.split(text or ""):
        clause = raw.strip()
        if not clause or not WORD_RE.search(clause):
            continue
        effect = detect_effect(clause)
        inherited = False
        if effect is None and carry is not None:
            effect, inherited = carry, True
        found: list[str] = []

        paths = _paths(clause)
        if paths and effect is not None:
            # «править» → чтение и запись; «читать» → только чтение;
            # запрет закрывает и то и другое, что бы ни было за глагол.
            writable = bool(WRITE_RE.search(clause)) or not READ_RE.search(clause)
            for p in paths:
                found.append(f"путь {p}")
                add("filesystem.read", p, effect, f"«{clause}»", "filesystem")
                if writable or effect == "deny":
                    add("filesystem.write", p, effect, f"«{clause}»", "filesystem")

        for name, pattern, section, pairs in SUBJECTS:
            if not pattern.search(clause):
                continue
            if effect is None:
                continue
            found.append(name)
            for tool, resource in pairs:
                add(tool, resource, effect, f"«{clause}»", section)

        if not found:
            reason = ("не понял, что разрешать или запрещать" if effect is not None
                      else "не распознаны ни действие (разрешить/спросить/запретить), "
                           "ни объект (путь, команда, ресурс)")
            unrecognised.append({"text": clause, "reason": reason})
            continue
        clauses_out.append({"text": clause, "effect": effect, "subjects": found,
                            "inherited_effect": inherited})
        carry = effect

    # deny должен побеждать: decide_effect применяет правила по порядку, последнее
    # слово за последним совпавшим — сортируем по возрастанию строгости.
    rules.sort(key=lambda r: STRICTNESS.get(r["effect"], 1))
    final = _dedupe(rules)
    return {"policy": _render_policy(final, sections), "tool_rules": final,
            "unrecognised": unrecognised, "clauses": clauses_out}


#: Как показать правило filesystem человеку: (эффект, «пишем ли») → ключ списка.
_FS_KEYS = {("auto", "filesystem.read"): "allow_read",
            ("auto", "filesystem.write"): "allow_write",
            ("ask", "filesystem.read"): "ask_read",
            ("ask", "filesystem.write"): "ask_write",
            ("deny", "filesystem.read"): "deny",
            ("deny", "filesystem.write"): "deny"}


def _render_policy(rules: list[dict], sections: dict[tuple[str, str], str]) -> dict:
    """Читаемое превью строится ИЗ ИТОГОВЫХ правил — то, что видит человек,
    совпадает с тем, что применит рантайм."""
    policy: dict[str, Any] = {}
    for rule in rules:
        key = (rule["tool"], rule["resource"])
        section = sections.get(key, "other")
        if section == "filesystem":
            fs = policy.setdefault("filesystem", {})
            name = _FS_KEYS.get((rule["effect"], rule["tool"]))
            if name:
                fs.setdefault(name, []).append(rule["resource"])
        else:
            policy.setdefault(section, {})[rule["resource"]] = rule["effect"]
    if "filesystem" in policy:
        policy["filesystem"] = {k: sorted(dict.fromkeys(v))
                                for k, v in policy["filesystem"].items()}
    return policy


def _dedupe(rules: list[dict]) -> list[dict]:
    seen: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for r in rules:
        key = (r["tool"], r["resource"])
        if key not in seen:
            order.append(key)
        seen[key] = r          # последнее правило по паре — оно и в силе
    return [seen[k] for k in order]


# ------------------------------------------------------------------ дельта

def effective_effect(rules: list[dict], tool: str, resource: str,
                     baseline: str = BASELINE) -> str:
    """Какой эффект дадут правила для этой пары (как в bcc.tools.decide_effect:
    последнее совпавшее правило побеждает)."""
    effect = baseline
    for rule in rules or []:
        if fnmatch.fnmatch(tool, str(rule.get("tool") or "*")) \
                and fnmatch.fnmatch(resource, str(rule.get("resource") or "*")):
            effect = str(rule.get("effect") or effect)
    return effect


def policy_delta(old: list[dict], new: list[dict]) -> dict:
    """Точная дельта: где стало свободнее (broadened) и где строже (narrowed).

    Проба берётся по самим шаблонам правил — и старых, и новых: снятие старого
    запрета тоже расширение доступа, даже если новых правил там нет.
    """
    probes: list[tuple[str, str]] = []
    for rule in list(new) + list(old):
        key = (str(rule.get("tool") or "*"), str(rule.get("resource") or "*"))
        if key not in probes:
            probes.append(key)
    broadened, narrowed = [], []
    for tool, resource in probes:
        was = effective_effect(old, tool, resource)
        now = effective_effect(new, tool, resource)
        if STRICTNESS.get(now, 1) < STRICTNESS.get(was, 1):
            broadened.append({"tool": tool, "resource": resource, "was": was, "now": now})
        elif STRICTNESS.get(now, 1) > STRICTNESS.get(was, 1):
            narrowed.append({"tool": tool, "resource": resource, "was": was, "now": now})
    return {"broadens": bool(broadened), "broadened": broadened, "narrowed": narrowed}


# ------------------------------------------------------------------ хранилище

async def _agent(svc, agent_id: Any) -> dict:
    try:
        agent_id = int(agent_id)
    except (TypeError, ValueError):
        raise HTTPException(422, {"message": "agent_id должен быть числом"})
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(agents_t)
                               .where(agents_t.c.id == agent_id))).first()
    if row is None:
        raise HTTPException(404, {"message": "агент не найден"})
    return dict(row._mapping)


def _perms(agent: dict) -> dict:
    perms = agent.get("permissions")
    if isinstance(perms, list):
        return {str(p): True for p in perms}
    return dict(perms) if isinstance(perms, dict) else {}


def _current_rules(agent: dict) -> list[dict]:
    rules = _perms(agent).get("tool_rules")
    return [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []


async def _store(svc, agent: dict, compiled: dict, text: str) -> dict:
    perms = _perms(agent)
    perms["tool_rules"] = compiled["tool_rules"]
    perms["policy"] = compiled["policy"]
    perms["policy_source"] = {"text": text, "compiled_at": utcnow().isoformat()}
    async with svc.db.session() as s:
        await s.execute(sa.update(agents_t).where(agents_t.c.id == agent["id"])
                        .values(permissions=perms))
        await s.commit()
    return perms


# ------------------------------------------------------------------ API

@router.post("/permissions/compile")
async def compile_permissions(request: Request):
    """NL → превью политики. НИЧЕГО не меняет."""
    svc = request.app.state.svc
    body = await request.json()
    text = str(body.get("text") or "")
    compiled = compile_policy(text)
    out: dict[str, Any] = {"policy": compiled["policy"], "tool_rules": compiled["tool_rules"],
                           "unrecognised": compiled["unrecognised"],
                           "clauses": compiled["clauses"],
                           "valid": bool(compiled["tool_rules"])}
    agent_id = body.get("agent_id")
    if agent_id is not None:
        agent = await _agent(svc, agent_id)
        delta = policy_delta(_current_rules(agent), compiled["tool_rules"])
        out["delta"] = delta
        out["broadening"] = delta["broadens"]
        out["requires_confirmation"] = delta["broadens"]
    await svc.bus.emit("permissions.compiled", rules=len(compiled["tool_rules"]),
                       unrecognised=len(compiled["unrecognised"]))
    return out


@router.post("/permissions/apply")
async def apply_permissions(request: Request):
    """Применить политику к агенту.

    Сужение — сразу. Расширение — только после одобрения строки approvals:
    первый вызов возвращает точную дельту и `approval_id`, повторный (с этим
    `approval_id` и тем же текстом) применяет её.
    """
    svc = request.app.state.svc
    body = await request.json()
    text = str(body.get("text") or "")
    agent_id = body.get("agent_id")
    if agent_id is None:
        raise HTTPException(422, {"message": "нужен agent_id"})
    agent = await _agent(svc, agent_id)
    agent_id = int(agent["id"])
    # политика ВСЕГДА компилируется на сервере из текста: присланному клиентом
    # объекту правил доверия нет.
    compiled = compile_policy(text)
    if not compiled["tool_rules"]:
        raise HTTPException(422, {"message": "из текста не выведено ни одного правила",
                                  "hint": "; ".join(u["text"] for u in compiled["unrecognised"])})
    delta = policy_delta(_current_rules(agent), compiled["tool_rules"])
    fingerprint = args_hash("permissions", {"agent_id": int(agent_id), "text": text})

    if not delta["broadens"]:
        await _store(svc, agent, compiled, text)
        await svc.bus.emit("permissions.applied", agent_id=int(agent_id), broadening=False)
        return {"applied": True, "broadening": False, "delta": delta,
                "policy": compiled["policy"], "tool_rules": compiled["tool_rules"],
                "unrecognised": compiled["unrecognised"]}

    approval_id = body.get("approval_id")
    if approval_id is None:
        preview = json.dumps({"agent_id": int(agent_id), "text": text,
                              "fingerprint": fingerprint, "broadened": delta["broadened"],
                              "policy": compiled["policy"]}, ensure_ascii=False)
        appr = await svc.approvals.create("permissions", preview=preview)
        return {"applied": False, "broadening": True, "requires_confirmation": True,
                "approval_id": int(appr["id"]), "delta": delta,
                "policy": compiled["policy"], "tool_rules": compiled["tool_rules"],
                "unrecognised": compiled["unrecognised"],
                "hint": "подтвердите расширение доступа и повторите вызов с approval_id"}

    async with svc.db.session() as s:
        row = (await s.execute(sa.select(approvals_t)
                               .where(approvals_t.c.id == int(approval_id)))).first()
    if row is None:
        raise HTTPException(404, {"message": "подтверждение не найдено"})
    appr = dict(row._mapping)
    if appr["kind"] != "permissions" or fingerprint not in (appr.get("preview") or ""):
        # подмена текста после одобрения не проходит
        raise HTTPException(409, {"message": "подтверждение выдано на другую политику"})
    if appr["status"] != "approved":
        raise HTTPException(409, {"message": "расширение доступа не подтверждено",
                                  "hint": f"статус подтверждения: {appr['status']}"})
    await _store(svc, agent, compiled, text)
    await svc.bus.emit("permissions.applied", agent_id=int(agent_id), broadening=True,
                       approval_id=int(approval_id))
    return {"applied": True, "broadening": True, "approval_id": int(approval_id),
            "delta": delta, "policy": compiled["policy"],
            "tool_rules": compiled["tool_rules"], "unrecognised": compiled["unrecognised"]}


@router.get("/permissions/{agent_id}")
async def get_permissions(agent_id: int, request: Request):
    agent = await _agent(request.app.state.svc, agent_id)
    perms = _perms(agent)
    return {"agent_id": agent_id, "policy": perms.get("policy") or {},
            "tool_rules": _current_rules(agent),
            "source": perms.get("policy_source") or {}}


FEATURE = Feature(name="nl_permissions", router=router)
