# Bossman Studio: репозиторная поставка фаз 1–5

Уточнение владельца 18 сентября: отдельный форк не нужен; лучшие продуктовые
идеи Higgsfield реализуются собственным кодом внутри Bossman. Ни одна строка
open-higgsfield не перенесена. Существующий Video Studio сохранён.

## Реализовано

- Одна физическая очередь Images. Studio хранит дополнительные метаданные;
  второго планировщика, polling daemon или ядра нет. Отмена освобождает worker
  посреди отправки/ожидания. После аварии отправленная заявка не повторяется.
- Галерея без потолка 60: пагинация, image/video/audio, поиск, избранное,
  корзина, восстановление, ZIP с provenance. Старые image_assets мигрируют
  лениво без удаления исходников. Демо явно обозначено и не является AI.
- Composer по схеме модели, seed, пакет 1–8, коллекции, роли референсов,
  Ctrl/Cmd+Enter. Рецепты раскадровки берутся из Video Studio.
- Шесть различимых отказов провайдера, SHA-256 и декодирование до completed,
  неизменяемое происхождение (SQL-триггеры), проверка файлов при скачивании.
- OpenRouter images/videos по уже использованному в репозитории контракту.
  Vault/env, GovernedAdapter, выключено по умолчанию, free_only, неизвестная
  цена запрещает вызов, резерв верхнего расхода атомарен, платного fallback нет.
  HTTPS CDN разрешены точным списком; ключ никогда не посылается на CDN.
- Подтверждение передачи точных байтов референса; отзыв останавливает
  последующие запросы. Локальные импорты никуда сами не загружаются.
- Video Studio: импорт через media.import с provenance_ref и revision/lease
  gate; Web Designer: существующий edit с base_version. Агентные generate/status
  привязаны к task/run, gate_completion не принимает наблюдение за результат.
- FFmpeg рефрейм pad/crop изображений и видео. Референсы сохраняются локально,
  ссылка+хэш могут быть записаны в настроенный существующий vault заметок.
- Проверенность модели появляется только после реальных скачанных байтов на
  этой установке, истекает через сутки и сбрасывается при смене ключа/конфига.
- Wheel содержит каталог и UI. В app-support добавлен studio_live_owner.py,
  в Windows workflow — его запуск и проверка каталога. Профиль установленного
  продукта вырос с 44/15 до 46/16 (2 настоящих браузерных сценария Studio).
  Unit/API-тесты на исходниках не выданы за проверку установленного архива.
- Freeze требует studio.json с общим binding. Отсутствие живого доступа —
  OWNER_REQUIRED; липовый PASS без проверенных файлов — BLOCKED.

## Что не объявляется готовым

Higgsfield: REFERENCE_ONLY. В этой сессии плагин не подключён и владелец
отказался его устанавливать. Полоса 16 проб — NOT_RUN, не 16 PASS. Официального
REST-контракта нет; ни HTTP-, ни MCP-адаптер до гейта не включён. Данные о
нулевых кредитах из входного документа исторические, сейчас не перепроверены.

TTS, апскейл и удаление фона: OWNER_REQUIRED, кандидаты. Нет подтверждённого
контракта/весов; заглушка не выдаётся за синтез. Audio-галерея принимает настоящие
импортированные аудиофайлы, но не обещает их генерацию. Локальный рефрейм — PASS
на настоящем FFmpeg этого Linux-хоста, целевой Radeon — OWNER_REQUIRED.

Облачные тесты используют явный HTTP MockTransport и не выставляют VERIFIED.
Финальный Windows ZIP, UI sweep на нём, живые image+video с ключом и целевой
Ryzen/Radeon остаются отдельными гейтами. STUDIO_VERIFIED/FROZEN не заявлены.

## Проверки и воспроизведение

```
python -m pytest -c command-center/pyproject.toml command-center/tests/test_studio_provider_states.py command-center/tests/test_studio_runtime.py command-center/tests/test_studio_cloud.py command-center/tests/test_studio_integrations.py command-center/tests/test_studio_gallery_ui.py -q --timeout=180
python -m pytest tests/test_studio_catalog.py tests/test_studio_live_owner.py tests/test_astra6_freeze.py tests/test_acceptance_registry.py tests/test_target_hardware_acceptance.py -q
python tools/skips_registry.py --check
python -m bcc.studio.catalog --check
```

Красные стадии до реализации воспроизведены для очереди/галереи, Web Designer,
пустого completed, новых UI-кнопок, fingerprint конфигурации, заметки референса,
freeze и owner runner. Тесты старого продукта не удалены и не пропущены.
Изменения чисел тестов реестра соответствуют ровно двум добавленным сценариям.
Известный timing-сбой test_operator_step_profile воспроизводился ещё до фаз
на исходной базе; порог 60 не изменён. Полный suite не объявляется зелёным.

Откат поставки: предыдущий коммит `d05354112b314582fdeda1c4fff7621463b0946e`;
последний ранее проверенный Windows-архив — d0535411 из отчёта Claude
`dffef5d` (44/44 на установленном архиве).
Новые таблицы аддитивны, старые image_jobs/image_assets и редакторы сохранены.


## Проверенные числа этой поставки

Основной commit `045d90c8d18cc22df467cfd7f03c365648666a8b` опубликован после
fetch/rebase на свежий `dffef5d` Claude. Проверено равенство Git tree перед
обновлением ветки, force не применялся.

- На исходниках: 80 PASS (Studio + прежние Images UI/cancel).
- Корневой набор: 1797 PASS, 11 SKIP, 1 FAIL — прежний overhead-тест;
  новых skip нет, реестр 235 актуален.
- Из отдельного установленного wheel 045d90c8 на Linux: 2/2 Studio browser
  PASS, import bcc и UI берутся из venv, source_dirty=false. Каталог 6/0/6.
  Это не Windows-проверка.
- Последний контроль форматов: 76 PASS (provider/runtime/cloud/integrations);
  после добавления полного видео HTTP-stub пути cloud+integrations 29 PASS.
  MP4 под именем JPG отвергается; MP3 имеет audio/mpeg. Локальный correlation
  UUID синхронного ответа больше не выдаётся за внешний request_id.
- Windows run для 045d90c8: 35297709382, сборка запущена; вердикт здесь не
  выдумывается. Новая продуктовая правка запускает следующий кандидат.
