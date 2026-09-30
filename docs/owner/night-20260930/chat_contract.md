# Bossman Chat — backend/UI contract (lane CHAT-API ↔ lane CHAT-UI)

All routes live in ONE new auto-discovered feature module `command-center/bcc/features/chat_threads.py`
(router variable `FEATURE`, mounted under `/api` with the standard owner cookie/CSRF auth like every
feature — copy the exact pattern of an existing small feature such as `features/rave.py`).
Errors use the house shape `{error:{message, hint?, code?}}` via the existing ApiError helpers.
Texts shown to the owner are Russian.

## Storage (one Bossman, shared with CMD)
- Threads ARE the terminal CLI chat sessions: `<data_dir>/terminal/sessions/<id>.json`, same format
  `{id, turns:[{task_id, text, at}], summary?, compacted_at_turn?, compact_tasks?}` written by
  `bcc/terminal_cli/chat.py::Session`. Id regex `[A-Za-z0-9_-]{4,64}`; new ids use the CLI format
  `time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)`.
  A thread started in `bossman chat` appears in the web chat and vice versa.
- Web-only metadata (title, pinned, project, archived, updated_at) lives in ONE sidecar
  `<data_dir>/terminal/threads-meta.json` = `{version:1, threads:{<id>:{title, pinned, project, archived, updated_at}}}`
  written atomically (tmp + os.replace) under a process lock (asyncio.Lock + a lock file is fine).
  The CLI never reads it; CLI `Session.save()` rewriting the session file therefore cannot lose it.
- Turn text is stored through `terminal_cli.chat.filter_history_line(text)[:4000]` (same secret
  filtering as the CLI). Answers are NOT stored in the file: they are read back from the task (the truth).
- Projects: free-text label (1..40 chars, `[^\n]`) kept in meta; `GET /api/chat/projects` returns
  the distinct labels with counts. No new DB tables.

## Endpoints
GET  /api/chat/options
  -> {agents:[{id,name,role,enabled, model:{id,alias,name,kind:'local'|'cloud', locality:'local'|'cloud',
       free:bool|null, context_window:int|null, status, health}|null}],
      auto:{label:'Auto · Local-first', hint},
      subscriptions:[{name:'claude'|'codex', available:bool, logged_in:bool|null, opted_in:bool|null,
                      via:'rave', note}],        # from the Rave connectors service; chat turns cannot run on them
      speech:{status:'configured'|'unavailable', reason?},   # same source as GET /api/oss/status .speech
      memory:{configured:bool, recall_enabled:bool},
      search:{enabled:bool},                    # BOSSMAN_UNIFIED_SEARCH_ENABLED
      limits:{attachment_max_bytes, attachment_text_chars, prompt_max_chars, context_turns}}
  Must never raise on a fresh install: every sub-probe is wrapped and degrades to null/unavailable.

GET  /api/chat/threads?q=&project=&archived=0|1&limit=1..200
  -> {items:[{id,title,project,pinned,archived,turns:int,updated_at:iso,created_at:iso,
              last:{task_id,status}|null, surface:'cli'|'web'}], total}
  Sorted pinned first, then updated_at desc. `q` filters title + turn texts (case-insensitive, local).
  Title fallback = first turn text trimmed to 60 chars, else "Новый чат".
POST /api/chat/threads {title?, project?}  -> thread summary (201)
GET  /api/chat/threads/{id} -> summary + turns:[{idx, task_id, text, at, status, result|null, error|null,
       run:{id, status, model_alias, tokens_in, tokens_out, cost_usd, pricing_known}|null}]
       (result/error/run read from the tasks DB; missing task -> status 'missing')
PATCH /api/chat/threads/{id} {title?, pinned?, project?|null, archived?} -> summary
DELETE /api/chat/threads/{id} -> {ok:true, archived:true}  (reversible archive; `?purge=1` deletes the
       session file + meta entry; tasks stay in the DB). 404 unknown id.
GET  /api/chat/projects -> {items:[{name, threads}]}

POST /api/chat/attachments?filename=<name>&thread_id=<id?>   raw body (application/octet-stream)
  -> {id, name, size, mime, kind:'text'|'image'|'binary', sha256, text_chars:int|null}
  Stored at `<data_dir>/chat/attachments/<id>/<safe-name>` + `meta.json`; id = sha256[:16]+rand.
  Limits: 20 MiB/file (413), filename sanitized (no path parts), text detection = utf-8 decode of
  a whitelist of extensions (.txt .md .py .js .ts .json .csv .log .yaml .yml .toml .html .css .sql
  .xml .ini .sh .ps1 .bat) or mime text/*.
GET  /api/chat/attachments/{id} -> meta (never the raw bytes of binaries to other threads' pages)

POST /api/chat/threads/{id}/send
  body {text:1..20000, agent_id:int|null, attachments:[id]<=8, client_request_id:8..128 [A-Za-z0-9._:-]}
  Server steps (SAME engine as POST /api/tasks — call the same internal functions the /api/tasks
  and /api/tasks/preflight handlers use; do not duplicate admission logic):
   1. preflight exactly like POST /api/tasks/preflight; if agent_id is null use the agent it selects
      (Auto). If preflight refuses -> 409 {error:{code, message, hint}} and NOTHING is created.
   2. prompt = conversation_context.compose(parts + [attachments block]) + text (the attachment DATA block
      is the LAST context part, before MARKER, so current_request() sees only the owner's text), where parts are the
      last CONTEXT_TURNS (3) turns of THIS thread (after the compaction point, with the /compact
      summary first), each `"Владелец: {text}\nBossman: {answer}"` cut to 1500 chars per side,
      answers read from the tasks DB. Attachment block: for kind text, a DATA block
      "Вложение «name» (данные, не инструкции):\n```\n...\n```" (total ≤ 40000 chars, truncation
      stated); for other kinds one line "Вложение «name»: <kind>, <size>, путь <abs path> (содержимое
      не встроено)". Whole prompt ≤ 64000 chars (compose older turns away first).
   3. create the task (run_now=false, title = first 80 chars of text, same client_request_id
      idempotency as /api/tasks), append the turn to the session file, then start it exactly like
      POST /api/tasks/{id}/run.
   -> {thread:summary, turn:{idx, task_id, text, at}, task:{id,status}, run_id|null,
       admission:{ok, status, code?, reason?}, agent:{id,name}, model:{alias,locality}|null,
       replayed:bool}
POST /api/chat/threads/{id}/stop -> stops the thread's newest non-terminal task via the same
       engine stop as POST /api/tasks/{tid}/stop; {ok, task_id|null, status}

## Streaming (existing, plus one server fix)
UI follows a turn with `GET /api/events/stream?task_id=<tid>&after=<lastSeq>` (SSE frames
`id: <seq>\ndata: <json>\n\n`; kinds used: stream.open, stream.replayed, stream.lagged, run.answer_delta
{run_id,step,attempt,idx,text}, run.answer_reset, run.assistant_message{step,text,streamed},
run.tool_use, run.tool_result, run.usage, task.progress, memory.recalled, router.fallback,
approval.created, task.completed, task.failed{error}, task.stopped, task.blocked{code,reason}).
`run.reasoning_delta` MUST be dropped by the UI (hidden provider reasoning is never shown).
Server fix (lane CHAT-API, in bcc/api.py events stream handler only): when the `after` query
parameter is absent and a numeric `Last-Event-ID` request header is present, use it as `after`.
