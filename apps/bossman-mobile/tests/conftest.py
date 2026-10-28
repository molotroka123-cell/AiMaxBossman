import json
import pathlib
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from bossmobile.api import create_app  # noqa: E402
from bossmobile.config import Settings  # noqa: E402

BOSS_TOKEN = "BOSSMAN-OWNER-TOKEN-0123456789abcdef"
SID = "UPSTREAM-SESSION-ID-abcdef0123456789"
REAL_CSRF = "UPSTREAM-CSRF-TOKEN-fedcba9876543210"
PAIR = "pairing-token-for-tests-123"

OPTIONS = {"agents": [
    {"id": 1, "name": "Qwen Оператор (компьютер)", "role": "", "enabled": True,
     "model": {"id": 11, "alias": "local-qwen", "name": "qwen:latest", "kind": "local", "locality": "local",
               "locality_detail": "local", "billing": "local", "usable": True, "refusal": ""}},
    {"id": 2, "name": "Пилот", "role": "", "enabled": True,
     "model": {"id": 11, "alias": "local-qwen", "name": "qwen:latest", "kind": "local", "locality": "local",
               "locality_detail": "local", "billing": "local", "usable": True, "refusal": ""}},
    {"id": 3, "name": "Nemotron", "role": "", "enabled": True,
     "model": {"id": 12, "alias": "nemotron", "name": "nvidia/nemotron:free", "kind": "cloud", "locality": "cloud",
               "locality_detail": "cloud", "billing": "free_cloud", "usable": True, "refusal": ""}},
    {"id": 4, "name": "GLM 5.3 Flash (7API)", "role": "", "enabled": True,
     "model": {"id": 13, "alias": "glm-5.3-flash", "name": "glm-5.3-flash", "kind": "cloud", "locality": "cloud",
               "locality_detail": "cloud", "billing": "paid", "usable": True, "refusal": ""}},
], "auto": {"agent": None}}
AGENTS = [{"id": 1, "tools": ["computer.observe", "computer.act"], "fallback_model_id": None, "permissions": {}},
          {"id": 2, "tools": [], "fallback_model_id": 12, "permissions": {}},
          {"id": 3, "tools": [], "fallback_model_id": None, "permissions": {}},
          {"id": 4, "tools": ["code.read"], "model_id": 13, "fallback_model_id": None, "permissions": {"mobile_cloud_ok": True}}]
MODELS = [{"id": 11, "kind": "local", "provider_id": 1}, {"id": 12, "kind": "cloud", "provider_id": 2},
          {"id": 13, "kind": "cloud", "provider_id": 3}]
PROVIDERS = [{"id": 1, "base_url": "http://127.0.0.1:11434/v1"}, {"id": 2, "base_url": "https://openrouter.ai/api/v1"},
             {"id": 3, "name": "7API", "base_url": "https://7api.st/v1"}]
DG_MODELS = {"models": [
    {"id": "z-image-turbo", "label": "Z-Image Turbo", "kind": "photo", "modes": ["T2I"], "available": True, "reason": "",
     "requires_image": False, "accepts_image": False, "default_steps": 8, "max_steps": 20, "default_resolution": "512x512",
     "min_side": 256, "max_side": 1536, "side_multiple": 16, "weights": [{"file": "secret-path.gguf"}]},
    {"id": "qwen-image-2.1-uc", "label": "Qwen-Image 2.1 UC (режим «uncensored»)", "kind": "photo", "modes": ["T2I"],
     "available": True, "reason": "", "requires_image": False, "accepts_image": False},
    {"id": "wan2.2-ti2v-5b", "label": "Wan2.2", "kind": "video", "modes": ["T2V"], "available": True}]}
