"""Self-extension of the PC operator: it saves the skills and small tools it worked out (owner 11.11 night).

Owner: «Я хочу просто локального агента которому дал команду — он сделал, по пути создавая tools and skills».

    skill.find {query}            -> best matching skills of the ONE Skill Library (.agents/skills/<id>/SKILL.md)
    skill.read {id}               -> the steps of one skill
    skill.save {id, title, when_to_use, steps, required_tools[]}
                                  -> writes/updates a skill through SkillLibrary.create (no second store), version + 1.
                                     Tools the agent does not already hold are NOT written: the draft goes to the
                                     canonical approvals queue exactly like Skill Forge (/api/skills/forge/apply).
    tool.create {name, description, language, code, args_schema}
                                  -> a script under %LOCALAPPDATA%\\Bossman\\operator_tools\\<name>\\ (versioned)
    tool.list_custom {}           -> the scripts saved so far
    tool.run_custom {name, args}  -> runs the script through terminal.run's own handler (same roots, STOP, TerminalPolicy)

A custom tool is never a bypass: its permission is ``terminal.run``; ``decide_effect`` sees the script's code through
the effect hook (hard DENY list of terminal.run, package installs / git push / sudo stay ASK even with «Полный доступ»),
money/mail/deploy/settings/cli.bypass code is refused at creation and at run, and the approved bytes are bound by
sha256 (bind_args) and copied to the task's scratch dir before they run, so approved code == executed code.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path
from typing import Any

from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from . import Feature

OWNER_TAG = "pc-operator"
TOOL_NAMES = ("skill.find", "skill.read", "skill.save", "tool.create", "tool.list_custom", "tool.run_custom")
NAME_RE = re.compile(r"[a-z][a-z0-9_-]{1,39}")
LANGS = {"python": "main.py", "powershell": "main.ps1"}
MAX_CODE = 32 * 1024
MAX_STEPS = 16 * 1024
MAX_ARGS = 20
MAX_ARG_CHARS = 500
TOOLS_DIR_ENV = "BOSSMAN_OPERATOR_TOOLS_DIR"

#: what a custom tool may never do, whatever the approval: money, mail, deploys, Bossman settings/agents, cli.bypass.
#: (constitution v3: these stay the owner's decision; terminal.run's HARD_DENY covers deletes/format/keys on top.)
PRIVILEGED = [
    (re.compile(r"(?i)\b(?:smtplib|Send-MailMessage|sendmail|imaplib)\b"), "отправка почты"),
    (re.compile(r"(?i)\b(?:stripe|paypal|invoice|payout|withdraw)\b"), "деньги / платежи"),
    (re.compile(r"(?i)\b(?:vercel|netlify|heroku|flyctl|wrangler)\b|\bdeploy\b"), "деплой"),
    (re.compile(r"(?i)/api/(?:settings|agents|operator|permissions|approvals|auth|providers)\b"),
     "настройки/права Bossman через API"),
    (re.compile(r"(?i)cli\.bypass|bypassPermissions|--dangerously"), "обход разрешений CLI"),
    (re.compile(r"(?i)\b(?:Format-Volume|Clear-Disk|Remove-Partition|Initialize-Disk|bcdedit|vssadmin)\b"),
     "диски / загрузка ОС"),
    (re.compile(r"""(?i)\bshutil\.rmtree\(\s*(?:r?["'](?:[A-Za-z]:[\\/]*|/|~)["']|Path\.home\(\))"""),
     "удаление корня диска / профиля"),
]
#: harmless text that the terminal's `\bformat\b` disk-format rule would misread in code (only for the code scan).
_FORMAT_FALSE_POSITIVES = re.compile(
    r"(?i)\.format\(|-format\s+[\"']|\bFormat-(?:Table|List|Wide|Custom|Hex)\b|\bformat_|_format\b")


# ------------------------------------------------------------------ custom tool storage

def tools_root() -> Path:
    override = os.environ.get(TOOLS_DIR_ENV)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "Bossman" / "operator_tools"


def _tool_dir(name: str) -> Path:
    """The folder of one custom tool; refuses anything that is not a plain name inside the root."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError("имя инструмента: 2–40 символов a-z 0-9 _ -, начинается с буквы")
    root = tools_root().resolve()
    d = (root / name).resolve()
    if d.parent != root:
        raise ValueError("имя инструмента выходит за папку инструментов")
    return d


def _load(name: str) -> tuple[dict, bytes]:
    d = _tool_dir(name)
    manifest = json.loads((d / "tool.json").read_text(encoding="utf-8"))
    code = (d / LANGS[manifest["language"]]).read_bytes()
    return manifest, code


def code_refusal(code: str) -> str:
    """Why this script may never run (terminal.run HARD_DENY + PRIVILEGED). Empty string = nothing found."""
    from .tools_terminal import hard_deny_reason
    scan = _FORMAT_FALSE_POSITIVES.sub(" ", code or "")
    deny = hard_deny_reason(scan)
    if deny:
        return deny
    for pattern, reason in PRIVILEGED:
        if pattern.search(code or ""):
            return f"{reason} — не для собственных инструментов, только решение владельца"
    return ""


def _argv(args: Any) -> list[str]:
    if args is None or args == "":
        return []
    if isinstance(args, dict):
        out: list[str] = []
        for k, v in args.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(k)):
                raise ValueError(f"имя аргумента «{k}» недопустимо")
            out += [f"--{k}", v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)]
    elif isinstance(args, list):
        out = [a if isinstance(a, str) else json.dumps(a, ensure_ascii=False) for a in args]
    else:
        out = [str(args)]
    if len(out) > MAX_ARGS * 2 or any(len(a) > MAX_ARG_CHARS for a in out):
        raise ValueError(f"слишком много или слишком длинные аргументы (до {MAX_ARGS}, по {MAX_ARG_CHARS} знаков)")
    return out


def _command(language: str, script: str, argv: list[str]) -> str:
    """The exact shell line terminal.run gets (host shell = sh -lc on Windows with Git, else /bin/sh)."""
    q = [shlex.quote(a) for a in argv]
    if language == "python":
        exe = shlex.quote(Path(sys.executable).as_posix())
        return " ".join([exe, shlex.quote(script), *q])
    return " ".join(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                     shlex.quote(script), *q])


def _run_custom_effect(args: dict) -> tuple[str, str] | None:
    """Policy by the script's CODE and the real command line. May only tighten (decide_effect is monotone)."""
    from .tools_terminal import _run_effect, extra_ask_reason
    name = str(args.get("name") or "")
    try:
        manifest, code = _load(name)
        argv = _argv(args.get("args"))
    except (ValueError, OSError, KeyError) as exc:
        return "deny", f"tool.run_custom: {exc}"
    text = code.decode("utf-8", errors="replace")
    refused = code_refusal(text)
    if refused:
        return "deny", f"деструктивный/запрещённый код инструмента «{name}»: {refused}"
    ask = extra_ask_reason(text)
    if ask:
        return "ask", f"требует подтверждения: {ask}"
    command = _command(manifest["language"], f"tool_{name}{Path(LANGS[manifest['language']]).suffix}", argv)
    return _run_effect({"command": command, "mode": "project_host"})


async def _bind_code(args: dict) -> str | None:
    """The approval (and the run) is bound to the script's sha256 at decision time."""
    name = str(args.get("name") or "")
    try:
        _manifest, code = await asyncio.to_thread(_load, name)
    except (ValueError, OSError, KeyError) as exc:
        return f"tool.run_custom: инструмент «{name}» не найден ({type(exc).__name__})"
    digest = hashlib.sha256(code).hexdigest()
    previous = str(args.get("_code_sha256") or "")
    if previous and previous != digest:
        return f"код инструмента «{name}» изменился после одобрения — нужен новый вызов"
    args["_code_sha256"] = digest
    return None


async def _t_tool_create(args: dict, ctx: ToolContext) -> ToolResult:
    name = str(args.get("name") or "")
    language = str(args.get("language") or "").lower()
    code = str(args.get("code") or "")
    description = str(args.get("description") or "").strip()[:500]
    schema = args.get("args_schema") or {}
    try:
        d = _tool_dir(name)
    except ValueError as exc:
        return ToolResult(content=f"tool.create: {exc}", error=True)
    if language not in LANGS:
        return ToolResult(content="tool.create: language — python или powershell", error=True)
    if not code.strip() or len(code.encode("utf-8")) > MAX_CODE:
        return ToolResult(content=f"tool.create: code пустой или больше {MAX_CODE} байт", error=True)
    if not description:
        return ToolResult(content="tool.create: нужен description — по нему инструмент найдут", error=True)
    if not isinstance(schema, dict):
        return ToolResult(content="tool.create: args_schema должен быть объектом", error=True)
    refused = code_refusal(code)
    if refused:
        return ToolResult(content=f"tool.create: такой код не сохраняется ({refused})", error=True)

    def write() -> dict:
        d.mkdir(parents=True, exist_ok=True)
        mf = d / "tool.json"
        old = json.loads(mf.read_text(encoding="utf-8")) if mf.is_file() else None
        version = int((old or {}).get("version") or 0) + 1
        if old:
            prev = d / LANGS[old["language"]]
            if prev.is_file():
                prev.replace(d / f"v{version - 1}{prev.suffix}")
        (d / LANGS[language]).write_text(code, encoding="utf-8", newline="\n")
        manifest = {"name": name, "description": description, "language": language, "args_schema": schema,
                    "version": version, "sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
                    "agent_id": (ctx.agent or {}).get("id"), "task_id": (ctx.task or {}).get("id"),
                    "updated_at": int(time.time())}
        mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        return manifest

    try:
        manifest = await asyncio.to_thread(write)
    except OSError as exc:
        return ToolResult(content=f"tool.create: не записался ({type(exc).__name__})", error=True)
    try:
        await ctx.svc.bus.emit("operator.tool.created", name=name, version=manifest["version"],
                               task_id=(ctx.task or {}).get("id"))
    except Exception:  # noqa: BLE001 - an event is not the result
        pass
    return ToolResult(content=(f"инструмент «{name}» сохранён (версия {manifest['version']}). "
                               f"Вызов: tool.run_custom name={name} args={{...}}"),
                      one_line=f"tool.create: {name} v{manifest['version']}",
                      data={"name": name, "version": manifest["version"], "sha256": manifest["sha256"][:16]})


def _list_tools() -> list[dict]:
    root = tools_root()
    out = []
    if not root.is_dir():
        return out
    for mf in sorted(root.glob("*/tool.json")):
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({k: m.get(k) for k in ("name", "description", "language", "args_schema", "version")})
    return out


async def _t_tool_list(args: dict, ctx: ToolContext) -> ToolResult:
    items = await asyncio.to_thread(_list_tools)
    if not items:
        return ToolResult(content="собственных инструментов пока нет (создай tool.create)",
                          one_line="tool.list_custom: 0", data={"tools": []})
    lines = [f"- {t['name']} (v{t['version']}, {t['language']}): {t['description']} "
             f"args={json.dumps(t['args_schema'] or {}, ensure_ascii=False)}" for t in items]
    return ToolResult(content="\n".join(lines), one_line=f"tool.list_custom: {len(items)}", data={"tools": items})


async def _t_tool_run(args: dict, ctx: ToolContext) -> ToolResult:
    from ..v2 import scratch
    from .tools_terminal import _tool_run
    name = str(args.get("name") or "")
    try:
        manifest, code = await asyncio.to_thread(_load, name)
        argv = _argv(args.get("args"))
    except (ValueError, OSError, KeyError) as exc:
        return ToolResult(content=f"tool.run_custom: {exc}", error=True)
    digest = hashlib.sha256(code).hexdigest()
    approved = str(args.get("_code_sha256") or "")
    if approved and approved != digest:
        return ToolResult(content=f"код инструмента «{name}» изменился после одобрения — нужен новый вызов",
                          error=True)
    refused = code_refusal(code.decode("utf-8", errors="replace"))
    if refused:      # effect-time re-check: the file is re-read, the decision is not trusted from before
        return ToolResult(content=f"инструмент «{name}» запрещён ({refused})", error=True)
    # Run exactly these bytes: a copy in the task's own scratch dir (always an allowed root of terminal.run).
    own = scratch.ensure(scratch.for_context(ctx))
    script = f"tool_{name}{Path(LANGS[manifest['language']]).suffix}"
    await asyncio.to_thread((own / script).write_bytes, code)
    command = _command(manifest["language"], script, argv)
    timeout = args.get("timeout") or 120
    result = await _tool_run({"command": command, "cwd": "scratch", "mode": "project_host",
                              "timeout": timeout}, ctx)
    result.data = {**(result.data or {}), "tool": name, "version": manifest.get("version"),
                   "sha256": digest[:16]}
    result.one_line = f"tool.run_custom {name}: " + (result.one_line or "")
    return result


# ------------------------------------------------------------------ skills (the existing Skill Library)

def _lib(svc):
    from .skills import _lib as skills_lib
    return skills_lib(svc)


def _owner_of(sk) -> str:
    meta = (sk.frontmatter or {}).get("metadata")
    return str(meta.get("owner") or "") if isinstance(meta, dict) else ""


def _words(text: str) -> list[str]:
    return [w[:6] for w in re.findall(r"[\wа-яё]+", (text or "").lower()) if len(w) >= 3]


def find_skills(lib, query: str, limit: int = 5) -> list[dict]:
    words = _words(query)
    if not words:
        return []
    scored = []
    for sk in lib.by_id().values():
        head = f"{sk.id} {sk.name} {sk.description}".lower()
        body = (sk.body or "").lower()[:20000]
        score = sum(3 * head.count(w) + min(body.count(w), 3) for w in words)
        if _owner_of(sk) == OWNER_TAG:
            score *= 2          # its own proven procedures first
        if score:
            scored.append((score, sk))
    scored.sort(key=lambda x: (-x[0], x[1].id))
    from ..v2.skill_library import skill_contract
    return [{"id": sk.id, "description": sk.description[:300], "score": s, "own": _owner_of(sk) == OWNER_TAG,
             "version": skill_contract(sk).version} for s, sk in scored[:limit]]


async def _t_skill_find(args: dict, ctx: ToolContext) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolResult(content="skill.find: нужен query", error=True)
    hits = await asyncio.to_thread(find_skills, _lib(ctx.svc), query)
    if not hits:
        return ToolResult(content=f"навыков по «{query}» нет — решай задачу, потом сохрани skill.save",
                          one_line="skill.find: 0", data={"skills": []})
    lines = [f"- {h['id']} (v{h['version']}{', твой' if h['own'] else ''}): {h['description']}" for h in hits]
    return ToolResult(content="Найдены навыки (прочитай подходящий через skill.read):\n" + "\n".join(lines),
                      one_line=f"skill.find: {len(hits)}", data={"skills": hits})


async def _t_skill_read(args: dict, ctx: ToolContext) -> ToolResult:
    from ..v2.skill_library import skill_contract
    sid = str(args.get("id") or "")
    sk = await asyncio.to_thread(lambda: _lib(ctx.svc).by_id().get(sid))
    if sk is None:
        return ToolResult(content=f"skill.read: навыка «{sid}» нет", error=True)
    con = skill_contract(sk)
    body = (sk.body or "")[:12000]
    tools = ", ".join(con.required_tools) or "—"
    return ToolResult(content=f"Навык {sk.id} v{con.version}\nИнструменты: {tools}\n\n{body}",
                      one_line=f"skill.read: {sk.id} v{con.version}",
                      truncated=len(sk.body or "") > 12000, more="навык длиннее 12000 знаков",
                      data={"id": sk.id, "version": con.version, "required_tools": con.required_tools})


def _skill_text(sid: str, title: str, when: str, steps: str, tools: list[str], version: int) -> str:
    import yaml
    fm: dict[str, Any] = {"name": sid,
                          "description": f"{title}. Когда: {when}"[:1000],
                          "metadata": {"owner": OWNER_TAG, "version": str(version)}}
    if tools:
        fm["required_tools"] = tools
    head = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, default_flow_style=False).strip()
    safe_title = title.replace("\r", " ").replace("\n", " ").strip()
    return (f"---\n{head}\n---\n\n# {safe_title}\n\n## Когда применять\n{when.strip()}\n\n"
            f"## Шаги\n{steps.strip()}\n")


