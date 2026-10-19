# Доказанный замерами разгон Bossman (Benchmark Speedup Report)

- **Дата замера (UTC)**: `2026-10-19T22:26:35.851450+00:00`
- **Число итераций на каждый контур (`N`)**: `60` (требование владельца: `N >= 50`)
- **Рабочее дерево**: `C:\Users\asd\Bossman\wt-argon-tier5-20261019`

## Сводная таблица `ДО / ПОСЛЕ` (мс)

| Горячий контур | Итераций (`N`) | ДО `min / p50 / p95 / max / mean` (мс) | ПОСЛЕ `min / p50 / p95 / max / mean` (мс) | Ускорение (`p50 ×` / `mean ×`) |
|---|---:|---:|---:|---:|
| `exchange_assistant.load_official` | 60 | `4.066 / 6.462 / 9.844 / 12.078 / 6.599` | `0.004 / 0.004 / 0.006 / 0.040 / 0.005` | **1468.59×** (`mean`: **1269.08×**) |
| `exchange_assistant.Store.manual_rates` | 60 | `2.445 / 2.485 / 2.812 / 3.615 / 2.546` | `0.001 / 0.001 / 0.001 / 0.015 / 0.001` | **3106.12×** (`mean`: **2314.55×**) |
| `web_access.policy.check_public_url_batch10` | 60 | `0.660 / 0.720 / 1.012 / 1.101 / 0.781` | `0.557 / 0.600 / 0.867 / 0.908 / 0.661` | **1.2×** (`mean`: **1.18×**) |
| `build_source_identity.blob_verify (7497 files)` | 60 | `960.798 / 1506.752 / 1759.687 / 1872.892 / 1453.597` | `77.378 / 97.552 / 154.351 / 158.625 / 114.800` | **15.45×** (`mean`: **12.66×**) |

## Детали оптимизаций (без потери строгости и проверок безопасности)

1. **`bossman_shared/exchange_assistant/rates.py::load_official`**:
   - **Узкое место**: При каждом запросе курса или котировки открывалось новое соединение SQLite к `fx.db` и вычитывались все исторические строки `fx_obs`.
   - **Решение**: Добавлена фильтрация на стороне SQL (`source IN ('cnb','ecb') AND pair IN (...)`) и мемоизация по ключу `(resolved_path, st_mtime_ns, st_size)` файла БД. При любом обновлении `fx.db` кеш инвалидируется мгновенно по `mtime_ns`/`size`.
2. **`bossman_shared/exchange_assistant/store.py::Store.manual_rates` и `Store.dashboard`**:
   - **Узкое место**: `manual_rates()` сканировал всю таблицу `manual_rates` в Python, перезаписывая словарь по `pair`; `dashboard()` дважды вызывал `SELECT * FROM deals`.
   - **Решение**: Добавлен составной индекс `idx_manual_rates_pair_id ON manual_rates(pair, id)` и запрос `WHERE id IN (SELECT MAX(id) FROM manual_rates GROUP BY pair)`, а в `dashboard()` таблица сделок читается за один проход.
3. **`command-center/bcc/web_access/policy.py::check_public_url` (`domain_denied` + `sensitive_reason`)**:
   - **Узкое место**: На каждый вызов `check_public_url` / `check_query` заново разбирался `os.environ['BCC_WEB_DENY_DOMAINS']`, выполнялся линейный обход `any(host.endswith('.' + d) for d in deny_domains())` по всем запрещённым доменам и последовательно запускались 17 отдельных регулярных выражений.
   - **Решение**: Кеширование `deny_domains()` по значению env-переменной, проверка суффиксов домена по точкам за `O(число_уровней_домена)` через `frozenset` и предварительный фильтр объединённым скомпилированным автоматом `_COMBINED_SECRET_RE` / `_COMBINED_PERSONAL_RE`.
4. **`tools/build_source_identity.py::source_identity`**:
   - **Узкое место**: При повторных проверках идентичности дерева в одном процессе каждый отслеживаемый файл вычитывался целиком с диска и хешировался SHA-1.
   - **Решение**: Мемоизация SHA-1 блобов по `(path, st_mtime_ns, st_size, st_ino)` при отсутствии скрытых индексных флагов (`git ls-files -v` без `h`/`S`). При наличии `--assume-unchanged` или `--skip-worktree` принудительно читаются сырые байты (все регрессионные тесты `tests/test_build_source_identity.py` зелёные).