JOB = {"job_id": "a" * 32, "model": "z-image-turbo", "kind": "photo", "status": "completed", "stage": "completed",
       "raw_prompt": "горы", "params": {"width": 512, "height": 512, "seed": 1}, "progress": {"kind": "steps", "percent": 100},
       "result": {"mime": "image/png", "file": "image.png"}, "elapsed_s": 20.0, "provenance": {"command": ["sd-cli"]}}
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 4
DG_STATUS = {"queue": {"running": None, "waiting": 0}, "sd_cli": {"present": True}}
FACESWAP = None          # GET /api/direct-gen/faceswap (None: 404, as an older Bossman)
SWAP_JOB = {"job_id": "d" * 32, "model": "faceswap-facefusion", "kind": "faceswap", "status": "queued", "stage": "queued",
            "params": {"preset": "fast", "frame": "original", "faces": 1}, "progress": {"kind": "none"}}
FAIL_NEXT: list = []     # [(status, body)] answered once by POST /api/direct-gen/jobs
SPEECH_REPLY = {"ok": True, "provider": "faster-whisper", "text": " привет, Босман ", "language": "ru",
               "duration_seconds": 3.0, "segments": [], "cloud_used": False}
TIMINGS = None           # GET /api/direct-gen/timings (None: 404, as an older Bossman)