def _held(agent: dict, tool: str) -> bool:
    import fnmatch
    from ..tools import _agent_tool_names
    return any(fnmatch.fnmatch(tool, p) for p in _agent_tool_names(agent or {}))


async def _t_skill_save(args: dict, ctx: ToolContext) -> ToolResult:
    from ..v2.skill_library import SkillManifestError, skill_contract
    from . import skills as skills_mod
    sid = str(args.get("id") or "").strip()
    title = str(args.get("title") or "").strip()[:200]
    when = str(args.get("when_to_use") or "").strip()[:600]
    steps = str(args.get("steps") or "").strip()
    tools = args.get("required_tools") or []
    if isinstance(tools, str):
        tools = [t for t in re.split(r"[\s,]+", tools) if t]
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        return ToolResult(content="skill.save: required_tools — список имён инструментов", error=True)
    tools = sorted(set(tools))
    if not title or not when or not steps:
        return ToolResult(content="skill.save: нужны title, when_to_use и steps", error=True)
    if len(steps.encode("utf-8")) > MAX_STEPS:
        return ToolResult(content=f"skill.save: steps больше {MAX_STEPS} байт — сократи", error=True)
    lib = _lib(ctx.svc)
    current = await asyncio.to_thread(lambda: lib.by_id().get(sid))
    if current is not None and _owner_of(current) != OWNER_TAG:
        return ToolResult(content=f"skill.save: «{sid}» — чужой навык (не твой), выбери другое id", error=True)
    try:
        version = int(float(skill_contract(current).version)) + 1 if current is not None else 1
    except ValueError:
        version = 2
    content = _skill_text(sid, title, when, steps, tools, version)
    widened = [t for t in tools if not _held(ctx.agent, t)]
    if widened:
        # Same gate as Skill Forge: the canonical approvals queue; the owner's yes + /api/skills/forge/apply writes it.
        preview = json.dumps({"skill": sid, "added_tools": widened, "agent_id": (ctx.agent or {}).get("id"),
                              "source": "skill.save"}, ensure_ascii=False)
        appr = await ctx.svc.approvals.create("skill_permissions", preview=preview,
                                              task_id=(ctx.task or {}).get("id"))
        state = await skills_mod._forge_state(ctx.svc)
        state["pending"][str(appr["id"])] = {"id": sid, "content": content, "added_tools": widened,
                                             "added_permissions": []}
        await skills_mod._save_forge_state(ctx.svc, state)
        return ToolResult(content=(f"навык «{sid}» НЕ сохранён: инструменты {', '.join(widened)} тебе не выданы. "
                                   f"Создано подтверждение владельцу #{appr['id']}. Сохрани без них или жди решения."),
                          one_line=f"skill.save: ждёт одобрения #{appr['id']}", error=True,
                          data={"approval_id": appr["id"], "added_tools": widened})
    try:
        sk = await asyncio.to_thread(lib.create, sid, content, overwrite=current is not None)
    except (SkillManifestError, ValueError, OSError) as exc:
        return ToolResult(content=f"skill.save: {exc}", error=True)
    con = skill_contract(sk)
    try:
        await skills_mod._persist_skill_version(ctx.svc, con, sk.description)
        await ctx.svc.bus.emit("skill.updated" if current is not None else "skill.created", slug=sk.id,
                               source="skill.save")
    except Exception:  # noqa: BLE001 - the file is the skill; the version row is bookkeeping
        pass
    return ToolResult(content=f"навык «{sk.id}» сохранён, версия {con.version}. В следующий раз: skill.find → skill.read.",
                      one_line=f"skill.save: {sk.id} v{con.version}",
                      data={"id": sk.id, "version": con.version, "fingerprint": sk.fingerprint[:16]})


