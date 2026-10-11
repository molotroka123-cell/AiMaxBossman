# Сведение владельческих линий, 20 сентября 2026 (UTC checkpoint 21 сентября)

Единственная целевая ветка: `claude/bossman-final-convergence-hu2702`.
Основной родитель: `6cb62d92978baaf16207199d973821cdf09e0ae1`.
Второй родитель: `7db0db970f92a2fac185acfc7b4492be5b96a823`, который уже включает
`release/bossman-owner` на `ce095d556fd9e65d7b7c84a35fb14dd3d0a240f6`.
Общий предок владельческих линий: `d4381e6c6f1640e0992942a864ed4e75ad78b21a`.
Native compare показал 148 коммитов release против 10 коммитов основной линии;
Dashboard Next добавляет один документ, не реализацию нового интерфейса.

Дерево строится из exact Git objects второй линии с переносом всех собственных
путей основной. Истории не squash/reset: оба родителя сохраняются. Другие ветки
не удаляются и не переписываются. Публикация ref — только force=false.

## Три пересечения, разобранные явно

- `bcc/metrics.py`: сохранена более общая реализация release через await_shared
  из single_flight.py. Она заменяет inline asyncio.wait основной линии, а НЕ
  возвращает отменённый asyncio.shield. Регрессионный тест основной линии
  test_metrics_cancelled_waiter_regression.py также сохранён.
- `tools/responsiveness_probe.py`: сохранён b0224c3 основной линии. В нём есть
  реальные PNG fixtures, отсчёт до dispatch, проверка нового view, finite guards,
  raw samples и отказ ложному installed mode. Более старый вариант release не
  должен вернуть ложный installed PASS. REFERENCE_ONLY_OVER_BUDGET и idle window
  присутствуют в выбранном варианте. Внешний installed runner и все его тесты
  основной линии сохранены. Это всё ещё не Windows/owner soak evidence.
- README: сохранён расширенный вариант release со всеми visual assets и
  scorecard, прежний README основной линии сохранён побайтно в корне как
  README_PRE_CONVERGENCE_6cb.md. Последующее обновление даты/skills идёт отдельно.

Authoritative tariff PREP-03 и обе группы price-gate тестов сохранены вместе.
Журнал PREPARATION_MEMORY_RU.md сохранён полностью с новейшей записью open-news.
Все остальные изменения release, включая owner scenarios, approvals/context/
media/terminal fixes, и документ Dashboard Next остаются в итоговом дереве.

Это merge исходников, не принятие приложения. Старые CI/архивные PASS не
переносятся на объединённый SHA. Полный общий CI, новая сборка и owner GUI/live/
Ryzen/intelligence/soak/rollback по-прежнему обязательны. В этой среде доступна
частичная Linux-копия: общего Windows/GPU/модельного прогона не заявляется.
