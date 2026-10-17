# YouTube для Shorts Factory: что сделать владельцу

Нужны две разные вещи:

* **API key** — только для чтения публичных трендов (`trends.fetch_trends`). Бесплатно.
* **OAuth client (Desktop app)** — для загрузки видео и статистики своего канала (`youtube.connect / upload / channel_stats`).

Ключи и JSON никогда не кладём в git и не пересылаем. Факты ниже проверены по официальным страницам 17.10.2026.

## 1. Проект Google Cloud

1. Создать проект: https://console.cloud.google.com/projectcreate
2. Включить **YouTube Data API v3**: https://console.cloud.google.com/apis/library/youtube.googleapis.com → «Enable».

## 2. API key для трендов

1. https://console.cloud.google.com/apis/credentials → «Create credentials» → «API key».
2. «Edit API key» → «API restrictions» → «Restrict key» → только «YouTube Data API v3».
3. Записать в `%LOCALAPPDATA%\Bossman\keys\provider-keys.env` строку `YOUTUBE_API_KEY=...`.

Стоимость: `videos.list` — 1 единица квоты за вызов (до 50 видео), лимит по умолчанию 10 000 единиц в сутки на «прочие» методы, сброс в полночь по тихоокеанскому времени.
Источники: https://developers.google.com/youtube/v3/docs/videos/list , https://developers.google.com/youtube/v3/determine_quota_cost

**Ограничение трендов:** с 21.07.2025 чарт `mostPopular` показывает Trending Music, Movies и Gaming (страница «В тренде» удалена), а не ленту Shorts. Это сигнал «что популярно в регионе», а не список Shorts.
Источник: https://developers.google.com/youtube/v3/revision_history (запись July 10, 2025)

## 3. OAuth client для загрузки

1. Экран согласия (Google Auth Platform): https://console.cloud.google.com/auth/overview → «Get started», тип **External**, название приложения, ваш e-mail.
2. «Audience» → **Test users** → добавить свой Google-аккаунт, на котором канал: https://console.cloud.google.com/auth/audience
   (в режиме Testing — до 100 тест-пользователей: https://support.google.com/cloud/answer/15549945 )
3. «Clients» → «Create client» → тип **Desktop app** → скачать JSON: https://console.cloud.google.com/auth/clients
4. Положить JSON вне репозитория, например `%LOCALAPPDATA%\Bossman\keys\youtube_client_secret.json`.
5. Один раз запустить `connect(config_dir, client_secrets_path)`: откроется браузер, выбрать аккаунт канала и нажать «Разрешить». Запрашиваются только `youtube.upload` и `youtube.readonly`.
6. Refresh token сохраняется в `config_dir\youtube_token.json`, зашифрованный Windows DPAPI (расшифровать может только ваш пользователь Windows на этом ПК). Токены не печатаются.

**Важно про режим Testing:** если экран согласия в статусе «Testing», Google выдаёт refresh token, который **истекает через 7 дней** — тогда `status()` покажет ошибку авторизации и нужно снова сделать `connect`.
Источник: https://developers.google.com/identity/protocols/oauth2 (раздел про срок действия refresh token).
Перевод экрана в «In production» для чувствительных scope (`youtube.upload`) может потребовать проверку приложения Google — решаем отдельно, если 7 дней мешают.

## 4. Квота загрузок

* `videos.insert`: «Quota impact: 100 calls per day. A call to this method has a quota cost of 1 unit in the Video Uploads quota bucket» — то есть **до 100 загрузок в сутки** по умолчанию, отдельная «корзина» квоты.
  Источник: https://developers.google.com/youtube/v3/docs/videos/insert
* История: до 04.12.2025 загрузка стоила ~1600 единиц, затем ~100; с 01.06.2026 у `videos.insert` и `search.list` свои корзины.
  Источник: https://developers.google.com/youtube/v3/revision_history
* Больше квоты — только через аудит: https://support.google.com/youtube/contact/yt_api_form

## 5. Приватность загрузок из непроверенного проекта (проверить на первой загрузке)

Официальные страницы сейчас **противоречат друг другу**:

* Правило от 28.07.2020: видео, загруженные через `videos.insert` из **непроверенных** API-проектов, созданных после 28.07.2020, **блокируются в режиме private**, пока проект не пройдёт аудит соответствия Terms of Service.
  Источник: https://developers.google.com/youtube/v3/revision_history (запись July 28, 2020)
* Текущая страница метода: «Videos uploaded from unverified API projects are not restricted to private viewing mode.»
  Источник: https://developers.google.com/youtube/v3/docs/videos/insert (описание `status.privacyStatus`)

Поэтому: по умолчанию грузим `private`; `upload()` возвращает `stored_status.privacyStatus`, который реально сохранил YouTube, и предупреждение, если он не совпал с запрошенным. Если видео заблокировано в private — подать аудит: https://support.google.com/youtube/contact/yt_api_form (политики: https://developers.google.com/youtube/terms/developer-policies ).

## 6. Поля статуса при загрузке

Источник: https://developers.google.com/youtube/v3/docs/videos

* `status.privacyStatus` — `private` / `unlisted` / `public` (по умолчанию в приложении `private`; политика YouTube требует, чтобы пользователь сам выбирал этот параметр).
* `status.publishAt` — отложенная публикация; «can be set only if the privacy status of the video is private».
* `status.selfDeclaredMadeForKids` — всегда `false` (контент не для детей).
* `status.containsSyntheticMedia` — раскрытие реалистичного изменённого/синтетического (A/S) контента; ставить `true`, если в ролике ИИ-лица, голос или сгенерированные реалистичные сцены (поле доступно с 30.10.2024).

## 7. Что считается Shorts

С 15.10.2024 Shorts — это видео **квадратного или вертикального** формата **до 3 минут** (раньше — до 60 секунд).
Источник: https://support.google.com/youtube/answer/15424877
Публичный API не отдаёт соотношение сторон чужих видео, поэтому `is_short` в трендах — признак по длительности (≤180 с), а не гарантия.

## 8. Анализ трендов моделью

`analyze_trends` отправляет бесплатной модели OpenRouter только названия, теги, длительности и числа просмотров (без каналов, id, e-mail/телефонов/ссылок). Ответ — темы, хуки и форматы. Модель **не прогнозирует просмотры**; фразы с прогнозами отбрасываются.
