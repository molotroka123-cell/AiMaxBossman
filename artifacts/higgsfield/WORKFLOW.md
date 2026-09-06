# Higgsfield Video Factory — воркфлоу для локальной модели

Дата: 2026-09-06. Автор: Bossman agent (opencode). Статус: рабочий прототип, проверен на 2+ сегментах.

## Что это

Автономная генерация связного минутного видео (6 сегментов x 10s, 9:16, 480p)
через Higgsfield Seedance 2.5 в режиме **Unlimited** (бесплатно, лимит сайта —
10s на генерацию, поэтому цепочка Extend-сегментов).

## Архитектура

```
Edge (копия профиля, залогинен)  --CDP:9222-->  tools/higgsfield_watch.py
     |                                            |
     | higgsfield.ai (UI, Google-логин)           |-- artifacts/higgsfield/log.txt     (журнал)
     |                                            |-- artifacts/higgsfield/state.json  (сегменты, watermark)
     |                                            |-- artifacts/higgsfield/videos/segNN.mp4
```

- **Браузер**: Edge запущен с `--remote-debugging-port=9222 --user-data-dir=C:\Users\timur\BossmanEdgeCDP`.
  Профиль — копия дефолтного профиля Edge (логин Higgsfield через Google:
  timurdarwaish228@gmail.com сделан руками 14:00 2026-09-06; пароль агенту НЕ передавался).
  Если 9222 не слушается — watcher сам перезапустит Edge (ensure_edge()).
- **Управление**: Playwright `connect_over_cdp("http://127.0.0.1:9222")` — работает
  в живом окне, которое видит человек. Не запускать headless!

## Команды

```powershell
# главный оркестратор (всё делает сам, до 6 сегментов):
python tools/higgsfield_watch.py --segments 6

# утилиты (устаревшие частично, см. уроки):
python tools/higgsfield_unlim.py status     # логин/unlimited/история
python tools/higgsfield_unlim.py poll --wait
```

Остановка: `Stop-Process -Name python` (только процесс с `higgsfield_watch` в
командной строке). Продолжение: просто запустить снова — state.json помнит,
сколько сегментов готово; недокачанный сегмент доскачается, недогенерированный
догенерируется.

## Файлы состояния

| Файл | Назначение |
|---|---|
| `artifacts/higgsfield/state.json` | segments[] (idx,file,url,kb,dur), next_idx |
| `artifacts/higgsfield/log.txt` | append-лог, читается человеком и моделью |
| `artifacts/higgsfield/videos/segNN.mp4` | скачанные сегменты (10s каждый) |
| `artifacts/higgsfield/gen_fail_*.png` | скрины неудачных кликов Generate |

## Алгоритм watcher v2 (текущий)

1. `ensure_edge()` — поднять Edge+CDP если надо.
2. Цикл пока `len(segments) < target`:
   - `wait_for_new_segment`: поллинг истории каждые 60с, новое видео =
     cloudfront URL с `hf_YYYYMMDD_HHMMSS` **> watermark** (максимальный ts
     среди сегментов). Таймаут 30 мин.
   - если нового нет — `extend_generate(scene_idx)`:
     Unlimited ON -> вкладка Extend Video -> убедиться что источник выбран
     (иначе пикер: "Add video to extend" -> вкладка **Video Generations** ->
     первая плитка) -> промт сцены в contenteditable (проверка чтением,
     fallback `document.execCommand('insertText')`) -> click `button[type=submit]`
     -> подтверждение **по сети**: POST `fnf-api-gw.higgsfield.ai/fnf/jobs/v2/seedance_2_5`
     = job создан (20с окно, 1 ретрай).
   - скачать segNN.mp4, ffprobe >= 5s, обновить state.
3. Сборка финального ролика (вручную/скриптом): `ffmpeg concat` сегментов.

## Промты-сцены

`SCENE_PROMPTS` в higgsfield_watch.py — продолжение истории про кофе на
терассе (@Image 3 пьёт кофе) и диверсанта за деревом (@Image 2). Референсы
@Image 2 / @Image 3 прикреплены в форме Higgsfield (Elements) и сохраняются
между генерациями. В Extend-режиме редактор может переименовать чипы
(@Image 3 -> @Audio 1) — это отображение, привязка к объектам сохраняется.

## Уроки (грабли, на которые наступили — НЕ повторять)

1. **Не скачивай «новое» = «не скачанное раньше»**: в истории лежат старые
   видео юзера — фильтр только по watermark `hf_YYYYMMDD_HHMMSS > max(state)`.
   (Баг 14:26: в state попали 5 старых чужих видео, цепь «дошла» до цели мусором.)
2. **«Processing» в DOM ненадёжен**: в Extend-режиме индикатор может не
   содержать слово Processing. Подтверждай генерацию сетевым ответом
   `fnf/jobs/v2/seedance_2_5` (HTTP 200 + json с job_sets -> status queued).
3. **BOM в state.json**: если писал state из PowerShell (`Set-Content -Encoding UTF8`),
   читай в Python только `encoding="utf-8-sig"`.
4. **Пикер extend открывается на вкладке Uploads** — надо переключить на
   «Video Generations», иначе плиток генераций нет.
5. **Клик по `<video>` перекрыт оверлеем play-кнопки** — кликать
   `ancestor::div[3]` с `force=True`.
6. **Кнопка Extend Video** — `button[role=radio]`; если уже `aria-checked=true`,
   клик не нужен (и может зависнуть).
7. **Поле промта** — `div[contenteditable=true]` (НЕ textarea). `keyboard.type`
   может не менять состояние React-редактора — после ввода читать текст обратно;
   fallback: `document.execCommand('selectAll')` + `insertText`.
8. **Кнопка Generate** — `button[type=submit]`, бейдж «Unlimited» перекрывает
   текст => `force=True`.
9. **Скачивание**: у cloudfront URL открытый доступ (подпись не нужна), хватает
   `urllib` с User-Agent. Видео появляются в DOM не сразу после job — ждать.
10. **Куки-баннер** («Принять все») мешает кликам — снимать первым шагом.
11. **Сессии не живут в копии профиля**: session-cookies умирают при force-kill
    Edge. Логин Google руками, потом профиль-копия хранит сессию пока Edge
    закрывается корректно. Проверка логина: нет «Login» в шапке + есть «Create Video».

## Контроль качества сегмента

- ffprobe длительность 9-11s (10.04s норма), размер 2-20MB.
- Кадр на 3s (`ffmpeg -ss 3 -i seg.mp4 -frames:v 1 frame.jpg`) — глазами/моделью
  проверить, что референсы применились (в seg01: камуфляжный человек выглядывает
  из-за дерева — ок).

## Что дальше (backlog локальной модели)

- [ ] Догнать цепочку до 6 сегментов (watcher уже запущен).
- [ ] ffmpeg concat -> final_coffee_60s.mp4 + превью-кадры.
- [ ] Вынести SCENE_PROMPTS в state.json (редактируемые промты).
- [ ] Автотест логина: если `logged_in()==False` — стоп и запрос человека.
- [ ] Ежедневный крон/расписание: каждые 5-10 мин проверка недоделанных генераций.
- [ ] OpenRouter бюджет ($5 тестовый ключ) НЕ используется этим воркфлоу —
      генерации бесплатные через Unlimited mode.
