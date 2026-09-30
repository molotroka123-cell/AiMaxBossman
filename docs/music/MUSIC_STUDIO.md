# Music Studio — local music generation

Bossman Music Studio uses a local ACE-Step 1.5 REST service. It does not silently fall back to a cloud provider.

## Why ACE-Step 1.5

ACE-Step 1.5 exposes an asynchronous REST API, supports full-song generation, metadata controls such as duration/BPM/key/time signature, reference/source audio, and local AMD ROCm operation. The Bossman adapter uses the official `POST /release_task -> POST /query_result -> GET /v1/audio` flow.

Environment:
- `BOSSMAN_ACESTEP_URL` — default `http://127.0.0.1:8001`.
- `BOSSMAN_ACESTEP_API_KEY` — optional local API key (passed to the service through its environment, never on the command line).
- `BOSSMAN_ACESTEP_DIR` — install directory (see below). `BOSSMAN_MEDIA_RUNTIME` — parent that contains `acestep`.
- `BOSSMAN_ACESTEP_PYTHON` — override the interpreter that starts the service (default: `venv_rocm` inside the install).
- `ACESTEP_LM_MODEL_PATH` — language model, default set by Bossman: `acestep-5Hz-lm-1.7B` (see "Known issues").

The adapter is deliberately loopback-only. A changed URL cannot turn Music Studio into arbitrary network egress; the Start button refuses a non-loopback URL as well.

## Установка и запуск на ПК владельца

Состояние на 2026-09-30 (Ryzen AI MAX+ 395 / Radeon 8060S gfx1151 / Windows 11, Smart App Control ВКЛ): ACE-Step 1.5 **установлен** в
`C:\Users\asd\Bossman\media-runtime\acestep` и **запускается кнопкой на странице** (REAL_LOCAL, см. «Проверка» ниже).

Раскладка каталога установки (ее ищет `bcc/features/music_studio.py`; порядок поиска: `BOSSMAN_ACESTEP_DIR`, `BOSSMAN_MEDIA_RUNTIME\acestep`,
`<папка рядом с checkout>\media-runtime\acestep`, `%USERPROFILE%\Bossman\media-runtime\acestep`):

