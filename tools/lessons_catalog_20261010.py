"""Build docs/owner/bossman_lessons_20261010.json — the day's lessons for Bossman memory (owner rule 10.10:
«обучай Bossman каждому шагу»). Import path is the existing one: tools/import_operational_lessons.py --catalog <this>.
Evidence sha256 is computed here (CRLF->LF) so entries never carry stale digests. Add a lesson = add one add() call.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "owner" / "bossman_lessons_20261010.json"
L: list[dict] = []


def dig(rel: str) -> str:
    return hashlib.sha256((ROOT / rel).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def ev(*paths: str) -> list[dict]:
    return [{"path": p, "sha256": dig(p), "kind": "doc"} for p in paths]


def cand(reason: str) -> dict:
    return {"state": "CANDIDATE", "reason": reason}


def add(id_, cls, title, sev, status, symptom, ex, rx, kw, det, cause, fix, prev, evidence, ver):
    L.append({"id": id_, "class": cls, "title": title, "severity": sev, "status": status, "symptom": symptom,
              "symptom_examples": ex, "symptom_regex": rx, "keywords": kw, "detection": det, "root_cause": cause,
              "fix": fix, "prevention": prev, "evidence": evidence, "verification": ver})


PROMO6 = "docs/owner/PROMO_6_GENJUTSU_STUDIO_PLAN_20261010.md"
RETRAIN = "docs/owner/GENJUTSU_LIVE_V2_RETRAIN_PROMPT_20261010.md"
STAGES = "docs/owner/VIDEO_PIPELINE_STAGES_20261010.md"
PULT_TEST = "command-center/tests/telegram_contracts/test_pult_owner_only_delivery.py"

add("RUNPOD-COMMUNITY-STARVED", "cloud-gpu", "RunPod community-под голодает по сети: pip висит, GPU 0%", "HIGH", "FIXED",
    "На community-хосте pip install -r requirements идёт 10+ мин при CPU 0.5%, входящий трафик ~190 КБ/с, GPU 0%; обучение не стартует.",
    ["pip install -q -r requirements.txt висит 14 мин", "nvidia-smi 0 %, 1 MiB", "rx ~190 KB/s"],
    [r"(?i)pip install.*requirements.*\d{2,} ?min", r"(?i)community.*(starv|слаб|медлен)"],
    ["runpod", "community", "secure", "network", "pip", "голодает", "сеть пода"],
    "Сразу после старта пода: тест скорости (100 МБ) >= 40 МБ/с; через 3 мин проверить ps -o etime,pcpu у pip и rx_bytes за 5 с.",
    "Community-хосты отдают GPU с перегруженной сетью/CPU; час дёшев, но установка не завершается.",
    "Терминировать под, поднять SECURE (A100 80GB $1.79/ч: установка 4–5 мин, 1500 шагов SDXL LoRA ~13 мин). Дешёвый час дороже пустого часа.",
    "Тест сети до установки; лимит на установку 8 мин, иначе пересоздать под; для SDXL LoRA по умолчанию SECURE.",
    ev(PROMO6), cand("наблюдение одного дня, внешний верификатор не оформлен"))

add("CUDNN-CU13-OVER-CU12", "cloud-gpu", "pip тянет nvidia-cudnn-cu13 поверх cu12 -> CUDNN_STATUS_NOT_INITIALIZED", "HIGH", "FIXED",
    "Первый запуск accelerate/kohya падает с CUDNN_STATUS_NOT_INITIALIZED на образе torch 2.4 cu124 после pip install -r requirements.",
    ["CUDNN_STATUS_NOT_INITIALIZED", "nvidia-cudnn-cu13 9.24"], [r"CUDNN_STATUS_NOT_INITIALIZED", r"nvidia-cudnn-cu13"],
    ["cudnn", "cu12", "cu13", "kohya", "sd-scripts", "torch 2.4"],
    "pip list | grep nvidia-cudnn показывает две версии (cu12 9.1 и cu13 9.24).",
    "requirements sd-scripts подтягивает свежий cudnn для CUDA 13, несовместимый с torch cu124.",
    "pip install nvidia-cudnn-cu12==9.1.0.70 и перезапуск; обучение стартовало (2 it/s на A100).",
    "В pod_setup.sh после requirements закреплять cudnn под версию torch; smoke-тест torch.backends.cudnn.version() до старта.",
    ev(PROMO6), cand("исправлено агентом 10.10, лог на поде; внешний верификатор не оформлен"))

add("LORA-ALONE-IDENTITY-PLATEAU", "identity-training", "SDXL LoRA сама по себе даёт сходство ~0.3–0.4; добавка фото не поднимает", "HIGH", "OPEN",
    "v1 (57 кадров) 0.28/0.36, v2 (+5 фото) 0.30/0.34 по arcface на масштабе 0.8/1.0; прироста нет.",
    ["v2 +0.008 at 0.8, -0.039 at 1.0", "arcface mean 0.342"], [r"(?i)arcface.*0\.[23]\d", r"(?i)lora.*(плато|no .*gain|без прироста)"],
    ["lora", "arcface", "identity", "сходство", "плато", "gfgirl", "InstantID", "IP-Adapter FaceID"],
    "Оценка: 6 фиксированных промптов x 2 сида, arcface к реальным фото; сравнивать одинаковым набором эталонов.",
    "LoRA dim16 unet-only учит стиль/волосы/одежду, личность лица в SDXL держится слабо; метрика на 6 картинках шумит на +-0.03.",
    "Личность держать адаптером лица (IP-Adapter-FaceID / InstantID; insightface некоммерческая) или FaceFusion поверх сгенерированных тел; LoRA — для одежды/волос/сцен. Проверяется руками B/C/D в v3.",
    "Пороги фиксировать до прогона (PASS >= 0.55 и >= prev+0.03); не называть «похоже» без числа; v1 пересчитывать тем же набором эталонов.",
    ev(RETRAIN), cand("v3-руки ещё идут; вывод по v1/v2"))

add("HAIR-RECOLOR-MASK-JAGGED", "genjutsu-video", "Перекраска волос: зубчатый край, затекание на шею/руки, платиновый уходит в рыжий", "MEDIUM", "OPEN",
    "Маска SegFormer даёт жёсткий зубчатый контур, синий блок на шее, руки у головы сливаются, WHITE — серая шапка без прядей, PLATINUM рендерится медным.",
    ["криво вырезан", "PLATINUM looks copper", "white hair grey cap"], [r"(?i)(криво|jagged).*(вырез|mask)", r"(?i)platinum.*(copper|рыж)"],
    ["hair", "recolor", "SegFormer", "matting", "alpha", "Lab", "платиновый", "белые волосы"],
    "Сравнить край перекраски с alpha-маттингом (MODNet/RVM): расстояние края > 2 px или утечка > 1% площади = дефект; dE до целевого цвета > 15.",
    "Бинарная маска сегментации без мягкой альфы; перекраска по хроме без сохранения L и бликов; кожа/руки не исключены.",
    "v2: мягкая альфа (маттинг/guided filter по trimap), исключение кожи/рук/шеи масками парсинга, перекраска в Lab с сохранением L, светлые цвета через подъём L + десатурация + блики, временное сглаживание альфы по оптическому потоку.",
    "Пороги до прогона: край <= 2 px, утечка <= 1%, кожа <= 0.5%, dE <= 15, std яркости >= 0.8x оригинала; тесты на утечку и цвет.",
    ev(STAGES), cand("план v2; реализация остановлена до конца обучения"))

add("PULT-OWNER-ONLY-DELIVERY", "telegram", "Пульт пишет только владельцу: проверять на установленной сборке, а не в ветке", "HIGH", "FIXED",
    "Владелец требует, чтобы управляющий бот никому кроме него не писал; риск — исправление есть в ветке, но не в запущенном процессе.",
    ["пульт никого кроме меня не уведомляет"], [r"(?i)пульт.*(кроме|только).*владел"],
    ["pult", "telegram_companion", "delivery_allowed", "owner only", "Jeff"],
    "Найти процесс (Win32_Process CommandLine ~ telegram_companion), взять его site-packages, grep delivery_allowed; сравнить mtime файла и время старта процесса; counts по who в companion.sqlite3 (без тел).",
    "Два хранилища: пульт (telegram-companion, только владелец) и Jeff/PIT (pit-v1.7, участники) — их легко перепутать.",
    "Guard person.role == owner в delivery_allowed + authorize_delivery на всех send_*; в установленной сборке есть (hotfix 6273524c); 3 теста test_pult_owner_only_delivery проходят.",
    "Любое правило «бот пишет только X» проверять на запущенной сборке и по базе, а не по ветке; Jeff не трогать.",
    ev(PULT_TEST),
    {"state": "VERIFIED", "observed_at": "2026-10-10T13:32:00Z",
     "verifier": {"principal_id": "tool:pytest", "model_id": "", "role": "verifier", "independence_class": "external_tool"},
     "expected": "3 теста owner-only delivery проходят", "actual": "3 passed in 0.37s",
     "external_verification": "pytest tests/telegram_contracts/test_pult_owner_only_delivery.py"})

add("SYNTH-REF-NO-IDENTITY", "identity-dataset", "Text-to-image без identity-условия игнорирует референс: «похожие» фото не личность", "MEDIUM", "ENV",
    "Сгенерированные фото дают arcface 0.49–0.64 к реальным; профиль 0.31; фото с базой gf5 дают 0.78–0.83 только потому, что gf5 — эталон (без него 0.49–0.57).",
    ["sim 0.31 profile", "0.83 vs ref gf5 but 0.57 without"], [r"(?i)(soul|text-to-image).*(ignor|игнор).*(ref|референс)"],
    ["higgsfield", "soul", "reference", "arcface", "leave-one-out", "дубли", "датасет"],
    "Сходство считать с исключением эталона-источника (leave-one-out); дубли (sim > 0.92 между кандидатами) ограничивать <= 3 с одной базы.",
    "Text-to-image без identity-условия рисует типаж, а не человека; высокая оценка к собственному источнику — утечка эталона.",
    "Порог >= 0.59 к 5 эталонам + leave-one-out; синтетику хранить отдельной папкой и помечать; реальные фото в приоритете.",
    "В обучение только после одобрения владельца одним сообщением-сеткой; не выдавать генерации за реальные фото.",
    ev(RETRAIN), cand("замер одного дня"))

add("WINPATH-UNICODEESCAPE", "tooling", "Windows-пути в не-raw строках Python ломают скрипт: truncated \\UXXXXXXXX", "LOW", "FIXED",
    "SyntaxError (unicode error) unicodeescape при C:\\Users в обычной строке; sed с обратными слэшами не совпадает после сворачивания shell.",
    ["truncated \\UXXXXXXXX escape"], [r"truncated \\\\UXXXXXXXX escape", r"(?i)unicodeescape"],
    ["unicodeescape", "raw string", "heredoc", "sed", "windows path"],
    "Трейсбек SyntaxError на строке с C:\\U…", "\\U в не-raw строке трактуется как unicode-escape; shell дополнительно сворачивает двойные слэши.",
    "Писать r-строки или прямые слэши; правки файлов делать Edit/Python по номеру строки, не sed с обратными слэшами.",
    "Проверять ast.parse перед запуском детачед-процессов.", ev(PROMO6), cand("повторяющаяся ошибка сессии"))


LORA_REPORT = "docs/owner/runs/LORA_V3_REPORT_20261010.md"

add("MAX-LORA-TE-WORSE-THAN-SMALL", "identity-training", "Макс-LoRA (dim64 + текстовые энкодеры, 3000 шагов) дала сходство хуже dim16", "MEDIUM", "OPEN",
    "Арм B (dim64, UNet+TE, 3000 шагов, регуляризация) — arcface 0.16–0.19 против 0.33 у dim16; diffusers не загрузил TE-часть (peft, ключи TE).",
    ["B mean 0.156/0.186/0.155/0.188", "peft TE keys error"], [r"(?i)dim ?64.*(хуже|worse)", r"(?i)peft.*text_encoder"],
    ["lora", "dim64", "text encoder", "overfit", "regularization", "sd-cli", "diffusers"],
    "Сравнивать чекпойнты 1500/2000/2500/3000 одним протоколом; если diffusers не грузит LoRA, вывод помечать «только sd-cli».",
    "Большая ёмкость + TE на 88 картинках переобучается на стиль/фон, а не на лицо; возможен артефакт применения TE-LoRA в sd-cli.",
    "Не гнаться за ёмкостью LoRA для личности; держать dim16 как базу, личность — адаптером лица.",
    "Любой «апгрейд» обучения сначала на одной руке с теми же порогами; слабый результат с оговоркой о загрузчике не выдавать за закон.",
    ev(LORA_REPORT), cand("одна серия 10.10; возможный артефакт загрузчика TE-LoRA"))

add("FACE-ADAPTER-BEATS-LORA", "identity-training", "IP-Adapter-FaceID с её фото на инференсе даёт 0.61 против 0.31 у LoRA", "HIGH", "OPEN",
    "LoRA A + IP-Adapter-FaceID-PlusV2 (7 референсов) — arcface 0.61 на 9 независимых фото; та же LoRA без адаптера 0.31.",
    ["C 7 refs 0.610 vs A 0.310"], [r"(?i)ip-?adapter.*faceid", r"(?i)instantid"],
    ["ip-adapter", "faceid", "instantid", "identity", "arcface", "insightface", "licence"],
    "Замер только на фото, не использованных как референсы адаптера; официальный протокол: 6 промптов, sd-cli, 2 сида.",
    "Личность в SDXL задаётся условием на эмбеддинг лица, LoRA такого условия не даёт; метрика частично циклична (insightface и arcface — одно семейство).",
    "Личность — адаптером лица (или FaceFusion поверх), LoRA — для одежды/волос/сцен. Перед продакшеном: официальный замер, лицензия insightface некоммерческая.",
    "Не перебирать варианты на одном наборе и объявлять лучший: оценка завышена; фиксировать протокол до перебора.",
    ev(LORA_REPORT), cand("замер не по официальному протоколу; перебор 6 вариантов на одном наборе"))


def main() -> int:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    cat = {"schema": "bossman.critical_errors.v1", "title": "Уроки дня 2026-10-10: Genjutsu Live, LoRA v3, RunPod, пульт",
           "captured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "observed_head_sha": head,
           "environment": "owner-machine Windows 11 Pro 10.0.26200, Ryzen AI MAX+ 395, Radeon 8060S; RunPod A100 SECURE / community 3090",
           "recorded_by": {"principal_id": "agent:claude-fable-5.1-coordinator", "model": "claude-fable-5-1", "agent": "coordinator",
                           "run_id": "lessons-20261010"},
           "privacy_class": "internal-owner-ops", "scope": "owner-machine/windows-11 + runpod",
           "notes": ["Каждый шаг дня пишется уроком по правилу владельца; CANDIDATE, пока нет внешнего верификатора."],
           "entries": L}
    OUT.write_text(json.dumps(cat, ensure_ascii=False, indent=1), encoding="utf-8")
    print("entries", len(L), "->", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
