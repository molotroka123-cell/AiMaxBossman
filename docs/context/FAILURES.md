# FAILURES (не повторять)

## FAIL-001 — фиктивное закрытие browser approvals
- attempt: считал P1 закрытым после добавления `_submit_like` + русских подписей.
- error: повторный red-team нашёл 3 живых обхода на новом HEAD.
- cause: гейт опирался на текст/структуру формы, но (а) `DANGEROUS_KEYS` содержал
  только Enter-семейство — `press("Space")` активировал сфокусированный submit;
  (б) `_select_impl` не звал структурную проверку — `<select onchange=submit>`
  проходил; (в) `role=button` считался консеквентным только внутри формы.
- fix: Space/Spacebar в DANGEROUS_KEYS; `_select_auto_submits` (inline onchange/
  oninput); `role=button` консеквентен везде. Коммит `19ba81c`.
- verification: `tests/test_browser_approvals_p1.py` — 16 passed (22 с policy).
- do_not_repeat: **не объявлять security-фикс закрытым без повторной атаки на
  новый HEAD.** Тест «зелёный» ≠ дыра закрыта.

## FAIL-002 — .gitignore глотал пакет-исходник
- attempt: коммит `bossman/projects/runner.py`.
- error: `paths are ignored by one of your .gitignore files`.
- cause: правила `projects/` и `workspace/` без ведущего слэша ловили и
  `bossman-core/bossman/projects/` — пакет НИКОГДА не был в git.
- fix: заякорил `/projects/`, `/workspace/`; добавил исходник в репозиторий.
- verification: `git ls-files bossman-core/bossman/projects/` непустой.
- do_not_repeat: правила рантайм-каталогов в .gitignore ВСЕГДА с ведущим слэшем.

## FAIL-003 — reconcile-агент воркфлоу деградировал / упёрся в лимит
- attempt: многоагентный recon и re-attack аудит.
- error: в первом воркфлоу reconcile вернул плейсхолдеры; во втором 7 агентов
  упали с session limit.
- cause: длинные многоагентные прогоны упираются в лимит сессии.
- fix: реальные результаты восстановлены из
  `subagents/workflows/<run>/journal.jsonl` (по одной строке `{"type":"result"}`
  на агента).
- do_not_repeat: при пустом/странном результате воркфлоу — СНАЧАЛА читать
  journal.jsonl, не переспрашивать агентов заново.
