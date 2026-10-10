# Локальный Genjutsu-подобный видеоконвейер — этапы и gate (с 10.10.2026)

Источник: мастер-промт владельца 10.10. Порядок: Gate 0 → 1 Person Swap → 2 Clothes → 3 Mimics 2.0 → 4 Background.
Следующий этап открывается только после VERIFIED и подтверждения владельца в Telegram-пульте.
Статусы: NOT_STARTED / IN_PROGRESS / BLOCKED / FAILED / VERIFIED. QUEUED, SKIPPED, mock или старый SHA — не PASS.

| Этап | Что именно меняется | Статус |
|---|---|---|
| Gate 0 — воспроизведение F1 | только область лица (FACE_ONLY) | IN_PROGRESS — evidence ниже, replay ждёт освобождения GPU (идёт Wan) |
| 1 — Person Swap | внешность человека целиком | NOT_STARTED |
| 2 — Clothes Swap | одежда (video try-on) | NOT_STARTED |
| 3 — Mimics 2.0 | собственный модуль Bossman (найти и описать до изменений) | NOT_STARTED |
| 4 — Background Swap | фон (matting + замена) | NOT_STARTED |

## Gate 0 — FaceFusion F1 (FACE_ONLY), evidence на 10.10

| Пункт | Значение | Статус |
|---|---|---|
| Код FaceFusion | facefusion/facefusion @ `7247081`, OpenRAIL-AS; ONNX Runtime DirectML 1.24.4 | MEASURED |
| Конфиг | hyperswap_1a_256, pixel-boost 1024, face-mask box+occlusion (xseg_1), gfpgan_1.4 blend 50, face-selector many, execution-thread-count 1 | MEASURED (лог/команда) |
| Тот же конфиг в Bossman | пресет `quality` в `bcc/direct_gen/faceswap.py` @ `2a77afba` | MEASURED |
| Исходники лица | r1, r4, r5 (sha256 c4b66b39…, 3aaad9a3…, 199658d2…) — приватно, локально | MEASURED |
| Целевой клип | `run-20261010/source.mp4` sha 935a5619…, 1320×1002, 30 к/с, 443–444 кадра, 14.81 с | MEASURED |
| Выход FaceFusion | `ff/F1-full.mp4` sha 1e168b99…, 1320×1002, 444 кадра, 14.8 с | MEASURED |
| Выход 16:9 | `facefusion-F1-16x9.mp4` sha 4967721f…, 1280×720, 444 кадра, 30 к/с, 14.8 с, звук оригинала | MEASURED |
| Копия у владельца | sha c702ab35… — перекодировка Telegram: 1280×720, 441 кадр, 14.7 с (910×512 — отображение в Telegram) | MEASURED |
| Скорость | 444 кадра за 4:14, 1.74 кадр/с (лог `F1-full.log`) | MEASURED (один прогон) |
| ArcFace 0.797 / исходная 0.153 | средний косинус arcface_w600k_r50 (модели FaceFusion) между лицом в каждом 6-м кадре и средним эмбеддингом r1/r4/r5; посчитано на 3-секундном фрагменте 3–6 с, не на всём ролике | CLAIMED для полного ролика — пересчитать на всех 444 кадрах при replay |
| Пиковая память | не измерялась | NOT_RUN |
| Replay на том же SHA | ждёт GPU | NOT_RUN |

Способ измерения ArcFace: `run-20261010/tune/score.py` (детектор yolo_face 640, эмбеддинг arcface FaceFusion, нормированный косинус).

## Wan2.2-Animate (кандидат Stage 1, локально) — журнал попыток 10.10
1. try1: невалидный JSON настроек (обратные слэши) — ошибка настройки, исправлено.
2. try2: режим «Replace» требует маску-видео — построена локально (FaceFusion background_remover u2net_human → бинарная маска, заливка дыр).
3. try3: модель загрузилась (150 с, краш 06.10 на загрузке больше не воспроизводится), упал на VAE-кодировании: MIOpen EvaluateInvokers → HIP unspecified launch failure.
4. try4: падение на подготовке int8-весов (Comfy Kitchen HIP kernels).
5. try5: `int8_kernels=disabled` + `MIOPEN_FIND_MODE=FAST` → VAE-кодирование 1 окна 1 мин 14 с; денойзинг идёт (~11 мин/шаг, 6 шагов, 2 окна).
Причина нестабильности: ROCm под Windows для gfx1151 — preview; Triton не компилирует свои ядра (нет stdlib.h), часть быстрых ядер падает.
