"""Rave account pool: the owner's OWN subscription accounts, official CLIs only.

Why: a long rave can run into the usage limit of one Claude/Codex subscription. The owner may have
more than one account and wants the rave to go on. This module keeps that in Bossman's own code, in
the existing rave architecture, with hard edges:

* only the official `claude` / `codex` CLIs, each account in its OWN profile directory
  (`CLAUDE_CONFIG_DIR` / `CODEX_HOME`); Bossman never reads, copies, refreshes or moves a credential —
  it runs each CLI's own status command and keeps {logged in, plan}, never e-mail or tokens;
* the owner logs every account in by hand (`claude auth login` / `codex login` in that profile);
* OFF by default; turning it on is an owner opt-in through the normal approvals queue
  (kind `rave_pool_optin`), bound to the exact list of accounts shown in the approval;
* the state file `<data>/rave/pool.json` holds no secret: ids, labels, profile directory paths,
  login state, limit windows and a switch journal;
* an account is switched only BETWEEN agent runs: when one `claude -p` / `codex exec` run ends with
  "usage limit", that account is marked limited and the next run of the agent starts on the next
  ready account — never in the middle of a step. Every switch is written to the journal, to the
  rave's event timeline and to the agent's record, and is shown in the Rave page.

The pool is an owner decision with a terms-of-service risk (see docs/v1.9/AGENTIC_RAVE.md, "Пул
аккаунтов"): using several accounts to get around a usage limit may contradict Anthropic's / OpenAI's
terms; the code neither checks nor guarantees anything about that.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from . import connectors as cx

POOL_OPTIN_KIND = "rave_pool_optin"
TOOLS = ("claude", "codex")
MAX_ACCOUNTS = 8                      # per pool, all tools together
MAX_SWITCHES = 3                      # account changes inside ONE agent run
DEFAULT_COOLDOWN = 3600.0             # seconds an account stays "limited" when the CLI gave no reset time
JOURNAL_MAX = 200
PROFILES_ENV = "BOSSMAN_RAVE_PROFILES_DIR"
_ID = re.compile(r"^(claude|codex)-[0-9]{1,3}$")
_LABEL_BAD = re.compile(r"[\x00-\x1f\x7f]")

TERMS_NOTE = ("Пул аккаунтов нужен, чтобы рейв не вставал, когда у одного аккаунта кончился лимит. Это решение "
              "и риск владельца: использование нескольких аккаунтов ради обхода лимитов может противоречить "
              "условиям использования Anthropic / OpenAI (аккаунты ограничены, заблокированы). Bossman этого "
              "не проверяет и ничего не гарантирует; запускаются только официальные CLI под вашими входами, "
              "токены Bossman не читает и не копирует.")


class PoolError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.detail = status, {"code": code, "message": message, **extra}


def _now() -> float:
    return round(time.time(), 3)


def profiles_root() -> Path:
    """Default place for the profile directories Bossman creates (outside the data dir: a profile holds
    the CLI's own credentials and must not travel with data backups or evidence archives)."""
    raw = os.environ.get(PROFILES_ENV, "").strip()
    return Path(raw) if raw else Path.home() / ".bossman-rave-profiles"


def opt_in_preview(accounts: list[dict]) -> str:
    """The text the owner approves. Deterministic for a given account list: the approval is consumed only
    against exactly this text, so a pool that changed after the request needs a new approval."""
    rows = "\n".join(f"- {a['id']} · {a['tool']} · «{a['label']}» · профиль "
                     f"{a['profile_dir'] or 'по умолчанию (обычный вход CLI)'}"
                     for a in sorted(accounts, key=lambda x: x["id"]))
    return ("Agentic Rave: включить ПУЛ АККАУНТОВ — при исчерпании лимита следующий запуск агента пойдёт на "
            "следующий аккаунт из списка (только между запусками, каждое переключение видно в Rave). "
            f"Аккаунты:\n{rows}\n{TERMS_NOTE}")


class Pool:
    """`<data>/rave/pool.json`. Reads are synchronous (small file); every read-modify-write that can race
    (several agents of several raves) takes `self.lock`."""

    def __init__(self, root: Path):
        self.path = Path(root) / "pool.json"
        self.lock = asyncio.Lock()

    # ---- storage

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("version", 1)
        data.setdefault("enabled", False)
        data.setdefault("opt_in", None)
        data.setdefault("accounts", [])
        data.setdefault("journal", [])
        return data

    def save(self, data: dict) -> None:
        data["journal"] = data["journal"][-JOURNAL_MAX:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name("pool.json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _account(data: dict, account_id: str) -> dict:
        for a in data["accounts"]:
            if a["id"] == account_id:
                return a
        raise PoolError(404, "NOT_FOUND", f"аккаунт {account_id} не найден в пуле")

    @staticmethod
    def _journal(data: dict, event: str, **fields: Any) -> dict:
        entry = {"at": _now(), "event": event, **{k: v for k, v in fields.items() if v is not None}}
        data["journal"].append(entry)
        return entry

    # ---- views

    @staticmethod
    def effective_state(account: dict, at: float | None = None) -> str:
        at = _now() if at is None else at
        if (account.get("limited_until") or 0) > at:
            return "limited"
        return account.get("login_state") or "unknown"

    def view(self) -> dict:
        data = self.load()
        approved = set((data.get("opt_in") or {}).get("accounts") or [])
        accounts = []
        for a in data["accounts"]:
            accounts.append({**a, "state": self.effective_state(a), "approved": a["id"] in approved,
                             "login_step": cx.profile_login_step(a["tool"], a["profile_dir"])})
        active = bool(data["enabled"] and data["opt_in"])
        return {"enabled": data["enabled"], "active": active, "opt_in": data["opt_in"], "accounts": accounts,
                "waiting_approval": [a["id"] for a in accounts if not a["approved"]],
                "journal": data["journal"][-30:], "terms_note": TERMS_NOTE, "profiles_root": str(profiles_root()),
                "max_accounts": MAX_ACCOUNTS, "max_switches": MAX_SWITCHES}

    def summary(self) -> dict:
        data = self.load()
        return {"enabled": data["enabled"], "active": bool(data["enabled"] and data["opt_in"]),
                "accounts": len(data["accounts"])}

    def active_accounts(self, tool: str) -> list[dict]:
        """Accounts of `tool` that may be used right now, in priority (registration) order."""
        data = self.load()
        if not (data["enabled"] and data["opt_in"]):
            return []
        approved = set(data["opt_in"].get("accounts") or [])
        return [a for a in data["accounts"] if a["tool"] == tool and a.get("enabled", True) and a["id"] in approved]

    def governs(self, tool: str) -> bool:
        """True when the pool decides which login a `tool` agent runs on: the pool is on, the owner opted
        in, and at least one account of that tool is registered. Then an agent NEVER falls back to the CLI's
        default login behind the owner's back (an account he disabled or did not approve stays unused):
        with nothing ready it is blocked with the reasons."""
        data = self.load()
        return bool(data["enabled"] and data["opt_in"] and any(a["tool"] == tool for a in data["accounts"]))

    # ---- accounts (owner actions)

    async def add_account(self, tool: str, label: str, *, profile_dir: str | None = None,
                          use_default: bool = False) -> dict:
        tool = str(tool or "").strip().lower()
        if tool not in TOOLS:
            raise PoolError(422, "USAGE", f"инструмент должен быть один из: {', '.join(TOOLS)}")
        label = _LABEL_BAD.sub("", str(label or "")).strip()[:40]
        if not label:
            raise PoolError(422, "USAGE", "нужно имя аккаунта (label)")
        async with self.lock:
            data = self.load()
            if len(data["accounts"]) >= MAX_ACCOUNTS:
                raise PoolError(409, "TOO_MANY", f"в пуле не больше {MAX_ACCOUNTS} аккаунтов")
            taken = {a["id"] for a in data["accounts"]}
            n = 1
            while f"{tool}-{n}" in taken:
                n += 1
            account_id = f"{tool}-{n}"
            if use_default:
                if profile_dir:
                    raise PoolError(422, "USAGE", "use_default и profile_dir вместе не задаются")
                if any(a["tool"] == tool and a["profile_dir"] is None for a in data["accounts"]):
                    raise PoolError(409, "DUPLICATE", f"обычный вход {tool} уже в пуле")
                path: str | None = None
            else:
                path = self._profile_path(tool, account_id, profile_dir, data)
            account = {"id": account_id, "tool": tool, "label": label, "profile_dir": path, "enabled": True,
                       "login_state": "unknown", "login_detail": None, "plan": None, "limited_until": None,
                       "last_checked": None, "last_used": None, "runs": 0, "added_at": _now()}
            data["accounts"].append(account)
            self._journal(data, "added", tool=tool, account=account_id, label=label)
            self.save(data)
        return account

    @staticmethod
    def _profile_path(tool: str, account_id: str, raw: str | None, data: dict) -> str:
        if raw:
            p = Path(str(raw)).expanduser()
            if not p.is_absolute() or ".." in p.parts:
                raise PoolError(422, "USAGE", "profile_dir — абсолютный путь без «..»")
        else:
            p = profiles_root() / tool / account_id
        if p.exists() and not p.is_dir():
            raise PoolError(422, "USAGE", f"{p} — не каталог")
        key = os.path.normcase(os.path.abspath(p))
        if any(a["profile_dir"] and os.path.normcase(os.path.abspath(a["profile_dir"])) == key
               for a in data["accounts"]):
            raise PoolError(409, "DUPLICATE", "этот каталог профиля уже занят другим аккаунтом пула")
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PoolError(422, "USAGE", f"каталог профиля не создан: {exc}") from None
        return str(p)

    async def remove_account(self, account_id: str) -> dict:
        async with self.lock:
            data = self.load()
            account = self._account(data, account_id)
            data["accounts"] = [a for a in data["accounts"] if a["id"] != account_id]
            if data["opt_in"]:
                data["opt_in"]["accounts"] = [i for i in data["opt_in"].get("accounts") or [] if i != account_id]
            if not data["accounts"]:
                data["enabled"], data["opt_in"] = False, None
            self._journal(data, "removed", tool=account["tool"], account=account_id)
            self.save(data)
        return account

    async def set_account_enabled(self, account_id: str, enabled: bool) -> dict:
        async with self.lock:
            data = self.load()
            account = self._account(data, account_id)
            account["enabled"] = bool(enabled)
            self._journal(data, "account_enabled" if enabled else "account_disabled", tool=account["tool"],
                          account=account_id)
            self.save(data)
        return account

    async def clear_limit(self, account_id: str) -> dict:
        async with self.lock:
            data = self.load()
            account = self._account(data, account_id)
            account["limited_until"] = None
            self._journal(data, "limit_cleared", tool=account["tool"], account=account_id, reason="владелец")
            self.save(data)
        return account

    # ---- opt-in / on-off

    async def disable(self) -> dict:
        async with self.lock:
            data = self.load()
            data["enabled"] = False
            self._journal(data, "pool_disabled", reason="владелец")
            self.save(data)
        return self.view()

    def needs_optin(self) -> bool:
        """True when the registered accounts are not all covered by the recorded opt-in."""
        data = self.load()
        covered = set((data.get("opt_in") or {}).get("accounts") or [])
        return not data["accounts"] or any(a["id"] not in covered for a in data["accounts"])

    def accounts_for_preview(self) -> list[dict]:
        return list(self.load()["accounts"])

    async def record_optin(self, approval_id: Any) -> dict:
        async with self.lock:
            data = self.load()
            if not data["accounts"]:
                raise PoolError(409, "NO_ACCOUNTS", "в пуле нет аккаунтов")
            data["opt_in"] = {"approval_id": approval_id, "at": _now(),
                              "accounts": [a["id"] for a in data["accounts"]]}
            data["enabled"] = True
            self._journal(data, "pool_enabled", reason=f"разрешение #{approval_id}")
            self.save(data)
        return self.view()

    async def enable_covered(self) -> dict:
        """Turn the pool on again when a recorded opt-in already covers every account (no new approval)."""
        async with self.lock:
            data = self.load()
            data["enabled"] = True
            self._journal(data, "pool_enabled", reason="прежнее разрешение покрывает все аккаунты")
            self.save(data)
        return self.view()

    # ---- login state

    async def check(self, account_id: str) -> dict:
        """Owner-triggered (or pre-run) status check of ONE account through the CLI's own status command."""
        data = self.load()
        account = self._account(data, account_id)
        try:
            if account["tool"] == "claude":
                login = await cx.claude_login(profile_dir=account["profile_dir"])
            else:
                login = await cx.codex_login(profile_dir=account["profile_dir"])
        except Exception as exc:  # noqa: BLE001 — bossman-core missing, CLI refused to start…: a state, not a crash
            login = {"installed": True, "logged_in": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
        if not login.get("installed"):
            state, detail = "error", str(login.get("reason") or f"{account['tool']} CLI не найден")
        elif login.get("error"):
            state, detail = "error", str(login["error"])
        elif not login.get("logged_in"):
            state, detail = "needs_login", "вход не выполнен"
        elif not login.get("subscription"):
            state, detail = "not_subscription", f"вход не по подписке ({login.get('auth')})"
        else:
            state, detail = "ready", None
        async with self.lock:
            data = self.load()
            account = self._account(data, account_id)
            account.update(login_state=state, login_detail=detail, plan=login.get("plan"), last_checked=_now())
            self.save(data)
        return account

    # ---- selection between agent runs

    async def pick(self, tool: str, exclude: list[str] | tuple[str, ...] = ()) -> tuple[dict | None, list[dict]]:
        """First account of `tool` that is approved, not limited and logged in (by its CLI's own status
        command). Returns (account | None, [{id, label, state, detail}] for the ones skipped)."""
        skipped: list[dict] = []
        data = self.load()
        approved = set((data.get("opt_in") or {}).get("accounts") or [])
        for account in [a for a in data["accounts"] if a["tool"] == tool]:
            if account["id"] in exclude:
                continue
            if not account.get("enabled", True):
                skipped.append({"id": account["id"], "label": account["label"], "state": "disabled",
                                "detail": "отключён владельцем"})
                continue
            if account["id"] not in approved:
                skipped.append({"id": account["id"], "label": account["label"], "state": "unapproved",
                                "detail": "ждёт разрешения владельца (включите пул заново)"})
                continue
            at = _now()
            if (account.get("limited_until") or 0) > at:
                skipped.append({"id": account["id"], "label": account["label"], "state": "limited",
                                "detail": f"лимит до {time.strftime('%H:%M', time.localtime(account['limited_until']))}"})
                continue
            checked = await self.check(account["id"])
            state = checked.get("login_state")
            if state == "ready":
                return checked, skipped
            skipped.append({"id": account["id"], "label": account["label"], "state": state,
                            "detail": checked.get("login_detail") or ""})
        return None, skipped

    async def mark_limited(self, account_id: str, reset_at: float | None, reason: str, **ctx: Any) -> dict:
        until = reset_at if reset_at and reset_at > _now() else _now() + DEFAULT_COOLDOWN
        async with self.lock:
            data = self.load()
            account = self._account(data, account_id)
            account["limited_until"] = round(until, 3)
            self._journal(data, "limited", tool=account["tool"], account=account_id, reason=reason[:200],
                          until=round(until, 3), **ctx)
            self.save(data)
        return account

    async def mark_used(self, account_id: str) -> None:
        async with self.lock:
            data = self.load()
            account = self._account(data, account_id)
            account["last_used"] = _now()
            account["runs"] = int(account.get("runs") or 0) + 1
            self.save(data)

    async def note_switch(self, tool: str, previous: str, current: str, reason: str, **ctx: Any) -> dict:
        async with self.lock:
            data = self.load()
            entry = self._journal(data, "switch", tool=tool, account=previous, to=current, reason=reason[:200], **ctx)
            self.save(data)
        return entry

    def exhausted_message(self, tool: str, skipped: list[dict], tried: list[str]) -> str:
        parts = [f"{a['id']} «{a['label']}»: {a['detail'] or a['state']}" for a in skipped]
        parts += [f"{i}: лимит исчерпан в этом запуске" for i in tried]
        body = "; ".join(parts) or "нет ни одного одобренного аккаунта"
        return (f"пул аккаунтов {tool}: нет готового аккаунта — {body}. Дождитесь сброса лимита или войдите в "
                f"аккаунт (карточка «Пул аккаунтов» в Rave), затем `bossman rave resume <id> --agent <имя>`")


def label_of(account: dict) -> str:
    return f"{account['id']} «{account['label']}»"
