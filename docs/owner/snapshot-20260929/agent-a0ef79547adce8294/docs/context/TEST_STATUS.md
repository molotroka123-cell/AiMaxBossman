# TEST STATUS

Последний полный прогон: **507 passed, 2 skipped** (`bossman-core`).

| Набор | Результат |
|---|---|
| tests/test_sandbox_core.py | 13 passed |
| tests/test_sandbox_security.py | 24 passed |
| tests/test_stage4_7.py (приёмочный этапов 4–7) | 4 passed |
| tests/test_resource_brain.py | 13 passed |
| tests/test_remote_client.py | 26 passed |
| tests/test_search_everything.py | 11 passed |
| tests/test_video_factory.py | 21 passed |
| tests/test_shared_seams_stage4_7.py | 15 passed |
| tests/test_hardening_p0_p1.py | 8 passed |
| tests/test_browser_approvals_p1.py | 16 passed |
| tests/test_gateway_failover_4xx.py | 4 passed |
| tests/test_core_route_authz.py (гейт ключа + отказ неизолированного exec) | 13 passed |

Команды:
```
cd bossman-core
python -m pytest -q                                   # 2 браузерных упадут без chromium
CHROME=$(ls -d /opt/pw-browsers/chromium-*/chrome-linux/chrome|head -1)
BOSSMAN_TEST_CHROMIUM="$CHROME" python -m pytest -q    # 265 passed
```
Известное: 2 браузерных теста требуют `BOSSMAN_TEST_CHROMIUM` (по умолчанию
playwright ищет /usr/bin/chromium, которого нет) — это инфра, не код.
