# Персональная модель лица 2.0 — план на опенсорсе (11.10.2026)

Слова владельца: «face swap 2.0 / Genjutsu object swap 2.0», «максимально улучшаем файнтюн локальный и опенсоурс»,
«как обучение зайдёт, надо проверять новые данные». Документ написан без GPU, без платных сервисов и без отправки
чего-либо в облако. Её фото (`C:\Users\asd\Bossman\lora-datasets-20261010\gf-v3-approved\`, 30 шт.) не открывались.

Обозначения: **ФАКТ** — есть источник или замер на этой машине; **ОЦЕНКА** — вывод исполнителя, не измерено;
**UNVERIFIED** — в открытых источниках не нашлось.

## 0. Выжимка

1. Сегодняшний потолок: FaceFusion hyperswap_1a_256 даёт arcface 0.70–0.76 на хороших клипах (ФАКТ, Gate 1), LoRA SDXL личность не держит (0.33–0.61, ФАКТ). Цель 2.0: **arcface mean ≥ 0.80, min ≥ 0.65**, мимика ≥ 0.99, резкость ≥ 1.5× V1, без артефактов.
2. Единственный опенсорс-путь к модели «именно её лица», который локальный FaceFusion уже умеет исполнять: **DeepFaceLab SAEHD → экспорт `.dfm` → FaceFusion `--processors deep_swapper --deep-swapper-model custom/<имя>`** (ФАКТ: код `deep_swapper/core.py` в нашей копии @7247081 читает `.assets/models/custom/*.dfm`, шаблон `dfl_whole_face`, `.hash` не нужен).
3. Обучение DFL — только на арендованной NVIDIA (CUDA). На Radeon 8060S обучение нереально (ФАКТ: ROCm на Windows — только PyTorch; DFL = TensorFlow; DirectX12-сборка DFL есть, но не поддерживается и нестабильна на AMD). Инференс `.dfm` на DirectML — правдоподобно (DFM = ONNX, DeepFaceLive работает на DX12-картах), но **ни одного источника про FaceFusion deep_swapper + DirectML нет → первый шаг плана: локальный дымовой тест за $0**.
4. Данные: 2 000–6 000 кадров лица из видео, разные ракурсы/свет/мимика (ФАКТ-secondhand, DFL-гайды); у нас 30 фото + кружки 400×400 + ref-clip-03 — мало и мелко. Нужна **новая съёмка 6–9 минут** (раздел 6).
5. Бюджет: RunPod Secure A100 $1.59/ч, H100 SXM $3.99/ч, RTX 4090 $0.89/ч (ФАКТ, страница цен RunPod от 27.09.2026). Сессия ≤2 ч = $1.78–7.98. Полное обучение SAEHD 256 — десятки GPU-часов (ОЦЕНКА: 150k итераций ≈ 17–50 ч ≈ $22–53 ≈ 9–25 сессий по 2 ч). **Баланса ~$24 на полный прогон не хватит** — план разбит на гейты со стоп-правилом; реальную скорость (ит/ч) меряем в первой GPU-сессии и пересчитываем.
6. Перед любым обучением каждый новый набор проходит `tools/dataset_check/check_faces.py` (CPU, ONNX-модели FaceFusion; раздел 7), коммит `c9d6b479`, 13 тестов.
7. От владельца: (а) её явное согласие на обучение модели лица и на временную загрузку кропов лица на арендованный под (удаляются в конце сессии); (б) новые видео по списку в разделе 6; (в) решение по бюджету RunPod сверх $24.

## 1. Кандидаты (a/b) — что это, лицензии, что умеет наш FaceFusion

| Кандидат | Что даёт | Лицензия | Статус в нашем FaceFusion @7247081 | Вердикт |
|---|---|---|---|---|
| **DeepFaceLab SAEHD/AMP → .dfm → FaceFusion deep_swapper** | модель, обученная на ней: выше похожесть и резкость при достаточных данных | DFL и DeepFaceLive GPL-3.0 (инструменты; обе репы архивированы 13.11.2024); своя обученная модель — наша (ОЦЕНКА, юридически не проверено) | `deep_swapper` есть; `custom/*.dfm` поддерживаются; `--deep-swapper-morph 0–100` работает только если в модели вход `morph_value:0` (AMP-экспорт) | **основной путь 2.0** |
| hyperswap_1a/1b/1c_256 | текущий baseline 0.70–0.76 | ResearchRAIL (FaceFusion Labs, 2025) | есть, DirectML работает | baseline; 1b/1c не сравнивались — дешёвый sweep (раздел 5, этап 0) |
| ghost_1/2/3_256 | универсальный свопер | Apache-2.0 | есть в списке моделей | запасной коммерчески чистый вариант; fine-tune на ней возможен (репо ai-forever/ghost), цена UNVERIFIED |
| simswap_256 / simswap_unofficial_512 | 512 вход | non-commercial | есть (512 скачан) | только для сравнения |
| inswapper_128 | классика roop/ReActor/Rope | non-commercial (InsightFace) | ещё в списке моделей | не для продукта |
| hififace_unofficial_256, uniface_256, blendswap_256 | альтернативы | hififace — лицензия неизвестна; остальные см. страницу лицензий FaceFusion | в списке | сравнить в sweep этапа 0 |
| ReSwapper (somanchiu) | открытая переимплементация inswapper, можно дообучать; выход совместим с inswapper-128 | AGPL-3.0 | модель 128/256 на HF; в FaceFusion подключается как custom face_swapper (UNVERIFIED, не пробовали) | альтернатива, если DFL не зайдёт; 3060 12 ГБ ≈ 12 ч / 50k шагов (ФАКТ из README) |
| LivePortrait (KwaiVGI) | перенос мимики | MIT (код и веса); оригинальная репа тянет InsightFace (non-commercial) — у FaceFusion свой ONNX-пайплайн | `expression_restorer` + `live_portrait_*.onnx` уже скачаны | **для Mimics 2.0**, локально |
| ReActor / Rope | GUI-обёртки над inswapper_128 | ReActor — текст лицензии UNVERIFIED; Rope GPL-3.0, есть форк Rope-directml | не нужны: то же inswapper | не брать |
| GPEN/CodeFormer/GFPGAN обучение | — | — | уже есть как рестор | не нужно (владелец) |

Отдельно: DFL-архитектуры (ФАКТ, гайд deepfakevfx): **DF** строгий, лучше на фронтальных; **LIAE** мягче, адаптируется к dst, лучше на углах; **-U** выше похожесть, больше VRAM; **-D** «удваивает» разрешение, дольше учится; **AMP** усиливает мимику dst (и даёт morph). Для нашей задачи (чужие видео, разные ракурсы): **LIAE-UD, 256** (ОЦЕНКА; 224 — быстрее/дешевле, 320 — дороже, прирост не доказан).

## 2. Что реально на Radeon 8060S (c)

| Операция | Реально? | Основание |
|---|---|---|
| Инференс `.dfm` через FaceFusion deep_swapper на DirectML | **вероятно, не доказано** | DFM = ONNX; DeepFaceLive требует «любую DX12-карту»; источников про deep_swapper+DirectML нет (UNVERIFIED) → этап 0 |
| Инференс hyperswap/ghost/LivePortrait ONNX на DirectML | да | Gate 0/1 уже пройдены на DirectML; thread-count 1 обязателен (ФАКТ: ORT DirectML — один поток на `Run`, issues #22867/#22147/#25577) |
| Обучение DFL локально | **нет** | DFL = TensorFlow; ROCm 7.x на Windows поддерживает только PyTorch (ФАКТ, docs AMD); DX12-сборка DFL ~30% медленнее CUDA и с зависаниями на AMD (ФАКТ-secondhand, issue #5488 на RX 6900 XT); на 8060S никто не мерил |
| Обучение ReSwapper/ghost (PyTorch) локально | теоретически через PyTorch-ROCm gfx1151 preview | ROCm-preview на этой машине нестабилен (Wan2.2-Animate падает по HIP, ФАКТ); не планировать |
| check_faces.py (CPU) | да | замер: 3 файла 0.8 с, 9 элементов 2.1 с на CPU |

## 3. Данные: сколько и каких (a)

ФАКТ-secondhand (DFL-гайды, dfblue 2019, deepfakevfx): src 2 000–6 000 кадров из видео (≤10 fps извлечение, PNG), разные yaw/pitch, выражения, освещение, один возраст; dst — лицо того, кого заменяем, кадры целевого ролика. Что есть у нас: 30 фото, кружки 400×400 (лицо ~150–250 px — на грани для 256-модели), ref-clip-03 720×1280. ОЦЕНКА: этого мало по углам и свету; без новой съёмки модель 2.0 упрётся в тот же потолок, что и hyperswap (источник-ограничение 0.63 на 480p уже видели).

Порог приёмки датасета (фиксируется сейчас, до съёмки): после `check_faces.py` **≥ 1 500 ACCEPT уникальных кадров**, покрытие по yaw: frontal ≥ 40 %, left ≥ 15 %, right ≥ 15 %; яркость: dark ≥ 10 %, bright ≥ 10 %; consistency mean ≥ 0.60; face_px ≥ 160 у 90 % принятых. Если не достигнуто — доснимать, а не учить.

## 4. Экспорт и подключение (a)

1. DFL: `6) export SAEHD as dfm` (или AMP) — в меню тренера; опция квантования (ФАКТ, гайд). Файл `<имя>.dfm`.
2. Положить в `C:\Users\asd\Bossman\apps-local\facefusion\.assets\models\custom\her_liae_ud_256.dfm` (папку создать; сейчас её нет).
3. Запуск (пресет quality без face_swapper):
   ```
   python facefusion.py headless-run --processors deep_swapper face_enhancer ^
     --deep-swapper-model custom/her_liae_ud_256 --deep-swapper-morph 100 ^
     --face-selector-mode reference --reference-frame-number 0 --reference-face-distance 0.5 ^
     --face-mask-types box occlusion --face-occluder-model xseg_1 --face-enhancer-model gfpgan_1.4 --face-enhancer-blend 25 ^
     --execution-providers directml --execution-thread-count 1 -t <clip.mp4> -o <out.mp4>
   ```
   (`-s` не нужен: личность зашита в модель. ОЦЕНКА: флаги по именам из `core.py`; точный синтаксис сверить `python facefusion.py headless-run --help` перед прогоном.)
4. Гейт тот же, что Gate 1: `tools/video_gate/animation_gate.py --gate 1` + `tools/trend_edit/face_id.py score`.

## 5. Этапы и гейты (d) — пороги фиксируются здесь, до прогонов

| Этап | Где / цена | Что делаем | Гейт (PASS → следующий) |
|---|---|---|---|
| **0. Дымовой тест deep_swapper на DirectML** | локально, $0, ~1 ч | скачать публичную DFM (напр. `iperov/elon_musk_224`, авто-загрузка FaceFusion), прогнать 3 с ref-clip-03 с thread-count 1 | работает без краша, ≥ 0.5 кадр/с, кадры меняются только в маске лица. FAIL → DFL-путь закрыт, идём в ReSwapper/ghost (строка 0b) |
| 0b. Sweep готовых моделей | локально, $0, ~2 ч GPU | hyperswap_1b/1c, ghost_3, uniface, hififace на 3 клипах Gate 1, pixel boost 1024 | фиксируем лучший baseline; если какой-то даёт mean ≥ 0.80 — цель 2.0 достигнута без обучения, обучение отменяется |
| **1. Данные** | локально CPU, $0 | съёмка по разделу 6 → `check_faces.py` → отчёт владельцу | порог раздела 3 |
| 2. Согласие + подготовка | — | явное «да» владельца/её на обучение и на загрузку кропов на под; только aligned-кропы (не исходные видео), архив ≤ 300 МБ | без «да» — стоп |
| 3. GPU-сессия A (извлечение + замер скорости) | RunPod Secure, A100 $1.59/ч (или 4090 $0.89/ч), ≤2 ч, ≤ $3.2 | docker-образ DFL (CUDA), `extract` src/dst, старт SAEHD LIAE-UD 256 от DFL-претрейна, замер **ит/ч** | записать ит/ч → пересчёт бюджета в этом документе; чекпойнт выгружен на ПК; `pods []`, 0 $/ч |
| 4. GPU-сессии B…N (обучение) | по $1.8–3.2 за сессию | продолжение с чекпойнта (модель ~1–2 ГБ туда-обратно; сеть ≥ 40 МБ/с); каждые 50k ит — экспорт DFM и локальный замер | **стоп-правило:** если на 100k ит arcface mean < 0.72 на ref-clip-03 — остановить, не докупать; на 150k ожидаем ≥ 0.78; цель ≥ 0.80 / min ≥ 0.65 / std ≤ 0.06 / T3 ≤ 0.05 / jitter ≤ 0.03 |
| 5. Mimics 2.0 | локально | `expression_restorer` (LivePortrait) sweep factor 60–100 поверх DFM-свопа; `tools/video_gate/mimics_gate.py` | blendshape-косинус ≥ 0.99 |
| 6. Резкость/артефакты | локально | gfpgan blend 25 vs 50, Real-ESRGAN x2 на мастере; `artifact_audit.py` | резкость ≥ 1.5× V1; 0 кадров с «лицом на макушке» (pose gate) |
| 7. Приёмка | пульт | 3 ролика до/после, честные подписи («лицо — модель, обученная на арендованной GPU») | слепая оценка владельца ≥ 7/10 |

## 6. Бюджет сессий (d)

ФАКТ (RunPod Secure on-demand, страница от 27.09.2026): A100 PCIe/SXM 80 ГБ $1.59/ч, H100 PCIe $2.89, H100 NVL $3.19, H100 SXM $3.99, RTX 4090 $0.89, L40S $1.09; биллинг посекундный. Владелец ранее называл $1.79–3.99/ч — верхняя граница совпадает, нижняя сейчас ниже.

ОЦЕНКА скорости (источников с ит/ч для 3090/4090/A100 нет; агент-оценка): 256-рес, batch 8: 0.6–1.2 с/ит на 3090/4090 → 3–6 тыс. ит/ч; A100 ×1.5–2 → 4.5–9 тыс. ит/ч. ФАКТ-secondhand: минимум 100k ит, 500k–1M «для резкости».

| Сценарий | Часы | $ на A100 ($1.59) | Сессий ≤2 ч | $ на 4090 ($0.89) |
|---|---|---|---|---|
| сессия A (извлечение + замер) | 2 | 3.2 | 1 | 1.8 |
| 100k ит (стоп-правило) | 11–22 | 18–35 | 6–11 | 15–30 (16–33 ч) |
| 150k ит (цель) | 17–33 | 27–53 | 9–17 | 22–45 |
| 300k ит (резкость) | 33–67 | 53–106 | 17–34 | 45–90 |

Вывод: **$24 хватает на этап 0–3 и ~5 сессий обучения (≈ 40–60k ит)** — этого достаточно, чтобы измерить реальную скорость и первую кривую arcface, но не на целевые 150k. Решение о +$30–60 — за владельцем после сессии A с реальными числами. Хранить её кропы на Network Volume ($0.07/ГБ/мес) не предлагается: каждая сессия — загрузка и удаление, чекпойнт модели едет на ПК.

## 7. Проверка новых данных — `tools/dataset_check/check_faces.py` (CPU)

```
python tools\dataset_check\check_faces.py <папка с фото/видео> --out <папка отчёта> [--video-step 10] [--min-face 160] [--min-sharp 60] [--min-consist 0.50] [--max-yaw 0.45] [--pitch 0.20,0.60] [--phash-dist 6]
```
Выход: `report.json`, `report.csv`, `rejected_contact_sheet.jpg`, `accepted_contact_sheet.jpg`. Бэкенд `onnx` = onnxruntime 1.30 CPU + модели FaceFusion (`yoloface_8n`, `arcface_w600k_r50`, `2dfan4`) — те же, что в Gate 1, поэтому consistency сопоставима с T7. Без моделей — `opencv`-fallback: личность/поза `NOT_RUN`, вердикт никогда не ACCEPT (только REJECT/INSUFFICIENT_EVIDENCE). Дубликаты — pHash (64 бит DCT), в кластере остаётся самый резкий кадр.

Замер 11.10 (ФАКТ): синтетика 7 файлов/9 элементов — 2.1 с, все NO_FACE (мультяшные лица yoloface не берёт — ожидаемо); публичные картинки (FaceFusion `.github/preview.png`, TripoSR `police_woman.png`) — 2 ACCEPT: det 0.83–0.85, pitch 0.35–0.37 (норма pose-gate 0.32–0.46), consistency 0.72, face_px 174–211. Тесты: `python -m pytest tests/test_dataset_check_faces.py -n 1` → 13 passed (дважды). Её фото инструментом не прогонялись — это делает владелец/следующий запуск с его ведома.

## 8. Что нужно от владельца

1. **Согласие** (её и его, явно в пульте): обучение модели её лица; временная загрузка aligned-кропов на арендованный под; хранение модели `.dfm` только локально.
2. **Новая съёмка, 6–9 минут суммарно**, телефон 1080p+/30 fps, без beauty-фильтра, лицо ≥ 1/3 высоты кадра, волосы не закрывают лицо:
   - 3 освещения × ~2–3 мин: дневное у окна, тёплое комнатное, тусклое/вечер;
   - в каждом: 45 с говорит в камеру фронтально; 45 с медленный поворот головы влево-вправо до ~60° и вверх-вниз ~20°; 45 с мимика (улыбка, смех, удивление, речь, моргание);
   - опционально 1 мин на улице. Это ≈ 3 000–5 000 кадров при 10 fps → после dedupe ~2 000 (попадает в 2 000–6 000).
3. **Бюджет RunPod**: подтвердить $24 на этапы 0–4 (первые сессии) и решить о добавке после сессии A.
4. Проверить этап 0 (DirectML дымовой тест) можно сразу, без согласия и без денег — он не касается её данных.

## 9. Источники

- FaceFusion deep_swapper (код): https://github.com/facefusion/facefusion — `facefusion/processors/modules/deep_swapper/core.py`, `choices.py`; локальная копия @7247081 (30.09.2026).
- FaceFusion лицензии моделей: https://docs.facefusion.io/introduction/licenses.md ; changelog: https://docs.facefusion.io/introduction/changelog
- DeepFaceLab (архив 13.11.2024, GPL-3.0): https://github.com/iperov/DeepFaceLab ; DeepFaceLive: https://github.com/iperov/DeepFaceLive
- DFL 2.0 guide (архитектуры, DX12-сборка, экспорт dfm): https://www.deepfakevfx.com/guides/deepfacelab-2-0-guide/
- Объём датасета (secondhand): https://pub.dfblue.com/pub/2019-10-25-deepfacelab-tutorial ; итерации: https://deepwiki.com/iperov/DeepFaceLab/3.3-training-guide
- Публичные RTM DFM-модели: https://huggingface.co/datasets/dimanchkek/Deepfacelive-DFM-Models
- DFL на AMD DX12 (зависания): https://github.com/iperov/DeepFaceLab/issues/5488 ; скорость на GTX 1650: https://github.com/iperov/DeepFaceLab/issues/5732
- ROCm на Windows — только PyTorch: https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest ; gfx1151 nightlies: https://rocm.nightlies.amd.com/v2/gfx1151/
- ORT DirectML (один поток на Run, sustained engineering): https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html ; issues #22867, #22147, #25577; PyPI onnxruntime-directml 1.24.4 (17.03.2026).
- RunPod цены (27.09.2026): https://www.runpod.io/gpu-instance/pricing
- ReSwapper (AGPL-3.0): https://github.com/somanchiu/ReSwapper ; GHOST (Apache-2.0): https://github.com/ai-forever/ghost
- LivePortrait (MIT): https://github.com/KwaiVGI/LivePortrait ; FasterLivePortrait: https://github.com/warmshao/FasterLivePortrait
- Rope-directml: https://github.com/a-krp/Rope-directml ; ReActor: https://github.com/Gourieff/ReActor-UI
- Процедура RunPod в Bossman: память `runpod-lora-procedure-20261009`; правила — `docs/owner/MOTION_STUDIO_V2_TZ_HANDOFF_20261011.md` §7.