| Путь | Что это |
|---|---|
| `repo\` | исходники ACE-Step-1.5 (commit `ca1e85fe`), `repo\checkpoints\` — модели (~9,4 ГБ основная загрузка) |
| `venv_rocm\` | Python 3.12 + AMD ROCm PyTorch `2.9.1+rocm7.2.1` (wheels с `repo.radeon.com`, ~2 ГБ) |
| `bossman_acestep_launcher.py` | копия `tools/music_studio_launcher.py`: обходит отсутствие `torch.distributed` в ROCm-сборке для Windows |

### Установка (один раз)

```
python tools\music_studio_install.py check                 # что уже есть
python tools\music_studio_install.py install --dry-run     # только план, ничего не меняет
python tools\music_studio_install.py install               # клон, venv_rocm, torch ROCm, зависимости, модели (~11,5 ГБ)
```

Нужен Python 3.12 (для Windows AMD публикует wheels ROCm только под 3.12) и git. Скрипт выполняет ровно те шаги, которые сделаны вручную
на этом ПК; его режимы `check` и `--dry-run` проверены, полный `install` одной командой с нуля **не прогонялся** (шаги выполнялись по отдельности).
Smart App Control не выключается и не нужен к выключению.

### Запуск и остановка

Страница «Music Studio» → панель «Сервис генерации» → **«Запустить ACE-Step»**. Bossman запускает сервис как дочерний процесс
(`127.0.0.1:8001`, журнал `<data>\music\service\acestep.log`, один экземпляр, без окна консоли), через ~40–60 с статус становится «Готов»
(первый запуск на новом ПК дольше: модели читаются с диска). **«Остановить ACE-Step»** гасит весь дерево процессов, которое запустил Bossman;
чужой процесс на порту 8001 Bossman не трогает. После перезапуска Bossman запущенный им сервис распознаётся по pid + времени создания + командной строке.

Вручную (без Bossman), из `media-runtime\acestep`:
`set ACESTEP_NO_INIT=false & set ACESTEP_LM_BACKEND=pt & set TORCH_COMPILE_BACKEND=eager & set MIOPEN_FIND_MODE=FAST & venv_rocm\Scripts\python.exe -u bossman_acestep_launcher.py --host 127.0.0.1 --port 8001`
(не ставьте `HSA_OVERRIDE_GFX_VERSION=11.0.0` из upstream `.bat`: он для дискретных RDNA3, на gfx1151 ROCm работает нативно).

## Статусы `GET /api/music/health`

| `status` | Значение | `action` / `can_start` | Что делает страница |
|---|---|---|---|
| `NOT_INSTALLED` | нет каталога установки / нет `repo\acestep\api_server.py` / нет `venv_rocm`; порт закрыт | `install` / нет | причина по-русски, команда установки, ссылка на этот документ |
| `NOT_RUNNING` | установлено, порт закрыт (или сервис завершился — тогда `last_exit_code` и `log_tail`) | `start` / да | кнопка «Запустить ACE-Step» |
| `STARTING` | наш дочерний процесс жив, порт ещё не открыт | `wait` / нет | страница сама перепроверяет каждые 3 с, журнал |
| `NOT_READY` | порт открыт, но модели не загружены (`models_initialized=false`), либо отвечает не ACE-Step, либо `/health` не ответил | `wait` или нет | причина, журнал |
| `READY` | `GET /health` вернул конверт ACE-Step `code=200`, `status=ok`, `models_initialized=true` | нет | кнопка «Сгенерировать трек» доступна |
| `NOT_CONFIGURED` | `BOSSMAN_ACESTEP_URL` не loopback | нет | причина |

Во всех ответах: `reason` и `remedy` по-русски, `installed`, `install_dir`, `owned`/`running`/`pid`, `log_path`. Сырой `ConnectError` пользователю
не показывается (тип ошибки лежит в `diagnostics.error_type` для диагностики). Ошибки `POST /api/music/generate|tasks` теперь тоже
`503 {message, code: MUSIC_NOT_INSTALLED|MUSIC_NOT_RUNNING|MUSIC_STARTING|MUSIC_NOT_READY|MUSIC_AUTH|..., hint}`.
Прочее: `POST /api/music/service/start|stop`, `GET /api/music/service` (установка, процесс, хвост журнала).

## Диагностика

1. `python tools\music_studio_install.py check` — есть ли исходники, venv, лаунчер и 4 каталога моделей.
2. `GET /api/music/service` или блок «Журнал сервиса» на странице — хвост лога без нативных стек-фреймов, секреты замаскированы.
3. Сервис падает сразу (`exit_code`): смотрите журнал; типичные причины — не 3.12-окружение, не тот torch (`python -c "import torch;print(torch.cuda.is_available())"`).
4. `ImportError: cannot import name 'group' from 'torch.distributed'` — запуск мимо `bossman_acestep_launcher.py` (upstream issue #644).
5. Порт 8001 занят чужим процессом — Bossman откажет в запуске (`MUSIC_PORT_BUSY`); освободите порт или задайте `BOSSMAN_ACESTEP_URL`.

## Known issues (проверено 2026-09-30)

- **LM 4B не грузится на этом ПК.** Upstream на большом GPU сам выбирает `acestep-5Hz-lm-4B` (+8,4 ГБ загрузки); загрузка падала нативно
  (`0xC0000005` на шарде 2/2, файлы целы — размеры совпали с Hugging Face). Причина не выяснена. Bossman по умолчанию ставит `ACESTEP_LM_MODEL_PATH=acestep-5Hz-lm-1.7B`
  (входит в основную загрузку, работает). Каталог `repo\checkpoints\acestep-5Hz-lm-4B` (8,4 ГБ) можно удалить.
- **PyWavelets (`pywt`) заблокирован Smart App Control** («Политика управления приложениями заблокировала этот файл»). Нужен только для
  необязательной DCW-коррекции сэмплера; генерация без неё работает. Обхода нет и не делается.
- ROCm-сборка PyTorch для Windows без `torch.distributed`; обход — заглушка в лаунчере (в site-packages ничего не патчится).
- На ROCm сервис использует `float32` (`ACESTEP_ROCM_DTYPE=bfloat16` может ускорить; не измерялось).
- Скорость: трек 20 с с `thinking=true` — ~75 с, одновременно с живой моделью Ollama на том же GPU.

## Owner presets

Initial presets:
- Phonk
- Drift Phonk
- Ultra Funk
- Nightcore
- Electro

These are generic musical directions, not requests to clone a named artist.

The owner's uploaded reference tracks may be analyzed locally for broad characteristics such as tempo, energy, arrangement and instrumentation. They are reference material, not permission to reproduce copyrighted recordings or impersonate a specific artist.

## Owner reference profile — initial seed

The first local preference seed is intentionally broad:
- dark/aggressive phonk and ultrafunk;
- strong distorted bass/808;
- cowbell-driven hooks;
- club/electronic energy;
- faster nightcore-like variants;
- preference for immediately recognizable hooks and high energy.

This profile is editable and must evolve from owner ratings of generated tracks rather than assuming every reference is equally preferred.

## Hardware acceptance

On the owner's Ryzen AI Max+ 395 / Radeon 8060S / 128 GB machine:
1. install/start ACE-Step 1.5 using its supported AMD/ROCm path;
2. GET `/api/music/health` must say READY;
3. generate one 60–90 second instrumental phonk track;
4. poll to completed;
5. save through Bossman;
6. verify with ffprobe/full decode;
7. generate at least three variants with fixed prompt and different seeds/settings;
8. owner rates each 1–5;
9. store only generalized preference features, not copyrighted audio, in the owner music profile.

CI may prove the adapter contract but must not claim REAL_LOCAL_MODEL until this hardware run succeeds.

### Status of that run (2026-09-30)

| Шаг | Статус |
|---|---|
| 1–2 install/start, `/health` READY | REAL_LOCAL: запуск кнопкой/эндпойнтом Bossman, READY за ~43 с (модели с диска) |
| 3–6 трек через Bossman, poll, save, ffprobe + полное декодирование | REAL_LOCAL для трека **20 с** и **30 с** (mp3, 48 кГц, stereo, не тишина). Трек 60–90 с **не генерировался** |
| 7 три варианта с разными seed | NOT_RUN |
| 8–9 оценка владельца, профиль предпочтений | NOT_RUN (нужен владелец) |

Доказательства: `C:\Users\asd\Bossman\swarm-20260930\music\` (журнал сервиса, файлы треков, отчёт в `..\reports\music-studio.md`).