class FakeBossman:
    def __init__(self):
        self.calls: list[dict] = []
        self.sessions = {SID}
        self.logins = 0
        self.stop_file = None       # the owner's global STOP file this fake Bossman clears on POST /api/computer/resume
        self.resume_clears = True
        self.speech_bytes = b""

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = request.content.decode("utf-8", "replace") if request.content else ""
        self.calls.append({"method": method, "path": path, "query": dict(request.url.params), "body": body,
                           "headers": dict(request.headers)})
        if path == "/api/oss/speech/transcribe" and method == "POST":
            self.speech_bytes = request.content
            return httpx.Response(200, json=SPEECH_REPLY)
        if path == "/health/live":
            return httpx.Response(200, json={"alive": True, "build_sha_short": "586345db8270", "status": "ALIVE"})
        if path == "/api/login":
            if json.loads(body).get("token") != BOSS_TOKEN:
                return httpx.Response(401, json={"error": {"message": "неверный токен"}})
            self.logins += 1
            self.sessions.add(SID)
            return httpx.Response(200, json={"ok": True, "csrf": REAL_CSRF},
                                  headers={"set-cookie": f"bcc_session={SID}; HttpOnly; Path=/; SameSite=Strict"})
        if path in ("/", "/index.html") and method == "GET" or path.endswith(".js") or path.endswith(".css"):
            if path.endswith(".js"):
                return httpx.Response(200, text="console.log('ui')", headers={"content-type": "text/javascript"})
            return httpx.Response(200, text="<!doctype html><html><head><title>BCC</title></head><body>panel</body></html>",
                                  headers={"content-type": "text/html; charset=utf-8"})
        cookie = request.headers.get("cookie", "")
        if f"bcc_session={SID}" not in cookie or SID not in self.sessions:
            return httpx.Response(401, json={"error": {"message": "нужна аутентификация"}})
        if method not in ("GET", "HEAD") and request.headers.get("x-bcc-csrf") != REAL_CSRF:
            return httpx.Response(403, json={"error": {"message": "csrf", "code": "csrf"}})
        if path == "/api/computer/resume" and method == "POST":
            if self.stop_file is not None and self.resume_clears:
                self.stop_file.unlink(missing_ok=True)
            return httpx.Response(200, json={"stopped": False, "generation": 3})
        if path == "/api/chat/options":
            return httpx.Response(200, json=OPTIONS)
        if path == "/api/agents":
            return httpx.Response(200, json=AGENTS)
        if path == "/api/models":
            return httpx.Response(200, json=MODELS)
        if path == "/api/providers":
            return httpx.Response(200, json=PROVIDERS)
        if path == "/api/chat/threads" and method == "GET":
            return httpx.Response(200, json={"items": [{"id": "T1", "title": "t", "turns": 1}], "total": 1})
        if path == "/api/chat/threads" and method == "POST":
            return httpx.Response(201, json={"id": "T2", "title": "new"})
        if path == "/api/chat/threads/T1" and method == "GET":
            return httpx.Response(200, json={"id": "T1", "turns": [{"idx": 0, "task_id": 5, "text": "hi", "status": "completed",
                                                                     "result": "ok"}]})
        if path == "/api/chat/threads/T1/send":
            return httpx.Response(200, json={"task": {"id": 7, "status": "queued"}, "admission": {"ok": True},
                                             "model": {"alias": "local-qwen"}})
        if path == "/api/chat/threads/T1/stop":
            return httpx.Response(200, json={"ok": True, "task_id": 7, "status": "stopped"})
        if path == "/api/tasks/7":
            return httpx.Response(200, json={"task": {"id": 7, "status": "completed", "agent_id": 1, "prompt": "p"},
                                             "result": "готово", "error": None, "runs": []})
        if path == "/api/events/stream":
            frames = [
                {"kind": "stream.open", "task_id": 7},
                {"kind": "run.tool_use", "seq": 1, "task_id": 7, "call_id": "c1", "tool": "computer.observe", "args": {}},
                {"kind": "run.tool_result", "seq": 2, "task_id": 7, "call_id": "c1", "tool": "computer.observe", "ok": True,
                 "summary": "3 окна", "preview": f"leak {BOSS_TOKEN} end"},
                {"kind": "system.metrics", "seq": 3, "cpu": 5},
                {"kind": "run.answer_delta", "seq": 4, "task_id": 7, "text": "Открыто 3 окна"},
                {"kind": "task.completed", "seq": 5, "task_id": 7, "secret_field": "x"},
            ]
            text = ": keepalive\n\n" + "".join(f"id: {f.get('seq', 0)}\ndata: {json.dumps(f, ensure_ascii=False)}\n\n" for f in frames)
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        if path == "/api/tasks/7/events":
            return httpx.Response(200, json={"task_id": 7, "status": "completed", "events": [
                {"kind": "run.tool_use", "seq": 1, "call_id": "c1", "tool": "computer.observe", "args": {}},
                {"kind": "system.metrics", "seq": 2}]})
        if path == "/api/approvals" and method == "GET":
            return httpx.Response(200, json=[{"id": 9, "kind": "tool", "preview": "computer.act click", "status": "pending",
                                              "task_id": 7, "secret": "x"}])
        if path == "/api/approvals/9" and method == "POST":
            return httpx.Response(200, json={"id": 9, "status": "rejected", "kind": "tool", "task_id": 7, "lease": None})
        if path == "/api/direct-gen/models":
            return httpx.Response(200, json=DG_MODELS)
        if path == "/api/direct-gen/status":
            return httpx.Response(200, json=DG_STATUS)
        if path == "/api/direct-gen/timings":
            return httpx.Response(200, json=TIMINGS) if TIMINGS else httpx.Response(404, json={"detail": "Not Found"})
        if path == "/api/direct-gen/faceswap":
            return httpx.Response(200, json=FACESWAP) if FACESWAP else httpx.Response(404, json={"detail": "Not Found"})
        if path == "/api/direct-gen/swap-jobs" and method == "POST":
            return httpx.Response(202, json=SWAP_JOB)
        if path == f"/api/direct-gen/jobs/{'c' * 32}/file":
            rng = request.headers.get("range")
            if rng:
                a, b = (int(x) for x in rng[6:].split("-"))
                return httpx.Response(206, content=MP4[a:b + 1], headers={
                    "content-type": "video/mp4", "content-range": f"bytes {a}-{b}/{len(MP4)}", "accept-ranges": "bytes"})
            return httpx.Response(200, content=MP4, headers={"content-type": "video/mp4", "accept-ranges": "bytes"})
        if path == "/api/direct-gen/jobs" and method == "POST" and FAIL_NEXT:
            code, err = FAIL_NEXT.pop(0)
            return httpx.Response(code, json={"detail": err})
        if path == "/api/direct-gen/jobs" and method == "POST":
            return httpx.Response(202, json={**JOB, "status": "queued", "stage": "queued"})
        if path == "/api/direct-gen/jobs" and method == "GET":
            return httpx.Response(200, json={"jobs": [JOB, {**JOB, "job_id": "b" * 32, "kind": "video"}]})
        if path == f"/api/direct-gen/jobs/{'a' * 32}/send-to-pult" and method == "POST":
            return httpx.Response(200, json={"ok": True, "sent": True, "message_id": 5, "bytes": 3, "caption": "x"})
        if path == f"/api/direct-gen/jobs/{'e' * 32}/send-to-pult" and method == "POST":
            return httpx.Response(409, json={"error": {"code": "no_result", "message": "задача ещё не завершена"}})
        if path == f"/api/direct-gen/jobs/{'a' * 32}" and method == "DELETE":
            return httpx.Response(200, json={"ok": True, "deleted": True, "job_id": "a" * 32})
        if path == f"/api/direct-gen/jobs/{'e' * 32}" and method == "DELETE":
            return httpx.Response(409, json={"error": {"code": "job_running", "message": "задача ещё выполняется — сначала остановите её"}})
        if path == f"/api/direct-gen/jobs/{'a' * 32}":
            return httpx.Response(200, json=JOB)
        if path == f"/api/direct-gen/jobs/{'a' * 32}/file":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        # ---- long-press chat actions (22.10)
        if path == "/api/chat/threads/T1/archive-encrypted" and method == "POST":
            return httpx.Response(200, json={"ok": True, "encrypted": True, "id": "T1", "archived_at": "2026-10-22T10:00:00", "size": 99})
        if path == "/api/chat/threads/T9/archive-encrypted" and method == "POST":
            return httpx.Response(503, json={"error": {"message": "ключ шифрованного архива недоступен", "code": "CHAT_ARCHIVE_KEY_UNAVAILABLE"}})
        if path == "/api/chat/archive" and method == "GET":
            return httpx.Response(200, json={"items": [{"id": "T1", "title": "Секретный чат", "archived_at": "2026-10-22T10:00:00", "size": 99}], "total": 1})
        if path == "/api/chat/archive/T1/restore" and method == "POST":
            return httpx.Response(200, json={"ok": True, "id": "T1", "restored": True})
        if path == "/api/chat/archive/T8/restore" and method == "POST":
            return httpx.Response(409, json={"error": {"message": "чат с таким id уже существует", "code": "CHAT_THREAD_EXISTS"}})
        if path == "/api/chat/archive/T1" and method == "DELETE":
            return httpx.Response(200, json={"ok": True, "deleted": True})
        if path == "/api/chat/threads/T1" and method == "DELETE":
            q = request.url.params
            if q.get("purge") != "1" or q.get("confirm") != "T1" or request.headers.get("x-bossman-surface") != "mobile":
                return httpx.Response(422, json={"error": {"message": "нужно подтверждение", "code": "CHAT_PURGE_CONFIRM_REQUIRED"}})
            return httpx.Response(200, json={"ok": True, "archived": False, "purged": True})
        # ---- panel-only endpoints (generic proxy)
        if path == "/api/echo":
            return httpx.Response(200, json={"method": method, "body": body, "query": dict(request.url.params)})
        if path == "/api/leak":
            return httpx.Response(200, text=f"token={BOSS_TOKEN} sid={SID} csrf={REAL_CSRF}",
                                  headers={"content-type": "text/plain", "set-cookie": f"bcc_session={SID}; Path=/",
                                           "x-debug": f"sid={SID}"})
        if path == "/api/redir":
            return httpx.Response(307, headers={"location": "http://127.0.0.1:8801/api/elsewhere"})
        if path == "/api/system":
            return httpx.Response(200, json={"ok": True})
        if path.startswith("/api/telegram/calls/"):
            sub = path[len("/api/telegram/calls/"):]
            payload = json.loads(body) if body else {}
            if sub == "status" and method == "GET":
                return httpx.Response(200, json={
                    "mode": "telegram", "transport": "telegram", "enabled": True,
                    "settings": {"enabled": True, "barge_in": True, "greeting": "Привет", "answering_machine": False,
                                 "answer_ring_delay_s": 12, "answer_allow_unknown": True, "answer_greeting": "Автоответчик"},
                    "peer": {"user_id": 7459432800, "label": "Plug @bushidoprague"},
                    "account": {"state": "ready", "has_api": True, "api_id": "…2999", "phone": "+••••9637", "has_session": True},
                    "stop": {"call": False, "global": False, "active": False},
                    "answering": {"enabled": False, "armed": False, "listening": False},
                    "deps": {"ready": True, "missing": []},
                })
            if sub == "settings" and method in ("GET", "PUT"):
                return httpx.Response(200, json={"enabled": True, "barge_in": True, **payload})
            if sub == "credentials" and method == "POST":
                return httpx.Response(200, json={"ok": True, "credentials": {"has_api": True, "api_id": f"…{str(payload.get('api_id', ''))[-4:]}"}})
            if sub in ("login/start", "login/code", "login/password", "logout") and method == "POST":
                return httpx.Response(200, json={"state": "ready" if sub != "logout" else "logged_out"})
            if sub == "contacts" and method == "GET":
                return httpx.Response(200, json={"contacts": [{"id": 7459432800, "label": "Plug", "username": "bushidoprague"}]})
            if sub == "peer" and method == "PUT":
                return httpx.Response(200, json={"peer": {"user_id": payload.get("user_id") or 7459432800, "label": payload.get("username") or "Plug"}, "enabled": True})
            if sub == "peer" and method == "DELETE":
                return httpx.Response(200, json={"peer": None})
            if sub == "call" and method == "POST":
                return httpx.Response(200, json={"call_id": "call-test-01", "accepted": True, "transport": "telegram"})
            if sub == "hangup" and method == "POST":
                return httpx.Response(200, json={"ended": True})
            if sub == "stop" and method == "POST":
                return httpx.Response(200, json={"stopped": True, "persisted": True, "stop_flag": True})
            if sub == "resume" and method == "POST":
                return httpx.Response(200, json={"stop_flag": False, "global_stop": False})
            if sub == "history" and method == "GET":
                return httpx.Response(200, json={"items": [{"call_id": "call-test-01", "outcome": "completed", "turns": 3}]})
            if sub in ("answering", "answering/start", "answering/stop"):
                return httpx.Response(200, json={"answering": {"enabled": True, "armed": sub != "answering/stop", "listening": sub == "answering/start"}})
            if sub == "answering/reports" and method == "GET":
                return httpx.Response(200, json={"items": [{"id": "ar-000000000001", "outcome": "answered", "summary": "Просил перезвонить"}]})
            if sub == "selftest" and method == "POST":
                return httpx.Response(200, json={"ok": True, "scenario": payload.get("scenario", "all"), "passed": True})
            if sub == "doctor" and method == "POST":
                return httpx.Response(200, json={"verdict": "PASS", "rows": [{"check": "Зависимости", "status": "PASS", "detail": "ок", "remedy": ""}]})
        if path.startswith("/api/jeff-kb") or path == "/api/jeff-model":
            payload = json.loads(body) if body else {}
            if path == "/api/jeff-kb/check" and method == "POST":
                return httpx.Response(200, json={"mode": "manual" if payload.get("use_manual") else "plain",
                                                 "answer": "Двенадцать минут (§1)." if payload.get("use_manual")
                                                 else "Обычно десять.", "cited": [{"n": 1, "title": "Разминка"}]
                                                 if payload.get("use_manual") else [],
                                                 # owner 21.10: provenance of the answer (manual = the cloud fallback)
                                                 "engine": "cloud" if payload.get("use_manual") else "local",
                                                 "provider": "OpenRouter" if payload.get("use_manual") else "ollama",
                                                 "model": "vendor/model-x:free" if payload.get("use_manual")
                                                 else "gemma"})
            return httpx.Response(200, json={"path": path, "method": method, "echo": payload})
        if path == "/api/agent/tools" and method == "GET":
            return httpx.Response(200, json={"total": 10, "groups": {"Computer": [{"name": "computer.observe"}], "Terminal": [{"name": "terminal.run"}], "Coding": [{"name": "code.search"}]}, "tools": []})
        if path == "/api/agent/models" and method == "GET":
            return httpx.Response(200, json={"tiers": [{"tier": 1, "id": "local:gemma4-31b-uncensored:latest", "model": "gemma4-31b-uncensored:latest", "ready": True}], "workers": [], "runpod_policy": "DENY_WITHOUT_EXPLICIT_OWNER_APPROVAL"})
        if path == "/api/agent/router/select" and method == "POST":
            payload = json.loads(body) if body else {}
            return httpx.Response(200, json={"selected_id": "local:gemma4-31b-uncensored:latest", "model": "gemma4-31b-uncensored:latest", "tier": 1, "mode": payload.get("mode", "AGENT")})
        if path == "/api/agent/sessions" and method == "GET":
            return httpx.Response(200, json={"items": [{"id": "S1", "title": "OpenCode Session", "mode": "AGENT", "model": "gemma4-31b-uncensored:latest"}], "total": 1})
        if path == "/api/agent/sessions" and method == "POST":
            payload = json.loads(body) if body else {}
            return httpx.Response(201, json={"id": "S1", "title": payload.get("title") or "New", "mode": payload.get("mode", "AGENT"), "model": "gemma4-31b-uncensored:latest", "turns": []})
        if path == "/api/agent/sessions/S1" and method in ("GET", "PATCH"):
            payload = json.loads(body) if body else {}
            return httpx.Response(200, json={"id": "S1", "title": payload.get("title") or "OpenCode Session", "mode": payload.get("mode", "AGENT"), "model": "gemma4-31b-uncensored:latest", "turns": [{"text": "secret turn"}], "spent_usd": 0.0123, "budget_usd": 5.0, "budget_remaining_usd": 4.9877})
        if path == "/api/agent/sessions/S1/messages" and method == "POST":
            payload = json.loads(body) if body else {}
            return httpx.Response(200, json={"mode": payload.get("mode", "AGENT"), "session_id": "S1", "task_id": 7, "task": {"id": 7, "status": "queued"}})
        if path == "/api/agent/sessions/S1/events" and method == "GET":
            return httpx.Response(200, json={"session_id": "S1", "after": 0, "latest_seq": 1, "events": [{"seq": 1, "type": "task.progress", "task_id": 7}]})
        if path == "/api/agent/sessions/S1/stop" and method == "POST":
            return httpx.Response(200, json={"ok": True, "session_id": "S1", "stopped_tasks": [7]})
        if path == "/api/agent/tasks/7/approve" and method == "POST":
            payload = json.loads(body) if body else {}
            return httpx.Response(200, json={"ok": True, "task_id": 7, "decision": payload.get("decision", "approve")})
        if path == "/api/agent/improve" and method == "POST":
            return httpx.Response(200, json={"id": "imp-test-01", "status": "awaiting_owner_approval", "steps": [{"step": i, "status": "completed"} for i in range(1, 17)], "mission_receipt": {"sha256": "abc123"}})
        if path == "/api/agent/improve" and method == "GET":
            return httpx.Response(200, json={"items": [{"id": "imp-test-01", "status": "awaiting_owner_approval"}], "steps_template": []})
        if path == "/api/terminal/capabilities" and method == "GET":
            return httpx.Response(200, json={"docker": {"available": False}, "default_mode": "project_host", "modes": ["sandbox", "project_host", "system_admin"]})
        if path == "/api/terminal/roots" and method == "GET":
            return httpx.Response(200, json={"roots": ["C:/Users/asd/Bossman", "C:/Users/asd/Bossman/wt-final"]})
        if path == "/api/terminal/run" and method == "POST":
            payload = json.loads(body) if body else {}
            if payload.get("command") == "echo test" and not payload.get("approval_id"):
                # Bossman: a command outside the read-only list -> HTTP 202 + approval id inside `detail`
                # the real shape: bcc's exception handler answers {"error": <detail>} (live audit 25.10 caught a
                # fake that said {"detail": ...} and hid a missing approval id)
                return httpx.Response(202, json={"error": {"message": "нужно подтверждение", "approval_id": 77,
                                                           "decision": "ask"}})
            if payload.get("command") == "echo legacy" and not payload.get("approval_id"):
                return httpx.Response(202, json={"detail": {"approval_id": "78"}})
            return httpx.Response(200, json={"session_id": "term-01", "pid": 1234, "mode": "project_host"})
        if path == "/api/terminal/sessions" and method == "GET":
            return httpx.Response(200, json=[{"id": "term-01", "command": "git status", "status": "finished", "exit_code": 0}])
        if path in ("/api/terminal/sessions/term-01", "/api/terminal/sessions/term-01/log") and method == "GET":
            return httpx.Response(200, json={"id": "term-01", "finished": True, "exit_code": 0, "output": ["On branch main"]})
        if path in ("/api/terminal/sessions/term-01/stdin", "/api/terminal/sessions/term-01/sigint", "/api/terminal/sessions/term-01/kill") and method == "POST":
            return httpx.Response(200, json={"ok": True})
        if path == "/api/terminal/files" and method == "GET":
            return httpx.Response(200, json={"kind": "dir", "path": "C:/Users/asd/Bossman", "entries": [{"name": "README.md", "is_dir": False}]})
        if path == "/api/terminal/diff" and method == "GET":
            return httpx.Response(200, json={"ok": True, "cwd": "C:/Users/asd/Bossman", "status": "## main", "diff": ""})
        if path.startswith("/api/coding-tasks"):
            return httpx.Response(200, json={"ok": True, "items": []})
        return httpx.Response(404, json={"error": {"message": "not found"}})


