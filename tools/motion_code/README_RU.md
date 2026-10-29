# Моушн-ролик кодом — как Bossman делает 20-секундный рэп-ролик

Оба способа проверены 29.10.2026 на этом ПК, облако $0. Ролики: пульт, сообщения 3838 (v1) и 3839 (v2).

## Шаг 1. Музыка — свой ACE-Step
`python canvas/make_rap.py` → `POST /api/music/generate` на :8801 (ACE-Step 1.5, сам стартует под арендой памяти),
`duration 22`, `bpm 96`, `thinking false`, затем `/tasks/{id}/save` с `master: "hype", duration: 20`.
Проверка: громкость ≈ −9.5 LUFS, BPM измерен (95.9). Затем **Whisper word timestamps** (faster-whisper, локально):
склейки сцен ставятся на реальные слова вокала, а не на догадку. Модель может не спеть часть строк — пиши это честно.

## Шаг 2a. Canvas-движок (v1): `canvas/`
`motion.html` — детерминированный `render(t)`, сцены из `scenes.json` (`kind`, `beats`, тексты). Рендер:
`python canvas/render.py scenes.json out.mp4 rap.mp3 --fps 60` (Playwright, кадр за кадром → ffmpeg). Превью кадров:
`--preview 1.6,6.4,13.9`. Медленно (≈6 мин на 1200 кадров), зато без зависимостей.

## Шаг 2b. Remotion (v2, лучше): `remotion/`
`npm i` → положить трек в `public/rap.mp3` → кадры `npx remotion render BossmanRap out/f --frames=60,250,560 --image-format=jpeg`
→ посмотреть → `npx remotion render BossmanRap out/v.mp4 --codec=h264 --crf=16 --concurrency=8` (≈30 с).
Сцена = `<Sequence>` между `CUTS` (в долях бита), шрифты `@remotion/google-fonts`.

## Правила вкуса (из плагинов, см. PLUGINS.json)
- Emil Kowalski: кривые `bezier(0.23,1,0.32,1)` вход, `bezier(0.77,0,0.175,1)` движение; ничего не появляется из scale(0)
  (маска/сдвиг, scale ≥ 0.95); пружины с малым bounce.
- Taste-skill / Impeccable / Frontend-design: идея из предмета (у Bossman — «квитанция за каждую функцию»),
  сплошные поля цвета, крупный шрифт, без свечений, тонких линеек и моно-подписей «для вида».
- Только настоящие факты в кадре (версия, sha256-квитанции, число тестов). Выдуманные цифры запрещены.
- QA: минимум 8 кадров по всему ролику + громкость + длительность ffprobe, только потом в пульт.

## Безопасность плагинов
HyperFrames и Impeccable по умолчанию шлют телеметрию — у пользователя стоят `DO_NOT_TRACK=1`,
`HYPERFRAMES_NO_TELEMETRY=1`, `IMPECCABLE_NO_TELEMETRY=1`. Хуки Impeccable (качают бинарник) не ставились.
HyperFrames требует Node ≥ 22 — на ПК Node 20, локальный рендер HyperFrames пока НЕ проверен.
