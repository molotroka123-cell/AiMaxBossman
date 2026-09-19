# Настоящий медиа-оборот в CI (No fake render)

Заготовка -> правка ЧЕРЕЗ BOSSMAN -> рендер продуктовым путём -> настоящий
выходной файл -> `ffprobe`. Никакого фальшивого рендера, заглушек и
синтетического запасного пути, дающего PASS.

* инструмент: `tools/media_roundtrip.py`
* тесты: `command-center/tests/test_media_roundtrip_studio.py`
* шаг CI: `.github/workflows/fable-media-fleet.yml` — «Настоящий медиа-оборот
  обеих студий»; сам набор тестов гоняется ещё и в `command-center-ci.yml`,
  где ffmpeg уже ставится шагом «FFmpeg для Video Studio».

---

## Сначала измерение: что было ДО

Честный ответ на вопрос «рендерит ли продукт настоящий файл в тестах или всё
останавливается на вызове команды»:

**Video Studio — ДА, рендерил настоящий файл и до этой работы.** Это измерено,
а не предположено:

```
$ cd command-center && python -m pytest tests/test_video_studio_render.py::test_real_render_preview_export_and_probe \
      tests/test_video_studio_cfr_frames.py -q -p no:randomly --timeout=300
23 passed in 17.37s
```

Прямая проверка продуктового пути (`apply_command` -> `render_project`) на
двухсекундной заготовке:

```
EXISTS: True BYTES: 43547
VERIFICATION: {"duration_ticks": 2000000, "width": 160, "height": 90,
               "has_video": true, "has_audio": true, "bytes": 43547,
               "decoded": true, "decoded_video_frames": 50, "passed": true}
```

`bcc/video_studio/render.py` действительно запускает ffmpeg, а `verify_output()`
действительно делает полное декодирование и считает кадры через
`nb_read_frames`. Существующий Video Studio НЕ заменён и НЕ обойдён — новый
оборот подключается к тому же пути.

**Image Studio — НЕТ.** Его тесты (`command-center/tests/test_feat_images.py`)
останавливаются на «байты сохранились и отдались»: тип выходного файла не
измеряется вообще. Измерено прямо:

```
IMPORT STATUS: 200
MIME STORED: image/png BYTES: 109
SERVED content-type: image/png len: 109
SERVED head: b'<!doctype html><html><head><title>502 Ba'
```

То есть страница ошибки 502, названная `screenshot.png`, сегодня принимается
эндпоинтом `/api/images/assets/import`, хранится как `image/png` и отдаётся с
заголовком `image/png`. Это дефект продукта в `bcc/features/images.py` — не в
зоне этой работы, правка за владельцем. Здесь он зафиксирован измерением и
закрыт со стороны приёмки: оракул такие байты отвергает.

---

## Оракул: тип ИЗМЕРЯЕТСЯ, а не читается из имени

`ffprobe` подбирает демуксер в том числе по расширению файла, поэтому HTML,
названный `scene.mp4`, он честно пробует открыть как MOV. Оракул убирает эту
подсказку: файл отдаётся ffprobe через симлинк **без расширения**, формат
определяется только по содержимому, и лишь потом сравнивается с обещанием
имени (`NAME_CONTRACT`).

Проверяется по каждому выходному файлу:

| что | чем |
| --- | --- |
| файл существует и ненулевой | `stat` |
| измеренный тип совпадает с именем | `ffprobe` через симлинк без расширения |
| длительность | `format=duration` с явным допуском |
| дорожки (video/audio) | `stream=codec_type` |
| кодеки и геометрия | `stream=codec_name,width,height` |
| число декодированных кадров | `-count_frames` -> `nb_read_frames` |
| файл читается целиком | `ffmpeg -xerror -f null -` |
| содержимое не подменено | sha256 |

Почему полное декодирование обязательно: обрезанный файл по-прежнему
рапортует ffprobe корректный формат и длительность.

```
truncated.mp4 (4096 байт):  ffprobe rc=0  duration=1.000000  video+audio
                            decode  rc=183
```

---

## Заготовки детерминированные

Бинарей в репозитории нет. Обе заготовки генерирует ffmpeg с фиксированными
параметрами и битэкзактными флагами (`-fflags +bitexact -flags +bitexact`,
`-threads 1`); детерминизм проверяется повторной генерацией и сравнением
sha256 прямо в тесте.

* видео: `testsrc=160x90:rate=25:duration=2` + `sine=440Hz`, h264/aac, mp4 —
  27 704 байта, `sha256 9d36db64cf5265ff…`
* картинка: один кадр `testsrc=320x240` в PNG — 2 687 байт,
  `sha256 3a4d41c65681168f…`

---

## Оборот Video Studio

Правка идёт **только** через настоящий командный слой
`bcc.video_studio.commands.apply_command`:

```
media.import -> clip.add -> clip.split(frame=25) -> clip.ripple_delete -> effect.apply(eq.brightness)
```

Рендер — через настоящий `bcc.video_studio.render.render_project`. Измеренный
результат (`ffprobe`, независимо от самоотчёта продукта):

```
bytes                22837
measured_format      mov,mp4,m4a,3gp,3g2,mj2
measured_duration_s  1.0            (исходник был 2.0 — правка дошла до выхода)
streams              h264 160x90, nb_read_frames=25
                     aac  48000 Hz, 2 ch, nb_read_frames=47
tracks               {"video": 1, "audio": 1}
full_decode          true
```

