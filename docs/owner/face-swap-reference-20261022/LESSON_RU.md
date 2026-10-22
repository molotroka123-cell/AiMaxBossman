# Face swap по референсам: существующий локальный workflow

Кодекс, 22.10.2026. Teacher-authored lesson, применение моделью и рендер NOT_RUN. Полный source-reviewed контракт: `docs/owner/face-swap-reference-20261022/WORKFLOW_RU.md`. Память — справочник, не разрешение или новый tool.

1. Сначала различи: сохранить лицо @1 при генерации/редактировании или перенести лицо в готовый target video. `/refs`, FaceLock и FaceFusion — разные функции. `/swap` Telegram — обменник.
2. Используй только явно выбранные source photos и target clip для согласованного неоткровенного изменения. Подтверди согласие и scope, не выдавай edit за настоящую запись. Старые личные refs не искать и не анализировать автоматически.
3. Для video face swap действующий API: GET `/api/direct-gen/faceswap`, POST `/api/direct-gen/swap-jobs`. Вход: `video_b64` target, `faces_b64` 1..5 PNG/JPEG одной source identity, выбранный preset, `frame:original`, фактически подтверждённое consent. На mobile есть proxy `/m/api/video/swap`.
4. Installation probe не доказывает исправность весов, безопасность или успешный inference. Сверь source/runtime, approved engine/model hash, compatibility/resources/STOP. Нет разрешённого dispatch tool — BLOCKED_CAPABILITY. Не используй shell или owner token для обхода.
5. Текущий API выбирает many и не предоставляет target-person selector. Несколько людей и требование изменить только одного → BLOCKED_TARGET_SELECTION. Не обещай функцию по историческому CLI примеру.
6. Content/consent/QA/STOP проверки сохраняются. Отказ контент-проверки не доказывает corruption. Ошибка загрузки/hash → диагностика approved artifact и честный FAIL/BLOCKED; не пропускай анализ и не заменяй веса на менее ограничительную сборку.
7. Создай ровно один existing job, сохрани job_id. Не повторяй create после timeout: исход может быть неизвестен, а endpoint даёт новый UUID. Сначала status/reconciliation. Не запускай новый worker/backend и не убивай чужие задачи.
8. Следи за GET `/api/direct-gen/jobs/{id}`. Cancel через POST `/api/direct-gen/jobs/{id}/cancel`; проверь terminal state и остановку процессов. Сообщение «остановлено» без подтверждения — не evidence.
9. После completed проверь actual file/hash, playable frames/FPS/duration/audio/framing и qa_gate. Полный animation Gate 1 — отдельная versioned проверка, не заменяется FaceLock sampling. Не переносить старые PASS, не менять пороги после прогона.
10. Receipt содержит реальные IDs, source/runtime/engine/model identity где доступны, input/output hashes, параметры, согласие/scope, время/ресурсы, evaluator/QA/STOP и измеренную стоимость. Неизвестное — N/A/UNKNOWN.
11. Личные файлы локальны и вне Git. Отправляй только владельцу по отдельному поручению existing `/jobs/{id}/send-to-pult`, один раз после QA. Сохрани SENDING перед запросом, message_id после; неизвестный исход не дублируй.
12. Сохранение/поиск урока = метод доступен для retrieval. Обучение весов, полноценный render PASS и перенос на новую задачу этим не доказаны. Новые модели и конфиги не устанавливай как побочный эффект урока.
