# CONTINUE — Bossman 1.0 final convergence (следующий запуск)

Голова конвергенции: `fix/owner-run-20260923-p1` = `f72af6a82569371365de7cc5fc983a842c13735b` (SHA4 + 3 P1-фикса). Release не тронут (`e0bf948d`, fast-forward возможен).
Локальные worktree: `C:\Users\asd\Bossman\wt-fix-crlf0923` (ветка конвергенции), `wt-l3-apply` (WIP apply `3fa3d33f`), `wt-l4-stress` (пусто, 12612c81). Тест-venv: `build-0923\tvenv`; раннер регрессии `build-0923\regress\run_full.sh` (у root/core в PYTHONPATH НЕ должно быть command-center — коллизия пакета `tests`).

## Порядок (без новых фич)
1. **Воспроизвести C1–C3** (REPORT_RU.md): тест через TestClient на `POST /api/snapshots {"kind":"/../../../x"}`, `POST /api/opencode/sessions {"worktree_name":"../x"}`, `POST /api/terminal/roots`. Подтвердился → failing test → минимальный фикс → соседние сьюты.
2. **Независимая верификация** `aa6bee3d`, `c0a7e039`, `f72af6a8` (агент ≠ автор; попытки обхода: computer.act без approval, download без файла → PASS, подмена байтов до/после снимка).
3. **Controlled apply**: взять `WIP_controlled_apply_3fa3d33f.patch` (или commit `3fa3d33f` в wt-l3-apply), прогнать соседние: `test_coding*.py test_approval_*.py test_secrem_f013_approval_identity.py test_terminal_cli_unit.py test_terminal_cli_e2e.py` + bossman-core `tests/apprentice`; исправить сообщение коммита; только потом влить.
4. **Разобрать падения полной регрессии SHA4** (6 root, 7+2 core) — PRODUCT / ENVIRONMENT / HARNESS; досчитать command-center.
5. **Windows-100**: написать `scripts/windows_stress_100.py` по плану (health 8, auth 16, memory 10, tasks 14, approvals 15, containment 18, CU STOP 4, browser 4, concurrency 5, restart 5, 1 NOT_RUN), workflow с другим именем, добавить в `DEFAULT_REQUIRED`. Ставить продукт из `git clone`, не `git archive` (иначе SOURCE_IDENTITY_UNKNOWN).
6. **RC0** = голова после 1–5 → `tools/release_candidate.json` (объявление) → push в CI-триггер-ref `claude/**` на тот же SHA → `tools/exact_sha_certify.py --sha <RC0> --fetch`.
7. RC0 live на установленном ZIP: TR-01…22, HW-02 (агент «Оператор ПК» + Блокнот), HW-06/TR-12/TR-13 (тестовый компаньон: `C:\Users\asd\Bossman Test 0923\tg-companion\config.json`, секреты через `TG_COMPANION_BOT_TOKEN`/`TG_COMPANION_CORE_TOKEN`, сначала остановить `runtime-shim\tg_inbox.py` файлом STOP), standard-user, media, Jev shadow, рой атак (P0: traversal/sibling/junction/CU semantic/stale observation/approval replay).
8. Self-repair ×3: достроить кейсы в `C:\Users\asd\Bossman\exam-sealed-0923\exam3\` (MANIFEST.json — всё INCOMPLETE; tz-кейсы без zoneinfo).
9. FREEZE → ZIP → SHA-256 → финальный аудитор → только затем merge в release/bossman-owner (fast-forward, проверить tree).

## Решение владельца
- Фальшивый `windows-stress-100.yml` на default-ветке `claude/bossman-control-v03-43igbk` — удалить/retire (не трогал: default-ветка).
