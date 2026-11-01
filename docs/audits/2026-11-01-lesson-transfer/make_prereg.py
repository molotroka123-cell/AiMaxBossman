"""Assemble prereg.json from the task prompts (tasks.json) + the fixed experiment config.

    python make_prereg.py <keys dir outside the repo>

Reads tasks.json (prompts only) and HASHES truth.json; the truth values never enter the repo before the results.
"""
import hashlib
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
keys = Path(sys.argv[1])
tasks = json.loads((keys / "tasks.json").read_text(encoding="utf-8"))
truth_sha = hashlib.sha256((keys / "truth.json").read_bytes()).hexdigest()
builder_sha = hashlib.sha256((keys / "build_truth.py").read_bytes()).hexdigest()
order = [t["id"] for t in tasks]
random.Random(20261101).shuffle(order)

LESSON_BODY = (
    "Правило обменного стола SwapMe (код: bossman_shared/exchange_assistant/quote.py, build_quote; наценка по "
    "умолчанию DEFAULT_MARGIN = 0.03). Считать только в Decimal, никогда во float.\n"
    "1) Клиент ПОКУПАЕТ валюту X за CZK (называет, сколько X хочет получить): платит = сумма × mid (CZK за 1 X) × 1.03, "
    "округление ВВЕРХ (ROUND_UP) до 0.01 CZK.\n"
    "2) Клиент ПРОДАЁТ валюту X за CZK (называет, сколько X отдаёт): получает = сумма × mid × 0.97, "
    "округление ВНИЗ (ROUND_DOWN) до 0.01 CZK.\n"
    "Округление всегда в пользу стола, а не «половина вверх».\n"
    "Пример: покупка 1000 USDT при mid 25 → 1000 × 25 × 1.03 = 25750.00 CZK; продажа 1000 USDT при mid 25 → 24250.00 CZK.\n"
    "Пример округления: покупка 360 EUR при mid 24.417 → 8790.12 × 1.03 = 9053.8236 → 9053.83 CZK (вверх); "
    "продажа 1310 USD при mid 23.581 → 30891.11 × 0.97 = 29964.3767 → 29964.37 CZK (вниз).\n"
    "Не применять, если в задаче явно задана другая комиссия, наценка или правило округления: тогда действует правило задачи.")

