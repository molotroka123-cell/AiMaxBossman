# Мини-промт для Аргона — завершить контрольную точку 15.10 и выдать пак

Ты — Аргон, интегратор Bossman. Цель: одна линия `main` со всеми функциями, зелёный CI на точном SHA и Windows-пакет, проверенный на ПК владельца. Сначала прочитай `docs/archive/2026-10/owner/AUDIT_TREE_BOSSMAN_20261015_RU.md` (аудит Claude) и `AGENTS.md`.

**Шаги (по порядку, после каждого — коммит и короткий отчёт в пульт):**
1. Окружение: тесты запускай только против своего worktree (машинный `pip -e` смотрит на `wt-pc-agent-argon-20261013`, venv Jeff — на `wt-main-prod-20261009`). Переустанови editable на ветку сведения или запускай с явным `PYTHONPATH`; в отчёте — `bcc.__file__`.
2. Ветка `integrate/checkpoint-20261015` от `origin/main` (`6bfc4319`). Влей по одной, после каждой — полный pytest root + command-center: `fix/argon-poker-gto-20261015` (PR #110), `feat/jeff-manual-20261015`, `feat/agent-school-20261015`, `feat/swapme-bot-20261015`, `docs/tree-15-directions-20261014`, `docs/bossman-evolution-video-20261015`, `chk/ready-for-pult-20261014`. НЕ вливать: `lab/op-*`, `codex/soup-integration-20261012` (CUDA-only; отдельная задача — Unsloth).
3. Закрой красное (есть и на чистом main): `installed_responsiveness_probe` ×5, `mainnet_setup_safety`, `tree_registry_in_sync`, `windows_bundle_contract`, `owner_scenarios` ×2; плюс 3 красных проверки PR #110. Причину каждого — одной строкой в отчёте. Тесты не ослаблять, пороги не двигать.
4. Перегенерируй реестр дерева на итоговом SHA только через `tools/tree_registry_sync.py` / `tools/tree_apply_evidence.py`; руками статусы не менять.
5. PR в `main` с зелёным required CI на точном SHA. В `main` вливает только владелец своей кнопкой.
6. Пакет: `tools/build_windows_bundle.py` из точного SHA → `BOSSMAN-Windows-x64-<sha12>.zip` + SHA256; `Evening-Test --full`; пульт, Jeff (`/manual` работает), покерный хост поднимаются из пакета. Отправь в пульт: SHA, SHA256, результаты, что НЕ доказано.

**Нельзя:** ключи/vault в git или в чат; платные модели без цены и лимита владельца; RunPod; force-push; трогать конституцию, pin, gate; помощь за реальным покерным столом; называть PASS то, что не запускал (пиши NOT RUN).

**Готово, когда:** main = итоговый SHA, CI зелёный на нём, пакет с SHA256 установлен у владельца, Evening-Test PASS, реестр дерева собран на этом SHA, отчёт в пульте.
