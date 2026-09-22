# Матрица кандидатов медиамоделей (раздел 3 ТЗ владельца)

**Дата проверки:** 2026-09-22
**Ветка:** `docs/media-candidates-20260922` (worktree `C:\Users\asd\Bossman\wt-mediadocs`, база `fc266856`)
**Машина:** Windows 11 Pro 26200, Ryzen AI Max+ 395, Radeon 8060S, 128 GB общей памяти

## Статус выполнения и честные ограничения

- GPU во время проверки был занят чужим прогоном (`sd-cli` PID 1644, старт 22.09.2026 10:18, WS 38.3 GB).
  **Ни одна генерация не запускалась.** Выполнялись только `sd-cli.exe --help`, чтение файлов, сетевые
  метаданные Hugging Face/GitHub и расчёты.
- Ни одна модель-кандидат **не скачивалась**. Максимальные сетевые обращения — два range-запроса для
  измерения скорости (20 MB + 32 MB, в `/dev/null`, файлы не сохранялись).
- Всё, что не проверено на этой машине запуском, помечено **NOT_RUN**. Сравнения качества между
  моделями в этом документе нет — на машине владельца ничего не сравнивалось.
- Ни одно пользовательское соглашение не принималось, персональные данные никуда не передавались.
  Gated-модели помечены **OWNER_REQUIRED_LICENSE** с точной ссылкой.

## Словарь статусов

| Статус | Значение |
|---|---|
| `SUPPORTED_BY_CURRENT_SDCLI` | Архитектура присутствует в enum `SDVersion` пиннутого коммита **и** строки-маркеры найдены в фактическом `stable-diffusion.dll` на этой машине **и** есть документация с рабочим примером CLI на этом же коммите |
| `ARCH_SUPPORTED_CHECKPOINT_UNDOCUMENTED` | Семейство архитектур поддержано, но конкретный чекпойнт не назван в docs пиннутого коммита — требуется проверка загрузчика на машине |
| `UNSUPPORTED` | Архитектуры/формата нет в бинарнике |
| `NEEDS_NEWER_RUNTIME` | Требуется более новая сборка, чем закреплённая |
| `OWNER_REQUIRED_LICENSE` | Нужно, чтобы владелец лично принял соглашение на HF |
| `NOT_RUN` | На этой машине не запускалось |
| `BASELINE_ROLLBACK` | Текущий рабочий baseline, точка отката, не трогать |

---

## 1. Что реально поддерживает ЭТОТ бинарник

### 1.1 Закреплённый runtime

