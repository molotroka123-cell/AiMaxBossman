# PHASE 2 — АУДИТ ПАДАЮЩИХ ТЕСТОВ

Дата: 2026-09-06 · HEAD прогона: e4a5a231 · Окружение: Windows / Python 3.14.3 / pytest 9.0.2

## Сводная таблица

| Тест | Файл:строка | Тип ошибки | N0-блокер | Human-speed path | Приоритет | Класс |
|---|---|---|---|---|---|---|
| test_file_observer_refuses_to_treat_a_symlink_as_the_named_file | tests/test_v5_observers.py:120 | OSError WinError 1314 (symlink privilege) | да | нет | P1 | средовой (setup невозможен) |
| test_directory_observer_refuses_symlink_escape | tests/test_v5_observers.py:148 | OSError WinError 1314 (symlink privilege) | да | нет | P1 | средовой (setup невозможен) |
| test_fingerprint_changes_on_add_delete_rename_untracked_symlink | tests/test_context_slice.py:94 | OSError WinError 1314 (symlink privilege) | да | нет | P1 | средовой (setup невозможен) |
| test_failing_test_slice_is_depth_bounded_and_hashed | tests/test_context_slice.py:203 | AssertionError (sha256 mismatch) | да | нет | **P1 — РЕАЛЬНЫЙ БАГ КОДА** | целостность манифеста |
| test_key_created_in_env_path_with_0600 | tests/test_evidence_signing_shared.py:41 | AssertionError (mode 0o666 ≠ 0o600) | да | нет | P1 | платформозависимая семантика chmod |

Критерии: P0 = N0/irrelevant-duplicate/admission — таких нет. P1 = ломает proof/durability — все пять блокируют зелёный гейт N0 на Windows-хосте. P2 — см. «Хвост» ниже.

---

## Разбор №1+2 — V5 observers (tests/test_v5_observers.py)

**Контекст (±20 строк):** тесты 115–122 и 140–152. Setup создаёт `outside.txt` → `link.symlink_to(outside)` → проверяет, что `FileStateObserver` сообщает `exists=False` (symlink ≠ файл, который назвал owner) и что `DirectoryStateObserver` считает `escaped_symlinks=1, symlinks_skipped=1`, tree_digest не меняется.

**Полный traceback:**
```
link.symlink_to(outside)
C:\Python314\Lib\pathlib\__init__.py:1213: in symlink_to
    os.symlink(target, self, target_is_directory)
E   OSError: [WinError 1314] A required privilege is not held by the client
```

**Root cause (не симптом):** падение происходит в SETUP теста, а не в проверяемом коде. `os.symlink` на Windows требует `SeCreateSymbolicLinkPrivilege` (admin или Developer Mode). Продакшен-логика отказа от symlink **реально существует и корректна**: `bossman_shared/objective_observer.py:237` (`if self.path.is_symlink() or not self.path.is_file()` → absent), `:304-316` (обход дерева с `follow_symlinks=False`, подсчёт escaped/skipped). На Linux CI тесты проходят (273 passed по fa6d45df).

**Fix:** условный skip с reason через probe-проверку способности среды создавать symlink. Реестр пропусков репозитория (`tools/skips_registry.py:25`) уже содержит хинт `("symlink|Windows|win", "права ФС / платформа")` — механизм предусмотрен конвенцией. После добавления skip требуется перегенерация `docs/testing/SKIPS_REGISTRY.md` (иначе `test_registry_is_current_and_every_skip_has_a_reason` падает).

**Human-speed path:** нет (чистая ФС-операция).

---

## Разбор №3 — fingerprint symlink (tests/test_context_slice.py)

**Контекст:** тест 79–102 строит 8 состояний worktree (clean/untracked/edited/added/deleted/renamed/symlink_a/symlink_b) и требует все 8 разных отпечатков. Падает на строке 94 `os.symlink("pkg/a.py", root / "link.py")` — та же WinError 1314 в setup.

**Root cause:** среда без привилегий symlink. Код `worktree_fingerprint`/`_hash_path` (`tools/context_slice.py:113-120`) корректно различает symlink по цели (`b"link\0" + rel + os.readlink(...)`) — это покрыто на платформах с поддержкой symlink.

**Fix:** skipif по probe, reason «создание symlink требует привилегий ФС (WinError 1314)»; перегенерация реестра.

---

## Разбор №4 — sha256 манифеста (test_context_slice.py:203) — ★ РЕАЛЬНЫЙ БАГ