def fake_ollama(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/tags":
        return httpx.Response(200, json={"models": [{"name": "ace-decider-08b:latest", "size": 1, "details": {"parameter_size": "0.8B"}}]})
    if request.url.path == "/api/ps":
        return httpx.Response(200, json={"models": []})
    return httpx.Response(404)


def war_down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


class Env:
    def __init__(self, tmp_path):
        tok = tmp_path / "bcc-token"
        tok.write_text(BOSS_TOKEN, encoding="utf-8")
        self.fake = FakeBossman()
        # stop_file in tmp: a test must never read (or be blocked by) the owner's live %LOCALAPPDATA% STOP
        st = Settings(bossman_url="http://127.0.0.1:8801", token_file=tok, data_dir=tmp_path / "mobile",
                      stop_file=tmp_path / "bcc" / "computer" / "STOP", niche_dir=tmp_path / "niche")
        self.fake.stop_file = st.stop_file
        self.app = create_app(st, bossman_transport=httpx.MockTransport(self.fake.handler),
                              ollama_transport=httpx.MockTransport(fake_ollama), war_transport=httpx.MockTransport(war_down),
                              pairing_token=PAIR)
        self.c = TestClient(self.app, base_url="http://127.0.0.1:8840", client=("127.0.0.1", 50000))
        self.csrf = ""

    def login(self):
        r = self.c.post("/m/api/login", json={"token": PAIR})
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrf"]
        return r

    def post(self, path, **kw):
        headers = kw.pop("headers", {})
        headers.setdefault("X-BM-CSRF", self.csrf)
        return self.c.post(path, headers=headers, **kw)

    def panel_csrf(self):
        row = next(iter(self.app.state.sessions._rows.values()))
        return row["panel_csrf"]


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


@pytest.fixture
def authed(env):
    env.login()
    return env
