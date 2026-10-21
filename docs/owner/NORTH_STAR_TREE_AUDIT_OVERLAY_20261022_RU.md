# CHECKPOINT 05 — обновлённое ДЕРЕВО 15 НАПРАВЛЕНИЙ: GitHub overlay 22.10.2026

> Это **честный audit overlay**, а НЕ принудительное изменение canonical `capability_tree_seed.json`, НЕ live Netlify publish и НЕ повышение зелёных листьев. Текущее дерево менять только через `tools/tree_registry_sync.py` / `tools/tree_apply_evidence.py` после exact-SHA тестов и owner receipts.

Baseline: `558780932430d2007067d724765269d050ece5d7`. Источник: `command-center/bcc/capability_tree_seed.json` в документационной ветке, его поля `as_of=2026-10-05`, `source_sha=a516eab8f89e0b951c5aaa06bcba30d2ca29ea99` исторические.

Всего узлов: **928**. Корень: 1; направлений: **15**; дочерних (включая вложенные): **912**, из них retired **8**, активных **904**. Это **не** количество непройденных тестов.

## Прямые дочерние узлы 15 зон (включая подгруппы)

| Зона | Непосредственных элементов | Исторический статус зоны |
|---|---:|---|
| `jeff` — Jeff · собеседник | 141 | mixed |
| `ux` — UX и CMD | 35 | mixed |
| `apps` — Приложения и бизнес | 16 | mixed |
| `models` — Локальные модели | 9 | mixed |
| `cloud` — API · облака · free | 15 | mixed |
| `computer` — Браузер и компьютер | 14 | mixed |
| `media` — Motion и медиа | 62 | mixed |
| `memory` — Память и обучение | 62 | mixed |
| `agents` — Агенты и оркестрация | 21 | mixed |
| `skills` — Навыки | 68 | mixed |
| `plugins` — Плагины и коннекторы | 30 | mixed |
| `ops` — Система и проверки | 271 | mixed |
| `oss` — Open source · каталог | 126 | mixed |
| `commerce` — Commerce · заработок и клиенты | 7 | idea |
| `devices` — Devices · другие устройства | 7 | idea |

Дополнительные вложенные родители (сохранять parent chain): `pv`, `pv-lora`, `skills-computer-browser-candidates`, `skills-agents-code-writing-candidates`. Индекс всех IDs и source paths: [NORTH_STAR_LEAF_INDEX_20261022.json](NORTH_STAR_LEAF_INDEX_20261022.json).

## Распределение статусов отдельных дочерних узлов (из старого seed, НЕ пересертифицированы)

- `reported`: 109
- `working`: 492
- `branch`: 31
- `blocked`: 12
- `recorded`: 172
- `idea`: 36
- `prepared`: 24
- `retired`: 8
- `code`: 25
- `mixed`: 3

**Обязательные четыре уровня в новом registry:**
`CODE_PRESENT` (файл и правильный SHA) → `TEST_GREEN_RECORDED` → `CI_GREEN_EXACT_SHA` → `OWNER_PC_VERIFIED_EXACT_SHA`.

Старые `working`/`recorded`/`reported` не равны `OWNER_PC_VERIFIED`. Исторический `mixed` у зоны не может автоматически сделать её READY. Независимое доказательство ещё не завершено для всех 904 активных IDs.

## North Star Ladder: консервативное положение

По последнему отдельно изученному `MP21_NORTH_STAR_TREE_20261011.md`: ступень 1 `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT` подтверждалась на старом снимке; ступени 2–8 ещё не имели достаточной evidence. Отчёты Argon от 21.10 заявляют 16-step `/improve` PASS, но **эти claims не подтверждают три последовательных self-authored skill циклов на актуальном owner-PC SHA**.

Следовательно: `NORTH_STAR_FULL_RELEASE=NOT_VERIFIED` на момент этого документа. Отдельные модули имеют test/code progress.

## Ссылки, которые Argon обязан пройти для КАЖДОГО ID

- [Вся machine-readable карта листьев](NORTH_STAR_LEAF_INDEX_20261022.json).
- [Мастер-промт исполнения](ARGON_NORTH_STAR_MASTER_PROMPT_20261023_RU.md).
- [15-векторный backlog](BOSSMAN_15_VECTORS_45_STEPS_20261019_RU.md).
- [Tool для registry](../../tools/tree_registry_sync.py).
- [Публичные правила evidence](../../tools/tree_apply_evidence.py).
- [Baseline seed](../../command-center/bcc/capability_tree_seed.json).
- [Аудит exact-SHA](CHECKPOINT_20261022_01_AUDIT_15_RU.md).

## Checklist 23.10

Generate new resolved registry for every active ID, mark stale paths, classify closure. Publish git checkpoints per gate. Promote leaf only by linked CI+owner run receipt. No stale green carry-over, no fake all-pass. **Изменение live дерева и rollout — отдельная операция после правильной проверки и approvals.**
