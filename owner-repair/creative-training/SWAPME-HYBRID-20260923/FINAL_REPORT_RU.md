# SwapMe 15 с hybrid — итог (23.09.2026)
FINAL_VIDEO = final/swapme_fox_15s_master.mp4 (локально: C:\Users\asd\Bossman\creative-runs\SWAPME-HYBRID-20260923\final\), отправлено в Telegram одним сообщением (финал + 3 шота)
SHA256 = 1a90f6fdfd063b22573abe8bbb5119957aef9371c9335102087b46dbe5426324
DURATION = 15.00 с · RESOLUTION = 1080x1920 · 24 fps · 360 кадров · AAC, −14 LUFS · декодирование 100 %
SEEDANCE_SECONDS = 10 (2 шота × 5 с, 720p 9:16) · CLOUD_COST = $2.33 (usage.cost 1.16523 × 2)
LOCAL_MODEL = Wan2.2 TI2V-5B Q8_0 GGUF · LOCAL_RUNTIME = stable-diffusion.cpp Vulkan через Bossman Studio (:8810, build 99970958), Radeon 8060S, SAC On
LOCAL в финале: 0–2.5 с — Wan I2V (бенчмарк-дубль 24 шага, 17 кадров, замедлен ×3.7 с интерполяцией, кроп лица); 12.34–15 с — НЕ генерация: наезд камеры на последний кадр S2 (компоновщик), потому что локальные шоты 65 кадров Bossman оценил в ~3 ч, а 33 кадра не успели за 34 мин — остановлено по решению владельца «заканчивать».
FAILED/CANCELLED: S1/S2 в Studio «failed: malformed» из-за бага (видео спасены загрузкой с ключом, деньги не пропали повторно); L1 65f и L2 65f/33f отменены по времени.
BUGS_FOUND = 2 (Seedance 2.5 только 15 с; оплаченное видео OpenRouter скачивалось без ключа → 401) · BUGS_FIXED = 2 (6a2b5907, 99970958; тесты красный→зелёный) · + баг обвязки (гонка записи state.json) исправлен
QUALITY_GATE = технический PASS (длительность/размер/декод/звук); vision-проверка: S1 PASS 9/10, S2 ложный FAIL на 360-px сетке (у учителя-человека кадры чистые) — после 540 px проверка упёрлась в занятый GPU
CLI_ONLY_WORKFLOW = PARTIAL: генерация через API Bossman Studio (тот же backend), не через команды bossman CLI
LESSON_SAVED = рецепт в памяти тестового Bossman (fact 3) + learning/HYBRID_VIDEO_RECIPE.md · TRANSFER_TEST = NOT_RUN (агент «Видеопродюсер» id 6 подготовлен)
ESTIMATED CLOUD-ONLY (15 с Seedance) = 3 × $1.165 = $3.50 · ACTUAL = $2.33 · MEASURED SAVING = $1.17 (−33 %) ценой ~1.5 ч локального GPU
Урок для Bossman: локальный Wan на этом железе годится для коротких (≤1 с) I2V-вставок/хуков; шоты ≥2.5 с при 640x1152 — часы → для них нужен либо облачный шот, либо компоновщик.