SPECS = [
    ToolSpec(name="skill.find",
             description="Найти сохранённые навыки (проверенные процедуры) по словам задачи. Вызывай ПЕРЕД задачей.",
             handler=_t_skill_find, input_schema={"query": {"type": "string", "description": "суть задачи"}},
             required=["query"], category="read", source="builtin", default_effect="auto"),
    ToolSpec(name="skill.read", description="Прочитать шаги навыка по id из skill.find.",
             handler=_t_skill_read, input_schema={"id": {"type": "string"}}, required=["id"],
             category="read", source="builtin", default_effect="auto"),
    ToolSpec(name="skill.save",
             description=("Сохранить процедуру, которая сработала, как навык (новая версия при том же id). "
                          "id: латиница-дефис; steps: markdown с точными командами; required_tools: только твои."),
             handler=_t_skill_save,
             input_schema={"id": {"type": "string", "description": "например disk-free-space"},
                           "title": {"type": "string"},
                           "when_to_use": {"type": "string"},
                           "steps": {"type": "string", "description": "markdown, нумерованные шаги"},
                           "required_tools": {"type": "array", "items": {"type": "string"}}},
             required=["id", "title", "when_to_use", "steps"],
             category="write", permission="filesystem.write", source="builtin", default_effect="ask",
             idempotent=True),
    ToolSpec(name="tool.create",
             description=("Сохранить повторяемый скрипт как свой инструмент (python или powershell). Аргументы "
                          "приходят в sys.argv / $args (dict → --ключ значение). Потом вызывай tool.run_custom."),
             handler=_t_tool_create,
             input_schema={"name": {"type": "string", "description": "a-z0-9_-"},
                           "description": {"type": "string"},
                           "language": {"type": "string", "enum": list(LANGS)},
                           "code": {"type": "string"},
                           "args_schema": {"type": "object"}},
             required=["name", "description", "language", "code"],
             category="write", permission="filesystem.write", source="builtin", default_effect="ask",
             idempotent=True),
    ToolSpec(name="tool.list_custom", description="Список твоих сохранённых инструментов (tool.create).",
             handler=_t_tool_list, input_schema={}, category="read", source="builtin", default_effect="auto"),
    ToolSpec(name="tool.run_custom",
             description="Запустить свой инструмент из tool.create: name и args (объект или список строк).",
             handler=_t_tool_run,
             input_schema={"name": {"type": "string"},
                           "args": {"type": "object", "description": "{ключ: значение} (список строк тоже можно)"},
                           "timeout": {"type": "number"}},
             required=["name"], category="exec", permission="terminal.run", source="terminal",
             default_effect="ask", timeout_seconds=300.0, idempotent=False, external_output=True,
             effect_hook=_run_custom_effect, bind_args=_bind_code),
]


async def _setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_selfext", router=None, setup=_setup)