Правка проверяется не только по длительности. Тот же проект рендерится дважды
— с эффектом и без — и сравнивается средний цвет кадра:

```
edited   [174.945, 178.934, 172.887]
baseline [120.625, 126.367, 126.664]
```

То есть `effect.apply` дошёл до реальных пикселей, а не остался в метаданных
проекта. Отдельно проверяется, что рендер не изменил собственный исходник.

---

## Оборот Image Studio

Два контура, оба на настоящем PNG:

1. **Хранилище продукта** — `bcc.v2.images_runtime.ImageStorage`: сохранение
   заготовки, преобразование (crop+scale 320x240 -> 160x120), сохранение
   обратно, извлечение, измерение. Плюс проверка границы медиакорня:
   путь наружу обязан давать `PermissionError`.
2. **Настоящий HTTP-контур** — `POST /api/images/assets/import` ->
   `PATCH /api/images/assets/{id}` -> `GET /api/images/assets/{id}/file`;
   отданные байты пишутся на диск и измеряются `ffprobe`.

Измерено: импорт `png_pipe 320x240`, 2 687 байт, sha256 совпадает с заготовкой;
экспорт `png_pipe 160x120`, 576 байт, полное декодирование проходит.

### Что осталось за провайдером — честно

* Генерация картинок в продукте держится на `MockImageProvider`
  (детерминированный SVG). Реальный провайдер не подключён: задание с любым
  другим `model_alias` честно падает с «реальный image provider ещё не
  подключён». В улике это помечено как `provider_gap.generation_is_real: false`
  — это адаптер CI, а не генерация.
* SVG `ffprobe` не измеряет, поэтому мок-выход НЕ сертифицируется. Оборот
  доказан на настоящем растровом PNG, а неизмеримый артефакт оракул отвергает
  отдельным правилом, а не пропускает.
* Эндпоинт импорта доверяет расширению имени, а не измеренному типу
  (см. измерение выше). Правка — в `bcc/features/images.py`, за владельцем.

---

## Отрицательный контроль

Без него измерение выше не значит ничего. Каждый случай обязан давать ОТКАЗ:

| случай | причина отказа |
| --- | --- |
| страница ошибки, названная `scene.mp4` | измеренный тип не совпал с именем: содержимое не опознаётся как медиаконтейнер, а `.mp4` обещает `mov,mp4,m4a,3gp,3g2,mj2` |
| нулевой `scene.mp4` | нулевой размер |
| обрезанный экспорт | метаданные проходят, отказ даёт полное декодирование |
| экспорт без звуковой дорожки | `audio track count 0 != expected 1` |
| неверная заявленная длительность | `duration 1.0s != expected 5.0s` |
| неверное заявленное число кадров | `decoded frame count … != expected` |
| контейнер Matroska с именем `.mp4` | `measured type 'matroska,webm' does not match the name` |
| поток h264 с именем `render.png` | `measured type 'h264' does not match the name` |
| `.svg` (неизмеримый тип) | `no measured-type contract for '.svg'` — не PASS, а честный отказ |
| файла нет вообще | `file does not exist` |

Парный законный случай (`test_a_legitimate_export_passes`) обязателен: без него
«всё красное» тоже выглядело бы как рабочая проверка.

---

## Мутационный прогон: проверка действительно краснеет

Проверка, которая никогда не краснеет, ничего не проверяет. Замерено:
ломаем проверку и прогоняем набор.

```
BASELINE                                        rc=0  21 passed in 12.26s
M1 тип берётся с подсказкой имени               rc=1   1 failed, 20 passed
M2 несовпадение типа и имени не отказ           rc=1   3 failed, 18 passed
M3 полное декодирование не обязательно          rc=1   2 failed, 19 passed
M4 длительность не сверяется                    rc=1   2 failed, 19 passed
ВОССТАНОВЛЕНО                                   rc=0  21 passed in 12.14s
```

И то же самое по ПРОДУКТУ — доказательство, что тест идёт через настоящий путь
BOSSMAN, а не мимо него:

```
P1 clip.ripple_delete ничего не удаляет (commands.py)   rc=1  3 failed, 18 passed
P2 эффекты клипа не применяются (render.py)             rc=1  3 failed, 18 passed
ПРОДУКТ ВОССТАНОВЛЕН                                    rc=0  21 passed in 11.77s
```

---

## Почему тесты лежат в `command-center/tests/`

Корневой CI ставит только `pytest pytest-timeout psutil httpx pyyaml`. Оборот
Image Studio идёт через настоящее приложение Command Center (фикстура `env` ->
sqlalchemy/aiosqlite), значит тесту место здесь — прямо или транзитивно
тянуть sqlalchemy в корневой набор нельзя. Это ровно дефект BL-085.

`skip`/`xfail` в наборе нет намеренно: ffmpeg в этом job'е ставится шагом
«FFmpeg для Video Studio», поэтому отсутствие бинаря обязано быть красным, а
не зелёным пропуском. Сам инструмент падает с именем недостающего бинаря — та
же политика, что у `tools/media_ci_preflight.py`.

## Запуск

```
python tools/media_roundtrip.py                          # оба оборота + отрицательный контроль
python tools/media_roundtrip.py --output evidence.json   # улика на диск
cd command-center && python -m pytest tests/test_media_roundtrip_studio.py -q
```