**Assertion:**
```
assert f["sha256"] == hashlib.sha256((root / f["path"]).read_bytes()).hexdigest()[:16]
E  AssertionError: assert '57dca557108d0ea3' == '0f702bee912ed2d7'
```

**Root cause (код, не среда):** `failing_test_slice` (`tools/context_slice.py:238-240`) считает хеш ПОВЕРХ текстового чтения:
```python
text = p.read_text(encoding="utf-8", errors="replace")   # universal newlines: CRLF→LF + lossy replace
manifest.append({..., "sha256": hashlib.sha256(text.encode()).hexdigest()[:16], ...})
```
Два независимых дефекта:
1. **Windows/CRLF:** `read_text()` с newline=None транслирует `\r\n`→`\n`, а файл на диске содержит `\r\n` (создан `write_text`). Манифест хеширует нормализованный текст, тест — сырые байты. На Linux совпадает случайно.
2. **Потеря байт:** `errors="replace"` превращает любой невалидный UTF-8 байт в U+FFFD — хеш манифеста ≠ хеш файла даже на Linux для не-UTF8 файлов.

**Почему это важно (инвариант MODEL_TEXT != PROOF):** манифест `file@sha256` — доказательный артефакт среза контекста. Хеш, посчитанный не над реальными байтами файла, — это PROOF, не привязанный к улике. Контракт теста (строка 203) явный: sha256 манифеста == sha256 сырых байтов файла.

**Fix (минимальный, tools/context_slice.py, функция failing_test_slice):**
```python
data = p.read_bytes()
text = data.decode("utf-8", errors="replace")
"sha256": hashlib.sha256(data).hexdigest()[:16]
```
`text` остаётся только для оценки токенов. Аналогичный паттерн в `repo_map` (строки 185-187) НЕ трогаю: его текущий тест (строка 72) сверяет нормализованное-vs-нормализованное и зелёный; изменение потребует правки зеленого теста — вне минимального скоупа (записан в P2-хвост).

---

## Разбор №5 — evidence key 0600 (test_evidence_signing_shared.py:41)

**Assertion:** `stat.S_IMODE(os.stat(...).st_mode) == 0o600` → фактически 438 (0o666).

**Root cause:** продакшен-код `bossman_shared/evidence.py:67-75` корректен для POSIX (`os.open(..., 0o600)` + `os.chmod(p, 0o600)` в try/except OSError: pass). На Windows POSIX-права не существуют: `os.chmod` влияет только на read-only бит, st_mode всегда 0o666. Свойство «ключ 0600» осмысленно только на POSIX; длина ключа (32) проверяется до mode-assert и на Windows проходит.

**Fix:** `@pytest.mark.skipif(os.name == "nt", reason="права 0600 — POSIX-семантика chmod; на Windows st_mode всегда 0o666")` + перегенерация реестра пропусков. На Linux CI тест продолжает выполняться — coverage не снижается.

---

## Хвост (P2, не блокируют N0, остаётся открытым)

| Пункт | Где | Почему P2 |
|---|---|---|
| SyntaxWarning `"\W" invalid escape` | bossman-core/tests/test_tools.py:118 | cosmetics; предупреждение, не отказ |
| repo_map sha256 — тот же класс CRLF/replace-паттерна | tools/context_slice.py:185-187 | нет падающего теста (контракт сверяет нормализованное-vs-нормализованное); изменение ломает текущий зелёный test_repo_map_rebuilds — требует отдельного решения |
| pytest atexit PermissionError на `pytest-current` | pytest-внутренний cleanup (site-packages/_pytest/pathlib.py:360) | шум окружения Windows, не код репо |

## Дополнительные проверки (по промту)

- **SKIPPED: 0** на момент прогона; после фиксов ожидаются 4 регистрации в реестре (2 observers + 1 fingerprint + 1 evidence-0600), каждая с причиной/владельцем/условием пересмотра.
- **ModuleNotFoundError: нет** (регрессия CI run 34028670388 не воспроизводится).
- **sleep()/fake timers в тестах: 0 совпадений** по tests/test_v5_*; human-speed regression risk отсутствует.
- Checkpoint 1 (SQLite lifetime): test_v5_connection_lifetime.py — 3 passed; storage-тесты зелёные. НЕ трогаем fb4740d2.
- Checkpoint 2 (V5 binding + admission): test_v5_admission_binding_regressions.py — 39 passed; test_v5_admission.py — 18 passed. fa6d45df + e4a5a23 зелёные.
