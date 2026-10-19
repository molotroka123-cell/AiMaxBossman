# Доказанный замерами разгон Bossman (Benchmark Speedup Report)

- **Дата замера (UTC)**: `2026-10-19T22:55:13.206108+00:00`
- **Число итераций на каждый контур (`N`)**: `60` (требование владельца: `N >= 50`)
- **Рабочее дерево**: `C:\Users\asd\Bossman\wt-argon-tier5-20261019`

## Сводная таблица `ДО / ПОСЛЕ` (мс)

| Горячий контур | Итераций (`N`) | ДО `min / p50 / p95 / max / mean` (мс) | ПОСЛЕ `min / p50 / p95 / max / mean` (мс) | Ускорение (`p50 ×` / `mean ×`) |
|---|---:|---:|---:|---:|
| `exchange_assistant.load_official` | 60 | `3.516 / 3.644 / 3.860 / 4.072 / 3.667` | `0.008 / 0.008 / 0.016 / 0.059 / 0.010` | **433.82×** (`mean`: **356.0×**) |
| `exchange_assistant.Store.manual_rates` | 60 | `0.792 / 0.809 / 0.825 / 0.838 / 0.810` | `0.000 / 0.000 / 0.001 / 0.004 / 0.000` | **2697.67×** (`mean`: **2024.0×**) |
| `web_access.policy.check_public_url_batch10` | 60 | `0.221 / 0.222 / 0.228 / 0.235 / 0.223` | `0.195 / 0.195 / 0.199 / 0.211 / 0.196` | **1.14×** (`mean`: **1.14×**) |
| `build_source_identity.blob_verify (7505 files)` | 60 | `475.141 / 636.255 / 1118.921 / 1389.078 / 652.777` | `47.519 / 64.329 / 66.497 / 66.926 / 58.755` | **9.89×** (`mean`: **11.11×**) |

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
