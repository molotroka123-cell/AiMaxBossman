"""Assemble lane receipts + classification table for lane jeffa from raw probe output (deterministic)."""
import importlib.util, json, re
from pathlib import Path
HERE = Path(__file__).resolve().parent
sp = importlib.util.spec_from_file_location("jeffa_probe", HERE / "jeffa_probe.py")
jp = importlib.util.module_from_spec(sp); sp.loader.exec_module(jp)
EVID = jp.EVID
TOP = {"cap-1", "module-09f63bf64e58", "module-1d61f405af4c", "module-0a1e9aa39f26", "module-6f3b41ccd19f",
       "module-62de71348d62", "module-7fe26d8e1063", "module-67a5b6d5c767", "module-4a1f6b8497c8", "module-64645c8f9fb0"}
AUTH = {"module-09f63bf64e58": "test_leaf_passport_sink.py", "module-1d61f405af4c": "test_leaf_resilient_chat.py",
        "module-27f9c4217e34": "test_leaf_telegram_calls_deps.py", "module-4a1f6b8497c8": "test_leaf_master_parser_corpus.py"}
KEEP = {
 "cap-10": "Звонки: код и тесты на отдельных ветках; реальный двусторонний звонок и задержки требуют живого Telegram-аккаунта владельца и подтверждения (нужен владелец/env).",
 "module-7d6b56dd11d4": "telegram_calls/guard.py отсутствует в этом worktree (ветка tgcalls-work); branch-лист без кода здесь: KEEP, не retire.",
 "module-7c917a080544": "telegram_calls/speech/testing.py отсутствует в этом worktree (ветка tgcalls-work); branch-лист без кода здесь: KEEP, не retire.",
}
raw = {r["node_id"]: r for r in json.loads((EVID / "raw" / "jeffa-raw.json").read_text(encoding="utf-8"))}
raw["cap-1"] = json.loads((EVID / "raw" / "jeffa-cap1.json").read_text(encoding="utf-8"))
recs, rows = [], []
for n in jp.lane_nodes():
    nid = n["id"]; label = n["label"].replace("|", "/")
    if nid in KEEP:
        rows.append((nid, label, "KEEP", KEEP[nid], "OK")); continue
    r = raw[nid]
    recs.append({k: v for k, v in r.items() if k not in ("module", "test_files_run", "test_candidates")} | ({"authored_by_lane": True} if nid in AUTH else {}))
    out = (EVID / "out" / f"{nid}.txt").read_text(encoding="utf-8")
    passed = sum(int(x) for x in re.findall(r"(\d+) passed", out))
    if r["verdict"] == "PASS":
        why = f"import в чистом subprocess из worktree + {passed} passed (pytest)"
        if nid in AUTH: why += f"; тест authored_by_lane: {AUTH[nid]}"
        if nid == "cap-1": why += "; proxy-проверка capability: vault + passport_commands, тесты consent/forget/pause_memory"
        rows.append((nid, label, "GREEN", why, "TOP" if nid in TOP else "OK"))
    else:
        rows.append((nid, label, "KEEP", f"FAIL {r.get('reason')}", "OK"))
(EVID / "jeffa.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
(EVID / "jeffa-retire.json").write_text("[]\n", encoding="utf-8")
md = ["# jeffa classification (zone jeff, first 67 code/branch leaves by sorted id)", "",
      "RETIRE: 0. Аудит (tools/tree_proof/jeffa_audit.py + grep): 6 модулей без импортёров вне тестов (j2.model_guard, j2.research, j2.media, j2.memory_palace, presentation_profile, voice_bench) "
      "не мёртвые: j2-модули подгружаются pipeline.KNOWN_MODULES и описаны в docs/pit/JEFF_2_0_*.md, voice_bench это `python -m` CLI, presentation_profile в tools/jeff_ux_packet.py; "
      "model_guard к тому же safety/health. Дубль cap-4 и module-639c8365e585 (один файл participant_admin.py) оставлены оба: cap-4 это публичная capability.", "",
      "| id | label | verdict | reason | value |", "|---|---|---|---|---|"]
md += [f"| {a} | {b} | {c} | {d} | {e} |" for a, b, c, d, e in rows]
(EVID / "jeffa-classification.md").write_text("\n".join(md) + "\n", encoding="utf-8")
from collections import Counter
print(Counter(r[2] for r in rows), sum(1 for r in rows if r[4] == "TOP"))
