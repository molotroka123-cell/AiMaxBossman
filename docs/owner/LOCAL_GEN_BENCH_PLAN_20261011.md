# Лучший локальный генератор видео/изображений: исследование, установка, план бенча (пороги зафиксированы ДО запуска)

Дата: 2026-10-11. Владелец (дословно): «Сделать лучший генератор доступный локально нужно».
Железо: Ryzen AI Max+ 395, Radeon 8060S (gfx1151, RDNA 3.5), 128 ГБ единой памяти (≈96 ГБ видимо GPU), Windows 11, драйвер 32.0.22021.1009.
Правило: раздел 5 (пороги) после первого прогона не редактируется; изменение метода = новый `metric_version` + новый датированный файл.
Платных сервисов и облака нет. Бюджет скачивания ≤ 60 ГБ, всё в `C:\Users\asd\Bossman\models\media\`.

## 1. Факты о текущем стеке (проверено локально 11.10)

- Рабочий sd-cli: `media-runtime\sdcpp\vulkan\sd-cli.exe`, commit `74988b2` (21.09.2026). Wan2.1 VACE 1.3B: ~31 с/кадр при 576x480, 20 шагов (замер владельца).
- Этот билд старше фикса 5-мерных conv3d-тензоров в GGUF (PR #2062, слит 27.09.2026, merge `168f7b8`): без него GGUF Wan2.2 и Wan2.1-14B «не принимаются» (это и есть заметка скилла «sd-cli refuses the model → use official safetensors, not community GGUF»). Регрессия авто-сегментации Wan2.2 (`--auto-fit`, issue #1976) исправлена в master-899.
- ROCm-Windows torch 2.9.1+rocm7.2.1: Wan2.2-Animate — HIP launch failure; T3-Video — VAE-decode 81 кадров даёт чёрный тензор (docs/archive/2026-10/owner/T3_VIDEO_4K_EVAL_PLAN_20261011.md §7). conv3d VAE на MIOpen-Windows не надёжен.
- Состояние GPU утром 11.10: все Vulkan-smoke в `motion-v1-review-20261011\04_v2_work\outpaint_test_input04\work49\` (1 шаг, vae=cpu, без FA, без coopmat) падают с `device lost on Vulkan0` за ≤5 с. Сброс драйвера (Win+Ctrl+Shift+B) координатором: smoke VACE 1.3B 5 кадров 256x256 2 шага = 12.6 с, без device lost → GPU-бенч разрешён (один GPU-процесс, таймаут, стоп при первом device lost).
- TdrDelay/TdrDdiDelay в реестре не заданы (по умолчанию 2 с). Нужны права администратора — у агента их нет.
- Уже на диске (0 ГБ скачивания): Wan2.2-TI2V-5B Q8 (5.0 ГБ) + wan2.2_vae + umt5-xxl Q8 (5.6 ГБ); Wan2.1 VACE 14B Q4_K_M (10.8 ГБ) и fp16 (32 ГБ); Wan2.1 VACE 1.3B; Qwen-Image-2.1 Q8; Qwen-Image-Edit-2509; FLUX.2 klein 4B Q8; Z-Image-Turbo Q8; lightx2v LoRA Wan2.1-I2V-480p и Wan2.2 T2V high-noise (в ComfyUI\models\loras).

## 2. Бэкенды на gfx1151 / Windows (2026) — что реально работает

| Бэкенд | Статус на этом ПК / по чужим данным | Вердикт |
|---|---|---|
| stable-diffusion.cpp **Vulkan** (ggml, KHR_coopmat, bf16) | Единственный путь, который здесь уже рендерил видео (VACE 1.3B). Релиз master-956 (10.10.2026) поддерживает Wan2.1/2.2 (5B, A14B MoE, VACE 2.1/2.2-Fun, S2V), LTX-2.3/2.5, Qwen-Image, Z-Image, Flux.2, MiniMax-H3. Регрессии: LTX-2.5 и MiniMax-H3 на 16 ГБ Vulkan сломаны с master-864 по master-956 (issue #1976) — у нас 96 ГБ, сегментация не нужна, но риск остаётся. | **Основной.** Изолированный билд `models\media\sdcpp-vulkan-master-956-1b0ba10\`, рабочий sd-cli не тронут |
| ROCm-Windows PyTorch 7.2 (AMD, янв. 2026) + ComfyUI | Единственный публичный успех Wan2.2 на Strix Halo/Windows: torch 2.12 nightly rocm7.13 + ComfyUI 0.34 + WanVideoWrapper; нативные Wan-ноды «не работоспособны», umt5 fp8 даёт шум; замеры 80 Вт: A14B fp8 + lightx2v 6 шагов 832x480/89 кадров = 40 мин 16 с (≈27 с/кадр, 396 с/итер.), 20 шагов без LoRA ≈ 3.5 ч, 56 ГБ (https://github.com/yangxiarui4-dotcom/wan22-strix-halo-windows-guide). У нас VAE-decode чёрный, HIP launch failure. | Резерв для A/B, не основной |
| TheRock / scottt gfx1151 wheels | scottt v6.5.0rc: torch 2.7.0a0, py3.12, май 2025 (устарел). TheRock nightly: зависания длинных последовательностей на gfx1151 (LTX-2.3 22B, ROCm/TheRock#7569, авг.–окт. 2026, workaround нет, откат на ROCm 7.14/torch 2.13). Контейнеры kyuz0/hec-ovi — только Linux (Fedora/Ubuntu, amdgpu.gttsize, --disable-mmap, --fp32-vae). | Не для Windows сейчас |
| sd.cpp **win-rocm-10.1.0** zip | Есть в релизе master-956; поддержка gfx1151 в этом zip не подтверждена. | Опциональный A/B после Vulkan (`-Flavor rocm-10.1.0`) |
| ZLUDA / DirectML | Ни одного свидетельства gfx1151+видео-DiT в 2026; DirectML без conv3d-VAE пути. | Пропуск |

## 3. Кандидаты (ранжирование по ожидаемому качество × скорость для outpaint 9:16, замена персонажа/объекта, фон, B-roll, кадры-референсы)

Числа «ожидаемо» — не замеры; единственный чужой замер на этом же GPU отмечен (ROCm). Собственные числа появятся только из бенча (§6).

| # | Модель | Бэкенд | Лицензия (коммерция) | Память | Ожидаемо с/кадр, 480p | VACE/outpaint | Зачем / риски |
|---|---|---|---|---|---|---|---|
| 1 | **Wan2.2-VACE-Fun-A14B** GGUF Q8_0 (high 18.7 + low 18.7 ГБ) + Wan2.2-Lightning 4-step LoRA V1.1 (2×1.23 ГБ) | sd.cpp Vulkan master-956 | Apache-2.0 (alibaba-pai; QuantStack; lightx2v) | 37 ГБ веса + umt5 5.6 + активации; auto-fit | 20–50 (на базе ROCm-замера 27 с/кадр fp8 6 шагов на том же GPU; Vulkan Q8 — неизвестно) | **да**: inpaint/outpaint/extension/R2V/контроль, MoE high/low | ОСНОВНОЙ. Риски: Lightning LoRA с VACE-Fun не задокументирована у sd.cpp (U1); маршрутизация LoRA по имени high_noise/low_noise (U2); PR #2062 проверен автором только на CUDA Q3_K_S («Vulkan verification pending») |
| 2 | **Wan2.2-TI2V-5B** GGUF Q8_0 (на диске) | sd.cpp Vulkan master-956 | Apache-2.0 | 5 ГБ + umt5 5.6 | неизвестно; плотная 5B, VAE 4×16×16 ⇒ токенов ~¼ от 14B при той же площади | нет (T2V/I2V, 24 fps, 121 кадр, 720p) | ЗАПАСНОЙ: быстрый B-roll/I2V/референс-кадры; не умеет outpaint |
| 3 | Wan2.1 VACE 14B GGUF Q4_K_M (на диске) | sd.cpp Vulkan master-956 | Apache-2.0 | 10.8 ГБ | как #1 при 20 шагах ≈ в 5 раз дольше (нет 4-step LoRA для VACE 2.1) | да | контроль «2.1 vs 2.2» на outpaint; ноль скачивания |
| 4 | Wan2.2 T2V/I2V-A14B GGUF Q8 | sd.cpp Vulkan или ComfyUI ROCm | Apache-2.0 | 2×15 ГБ | 27 (ROCm fp8 6 шагов, замер выше) | нет | чистый B-roll; покрывается #1 — не ставим |
| 5 | LTX-2.3 22B GGUF Q4_K_M (+ Gemma-3-12B, video/audio VAE, connectors ≈ 25 ГБ) / LTX-2.5 | sd.cpp Vulkan (2.3 работает, 2.5 сломан на 16 ГБ #1976) | LTX-2 Community: коммерция только при выручке < 10 млн $/год | 13 + 8 ГБ | нет данных по Vulkan/Strix; на ROCm gfx1151 зависания (#7569) | нет | фаза 2: быстрый на CUDA, здесь не подтверждён; лицензия с порогом |
| 6 | HunyuanVideo 1.5 (8.3B) | только ComfyUI (sd.cpp не поддерживает) ⇒ ROCm-Windows | Tencent Community: **не действует в ЕС, Великобритании, Ю. Корее** | 14–24 ГБ | нет данных | нет | пропуск: единственный путь — тот, что у нас даёт чёрный VAE |
| 7 | SkyReels-V2 DF 14B (на базе Wan2.1) | sd.cpp как Wan2.1 — не проверено | Skywork Community (коммерция разрешена) | 2×? | как Wan2.1 14B | нет | «бесконечная длина»; позже |
| 8 | CogVideoX 5B | diffusers | CogVideoX license | — | — | нет | устарел (2024), слабее Wan2.2 — пропуск |
| 9 | Стили/референсы: Qwen-Image-Edit-2509, FLUX.2 klein 4B, Z-Image-Turbo (все на диске) | sd.cpp Vulkan | Apache-2.0 все три | 4–8 ГБ | секунды/кадр | — | референс-кадры для I2V/VACE. **Qwen-Image-2.1 (на диске) — Qwen Research License, не для коммерции** |

Источники: https://github.com/leejet/stable-diffusion.cpp (docs/wan.md, docs/ltx2.md, releases master-956-1b0ba10, PR #2062, issue #1976); https://huggingface.co/QuantStack/Wan2.2-VACE-Fun-A14B-GGUF; https://huggingface.co/alibaba-pai/Wan2.2-VACE-Fun-A14B; https://huggingface.co/lightx2v/Wan2.2-Lightning; https://github.com/yangxiarui4-dotcom/wan22-strix-halo-windows-guide; https://github.com/kyuz0/amd-strix-halo-image-video-toolboxes; https://github.com/ROCm/TheRock/issues/7569; https://github.com/scottt/rocm-TheRock/releases; https://www.amd.com/en/resources/support-articles/release-notes/RN-AMDGPU-WINDOWS-PYTORCH-7-2.html; https://huggingface.co/Lightricks/LTX-2.5; https://blog.comfy.org/p/hunyuanvideo-15-native-support; https://huggingface.co/skywork/skyreels-v2-df-14b-720p; https://huggingface.co/leejet/Qwen-Image-2.1-GGUF.

## 4. Установлено (изолированно, без изменения рабочего sd-cli)

- `tools/local_gen/install_sdcpp_vulkan.ps1` → `models\media\sdcpp-vulkan-master-956-1b0ba10\sd-cli.exe` (commit 1b0ba10, 30 МБ). Выполнено.
- `tools/local_gen/install_wan22_vace_fun_a14b.ps1` → `models\media\wan22-vace-fun-a14b\{HighNoise,LowNoise}\*Q8_0.gguf`, `loras\wan22_lightning_t2v_v1.1_{high,low}_noise.safetensors`, `wan_2.1_vae.safetensors`, `MANIFEST-local.json`. Выполнено 11.10 08:32: 18.66 ГБ × 2 + 1.23 ГБ × 2 + 0.24 ГБ (≈40 ГБ). sha256 обоих GGUF совпадают с HF LFS (high `89b43d99…`, low `854d70e5…`, файл `SHA256SUMS.txt` в папке).
- `tools/local_gen/install_wan22_ti2v_5b.ps1` → проверка трёх файлов на диске, `MANIFEST-local.json`. Выполнено, 0 ГБ скачано.
- Общие: umt5-xxl Q8 из `wan22-ti2v-5b\`, VAE 2.1 из `wan21-vace-1.3b\`.

## 5. ФИКСИРОВАННЫЕ пороги (metric_version `lg-v1`, файл `tools/local_gen/bench_thresholds.json` — единственный источник для харнесса)

Вход: `input_04.mp4` (1280×720, 30 fps), панель crop 740×720 @ x=268, с 1.0 с, 5 с. Промпты и seed 20261011 зафиксированы в `bench_local_gen.py`. Сэмплер euler, `--diffusion-fa --vae-tiling --temporal-tiling`.

| Рунг | T2V/I2V | Outpaint 9:16 | Кадров/fps | Кап по времени |
|---|---|---|---|---|
| 480p | 832×480 | 448×768 | 81 @16 (Wan 14B/VACE), 121 @24 (TI2V-5B) | 3600 с |
| 720p | 1280×704 | 704×1280 | те же | 5400 с |

| Гейт | Метрика | PASS |
|---|---|---|
| G1 completion | exit 0, ровно N кадров нужного размера, wall ≤ кап, нет `device lost` | всё верно |
| G1b speed | с/кадр = wall / N | 480p ≤ 30; 720p ≤ 60 (FAST: ≤ 10 / ≤ 20) |
| G2 validity | доля кадров с mean ∈ [8, 247] и std ≥ 4 (gray 0–255) | ≥ 0.95 |
| G3 sharpness | дисперсия лапласиана 3×3 (gray float, native), среднее по 5 кадрам (0, ¼, ½, ¾, N-1) | ≥ 50 и ≥ 1.0× baseline (Wan2.1 VACE 1.3B, тот же task/rung) |
| G4 flicker | T2V/I2V: средняя |Δ| соседних кадров в статичной ROI (t2v: 0–25 % × 0–12 % = небо; i2v: 0–20 % × 0–20 % = стена). Outpaint: flicker сгенерированной зоны / flicker оригинальной зоны | ≤ 2.0 и ≤ 1.5× baseline; outpaint ≤ 2.0 |
| G5 outpaint | PSNR оригинальной области (сырой выход vs control-кадр, внутри ±8 px от шва) min / mean; средняя яркостная разница на шве (16 px) | min ≥ 26 дБ, mean ≥ 30 дБ, шов ≤ 12 |
| G6 memory | пик (system used + GPU shared+dedicated) и рост swap | < 110 ГБ, swap < 2 ГБ |

Вердикт кандидата на рунге: PASS = все гейты на всех своих задачах; PARTIAL = G1/G2 на всех, G3–G6 на ≥ 2 из 3 задач; иначе FAIL. Остановка по первому FAIL/TIMEOUT у кандидата («stop climbing»), по первому `device lost` — стоп всей матрицы и запись в отчёт.
«Лучше текущего стека» = PASS на 480p при с/кадр ≤ 30 и G3 ≥ baseline и G4 ≤ baseline. Baseline (1.3B) не гейтится, кроме G1.
Ручной чек (не автомат): владелец смотрит 3 кадра на задачу — соответствие промпту и артефакты (швы тайлов, «лишние люди», морфинг).

Неизвестные, фиксируем до запуска: U1 Lightning-T2V-LoRA на VACE-Fun (если артефакты — повтор с 20 шагами без LoRA, cfg 6, отдельной строкой); U2 маршрутизация LoRA по имени high_noise/low_noise в sd.cpp; U3 реальная память двух Q8-экспертов на Vulkan с auto-fit; U4 TI2V-5B при 121 кадре 24 fps на Vulkan-внимании.

## 6. Команды (после подтверждения, что GPU «чист»)

```
cd C:\Users\asd\Bossman\wt-unified-20261010
python tools\local_gen\bench_local_gen.py preflight
# smoke (не гейт): новый билд на GPU, 9 кадров, 4 шага
python tools\local_gen\bench_local_gen.py run --candidate wan22-ti2v-5b-q8 --task t2v --rung 480p --frames 9 --steps 4 --cap 600
# самый короткий рунг: запасной кандидат, T2V 480p (полный гейт)
python tools\local_gen\bench_local_gen.py run --candidate wan22-ti2v-5b-q8 --task t2v --rung 480p
# вся матрица 480p (порядок: 5B → 1.3B baseline → VACE-Fun A14B → VACE 2.1 14B), затем 720p
python tools\local_gen\bench_local_gen.py matrix --rung 480p
python tools\local_gen\bench_local_gen.py matrix --rung 720p
python tools\local_gen\bench_local_gen.py report     # -> C:\Users\asd\Videos\Bossman-Genjutsu\local-gen-bench-20261011\results.md
```

## 7. Что требуется от владельца

- Перезагрузка ПК (сброс драйвера помог на smoke, но стабильность под 30–60-минутной нагрузкой не доказана).
- С правами администратора: `HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers` → DWORD `TdrDelay` = 60, `TdrDdiDelay` = 60 (сейчас не заданы = 2 с), затем перезагрузка. Без этого длинный VAE-encode/decode на Vulkan может ловить TDR (LiveKernelEvent 141).
- Во время бенча ПК свободен от других генераторов (ffmpeg/facefusion/ComfyUI/Wan2GP) — харнесс отказывает при их наличии.

## 8. Результаты

Заполняется только из `results.md` харнесса. Гейтовых прогонов нет. Что было запущено 11.10 после фиксации порогов (все логи в `C:\Users\asd\Videos\Bossman-Genjutsu\local-gen-bench-20261011\`):

| Прогон | Билд | Результат |
|---|---|---|
| smoke (не гейт): wan22-ti2v-5b-q8, t2v 480p, 9 кадров, 4 шага, кап 600 с | sd.cpp master-956 Vulkan | `DEVICE_LOST` на первом DiT-графе через 22.5 с (загрузка тензоров 2.5 с и umt5-кондишн 6.4 с прошли). `480p\wan22-ti2v-5b-q8\t2v\run.json` |
| диагностика (не повтор): точный smoke координатора — prod sd-cli 74988b2, VACE 1.3B, 5 кадров 256x256, 2 шага | sd.cpp 74988b2 Vulkan | `device lost on Vulkan0` сразу после загрузки тензоров. `diag-gpu-alive\sd-cli.log` |

### Инструкция владельцу (перезагрузка после завершения пода H100; бенч стартует сам после входа)

1. PowerShell **от имени администратора** (у агента прав нет, UAC на защищённом рабочем столе):
   ```
   reg add "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v TdrDelay /t REG_DWORD /d 60 /f
   reg add "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v TdrDdiDelay /t REG_DWORD /d 60 /f
   ```
2. Перед перезагрузкой (обычный PowerShell, без админа) один раз зарегистрировать пользовательскую задачу:
   `powershell -ExecutionPolicy Bypass -File C:\Users\asd\Bossman\wt-unified-20261010\tools\local_gen\register_autorun_bench.ps1`
   (задача `BossmanLocalGenBench`, триггер ONLOGON для `asd`, задержка 3 мин; удалить: тот же скрипт с `-Remove`).
3. Перезагрузить ПК, войти под `asd`, ничего не запускать (ffmpeg/facefusion/ComfyUI/Wan2GP держат GPU — харнесс откажет).
4. Через ~3 мин после входа задача сама выполняет `preflight` → smoke (TI2V-5B, 9 кадров) → `matrix --rung 480p` → `report` и удаляет себя. Лог: `C:\Users\asd\Videos\Bossman-Genjutsu\local-gen-bench-20261011\autorun.log`, итог — `results.md` там же. При `device lost` на smoke матрица не запускается (без слепых повторов). Ориентир по времени: до 4 прогонов × ≤ 1 ч.

Вердикт 11.10 утро: GPU снова в сбойном состоянии (сброс драйвера Win+Ctrl+Shift+B не удержался или слетел на первой «большой» нагрузке); проблема не в новом билде и не в 5B. GPU-часть бенча = INSUFFICIENT_EVIDENCE до перезагрузки ПК и (желательно) TdrDelay=60. Параллельно на CPU шли агенты роликов `hq_v2.py a/b/d` + 3×ffmpeg (записано в `other_load` run.json).
