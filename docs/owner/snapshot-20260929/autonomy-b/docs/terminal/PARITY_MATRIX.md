# Terminal 1.2 — parity matrix (capability → handler → CLI → permissions → readiness → tests)

Readiness: CONTRACT_PASS = tested on Linux against a real backend process with the
DETERMINISTIC TEST MODEL (MOCK_MODEL); WIRED = calls the existing API, not tested end to end;
UNAVAILABLE = no terminal operation yet (reason given). No row is a Windows owner PASS.

| Capability | Existing handler | CLI operation | Permissions | Readiness | Tests |
|---|---|---|---|---|---|
| Chat / agent task | POST /api/tasks (+/tasks/preflight, /tasks/{id}/run) | `bossman chat`, `-p`, `exec` | agent tools/policy unchanged | CONTRACT_PASS | test_terminal_cli_e2e::test_headless_stream_json_run_with_a_tool |
| Live events | GET /api/events/stream (new, SSE) | follow in chat/-p/exec, `events --follow` | token/session | CONTRACT_PASS | e2e (all) |
| Event replay by cursor | GET /api/tasks/{id}/events (new) | `bossman events <id> --after N` | token/session | CONTRACT_PASS | e2e replay part |
| Idempotent submit | POST /api/tasks `client_request_id` (new, optional) | `exec` `request_id` | — | CONTRACT_PASS | test_idempotent_exec_input_file_gives_one_task |
| Result | GET /api/tasks/{id} | `bossman result` | — | CONTRACT_PASS | e2e |
| Status / identity | /api/identity, /api/health, /api/tasks, /api/approvals | `bossman status`, `/status` | — | CONTRACT_PASS | test_non_tty_output_has_no_ansi_and_no_token |
| Approvals | GET/POST /api/approvals | inline y/n/d, `/approve`, `approve`, `deny` | owner decision only; no auto-approve | CONTRACT_PASS (fail mode, deny, no-TTY refusal) | test_approval_required_with_fail_mode_exits_4_and_approves_nothing |
| Stop / pause / resume | POST /api/tasks/{id}/stop|pause|resume | `stop`, `pause`, `continue`, `/stop`, Ctrl+C | — | CONTRACT_PASS (stop) | test_stop_cancels_a_running_task |
| Global STOP | tasks stop + POST /api/computer/stop + coding cancel | `stop --all`, `/stop all` | — | WIRED | — |
| Models / agents | /api/models, /api/agents (PATCH model_id) | `list models|agents`, `/models use`, `/agent` | `/models use` = owner's explicit agent change | WIRED | — |
| Skills | /api/skills, /api/skill-catalog(/select) | `list skills`, `/skills [q]` | skills grant nothing | WIRED | — |
| Tools & policy | /api/capabilities + agent permissions | `list tools`, `/tools` | shows never/on-demand/allowed | WIRED | — |
| Memory | /api/memory/search, /api/memory/facts, /api/memory/stats | `/memory <q>`, `/panel` | read only | WIRED (memory hits in stream: CONTRACT via run.log) | unit: memory count |
| Coding path (diff + verify) | /api/coding-tasks(+readiness, cancel) | `bossman code`, `/code`, `/diff` | allowed_paths explicit | WIRED | — |
| Computer control | /api/computer/status|stop|resume, computer.* tools | `/computer`, `run automation` | tool effects ask as usual | WIRED (minimal view) | — |
| Keys | POST /api/providers, PATCH/DELETE /api/providers/{id}/key (new), GET /api/providers/{id}/catalog (new), /api/openrouter/connect | `keys [set|remove|import-env]`, `/keys` | key encrypted in vault; mask only | CONTRACT_PASS (set via stdin, mask, DB bytes, argv refusal) | test_keys_set_via_stdin_is_encrypted_and_never_printed |
| Evolution 1.1 | /api/evolution/* (other lane) | `evolution …`, `/evolve`, `repair --self` | — | UNAVAILABLE until that API lands (404 → code 10) | — |
| Self-improve lab | tools/self_improve_lab.py (other lane) | `evolve --lab` | — | WIRED (runs the runner, prints its JSON) | — |
| Browser, Studio (image/video/music), Web Designer, Telegram, Fleet, diagnostics | their feature APIs | via chat tasks only (agent tools) | as in web | UNAVAILABLE as dedicated CLI operations (no time in 1.2.0) | — |
