"""Owner-only Jeff passport checkpoint from consent-backed local memory.

This reads the existing PIT namespace and uses its local Ollama adapter. It
does not read raw conversations, message text, owner memory, or write a
participant profile. The result is a review draft, not an authority source.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from bcc.auth import _restrict_to_owner

from .config import PITSettings, pit_home
from .ollama_native import EmptyAnswer, OllamaNativeChatAdapter
from .resilient_chat import ResilientChat
from .vault import PersonaVault, _atomic_json

LOCAL_MODEL = "bossman-community-qwen-uncensored:latest"
LOCAL_URL = "http://127.0.0.1:11434/v1"


def _participant_ids(settings: PITSettings, home: Path) -> list[int]:
    ids = {person.user_id for person in settings.people}
    database = home / "companion.sqlite3"
    if database.is_file():
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
            for (who,) in db.execute("SELECT DISTINCT who FROM inbox"):
                try:
                    ids.add(int(str(who).split(":", 1)[0]))
                except ValueError:
                    continue
    return sorted(uid for uid in ids if uid > 0)


def _consented_facts(vault: PersonaVault, user_id: int) -> list[dict]:
    key = vault.key_for_telegram(user_id)
    consent = vault.consent(key)
    if not consent.memory_enabled:
        return []
    facts = []
    for row in vault.iter_candidate_records(key):
        if row.get("sensitivity") == "sensitive" and not consent.sensitive_memory_enabled:
            continue
        value = row.get("value")
        if not isinstance(value, str) or not value.strip():
            continue
        facts.append({"category": str(row.get("category") or "")[:80],
                      "value": value[:500], "confidence": row.get("confidence")})
    return facts


def _parse_summary(text: str) -> tuple[str, str]:
    value = text.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("model did not return an object")
    context = str(data.get("context") or "").strip()
    tag = str(data.get("topic_tag") or "").strip()
    if not context or not tag:
        raise ValueError("model omitted context or topic tag")
    return context[:400], tag[:60]


async def build_checkpoint(settings: PITSettings, *, adapter=None, chat: ResilientChat | None = None,
                           narratives: dict[int, dict] | None = None) -> dict:
    """Gather every known participant; summarize opted-in facts and, when given, the narrative.

    The model call goes through ``ResilientChat`` (the same empty-answer detection and
    bounded runner recovery as the Master Parser); ``chat`` may be the parser's own route.
    ``narratives`` maps a Telegram id to that participant's own narrative (2.0).
    """
    started = time.perf_counter()
    home = pit_home(settings.data_dir)
    vault = PersonaVault(settings.data_dir, bytes.fromhex(settings.identity_salt))
    chat = chat or ResilientChat(adapter or OllamaNativeChatAdapter(LOCAL_URL), LOCAL_MODEL, timeout=120)
    narratives = narratives or {}
    rows = []
    for user_id in _participant_ids(settings, home):
        facts = _consented_facts(vault, user_id)
        row = {"telegram_id": user_id, "fact_count": len(facts),
               "status": "INSUFFICIENT_DATA", "context": "Нет сохранённых фактов",
               "topic_tag": "не определён", "telegram_handle": None}
        story = narratives.get(user_id)
        if story and story.get("status") == "OK":
            row["narrative"] = {"context": story["paragraphs"]["context"][:450],
                                "personality": story["paragraphs"]["personality"][:450],
                                "provenance": story.get("provenance")}
        if facts:
            prompt = ("Кратко обобщи эти сохранённые факты участника для "
                      "проверки владельцем. "
                      "Ответь по-русски. Верни только JSON: "
                      "{\"context\":\"...\",\"topic_tag\":\"...\"}.\n"
                      + json.dumps(facts, ensure_ascii=False))
            try:
                answer = await chat.chat([{"role": "user", "content": prompt}],
                                         scope=vault.key_for_telegram(user_id) + "|checkpoint",
                                         max_tokens=512, timeout=120)
                if answer.finish == "length":
                    raise ValueError("model answer was truncated")
                row["context"], row["topic_tag"] = _parse_summary(answer.text)
                row["status"] = "DRAFT_REVIEW"
            except Exception as exc:  # one bad local call must not erase others
                row["status"] = "EMPTY_ANSWER" if isinstance(exc, EmptyAnswer) else "MODEL_ERROR"
                row["error_type"] = type(exc).__name__
                row["context"] = "Локальная модель не дала проверяемый ответ"
        elif row.get("narrative"):
            row["status"] = "DRAFT_REVIEW"
            row["context"] = row["narrative"]["context"][:400]
            row["topic_tag"] = "по переписке"
        rows.append(row)
    return {"schema": "bossman.jeff.passport-checkpoint.v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": chat.model, "source": "PIT consent-backed facts and narratives only",
            "participants": rows, "duration_seconds": round(time.perf_counter() - started, 2)}


def save_checkpoint(settings: PITSettings, report: dict) -> Path:
    target = pit_home(settings.data_dir) / "passport-checkpoints" / "latest.json"
    _atomic_json(target, report)
    _restrict_to_owner(target)
    return target


def run_checkpoint(settings: PITSettings) -> tuple[dict, Path]:
    report = asyncio.run(build_checkpoint(settings))
    return report, save_checkpoint(settings, report)
