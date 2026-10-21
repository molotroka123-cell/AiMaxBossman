# Матрица доказательств: инструменты и навыки Bossman (WIP, 22.10.2026)

Независимая проверка на `origin/main 09d4a025`, синтетические данные, песочница вместо LOCALAPPDATA. Статус не повышался: нет пробы = NOT_RUN. PROVEN_EXECUTED у навыка = доказан ВЫБОР и ДОСТАВКА текста, не изменение результата.

**Инструменты (112):** PROVEN_EXECUTED=19, PROVEN_GATED=5, STRUCTURAL_ONLY=0, FAILED=1, NOT_RUN=87
**Навыки (67):** PROVEN_EXECUTED=12, PROVEN_GATED=5, STRUCTURAL_ONLY=50, FAILED=0, NOT_RUN=0
**Заявления Argon (20):** PROVEN_EXECUTED=8, PROVEN_GATED=0, STRUCTURAL_ONLY=9, FAILED=1, NOT_RUN=2
**Меняет ли навык результат:** `OUTCOME_UNPROVEN`

## Инструменты

| tool | группа | право | эффект | статус | доказательство | оговорка |
|---|---|---|---|---|---|---|
| terminal.run | Terminal | terminal.run | ask | PROVEN_EXECUTED | ASK->approve wrote marker (pre-approval absent=True, content proof-42); reject/deny/self-approve/outside-root all blocked | executes via project_host only (sandbox needs docker); python -c is ASK even when terminal.run is granted |
| terminal.stdin | Terminal | terminal.run | ask | PROVEN_EXECUTED | stdin 'abcdef' -> process printed got:fedcba, read back via terminal.status; effect=('auto', 'executed') | granted terminal.run lets stdin/kill run without a fresh ask (by design: effect auto when the permission is he |
| terminal.status | Terminal | terminal.read | auto | PROVEN_EXECUTED | returned live output of the running session; unknown session refused ('не найдена') |  |
| terminal.kill | Terminal | terminal.run | auto | PROVEN_EXECUTED | pid 28392 alive before, gone after (tasklist, independent of the tool) |  |
| memory.index | Memory |  | auto | PROVEN_EXECUTED | forced index of 3 synthetic notes ('индекс обновлён'), a later new note became searchable after re-index | BM25 lexical backend (sqlite) on a 3-note synthetic vault; semantic/qdrant backends not exercised |
| memory.stats | Memory |  | auto | PROVEN_EXECUTED | stats string returned for the indexed vault | BM25 lexical backend (sqlite) on a 3-note synthetic vault; semantic/qdrant backends not exercised |
| memory.search | Memory |  | auto | PROVEN_EXECUTED | payments-rollback.md ranked first for the rollback query; nonsense query returned 'ничего не найдено' | BM25 lexical backend (sqlite) on a 3-note synthetic vault; semantic/qdrant backends not exercised |
| memory.expand | Memory |  | auto | PROVEN_EXECUTED | chunk_hash taken from the search output expanded to the full 'Payments rollback' section | BM25 lexical backend (sqlite) on a 3-note synthetic vault; semantic/qdrant backends not exercised |
| memory.write | Memory | filesystem.write | ask | PROVEN_EXECUTED | ASK->approve; vault had no BOSSMAN Memory notes before approval, note ['2026-10-21-2237-Latency-budget-lesson.md'] with the content after; an sk- key inside the text was  | BM25 lexical backend (sqlite) on a 3-note synthetic vault; semantic/qdrant backends not exercised |
| memory.fact.add | Memory | filesystem.write | ask | PROVEN_EXECUTED | 2 adds each ASK->approve->executed; preview names tool | SQLite fact store on a synthetic subject; no LLM involved |
| memory.fact.search | Memory |  | auto | PROVEN_EXECUTED | current-only search returns Team Beta only (Alpha invalidated by replace-current) | SQLite fact store on a synthetic subject; no LLM involved |
| memory.fact.at_time | Memory |  | auto | PROVEN_EXECUTED | world_at 2025-06 -> Alpha only; world_at 2026-06 -> Beta only (bi-temporal) | SQLite fact store on a synthetic subject; no LLM involved |
| memory.fact.history | Memory |  | auto | PROVEN_EXECUTED | history lists both facts | SQLite fact store on a synthetic subject; no LLM involved |
| code.index | Files |  | auto | PROVEN_EXECUTED | background index started over a 2-file synthetic repo ('индексация запущена') |  |
| code.status | Files |  | auto | PROVEN_EXECUTED | index stats returned ({"files": 2, "chunks": 2, "terms": 42, "index_path": "C:\\Users\\asd\\AppData\\Local\\Temp\\proof-y…) |  |
| code.search | Files |  | auto | PROVEN_EXECUTED | top-1 hit = billing/invoice.py::compute_invoice_total for the invoice query and auth/login.py::verify_password for the password query | local BM25 over a tiny repo |
| code.expand | Files |  | auto | FAILED | handler works with a hash from the index, BUT code.search's text never prints chunk_hash, so the model has no way to obtain a valid argument | USABILITY DEFECT: memory.search prints 'chunk_hash:' but code.search does not (data.chunk_hashes is dropped by |
| mimik.guide | Diagnostics |  | auto | PROVEN_EXECUTED | 2 action steps -> checklist with execution_status NOT_RUN, network_requests=0; an embedded 'run rm -rf /' step stayed text; both inputs refused | text-to-checklist adapter only: it does not record the screen or execute steps (its own limitations list says  |
| open_news.process | Diagnostics |  | auto | PROVEN_EXECUTED | 3 articles (1 duplicate URL, 1 off-topic) + query 'quantum' -> 1 article returned | offline filter/dedup/summary only; no network |
| open_news.search | Diagnostics | browser.read | ask | PROVEN_GATED | ASK even though browser.read is granted (hook floor); canary handler: 0 calls while parked, 0 after reject, exactly 1 after approve | canary handler: the real network call to news.google.com was NOT made |
| apps.status | Diagnostics |  | auto | PROVEN_EXECUTED | known app answered 'war-terminal: не запущено (pid=None, port=8830)'; unknown app refused ('приложение 'no-such-app' не найдено среди манифестов') | read-only; does not start anything |
| apps.start | Diagnostics |  | ask | PROVEN_GATED | ASK -> approved -> the REAL handler ran and refused by owner policy ('выключено политикой'): no process spawned | the success path (actually starting an app) was deliberately not run: it would spawn a process on the owner's  |
| apps.stop | Diagnostics |  | ask | PROVEN_GATED | ASK -> approved -> the REAL handler ran and refused by owner policy ('выключено политикой'): no process spawned | the success path (actually starting an app) was deliberately not run: it would spawn a process on the owner's  |
| jeff.broadcast | Diagnostics | channel.send | ask | PROVEN_GATED | channel.send granted yet still ASK; preview carries the exact text; empty text DENY; canary: 0 calls parked/rejected/denied, exactly 1 after approve | canary handler: no Telegram message was sent; real recipient list/outbound guard not exercised |
| computer.act | Computer | computer.control | ask | PROVEN_GATED | even with computer.control GRANTED every mutating action is ASK (hook floor); canary: 0 calls parked/rejected, exactly 1 after approve; payment-semantic action: denied | canary handler: no click/keystroke was sent to the owner's desktop; real Windows UIA actions were not executed |
| computer.observe | Computer | computer.observe | ask | NOT_RUN | not executed: it captures the owner's real desktop (window titles + screenshot) = personal data (owner rule 21.10). Permission algebra proven: ungranted -> ask, granted - | the effect (UIA tree + screenshot of a real desktop) was never run; an in-repo test uses a fake desktop but th |
| browser.back | Browser | browser.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| browser.click | Browser | browser.control | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| browser.download | Browser | browser.control | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| browser.fill_owner_fields | Browser | browser.control | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| browser.login | Browser | browser.control | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| browser.open | Browser | browser.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| browser.read_dom | Browser | browser.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| browser.reload | Browser | browser.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| browser.request_owner_fields | Browser | browser.control | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| browser.screenshot | Computer | browser.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| browser.select | Browser | browser.control | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| browser.submit | Browser | browser.control | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| browser.type | Browser | browser.control | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| browser.verify_owner_input_success | Browser | browser.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| code:definition | Diagnostics | filesystem.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| code:diagnostics | Diagnostics | filesystem.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| code:hover | Diagnostics | filesystem.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| code:references | Diagnostics | filesystem.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| code:symbols | Diagnostics | filesystem.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| openclaw.agents | Diagnostics | channel.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| openclaw.channels | Diagnostics | channel.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| openclaw.health | Diagnostics | channel.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| openclaw.send | Diagnostics | channel.send | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| openclaw.session_describe | Diagnostics | channel.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| openclaw.sessions | Diagnostics | channel.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| opencode.abort | Coding |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| opencode.diff | Coding | terminal.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| opencode.send | Coding | terminal.run | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| opencode.session.start | Coding | terminal.run | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| opencode.status | Coding | terminal.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:browser.form_submit | Diagnostics | browser.control | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:browser.open | Diagnostics | browser.read | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:calendar.create | Diagnostics |  | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:calendar.search | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:drive.search | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:drive.write | Diagnostics |  | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:github.issue_create | Diagnostics |  | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:github.repo_read | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:gmail.draft | Diagnostics | email.send | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:gmail.read | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:gmail.search | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:gmail.send | Diagnostics | email.send | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:http.get | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:mcp.tool_call | Diagnostics |  | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:mcp.tool_list | Diagnostics |  | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:monitor.feed | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:n8n.workflow_list | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:n8n.workflow_run | Diagnostics |  | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:obsidian.read | Diagnostics | filesystem.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:obsidian.write | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:ollama.chat | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:openrouter.chat | Diagnostics |  | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:sql.read | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:telegram.send | Diagnostics | channel.send | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| plugin:telegram.status | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| studio.generate | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| studio.status | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.audio.process | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.captions.edit | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.captions.transcribe | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.clip.move | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.clip.split | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.clip.trim | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.effect.apply | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.export.cancel | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.export.start | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.export.status | Diagnostics | filesystem.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.history.undo | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.keyframe.set | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.media.analyse | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.media.import | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.media.probe | Diagnostics | filesystem.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.media.relink | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.output.verify | Diagnostics | filesystem.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.preview.render | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.project.create | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.project.inspect | Diagnostics | filesystem.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.project.interchange | Diagnostics | filesystem.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.project.open | Diagnostics | filesystem.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.timeline.apply | Diagnostics | filesystem.write | ask | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| video.timeline.inspect | Diagnostics | filesystem.read | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| war_terminal.analyze_tender | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| war_terminal.check_compliance | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| war_terminal.draft_bid_checklist | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| war_terminal.estimate_cashflow | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |
| war_terminal.summarize_conflict | Diagnostics |  | auto | NOT_RUN | no probe completed for this tool in this WIP run (not claimed) | probe family unfinished at shutdown; nothing is claimed for this tool |

## Навыки

| skill | статус | доказательство | оговорка |
|---|---|---|---|
| anthropics/skill-creator | PROVEN_EXECUTED | selected by positive task, absent for negative task, sha256+license verified, text within budget (3532 chars), banner present, grants empty | Proves selection + delivery by SkillCatalog on synthetic texts only. NOT proof that using the skill changes a  |
| anthropics/webapp-testing | PROVEN_EXECUTED | selected by positive task, absent for negative task, sha256+license verified, text within budget (3845 chars), banner present, grants empty | Proves selection + delivery by SkillCatalog on synthetic texts only. NOT proof that using the skill changes a  |
| huggingface/hf-mem | PROVEN_EXECUTED | selected by positive task, absent for negative task, sha256+license verified, text within budget (3621 chars), banner present, grants empty | Proves selection + delivery by SkillCatalog on synthetic texts only. NOT proof that using the skill changes a  |
| huggingface/huggingface-local-models | PROVEN_EXECUTED | selected by positive task, absent for negative task, sha256+license verified, text within budget (3996 chars), banner present, grants empty | Proves selection + delivery by SkillCatalog on synthetic texts only. NOT proof that using the skill changes a  |
| superpowers/executing-plans | PROVEN_EXECUTED | selected by positive task, absent for negative task, sha256+license verified, text within budget (3500 chars), banner present, grants empty | Proves selection + delivery by SkillCatalog on synthetic texts only. NOT proof that using the skill changes a  |
| superpowers/receiving-code-review | PROVEN_EXECUTED | selected by positive task, absent for negative task, sha256+license verified, text within budget (3501 chars), banner present, grants empty | Proves selection + delivery by SkillCatalog on synthetic texts only. NOT proof that using the skill changes a  |
| superpowers/requesting-code-review | PROVEN_EXECUTED | selected by positive task, absent for negative task, sha256+license verified, text within budget (3274 chars), banner present, grants empty | Proves selection + delivery by SkillCatalog on synthetic texts only. NOT proof that using the skill changes a  |
| superpowers/systematic-debugging | PROVEN_EXECUTED | selected by positive task, absent for negative task, sha256+license verified, text within budget (3720 chars), banner present, grants empty | Proves selection + delivery by SkillCatalog on synthetic texts only. NOT proof that using the skill changes a  |
| superpowers/test-driven-development | PROVEN_EXECUTED | selected by positive task, absent for negative task, sha256+license verified, text within budget (3444 chars), banner present, grants empty | Proves selection + delivery by SkillCatalog on synthetic texts only. NOT proof that using the skill changes a  |
| superpowers/using-git-worktrees | PROVEN_EXECUTED | selected by positive task, absent for negative task, sha256+license verified, text within budget (3157 chars), banner present, grants empty | Proves selection + delivery by SkillCatalog on synthetic texts only. NOT proof that using the skill changes a  |
| superpowers/verification-before-completion | PROVEN_EXECUTED | selected by positive task, absent for negative task, sha256+license verified, text within budget (3631 chars), banner present, grants empty | Proves selection + delivery by SkillCatalog on synthetic texts only. NOT proof that using the skill changes a  |
| superpowers/writing-plans | PROVEN_EXECUTED | selected by positive task, absent for negative task, sha256+license verified, text within budget (3356 chars), banner present, grants empty | Proves selection + delivery by SkillCatalog on synthetic texts only. NOT proof that using the skill changes a  |
| mechanism/revocation | PROVEN_GATED | selected before=True; after revoke selected=False, status=REVOKED | revocation list persistence (settings_kv) not exercised here, only the honouring of it |
| mechanism/tamper | PROVEN_GATED | tampered copy -> QUARANTINED (SKILL.md изменён после импорта (sha256 не совпадает)); delivered=False |  |
| mechanism/poison-critical | PROVEN_GATED | sha matches, text has 'curl/sh' -> QUARANTINED ['remote_exec']; delivered=False |  |
| mechanism/policy-strip | PROVEN_GATED | 'skip the tests/push to main' line removed from delivered text, skill still delivered: True; codes=['disable_tests', 'push_main'] |  |
| mechanism/license | PROVEN_GATED | license file removed -> QUARANTINED (файл лицензии из provenance не найден внутри каталога скиллов); delivered=False |  |
| mechanism/agent-assignment | STRUCTURAL_ONLY | static grep: assignment symbols per file {'command-center/bcc/engine.py': 0, 'command-center/bcc/features/chat_threads.py': 0, 'command-center/bcc/features/skills.py': 9, | static reading only; no engine run performed to show an assigned skill is absent from the model's prompt (time |
| agents-skills/agent-method | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=1852 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/bossblocks-video-style | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=4050 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/bossman-elder-brother-agent | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=5609 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/bossman-motion-concert | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=3209 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/bossman-orca-slicer | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=3795 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/bossman-owner-run-lessons | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=3485 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/bossman-receipt-proof | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=2104 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/bossman-self-repair-lessons | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=2055 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/bossman-shared-helper-change-safety | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=1596 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/bossman-windows-test-pitfalls | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=1900 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/browser-contract-scraping | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=3982 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/browser-research | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=1201 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/context-builder | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=1775 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/differential-review | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=3082 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/external-evidence-check | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=2771 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/facelock | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=5581 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/failure-retrospective | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=1455 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/frontend-design | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=3092 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/fx-daily-rates | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=6499 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/genjutsu-character-swap | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=3365 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/mcp-builder | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=3291 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/memory-curator | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=1412 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/memory-recall | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=1571 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/mimik | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=['mimik.guide']; body=2542 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/model-eval | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=948 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/motion-graphics-effectcraft | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=2732 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/night-mission | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=922 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/obsidian-knowledge | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=376 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/open-news | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=['open_news.process', 'open_news.search']; body=2263 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/openrouter-sync | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=1339 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/permission-auditor | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=1767 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/photo-upscale-local | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=5841 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/print3d-orca | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=3185 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/proof-before-done | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=1646 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/property-based-testing | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=3189 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/repo-audit | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=1403 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/requirement-clarify | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=2370 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/safe-code-change | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=1296 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/safe-terminal | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=1595 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/skill-evaluator | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=1841 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/skill-forge | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=1899 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/solana-volume-suite | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=3397 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/systematic-debugging | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=3073 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/test-driven-development | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=3007 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/ui-ux-pro-max | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=17374 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/variant-analysis | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=3127 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/video-editing | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=['video.project.create', 'video.project.inspect', 'video.media.probe', 'video.media.analyse', 'video.media.relink',  | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/video-face-swap-facefusion | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=4868 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |
| agents-skills/webapp-testing | STRUCTURAL_ONLY | parsed; manifest errors=0, warnings=0; required_tools=-; body=3271 chars | registered/parses/manifest-valid only; not run through /api/skills/{id}/run and the engine in this section (ti |

## Заявления Argon

| заявление | статус | доказательство |
|---|---|---|
| test_mobile_opencode_agent.py runs green | PROVEN_EXECUTED | 19 passed in 50s (16 scenarios, test_08 has 4 params); apps/bossman-mobile test_mobile_opencode.py 2 passed; 0 skips |
| 01 chat basic response | STRUCTURAL_ONLY | asserts session/mode/route tier/task_id only; workers are not started, no model reply is checked |
| 02 multi-step agent tool execution | STRUCTURAL_ONLY | asserts a 'task.progress' event exists; no tool is called, no model runs |
| 03 file read + patch + test | STRUCTURAL_ONLY | file READ via /api/terminal/files is real; patch+tests are only a posted message (mode asserted), nothing patched |
| 04 web terminal run + WS + diff | PROVEN_EXECUTED | real 'git status --short' subprocess, WebSocket first frame and /terminal/diff ok |
| 05 Ctrl+C and STOP | STRUCTURAL_ONLY | sigint/stop endpoints return ok:true; the test never checks the process actually ended |
| 06 screenshot + window list (COMPUTER) | STRUCTURAL_ONLY | only checks the 10 tool groups and that computer.observe is LISTED; no screenshot is taken |
| 07 computer action approval | STRUCTURAL_ONLY | test inserts its own approval row and approves it; does not show computer.act is gated (that gate is proven separately: computer.act PROVEN_GATED) |
| 08 dangerous commands -> 403 | PROVEN_EXECUTED | 4 real POSTs through TerminalPolicy all 403 |
| 09 local->cloud fallback router | PROVEN_EXECUTED | real select_route_for_request logic; it is route SELECTION, no model call/fallback happens |
| 10 budget stop + RunPod guard | STRUCTURAL_ONLY | 402/403 come from the router PREVIEW endpoint; no real run is stopped by a budget |
| 11 WS reconnect + replay | PROVEN_EXECUTED | real WebSocket, seq replay with replayed:true |
| 12 persistence after restart | PROVEN_EXECUTED | second app on same data_dir loads session/budget/turns |
| 13 parallel isolation | STRUCTURAL_ONLY | two sessions keep their own turn text; nothing runs concurrently |
| 14 /improve 16 steps + MissionReceipt | NOT_RUN | pipeline body not re-verified in this WIP (test passes; its depth not read) |
| 15 secret redaction | PROVEN_EXECUTED | emit() with an sk- key: stored event has no key (regex redact_structure, event level only) |
| 16 AUTO bypass keeps DENY | PROVEN_EXECUTED | python -c print runs with bypass_permissions; rm -rf / stays 403 |
| /m/api gateway test | STRUCTURAL_ONLY | bossman-mobile test talks to a FakeBossman upstream: proves proxying, not the backend |
| live :8801 exposes /api/agent/* | FAILED | live runtime build 288f79f56a1a (v2.6.0) answers 404 for /api/agent/tools; that commit predates agent_opencode.py. Acceptance doc cites 099b497f |
| Stage 6 +40% learning gain | NOT_RUN | not re-verified in this WIP (skills fork incomplete) |

## Что НЕ доказано

- Изменение результата навыком: `OUTCOME_UNPROVEN`. Выбор и доставка текста доказаны для каталога; 49 локальных .agents/skills только распарсены (STRUCTURAL_ONLY); чат-агент Gemma на телефоне текст навыков не получает вовсе.
- Инструменты со статусом NOT_RUN выше: пробы browser/video/plugin/openclaw/opencode/studio/war_terminal/code:*/web.* не завершены.
- computer.observe не запускался (снимает реальный экран владельца).
- code.expand: обработчик работает, но code.search не печатает chunk_hash, модель не может получить аргумент.
- Живой рантайм :8801 (288f79f56a1a) не содержит /api/agent/*.
- Сравнение с моделью (A/B): OUTCOME_NOT_RUN (свободно ~46.9 ГБ < 50 ГБ).