prereg = {
    "schema": "lesson-transfer-prereg.v1",
    "date": "2026-11-01",
    "question": ("Does a lesson written into Bossman's EXISTING memory (POST /api/memory/lessons -> facts) and recalled "
                 "automatically at TASK_START change a LOCAL model's answers on NEW held-out tasks of the lesson's class, "
                 "without regressing unrelated tasks? Memory record != weight training != proven transfer."),
    "what_is_measured": ("Transfer of a CONVENTION the model cannot derive from the task text (SwapMe desk margin 3 % and "
                         "desk-favour rounding direction). Held-out prompts name the desk rule but never state it. This "
                         "measures whether recall injects the lesson and whether the model applies it; it does NOT measure "
                         "reasoning improvement and NEVER weight training."),
    "path": "POST /api/tasks (run_now, explicit agent) -> Engine -> TASK_START recall -> model via OpenAI-compat provider -> GET /api/tasks/{id}",
    "backend": {"port": 8897, "data_dir": "temp (throw-away BCC_DATA_DIR)", "localappdata": "temp",
                "exercise_keys_dir": "temp, outside data dir and repo", "owner_backend_8801": "never touched"},
    "model": {"ollama_name": "qwen3-coder-30b-ctx32k:latest", "family": "qwen3moe 30.5B Q4_K_M (A3B MoE)",
              "ollama": "0.40.13 at 127.0.0.1:11434 (owner's, read-only use)",
              "proxy_base_url": "http://127.0.0.1:11536/v1",
              "why_this_model": ("local Bossman agent model (pult AGENT mode qwen3-coder); fast A3B MoE so 2 x 3 x 38 runs fit; "
                                 "Gemma 4 31B is loaded by another session and is not disturbed"),
              "sampling": {"temperature": 0, "seed": 42, "reasoning_effort": "none",
                           "how": ("sampling_proxy.py injects temperature/seed (Bossman's engine sends none; the Modelfile "
                                   "default is 0.7). Messages/tools/response pass through unchanged.")},
              "unload_after": "keep_alive 0 for this model only, if it was not loaded before the run"},
    "agent": {"name": "Bossman lesson-transfer probe (local)", "max_steps": 2, "max_tokens": 1024, "tools": [],
              "system_prompt": ("Ты — Bossman, деловой ассистент. Считай аккуратно, покажи короткий расчёт и дай ответ "
                                "в требуемом формате.")},
    "lesson": {"payload": {
        "title": "SwapMe: котировка стола — наценка 3 %, округление в пользу стола",
        "body": LESSON_BODY, "category": "business", "author": "claude",
        "source": "file:bossman_shared/exchange_assistant/quote.py", "observed_at": "2026-11-01",
        "valid_from": "2026-10-19", "tags": ["swapme", "fx", "decimal", "rounding"],
        "limitations": [
            "Только наценка по умолчанию 3 %: если менеджер задал свою ставку или включён строгий режим "
            "compute_quote_strict (COM-UP-1), коэффициенты 1.03/0.97 не действуют.",
            "mid в задачах берётся из условия; настоящий стол берёт курсы ЧНБ/ЕЦБ/ручные с TTL, USDT через USDT/USD.",
            "Проверено на синтетических котировках и упражнении swap_desk_quote_v1; на реальных сделках не проверялось.",
            "Запись урока — не обучение весов модели; применение урока доказывается отдельным экспериментом.",
        ]}},
    "exercise": {"exercise_id": "swap_desk_quote_v1",
                 "use": "AFTER phase: the model solves it through the task path; ITS answer goes to /verify (server runs the checker)"},
    "sets": {"heldout": "20 NEW SwapMe desk quotes (H01-H20), rule not stated; 12/20 also discriminate desk rounding vs half-up",
             "independent": "6 tasks the lesson was not written for (I01-I06): same rule, other framing, no 'SwapMe'/'стол', composites",
             "control": "12 tasks (C01-C12): 4 near-miss money tasks with an EXPLICIT different rule + 8 unrelated"},
    "tasks": tasks,
    "run_order": order,
    "repetitions": 3,
    "phases": ["baseline (fresh temp DB, no lesson)", "lesson write + provenance + restart survival",
               "after (same backend data dir after restart, lesson in memory, recall on)"],
    "ground_truth": {"location": "outside the repo and outside the temp data dir (session scratchpad keys/)",
                     "truth_json_sha256": truth_sha, "builder_sha256": builder_sha,
                     "published": "with results.json after the runs (post-hoc disclosure)"},
    "scorer": {"file": "scorer.py", "type": "code, not an LLM",
               "parse": ("take the value after the LAST 'ОТВЕТ:'; if absent, the last number in the answer (flagged "
                         "format_fallback). Numbers: drop spaces/nbsp, ',' is a decimal point unless '.' also present "
                         "(then ',' is a thousands separator). Correct iff Decimal(parsed) == Decimal(truth) exactly "
                         "(money truth has 2 dp, so 18828.1425 != 18828.15). Text: casefold, strip punctuation, equal."),
               "unit": "task passes in a phase iff >= 2 of 3 repetitions are correct (majority)",
               "infra_failure": "a run whose task status != completed counts as incorrect and is reported"},
    "decision_rule": {
        "headroom_gate": "baseline heldout passes >= 17/20 -> INCONCLUSIVE ('no headroom - transfer not demonstrable')",
        "recall_gate": ("AFTER: the lesson's fact id appears in memory.recalled sources in >= 95 % of heldout runs, and "
                        "in 0 baseline runs; otherwise FAIL (memory did not inject the lesson)"),
        "infra_gate": "> 10 % of runs in either phase not completed -> INCONCLUSIVE",
        "PASS": ("delta = heldout_after - heldout_baseline >= 8 tasks AND one-sided exact sign test on discordant "
                 "heldout tasks p <= 0.01 AND control regressions == 0 (a control task majority-pass at baseline "
                 "and majority-fail after)"),
        "FAIL": "delta <= 3 OR control regressions >= 2 OR recall gate fails",
        "INCONCLUSIVE": "anything else",
        "independent_set": "secondary, descriptive: 'generalises' only if independent_after - independent_baseline >= 3 of 6",
    },
    "deviations_policy": "any deviation from this file is listed in README_RU.md with the reason; nothing is re-run selectively",
    "pipeline_smoke_before_prereg": ("one non-experimental prompt ('2 + 3') through the same path, to check the "
                                     "plumbing only; it is not part of any set"),
}
(HERE / "prereg.json").write_text(json.dumps(prereg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print("tasks", len(tasks), "truth_sha256", truth_sha)