| Поле | Значение | Источник |
|---|---|---|
| Репозиторий | `github.com/leejet/stable-diffusion.cpp` | `stable-diffusion.cpp.txt` (MIT, © leejet) + build-path `D:\a\stable-diffusion.cpp\stable-diffusion.cpp\` внутри DLL |
| Релиз | `master-890-74988b2` | `C:\Users\asd\Bossman\models\media\MANIFEST.json` → `engine.release` |
| Полный коммит | `74988b290e40155fe2313914e44b979b750e958b` | GitHub API |
| Дата коммита | 2026-09-21 13:48:42 UTC | GitHub API |
| Сообщение коммита | `fix: reject video models in image generation (#2017)` | GitHub API |
| Бэкенд | Vulkan (`ggml-vulkan.dll`, 50 544 640 B) | каталог сборки |
| Версия в `--help` | `stable-diffusion.cpp version unknown, commit 74988b2` | запуск `--help` на этой машине |

Хэши фактических файлов на диске (проверено `Get-FileHash` **на этой машине**, 2026-09-22):

| Файл | SHA-256 |
|---|---|
| `media-runtime/sdcpp/vulkan/sd-cli.exe` | `ee6fb793824759f322202deec69d9b5526765bb3308930ff754cdf906cb9cfd1` |
| `media-runtime/sdcpp/vulkan/stable-diffusion.dll` | `c47cc045f690ce72ba680834c9d3b0df7ced6b223cb1565b92eb7e409809d05a` |
| `media-runtime/sdcpp/vulkan/ggml-vulkan.dll` | `c663d7002cf2710ddef080b5b5433b8527eeafe86f635efc73dfb2be13da60b2` |
| `media-runtime/sdcpp/sd-vulkan.zip` (исходный архив релиза) | `744c8f817c66ecfd02fbb9dc8b122e1f29f7240db1f6086dfde2669403c5d896` |

`sd-cli.exe` совпадает с `engine.binary_sha256` в `models/media/MANIFEST.json` — манифест актуален.

### 1.2 Полный список архитектур, вкомпилированных в бинарник

Источник A — `enum SDVersion` в `src/model.h` на коммите `74988b2`.
Источник B — таблица человекочитаемых имён, извлечённая **из фактического `stable-diffusion.dll` на этой машине**
(смещение строк 65855–65902):

```
SD 1.x / SD 1.x Inpaint / SD 1.x Tiny UNet
SD 2.x / SD 2.x Inpaint / SD 2.x Tiny UNet
SDXL / SDXL Inpaint / SDXL Instruct-Pix2Pix / SDXL (Vega) / SDXL (SSD1B)
SD3.x
Flux / Flux Fill / Flux Control
Chroma Radiance
Wan 2.x / Wan 2.2 I2V / Wan 2.2 TI2V / Wan 2.2 S2V
LingBot Video
Qwen Image / Qwen Image Layered / Qwen Image 2.1
Hunyuan Video
Anima
Flux.2 / Flux.2 klein
LTXAV
MiniMax-H3
HiDream O1
Z-Image
Boogu Image / Ovis Image / Lens / MiniT2I / Ideogram 4
SeFi-Image / Krea2 / Mage Flow / SenseNova U1.5 / LLaDA-Image
ESRGAN
```

Дополнительные маркеры, найденные **в самом DLL** (подтверждают, что код действительно собран, а не только
описан в upstream README):

| Маркер в DLL | Что подтверждает |
|---|---|
| `LTXAV`, `ltxav`, `ltxav_text_projection`, `ltxav_video_connector_pe` | LTX-2 транформер + проекция текста + connector-PE |
| `gemma4_12b` | путь энкодера Gemma-4 для LTX-2.5 |
| `loading embeddings connectors from '%s'` | `--embeddings-connectors` (нужно для LTX-2.3) |
| `ltx_audio_vae`, `loading audio VAE from '%s'` | `--audio-vae` |
| `ltx_latent_upsampler`, `LTX latent upsampler` | spatial upscaler LTX |
| `LTX2 scheduler: tokens=%d, shift=%.4f, stretch=%d, terminal=%.4f` | планировщик `ltx2` |
| `LTX VAE encoder is only implemented for version >= 2` | I2V-путь LTX-2 |
| `flux2`, `klein`, `Flux2 scheduler` | FLUX.2 / FLUX.2-klein |
| `Qwen Image 2.1`, `qwen_image_2.1` | Qwen-Image 2.1 |
| `Qwen Image 2.1 editing requires Qwen3-VL vision weights; provide --llm_vision or a combined encoder` | edit-путь Qwen 2.1 требует `--llm_vision` |
| `QwenImageEditPlusPipeline` | Qwen-Image-Edit (2509/2511) |
| `convrot` | формат `int8_convrot` из ComfyUI-квантовок |
| `Wan2.2-I2V-14B`, `Wan2.2-TI2V-5B`, `Wan2.2-S2V-14B` | Wan2.2 варианты, включая A14B I2V |
| `minimax` | MiniMax-H3 |

### 1.3 Ключевые флаги, подтверждённые `--help` на этой машине

```
-M, --mode                      img_gen | adetailer | vid_gen | upscale | convert | metadata
--llm <string>                  LLM-энкодер (qwenvl2.5 для qwen-image, mistral-small3.2 для flux2, ...)
--llm_vision <string>           ViT LLM (нужен для edit-режимов)
--clip_l / --clip_g / --t5xxl   классические энкодеры
--embeddings-connectors <s>     LTXAV embeddings connectors
--audio-vae <string>            LTX audio VAE
--audio-encoder <string>        wav2vec2 (Wan2.2 S2V)
--diffusion-model <string>
--high-noise-diffusion-model    высокошумная половина MoE (Wan2.2 A14B)
--uncond-diffusion-model        Ideogram4 CFG
--vae-format                    auto | flux | sd3 | flux2 | wan
--moe-boundary <float>          граница таймстепа Wan2.2 MoE (0.875)
--backend / --params-backend    cpu | cuda0 | vulkan0 | disk, per-module
--max-vram / --auto-fit         бюджет VRAM и авто-раскладка весов
--offload-to-cpu / --mmap       веса в RAM
--model-args                    chroma_use_dit_mask, chroma_use_t5_mask, chroma_t5_mask_pad, qwen_image_zero_cond_t
--scheduler                     ... ltx2, flux2, flux, logit_normal, bong_tangent ...
--extra-sample-args             ltx2 поддерживает max_shift, base_shift, stretch, terminal
-r, --ref-image                 Flux Kontext или MiniMax-H3 Ref2VA
--ref-video / --ref-audio       MiniMax-H3 Ref2VA
--list-devices                  перечисление ggml-устройств
```

**Чего в этом бинарнике НЕТ (проверено):** отдельного флага/режима для LTX «duration head» и temporal
latent upscaler — upstream-документация на этом же коммите прямо пишет «Not Implemented: the diffusion
video decoder (`ltx-2.5-video-vae-bf16.safetensors`), temporal latent upscaler and duration head;
`--video-frames` must be specified explicitly».

---

## 2. Матрица кандидатов

### Замер скорости загрузки (эта машина, 2026-09-22)

| Проба | Источник | Объём | Время | Скорость |
|---|---|---|---|---|
| 1 | `leejet/FLUX.2-klein-4B-GGUF` Q8_0 | 20 971 520 B | 1.252 s | 16 751 900 B/s |
| 2 | то же, повтор | 20 971 520 B | 0.986 s | 21 268 664 B/s |
| 3 | `vantagewithai/LTX-2.5-GGUF` distilled Q4_K_M | 33 554 432 B | 1.841 s | 18 227 713 B/s |

**Расчётная скорость для планирования: 18.0 MB/s (~145 Мбит/с), одно соединение.** Для сравнения:
`media-runtime/install.log` показывает 57–73 MB/s с `repo.radeon.com` — то есть канал шире, узкое место
именно CDN Hugging Face на одном потоке. Параллельная загрузка (`hf_transfer`, `aria2 -x8`) может
дать больше, но **на этой машине не измерялась — NOT_RUN**.

Ориентир владельца «25 ГБ» при 18 MB/s ≈ 23 мин 8 с — согласуется с прошлой загрузкой.

### Свободное место

**Диск C: свободно 1 671 589 744 640 B = 1 556.8 GiB.** Все профили ниже вместе (~200 GiB) помещаются
с большим запасом; дисковый бюджет ограничением не является.

---

### 2.1 Кандидат A — `black-forest-labs/FLUX.2-klein-4B` (изображения)

| Поле | Значение |
|---|---|
| **Статус** | `SUPPORTED_BY_CURRENT_SDCLI` + `NOT_RUN` |
| Архитектура | `VERSION_FLUX2_KLEIN` («Flux.2 klein» в DLL) |
| Документация на коммите | `docs/flux2.md` @ `74988b2`, раздел FLUX.2-klein-4B, с рабочим примером CLI |
| Gated | **Нет** ни по одному обязательному компоненту |
| Лицензия весов | **Apache-2.0** (`black-forest-labs/FLUX.2-klein-4B`, поле `license` = apache-2.0, файл `LICENSE.md` в репо) |
| Лицензия энкодера | **Apache-2.0** (`unsloth/Qwen3-4B-GGUF`) |
| Лицензия VAE | **Apache-2.0** (`black-forest-labs/FLUX.2-small-decoder`) |
| Лицензия квантовки | **Apache-2.0** (`leejet/FLUX.2-klein-4B-GGUF`) |
| Коммерческое использование | Разрешено во всех компонентах, отдельного согласия не требуется |
| Runtime/backend | sd-cli Vulkan, `img_gen`, `--diffusion-model` + `--vae` + `--llm` |

Рекомендуемый профиль (Q8_0):

| Роль | Репозиторий | Revision | Файл | Байт | SHA-256 (LFS oid, HF paths-info) |
|---|---|---|---|---|---|
| diffusion | `leejet/FLUX.2-klein-4B-GGUF` | `3b1f5a9dc3abb32238b053aeb3d823c30afdacbd` | `flux-2-klein-4b-Q8_0.gguf` | 4 300 629 440 | `0bba6951258ec8f92d51114a8fa13e66828297bfff58a738f52729b3ef66fa28` |
| vae | `black-forest-labs/FLUX.2-small-decoder` | `a3efc24f613ef42d9428af62fdbd6f5fd8856c4a` | `full_encoder_small_decoder.safetensors` | 249 519 092 | `ea4273f02d1fafbf8e1d1c2cf6018ed8748652eb0bf34f2dd91171f16f15ab62` |
| llm (text encoder) | `unsloth/Qwen3-4B-GGUF` | `22c9fc8a8c7700b76a1789366280a6a5a1ad1120` | `Qwen3-4B-Q8_0.gguf` | 4 280 405 792 | `eed555233267a33c7e8ee31682762cc7751b3f6d224039086e0e846f05fffa5d` |

**Итого: 8 830 554 324 B = 8.22 GiB, ≈ 8 мин 11 с при 18 MB/s.**

Экономичный вариант (Q4):

| Роль | Файл | Байт | SHA-256 |
|---|---|---|---|
| diffusion | `flux-2-klein-4b-Q4_0.gguf` | 2 460 378 560 | `d1023499ef3f2f82ff7c50e6778495195c1b6cc34835741778868428111f9ff4` |
| vae | `full_encoder_small_decoder.safetensors` | 249 519 092 | как выше |
| llm | `Qwen3-4B-Q4_K_M.gguf` | 2 497 281 312 | `f6f851777709861056efcdad3af01da38b31223a3ba26e61a4f8bf3a2195813a` |

**Итого: 5 207 178 964 B = 4.85 GiB, ≈ 4 мин 49 с.**

Альтернатива для VAE, если понадобится полный декодер: `black-forest-labs/FLUX.2-klein-4B` →
`vae/diffusion_pytorch_model.safetensors`, 168 120 878 B,
SHA-256 `ca70d2202afe6415bdbcb8793ba8cd99fd159cfe6192381504d6c4d3036e0f04` (Apache-2.0, ungated).
Upstream-пример использует VAE из `black-forest-labs/FLUX.2-dev` — **этот репозиторий gated
(`gated=auto`), брать его не нужно**, `FLUX.2-small-decoder` явно назван документацией как альтернатива.

Параметры из upstream-примера: `--cfg-scale 1.0 --steps 4 --offload-to-cpu --diffusion-fa`.

Ограничения: `NOT_RUN` — на Radeon 8060S / Vulkan не запускалось. Скорость, качество и потребление памяти
на этой машине неизвестны.

**Путь отката:** удалить каталог `models/media/flux2-klein-4b/`, убрать запись `sdcpp:flux2-klein-4b` из
`tools/studio_models.json` и из `models/media/MANIFEST.json`. Baseline-модели не затрагиваются.

---

### 2.2 Кандидат B — `Lightricks/LTX-2.5`, distilled-профиль T2V/I2V (видео+аудио)

| Поле | Значение |
|---|---|
| **Статус** | `SUPPORTED_BY_CURRENT_SDCLI` + **`OWNER_REQUIRED_LICENSE`** + `NOT_RUN` |
| Архитектура | `VERSION_LTXAV` («LTXAV» в DLL); в DLL есть `gemma4_12b`, `ltxav_text_projection`, `ltx_audio_vae`, `LTX2 scheduler` |
| Документация на коммите | `docs/ltx2.md` @ `74988b2` — LTX-2.5 назван явно, с примерами T2V, I2V, FLF2V, hires |
| Gated | **ДА** на `Lightricks/LTX-2.5` (`gated=auto`) и на `elix3r/gemma4-12b-with-proj-ltx-2.5-GGUF` (`gated=auto`) |
| Лицензия | `ltx-2.x-community-license-agreement`, текст: https://github.com/Lightricks/LTX-2/blob/main/LICENSE-2_x |
| Runtime/backend | sd-cli Vulkan, `-M vid_gen`, `--diffusion-model` + `--vae` + `--audio-vae` + `--llm` |

#### Применимость LTX Community License к владельцу

Прочитан текст `LICENSE-2_x` (Lightricks/LTX-2, ветка main, 2026-09-22):

- Коммерческое использование **разрешено** организациям с годовой выручкой **менее 10 000 000 USD**.
  Цитата порога: *«Entities with annual revenues of at least $10,000,000»* обязаны получить отдельное
  Commercial Use Agreement. → Для владельца при выручке ниже порога отдельная лицензия **не требуется**;
  при приближении к порогу — требуется.
- Выход (Output) принадлежит пользователю: *«Licensor claims no rights in the Output you generate»*.
- Обязательства, которые надо соблюдать в продукте:
  1. Передавать копию соглашения любым третьим лицам, которым передаются веса.
  2. Сохранять уведомления об авторских правах и атрибуции.
  3. **Машинно-сгенерированный контент должен явно и понятно помечаться как сгенерированный ИИ**
     (*«expressly and intelligibly»*) — это требование к UI/выдаче Bossman, а не только к весам.
- Acceptable-use ограничения: запрещены дипфейки без согласия, дезинформация, автоматические решения с
  юридическими последствиями, дискриминация, медицинские советы, оружие, отключение защитных механизмов,
  обучение конкурирующих моделей (для коммерческих пользователей), прямая конкуренция с сервисами
  Lightricks без лицензии.

**Вывод: лицензия совместима с коммерческим профилем Bossman при выручке < $10M, при условии
AI-дисклеймера в выдаче и передачи текста лицензии при любой раздаче весов.**

#### Что должен нажать владелец (OWNER_REQUIRED)

1. Открыть https://huggingface.co/Lightricks/LTX-2.5 под своим аккаунтом HF и нажать кнопку принятия
   условий («Agree and access repository» / «You need to agree to share your contact information to access
   this model»). Репозиторий помечен `gated: auto` — доступ выдаётся автоматически сразу после согласия.
2. Если будет выбран GGUF-энкодер вместо bf16: открыть
   https://huggingface.co/elix3r/gemma4-12b-with-proj-ltx-2.5-GGUF и принять условия там же
   (`gated: auto`, лицензия `ltx-2-community-license-and-apache-2.0`).
3. Репозиторий квантовок трансформера `vantagewithai/LTX-2.5-GGUF` **не gated** — согласие не требуется,
   но условия LTX Community License на веса всё равно распространяются (поле `license_name` =
   `ltx-2-community-license-agreement`).

**Я не принимал ни одно соглашение и не передавал контактные данные владельца.**

#### Про энкодер: подмена похожих файлов запрещена

LTX-2.5 использует **не обычный Gemma**, а специализированный энкодер с проекцией под LTX:
`gemma4-12b-with-proj-ltx-2.5-*`. Строка `ltxav_text_projection` в DLL подтверждает, что рантайм ждёт
именно веса проекции внутри энкодера. Поэтому:

- Обычный `gemma-3-12b-it` GGUF (который используется для **LTX-2.3**) для LTX-2.5 **не подходит**.
- Для LTX-2.5 `--embeddings-connectors` **не нужен**: connector-веса вшиты в `-with-proj` энкодер
  (в upstream-примере для 2.5 этого флага нет, а для 2.3 — есть).
- VAE: обязателен **conv**-вариант `ltx-2.5-video-vae-conv-bf16.safetensors`. Диффузионный декодер
  `ltx-2.5-video-vae-bf16.safetensors` (1 472 223 346 B) в этом рантайме **НЕ реализован** — upstream-док
  прямо пишет «Use the conv VAE». Файлы отличаются на 20 MB и лежат рядом — перепутать легко, это ошибка.

#### Профиль B1 (рекомендуемый, distilled Q4_K_M + GGUF-энкодер)

| Роль | Репозиторий | Revision | Файл | Байт | SHA-256 |
|---|---|---|---|---|---|
| diffusion | `vantagewithai/LTX-2.5-GGUF` | `9a5c1bed3ff27b5e5d055fc04e3ae884381c2343` | `distilled/ltx-2.5-22b-distilled-transformer-Q4_K_M.gguf` | 15 687 639 424 | `8954332873a705cf1a980a188ace1e0fa015fc84422d2a4a4948df6a629977a7` |
| llm | `elix3r/gemma4-12b-with-proj-ltx-2.5-GGUF` | `2a18e836d286eb0570ec9f013c3591eb8c614d57` | `gemma4-12b-with-proj-ltx-2.5-Q5_K_M.gguf` | 9 514 920 864 | **UNAVAILABLE_GATED** |
| vae (video, conv) | `Lightricks/LTX-2.5` | `5e6e71018ee1756ed329b697a7b4aedc934dfce9` | `vae/ltx-2.5-video-vae-conv-bf16.safetensors` | 1 452 269 922 | **UNAVAILABLE_GATED** |
| audio-vae | `Lightricks/LTX-2.5` | `5e6e71018ee1756ed329b697a7b4aedc934dfce9` | `vae/ltx-2.5-audio-vae-bf16.safetensors` | 364 866 540 | **UNAVAILABLE_GATED** |

**Итого: 27 019 696 750 B = 25.16 GiB, ≈ 25 мин 1 с при 18 MB/s.**

Опционально spatial upscaler `Lightricks/LTX-2.5` → `latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors`,
995 778 752 B (0.93 GiB, +55 с), SHA-256 **UNAVAILABLE_GATED**.

> **Про UNAVAILABLE_GATED:** Hugging Face **маскирует LFS-oid у gated-репозиториев** — и `paths-info`
> отдаёт HTTP 401, и `tree?expand=true` возвращает `****…****` вместо хэша. Честный вывод: доверенный
> SHA-256 для этих четырёх файлов **получить нельзя, пока владелец не примет соглашение**. Размеры в
> байтах при этом публичны и приведены выше — их можно проверить сразу после загрузки. После принятия
> gate следует повторить `POST /api/models/Lightricks/LTX-2.5/paths-info/5e6e71018ee1756ed329b697a7b4aedc934dfce9`
> с токеном и занести настоящие хэши в `models/media/MANIFEST.json`.
> Писать «хэш скачанного файла, сверенный сам с собой» в манифест **нельзя**.

#### Альтернативные профили B

| Профиль | Состав | Байт | GiB | Время @18 MB/s |
|---|---|---|---|---|
| B1 | distilled Q4_K_M + gemma4 Q5_K_M(GGUF) + conv VAE + audio VAE | 27 019 696 750 | 25.16 | 00:25:01 |
| B2 | distilled Q8_0 (23 604 666 752, SHA `e268b018768e00fa987c128d76384f487f9cf2ac3e3bddb077b979224ded467a`) + то же | 34 936 724 078 | 32.54 | 00:32:20 |
| B3 | distilled Q4_K_M + **bf16** энкодер `text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors` (26 263 858 182 B) + conv VAE + audio VAE. Энкодер потом конвертируется локально: `sd-cli -M convert -m gemma4-...-bf16.safetensors --type q8_0 -o gemma4-...-Q8_0.gguf` | 43 768 634 068 | 40.76 | 00:40:31 |

B3 избавляет от второго gate (`elix3r`), но требует на 15.6 GiB больше загрузки и локальной конвертации
на CPU. B1 дешевле по трафику; B3 — по числу принятых соглашений (только один, `Lightricks/LTX-2.5`).

Дополнительно доступен ungated зеркальный репозиторий квантовок `ruygar/LTX-2.5-Comfy-GGUF`
(`license_name: ltx-2.x-community-license`), но **VAE и энкодер там отсутствуют** — gate на
`Lightricks/LTX-2.5` обойти нечем.

Параметры из upstream-примера LTX-2.5: `-M vid_gen --cfg-scale 3.0 --sampling-method euler -W 1280 -H 720
--diffusion-fa --offload-to-cpu --video-frames 121 --fps 24 -o t2v.webm`. I2V — то же плюс `-i`.
`--video-frames` **обязателен явно** (duration head не реализован).

Ограничения: `NOT_RUN`. 22B-трансформер в Q4_K_M — 14.6 GiB весов только у диффузии, плюс ~8.9 GiB
энкодера; на 128 GB общей памяти с `--offload-to-cpu` должно помещаться, но скорость на Vulkan/8060S
неизвестна. `nvfp4`-вариант (`ltx-2.5-22b-distilled-transformer-nvfp4.safetensors`) — `UNSUPPORTED`
этим рантаймом.

**Путь отката:** удалить `models/media/ltx25-distilled/`, снять запись из `tools/studio_models.json`
и `MANIFEST.json`. Baseline не затрагивается.

---

### 2.3 Кандидат C — `Qwen/Qwen-Image-2.1` (изображения) — ЛИЦЕНЗИОННО ОГРАНИЧЕН

| Поле | Значение |
|---|---|
| **Статус** | `SUPPORTED_BY_CURRENT_SDCLI` + **`OWNER_REQUIRED_LICENSE` (коммерция)** + `NOT_RUN` |
| Архитектура | `VERSION_QWEN_IMAGE_2_1` («Qwen Image 2.1» в DLL) |
| Документация | `docs/qwen_image_2.1.md` @ `74988b2` |
| Gated на HF | Нет (`gated=False`) — скачать технически можно без согласия |
| **Лицензия весов** | **`qwen-research`** (не Apache!). `Qwen/Qwen-Image-2.1` → `license: other`, `license_name: qwen-research`, текст: https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE |
| Лицензия зеркала ComfyUI | `Comfy-Org/Qwen-Image-2.1` — та же, `license_name: qwen-research`, ссылка указывает на LICENSE в репо Qwen |
| Лицензия квантовки | `leejet/Qwen-Image-2.1-GGUF` — **поле license в карточке ПУСТОЕ**. Отсутствие лицензии не делает веса свободными: производная от `qwen-research` наследует ограничения |
| Лицензия энкодера | `Qwen/Qwen3-VL-8B-Instruct-GGUF` — Apache-2.0 (энкодер сам по себе не проблема) |

**Решение по правилу владельца: `Qwen-Image-2.1` НЕ включать в коммерческий профиль без отдельной
лицензии.** Лицензия `qwen-research` — исследовательская; для коммерческого использования нужна отдельная
договорённость с Alibaba/Qwen. Это ровно тот случай, который владелец указал буквально.
Разрешённое применение: локальные R&D-прогоны и сравнения, помеченные как некоммерческие.

Состав профиля (на случай отдельно разрешённого research-профиля):

| Роль | Репозиторий | Revision | Файл | Байт | SHA-256 |
|---|---|---|---|---|---|
| diffusion | `leejet/Qwen-Image-2.1-GGUF` | `cc11433936a06e9765f7c0c0b1f0436cfd2b9856` | `qwen_image_2.1-Q8_0.gguf` | 7 687 155 744 | `f8b244b00937f0e444a40dbf7866460871b89b30142594973b6012d1b471dc0a` |
| vae | `Comfy-Org/Qwen-Image-2.1` | `5dc5850eb514a3685f6a03a2641728a8f7549c69` | `vae/qwen_image_2.1_vae_bf16.safetensors` | 675 509 688 | `bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9` |
| llm | `Qwen/Qwen3-VL-8B-Instruct-GGUF` | `f982a07559d4a2f6c8744d840bf6fccab30eea96` | `Qwen3VL-8B-Instruct-Q8_0.gguf` | 8 709 519 456 | `0d264b3941185d00a74f75c4245521dae088ff1efc90ab8d1754e83f5844adb0` |
| llm_vision (только для edit) | `Qwen/Qwen3-VL-8B-Instruct-GGUF` | тот же | `mmproj-Qwen3VL-8B-Instruct-F16.gguf` | 1 159 029 824 | `ca524100ebf825c9a870db1c580d03879e0da0ab2541697e2458e64891cf9d38` |

**T2I: 17 072 184 888 B = 15.90 GiB ≈ 15 мин 48 с. С edit (+mmproj): 18 231 214 712 B = 16.98 GiB ≈ 16 мин 53 с.**

Требования рантайма: размеры кратны 32; для edit обязателен `--llm_vision` (строка в DLL:
`Qwen Image 2.1 editing requires Qwen3-VL vision weights; provide --llm_vision or a combined encoder`).

**Путь отката:** не устанавливать до решения по лицензии. Если ставится research-профиль — отдельный
каталог `models/media/qwen-image-2.1/` с пометкой `noncommercial: true` в `studio_models.json`, чтобы
Studio не подставлял его в коммерческие сценарии.

---

### 2.4 Кандидат C′ — `Qwen/Qwen-Image-2512` (коммерчески чистая альтернатива Qwen)

Владелец в ТЗ назвал `Qwen/Qwen-Image-2512`. Это **другая** модель, чем `Qwen-Image-2.1`:

| Поле | Значение |
|---|---|
| **Статус** | `ARCH_SUPPORTED_CHECKPOINT_UNDOCUMENTED` + `NOT_RUN` |
| Репозиторий | `Qwen/Qwen-Image-2512`, revision `25468b98e3276ca6700de15c6628e51b7de54a26` |
| **Лицензия** | **Apache-2.0** — коммерчески чистая, в отличие от 2.1 |
| Gated | Нет |
| Параметры | 20B (по карточке модели) |
| Почему не `SUPPORTED` | В `docs/` коммита `74988b2` есть `qwen_image.md` (базовый Qwen-Image), `qwen_image_2.1.md` и `qwen_image_edit.md`. Чекпойнт **2512 не назван ни в одном из них**. В enum есть `VERSION_QWEN_IMAGE` — по семейству это тот же 20B MMDiT + Qwen2.5-VL + qwen_image_vae, то есть загрузиться он **должен** по пути базового Qwen-Image, но **это не проверено ни документацией upstream, ни запуском на этой машине** |

Предполагаемый состав (по образцу `docs/qwen_image.md`):

| Роль | Репозиторий | Revision | Файл | Байт | SHA-256 |
|---|---|---|---|---|---|
| diffusion | `unsloth/Qwen-Image-2512-GGUF` | `1626d7531f84b4d2ea1cd6d2e69f41ec027dd354` | `qwen-image-2512-Q8_0.gguf` | 21 761 817 120 | `e285a0692582acf09bb4086d9b120eb0e357c4386565d169a033cb968c6fa9a5` |
| diffusion (lite) | то же | то же | `qwen-image-2512-Q4_K_M.gguf` | 13 244 758 560 | `b2a5f6249eb58ee10c9e2ce8cb1114b89897db23de2fdf7dc49140800aa928fc` |
| vae | `Comfy-Org/Qwen-Image_ComfyUI` | `7beb7b647f04469fbe64ba8adc2bb0d7e5e9f73f` | `split_files/vae/qwen_image_vae.safetensors` | 253 806 246 | `a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f` |
| llm | `mradermacher/Qwen2.5-VL-7B-Instruct-GGUF` | `cfa2baa09946b211c107e6e104948987a64dd2c1` | `Qwen2.5-VL-7B-Instruct.Q8_0.gguf` | 8 098 524 160 | `577bfb3e41f93f00414e594c56a38bbe91207b3b636ab7faf343e34a20aa73ca` |

**Q8_0: 30 114 147 526 B = 28.05 GiB ≈ 27 мин 53 с. Q4_K_M: 21 597 088 966 B = 20.11 GiB ≈ 19 мин 59 с.**
Лицензия `unsloth/Qwen-Image-2512-GGUF` — Apache-2.0; `Comfy-Org/Qwen-Image_ComfyUI` — Apache-2.0;
`mradermacher/...` — поле лицензии пустое, но базовая модель `Qwen/Qwen2.5-VL-7B-Instruct` — Apache-2.0.

**Требуется проверка загрузчика на машине владельца перед включением в профиль.** Дешёвая проверка без
полного скачивания невозможна — нужен хотя бы Q4_K_M (13.2 GiB). Решение о загрузке — за владельцем.

---

### 2.5 Кандидат D (дополнительно, без загрузки) — `Wan-AI/Wan2.2-I2V-A14B`

| Поле | Значение |
|---|---|
| **Статус** | `SUPPORTED_BY_CURRENT_SDCLI` + `NOT_RUN` (загрузка не запрашивалась) |
| Архитектура | `VERSION_WAN2_2_I2V`; в DLL есть строка `Wan2.2-I2V-14B` и флаги `--high-noise-diffusion-model`, `--moe-boundary` |
| Официальный репозиторий | `Wan-AI/Wan2.2-I2V-A14B`, revision `206a9ee1b7bfaaf8f7e4d81335650533490646a3`, **Apache-2.0, не gated** |
| Размер официального репо | 126 205 359 703 B = 117.5 GiB (native, шардированный, `.pth`-энкодер — sd-cli это не ест) |
| Практический путь | GGUF от `QuantStack/Wan2.2-I2V-A14B-GGUF`, revision `6c6717459277b9cd1f72579d78a0fd62a79e57dc`, Apache-2.0, не gated |

| Роль | Файл | Байт | SHA-256 |
|---|---|---|---|
| high-noise diffusion | `HighNoise/Wan2.2-I2V-A14B-HighNoise-Q8_0.gguf` | 15 406 608 896 | `619a66032c28e1b27882dfccc0bf93e51edb1491e8d4e4c6f291726abe4de8aa` |
| diffusion (low noise) | `LowNoise/Wan2.2-I2V-A14B-LowNoise-Q8_0.gguf` | 15 406 608 896 | `029c7adc74de4f7804905c5e4fb9335d0862cd2fc37191df526aeac13b64425e` |
| vae | `VAE/Wan2.1_VAE.safetensors` | 253 815 318 | `2fc39d31359a4b0a64f55876d8ff7fa8d780956ae2cb13463b0223e15148976b` |
| text encoder | **УЖЕ ЕСТЬ НА ДИСКЕ**: `models/media/wan22-ti2v-5b/umt5-xxl-encoder-Q8_0.gguf`, 6 043 068 256 B, SHA-256 `2521d4de0bf9e1cc6549866463ceae85e4ec3239bc6063f7488810be39033bbc` | 0 новых | — |

**Новая загрузка: 31 067 033 110 B = 28.93 GiB ≈ 28 мин 45 с** (umt5 переиспользуется из baseline).
Запуск: `-M vid_gen --diffusion-model <LowNoise> --high-noise-diffusion-model <HighNoise> --vae <Wan2.1_VAE>
--t5xxl <umt5-xxl-encoder-Q8_0.gguf> --moe-boundary 0.875 -i <img>`.

Заметка: MoE-пара занимает ~28.7 GiB весов диффузии одновременно; на 128 GB общей памяти с
`--offload-to-cpu` это выполнимо, но скорость на 8060S — `NOT_RUN`.

---

### 2.6 Кандидат E (дополнительно, без загрузки) — `Qwen/Qwen-Image-Edit-2511`

| Поле | Значение |
|---|---|
| **Статус** | `SUPPORTED_BY_CURRENT_SDCLI` + `NOT_RUN` |
| Архитектура | `VERSION_QWEN_IMAGE` / `QwenImageEditPlusPipeline` (строка присутствует в DLL) |
| Документация | `docs/qwen_image_edit.md` @ `74988b2` — **раздел «Qwen Image Edit 2511» назван явно, с примером CLI** |
| Репозиторий весов | `Qwen/Qwen-Image-Edit-2511`, revision `6f3ccc0b56e431dc6a0c2b2039706d7d26f22cb9`, **Apache-2.0, не gated** |
| Квантовка | `unsloth/Qwen-Image-Edit-2511-GGUF`, revision `0d33d9692b4b26212297240d87b0d4719aa4fd06`, Apache-2.0, не gated |

| Роль | Файл | Байт | SHA-256 |
|---|---|---|---|
| diffusion | `unsloth/…-2511-GGUF` → `qwen-image-edit-2511-Q8_0.gguf` | 21 761 817 184 | `ab4f0622fb002fccaaa679a2ecce6fd1b3190d8ea28a5b7b2b17b8669bc24afa` |
| diffusion (lite) | то же → `qwen-image-edit-2511-Q4_K_M.gguf` | 13 244 758 624 | `8677bac90627adbbc11efab87b1870e701c4eb3689ee865a3de8ab81b705a723` |
| vae | `Comfy-Org/Qwen-Image_ComfyUI` @ `7beb7b6…` → `split_files/vae/qwen_image_vae.safetensors` | 253 806 246 | `a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f` |
| llm | `mradermacher/Qwen2.5-VL-7B-Instruct-GGUF` @ `cfa2baa…` → `Qwen2.5-VL-7B-Instruct.Q8_0.gguf` | 8 098 524 160 | `577bfb3e41f93f00414e594c56a38bbe91207b3b636ab7faf343e34a20aa73ca` |
| llm_vision | то же → `Qwen2.5-VL-7B-Instruct.mmproj-Q8_0.gguf` | 853 119 712 | `2185ca0adf339f96845318c7b666f5d242f86bbace5657a89cb834785d0cea57` |

**Q8_0: 30 967 267 302 B = 28.84 GiB ≈ 28 мин 40 с. Q4_K_M: 22 450 208 742 B = 20.91 GiB ≈ 20 мин 47 с.**
Upstream-пример требует `--model-args qwen_image_zero_cond_t=true` и `--flow-shift 3 --cfg-scale 2.5`.

Заметка: `Qwen-Image-Edit-2511` — Apache-2.0, в отличие от `Qwen-Image-2.1` (qwen-research).
Это отдельные лицензии, путать нельзя.

---

### 2.7 MiniMax-H3 — юрисдикционное ограничение владельца

| Поле | Значение |
|---|---|
| **Статус** | `SUPPORTED_BY_CURRENT_SDCLI` (архитектура `VERSION_MINIMAX_H3`, `docs/minimax_h3.md` @ `74988b2`, флаги `--ref-video`, `--ref-audio`, `-r`) — **но DO_NOT_DEPLOY** |
| Ограничение | **Не разворачивать в Чехии** (прямое указание владельца). Модель не включается в профили, разворачиваемые на территории Чехии, включая эту машину, если она находится в CZ |
| Действие | В каталог Studio не добавлять. Если когда-нибудь понадобится — отдельное решение владельца с указанием юрисдикции хоста |

Никакие компоненты MiniMax-H3 не исследовались на предмет загрузки и не загружались.

---

## 3. Baseline / точка отката — НЕ ТРОГАТЬ

Источник: `C:\Users\asd\Bossman\models\media\MANIFEST.json` (`schema_version: 1`,
`written_at: 2026-09-22T17:21:01Z`, `tool: tools/media_bootstrap.py`).
Статус всех строк ниже — **`BASELINE_ROLLBACK`**. Файлы уже на диске, хэши из манифеста.

### 3.1 `sdcpp:wan2.2-ti2v-5b` — BASELINE (видео)

| Роль | Путь | Байт | SHA-256 |
|---|---|---|---|
| diffusion | `wan22-ti2v-5b/Wan2.2-TI2V-5B-Q8_0.gguf` | 5 400 179 040 | `57bece983817ab2f957546683bb670f13be7d99022d45674840cd999a050ea8f` |
| vae | `wan22-ti2v-5b/wan2.2_vae.safetensors` | 1 409 400 960 | `e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156` |
| text_encoder | `wan22-ti2v-5b/umt5-xxl-encoder-Q8_0.gguf` | 6 043 068 256 | `2521d4de0bf9e1cc6549866463ceae85e4ec3239bc6063f7488810be39033bbc` |

### 3.2 `sdcpp:z-image-turbo` — BASELINE (изображения)

| Роль | Путь | Байт | SHA-256 |
|---|---|---|---|
| diffusion | `z-image-turbo/z_image_turbo-Q8_0.gguf` | 6 577 440 704 | `df1c5baa86d1398c979495a6072dbcee79444fdb884a2445582ba0769c44e9a1` |
| vae | `z-image-turbo/ae.safetensors` | 335 304 388 | `afc8e28272cd15db3919bacdb6918ce9c1ed22e96cb12c4d5ed0fba823529e38` |
| text_encoder | `z-image-turbo/Qwen3-4B-Instruct-2507-Q8_0.gguf` | 4 280 405 600 | `391c1e410fd9f4cf2de2b510273b56a84c19ce18f4fa3bfb3774031dac4ef068` |

### 3.3 Уже добавленные (не baseline, но установлены)

| Профиль | Файлы | Байт | SHA-256 |
|---|---|---|---|
| `sdcpp:flux1-schnell` | `flux1-schnell/flux1-schnell-Q8_0.gguf` | 12 687 821 728 | `f6694941193b10148dbf1f0f498d4ccd3e9875c127fc53946213b68580c66f10` |
| | `flux1-schnell/clip_l.safetensors` | 246 144 152 | `660c6f5b1abae9dc498ac2d21e1347d2abdb0cf6c0c0c8576cd796491d9a6cdd` |
| | `flux1-schnell/t5xxl_fp8_e4m3fn.safetensors` | 4 893 934 904 | `7d330da4816157540d6bb7838bf63a0f02f573fc48ca4d8de34bb0cbfd514f09` |
| | vae: переиспользует `z-image-turbo/ae.safetensors` | 335 304 388 | `afc8e28272cd15db3919bacdb6918ce9c1ed22e96cb12c4d5ed0fba823529e38` |
| `sdcpp:sdxl-base` | `sdxl-base/sd_xl_base_1.0.safetensors` | 6 938 078 334 | `31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b` |
| | `sdxl-base/sdxl_vae.safetensors` | 334 641 162 | `235745af8d86bf4a4c1b5b4f529868b37019a10f7c0b2e79ad0abca3a22bc6e1` |

**Правило отката для любого кандидата:** новый профиль ставится **в отдельный подкаталог**
`models/media/<id>/` и регистрируется **новой** записью в `tools/studio_models.json`
(текущие id: `sdcpp:wan2.2-ti2v-5b`, `sdcpp:z-image-turbo`, `sdcpp:flux1-schnell`, `sdcpp:sdxl-base`).
Файлы baseline не перезаписываются и не переиспользуются под другим именем. Откат =
удалить подкаталог + удалить запись из `studio_models.json` и `MANIFEST.json`. Провайдер
`command-center/bcc/studio/providers/sdcpp.py` при этом не меняется, если новая модель ложится в
существующую схему аргументов.

---

## 4. Закрепление runtime и сохранение рабочего бинарника для отката

### 4.1 Что закреплено сейчас

- `models/media/MANIFEST.json` → `engine.release = "master-890-74988b2"`,
  `engine.binary_sha256 = "ee6fb793…cb9cfd1"`.
- Проверено на этой машине: фактический `sd-cli.exe` даёт ровно этот SHA-256. Пиннинг честный.
- Исходный архив релиза сохранён: `media-runtime/sdcpp/sd-vulkan.zip` (31 932 748 B,
  SHA-256 `744c8f817c66ecfd02fbb9dc8b122e1f29f7240db1f6086dfde2669403c5d896`).
  Это уже готовая точка отката бинарника — распаковка архива поверх каталога возвращает рабочую сборку.

### 4.2 Что нужно добавить для безопасного отката

1. **Скопировать рабочий каталог целиком перед любым обновлением рантайма:**
   `media-runtime/sdcpp/vulkan` → `media-runtime/sdcpp/vulkan.known-good-74988b2`.
   Одного `sd-cli.exe` недостаточно: `stable-diffusion.dll` (35 119 616 B) и `ggml-vulkan.dll`
   (50 544 640 B) содержат весь код архитектур и шейдеры, версии должны совпадать.
2. **Занести в манифест хэши всех трёх ключевых файлов**, а не только `sd-cli.exe`
   (сейчас в `MANIFEST.json` есть только `binary_sha256`). Хэши приведены в разделе 1.1.
3. **Не ставить непроверенный nightly поверх рабочей сборки.** Новая сборка разворачивается в
   соседний каталог (`media-runtime/sdcpp/vulkan-<release>`), проверяется на baseline-профиле
   (Z-Image-Turbo и Wan2.2 TI2V-5B, те же seed/prompt), и только после совпадения результата
   переключается путь в конфиге. `sd-vulkan.zip` предыдущего релиза сохраняется.
4. **CUDA-пример из upstream НЕ доказывает совместимость с AMD.** В `--help` этой сборки есть
   явно CUDA-only опции: `--sage-attn` («native CUDA SageAttention»), `--split-mode row`
   («matmul rows split across devices, **CUDA only**»), `--rng cuda`. Любой upstream-пример,
   использующий эти флаги, на Radeon 8060S/Vulkan работать так же не обязан. Единственное
   доказательство — прогон на этой машине. Все кандидаты выше помечены `NOT_RUN` именно поэтому.
5. **Проверка устройств перед прогоном:** `sd-cli.exe --list-devices` (безопасно, без нагрузки на GPU)
   — убедиться, что Vulkan-устройство видно и это именно Radeon 8060S.

### 4.3 Провенанс сборки

Внутри `stable-diffusion.dll` присутствуют пути сборки вида
`D:\a\stable-diffusion.cpp\stable-diffusion.cpp\src\model\diffusion\ltxv.hpp` — формат рабочей
директории GitHub Actions (`D:\a\<repo>\<repo>`). Это подтверждает, что бинарник собран официальным
CI `leejet/stable-diffusion.cpp`, а не пересобран третьей стороной.

---

## 5. Сводная таблица

| # | Кандидат | Тип | Статус | Лицензия | Gated | Новая загрузка | Время @18 MB/s |
|---|---|---|---|---|---|---|---|
| A | `black-forest-labs/FLUX.2-klein-4B` | img | `SUPPORTED_BY_CURRENT_SDCLI` / `NOT_RUN` | Apache-2.0 (все компоненты) | нет | 8.22 GiB (Q8) / 4.85 GiB (Q4) | 08:10 / 04:49 |
| B | `Lightricks/LTX-2.5` distilled | vid+audio | `SUPPORTED_BY_CURRENT_SDCLI` / **`OWNER_REQUIRED_LICENSE`** / `NOT_RUN` | LTX 2.x Community (коммерция OK при выручке < $10M) | **ДА** | 25.16 GiB (B1) … 40.76 GiB (B3) | 25:01 … 40:31 |
| C | `Qwen/Qwen-Image-2.1` | img | `SUPPORTED_BY_CURRENT_SDCLI` / **`OWNER_REQUIRED_LICENSE`** / `NOT_RUN` | **qwen-research — НЕ коммерческая** | нет | 15.90 GiB | 15:48 |
| C′ | `Qwen/Qwen-Image-2512` | img | `ARCH_SUPPORTED_CHECKPOINT_UNDOCUMENTED` / `NOT_RUN` | Apache-2.0 | нет | 28.05 GiB (Q8) / 20.11 GiB (Q4) | 27:53 / 19:59 |
| D | `Wan-AI/Wan2.2-I2V-A14B` (GGUF QuantStack) | vid | `SUPPORTED_BY_CURRENT_SDCLI` / `NOT_RUN` | Apache-2.0 | нет | 28.93 GiB (umt5 уже есть) | 28:45 |
| E | `Qwen/Qwen-Image-Edit-2511` | img-edit | `SUPPORTED_BY_CURRENT_SDCLI` / `NOT_RUN` | Apache-2.0 | нет | 28.84 GiB (Q8) / 20.91 GiB (Q4) | 28:40 / 20:47 |
| — | MiniMax-H3 | vid+audio | `SUPPORTED_BY_CURRENT_SDCLI` / **DO_NOT_DEPLOY (CZ)** | — | — | не рассматривалась | — |
| BL | `sdcpp:wan2.2-ti2v-5b` | vid | `BASELINE_ROLLBACK` | — | — | установлена | — |
| BL | `sdcpp:z-image-turbo` | img | `BASELINE_ROLLBACK` | — | — | установлена | — |

Суммарно все кандидаты A+B1+C′+D+E ≈ 119.2 GiB при свободных 1 556.8 GiB — места хватает с запасом ~13×.

## 6. Что требуется от владельца

1. **LTX-2.5:** принять условия на https://huggingface.co/Lightricks/LTX-2.5 (кнопка согласия на
   странице модели, `gated: auto` — доступ выдаётся сразу). Для GGUF-энкодера дополнительно
   https://huggingface.co/elix3r/gemma4-12b-with-proj-ltx-2.5-GGUF. Профиль B3 обходится одним согласием.
2. **Qwen-Image-2.1:** решить, нужен ли он вообще. Для коммерческого профиля потребуется отдельная
   лицензия от Alibaba/Qwen (лицензия весов — `qwen-research`). Альтернатива без этой проблемы —
   `Qwen-Image-2512` (Apache-2.0) или `Qwen-Image-Edit-2511` (Apache-2.0).
3. **AI-дисклеймер:** если LTX-2.5 попадёт в продукт, лицензия требует явной пометки контента как
   сгенерированного ИИ. Это задача для UI/выдачи Bossman, а не только для загрузки весов.
4. **Запуск загрузок:** ни одна модель не скачивалась. Команды на загрузку запускает владелец.

---

*Документ составлен без запуска генерации (GPU был занят чужим прогоном) и без принятия каких-либо
пользовательских соглашений. Все SHA-256 взяты из Hugging Face LFS oid через
`POST /api/models/{repo}/paths-info/{revision}`, кроме файлов gated-репозиториев, где HF маскирует oid —
такие места помечены `UNAVAILABLE_GATED`, а не заполнены выдуманными или самоподтверждающими хэшами.*
