"""Short-lived loopback setup form, no external assets, no credentials in URLs/logs."""
from __future__ import annotations

import hmac
import json
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .config import CompanionError, Person, Settings
from .store import single_instance

HTML = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Bossman · Telegram</title>
<style nonce="NONCE">body{font:16px system-ui;background:#0f1420;color:#eef1f6;max-width:760px;margin:36px auto;padding:24px}label{display:block;margin:14px 0 4px}input{box-sizing:border-box;width:100%;padding:10px;border:1px solid #39435a;border-radius:8px;background:#192235;color:inherit}button{padding:13px 22px;margin:20px 0;background:#bafc73;border:0;border-radius:8px}small{color:#bcc5d4}details{margin-top:20px}#status{white-space:pre-wrap}</style>
<h1>Bossman · Telegram-помощник</h1><p>Настройка сохраняется только на этом компьютере. Мост выключен до запуска. Используйте отдельного бота, чтобы не мешать существующим уведомлениям и approvals.</p>
<form id="form" autocomplete="off"><label>Токен бота</label><input name="bot_token" type="password" required>
<label>Ваш числовой Telegram user ID (личный чат)</label><input name="owner_id" type="number" min="1" required>
<label>Сервер локальной модели</label><input name="local_url" value="http://127.0.0.1:8080/v1" required>
<label>Точное имя уже загруженной локальной модели</label><input name="local_model" required>
<small>Модель не скачивается. Нужен работающий OpenAI-compatible локальный сервер.</small>
<label>Адрес Bossman</label><input name="core_url" value="http://127.0.0.1:8800" required>
<label>Токен Command Center (можно оставить пустым для обычного чата)</label><input name="core_token" type="password">
<label>ID отдельного исполнителя ваших поручений (необязательно)</label><input name="owner_agent_id" type="number" min="1">
<details><summary>Второй пользователь · отдельные права и память</summary><label>Telegram user ID гостя</label><input name="guest_id" type="number" min="1"><label>ID ограниченного исполнителя гостя (необязательно)</label><input name="guest_agent_id" type="number" min="1"><small>Не назначайте гостю агента с доступом к личным документам владельца. Без исполнителя доступны только беседа и поиск.</small></details>
<details><summary>Интернет-поиск и прокси</summary><label>Существующий локальный SearXNG (необязательно)</label><input name="search_url"><label>Явный HTTP(S) proxy для Telegram/Claude (необязательно)</label><input name="proxy" type="password"><label>Ключ локального модельного сервера (необязательно)</label><input name="local_token" type="password"></details>
<details><summary>Резерв Claude · выключен по умолчанию</summary><p>Нужен отдельный ключ OpenRouter и точная модель anthropic/claude-*. После настройки каждый пользователь отдельно разрешает резерв командой /cloud on. Передаётся только его новое сообщение, не локальная память и не результаты задач.</p><label>Ключ OpenRouter</label><input name="cloud_token" type="password"><label>Точное имя Claude в OpenRouter</label><input name="cloud_model"><label>Предел расходов в день, USD (0 = выключено)</label><input name="cloud_daily_usd" type="number" min="0" step="0.01" value="0"><label>Предел одного запроса, USD</label><input name="cloud_request_usd" type="number" min="0" step="0.01" value="0.05"></details>
<button type="submit">Сохранить на компьютере</button></form><p id="status" role="status"></p>
<script nonce="NONCE">const token=location.hash.slice(1);history.replaceState(null,'','/');const form=document.getElementById('form'),out=document.getElementById('status');form.addEventListener('submit',async e=>{e.preventDefault();const button=form.querySelector('button');button.disabled=true;try{const response=await fetch('/setup',{method:'POST',headers:{'Content-Type':'application/json','X-Setup-Token':token},body:JSON.stringify(Object.fromEntries(new FormData(form)))});const result=await response.json();out.textContent=result.message;if(response.ok){form.reset();form.hidden=true;}else button.disabled=false;}catch{out.textContent='Окно настройки больше не отвечает. Закройте его и повторно откройте настройку; токены в чат не отправляйте.';button.disabled=false;}});</script></html>'''


def save_form(path: Path, values: dict) -> None:
    if not isinstance(values, dict) or any(not isinstance(v, str) for v in values.values()):
        raise ValueError("form must contain text values")
    owner = int(values.get("owner_id", ""))
    people = [Person(owner, owner, "owner", int(values['owner_agent_id']) if values.get('owner_agent_id') else None)]
    if values.get('guest_id'):
        guest = int(values['guest_id'])
        people.append(Person(guest, guest, "guest", int(values['guest_agent_id']) if values.get('guest_agent_id') else None))
    config = {"people": [vars(p) for p in people], "local_url": values.get("local_url", "").strip(),
              "local_model": values.get("local_model", "").strip(), "core_url": values.get("core_url", "").strip(),
              "search_url": values.get("search_url", "").strip(), "cloud_model": values.get("cloud_model", "").strip(),
              "cloud_daily_usd": float(values.get("cloud_daily_usd") or 0),
              "cloud_request_usd": float(values.get("cloud_request_usd") or 0.05)}
    credentials = {k: values.get(k, "").strip() for k in ("bot_token", "core_token", "local_token", "cloud_token", "proxy")}
    if not credentials['bot_token'] or not config['local_model']:
        raise ValueError("token and model required")
    Settings(**{**config, "people": tuple(people)}, **credentials)
    from bcc.auth import _restrict_to_owner
    from bcc.secrets import Vault
    with single_instance(path.parent):
        if path.exists() or (path.parent / 'credentials.enc').exists():
            raise CompanionError("CONFIG_EXISTS_EDIT_LOCALLY_WITH_BACKUP")
        # Apply directory restrictions BEFORE creating the encryption key or data.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('error', UserWarning)
            _restrict_to_owner(path.parent)
        vault = Vault(path.parent)
        _restrict_to_owner(vault.path)
        for target, text in ((path.parent / 'credentials.enc', vault.encrypt(json.dumps(credentials))),
                             (path, json.dumps(config, ensure_ascii=False, indent=2) + '\n')):
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                output.write(text)
            _restrict_to_owner(target)


def make_server(path: Path):
    token, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass

        def respond(self, code: int, data: bytes, kind='application/json'):
            self.send_response(code)
            self.send_header('Content-Type', kind + '; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path != '/' or self.headers.get('Host') != self.server.expected_host:
                return self.respond(404, b'{}')
            self.respond(200, HTML.replace('NONCE', nonce).encode(), 'text/html')

        def drain(self, limit=65536):
            """Read the request body before refusing it.

            Answering with unread bytes still in the socket makes Windows reset
            the connection, so the caller sees a broken connection instead of
            the refusal. Bounded: an oversized body is refused, not swallowed.
            """
            try:
                size = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                size = 0
            remaining = min(max(size, 0), limit)
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 8192))
                if not chunk:
                    break
                remaining -= len(chunk)
            if size > limit:
                self.close_connection = True

        def do_POST(self):
            refused = (self.path != '/setup' or self.headers.get('Host') != self.server.expected_host or
                    self.headers.get('Origin') != 'http://' + self.server.expected_host or
                    not hmac.compare_digest(self.headers.get('X-Setup-Token', ''), token) or self.server.saved)
            if refused:
                self.drain()
                return self.respond(403, b'{}')
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 16384 or self.headers.get('Content-Type') != 'application/json':
                    self.drain()
                    raise ValueError
                save_form(path, json.loads(self.rfile.read(size)))
            except (CompanionError, OSError, ValueError, TypeError, UserWarning):
                self.respond(400, json.dumps({'message': 'Не сохранено. Проверьте обязательные поля, числовые ID, разные исполнители, локальные адреса и бюджеты. Существующие настройки не перезаписываются; остановите мост перед настройкой.'}, ensure_ascii=False).encode())
            else:
                self.server.saved = True
                self.respond(200, json.dumps({'message': 'Сохранено локально. Закройте это окно, запустите Telegram Companion и отправьте боту /start. Это ещё не проверка живого подключения.'}, ensure_ascii=False).encode())
    server = HTTPServer(('127.0.0.1', 0), Handler)
    server.timeout, server.saved = 1, False
    server.expected_host = '127.0.0.1:' + str(server.server_port)
    return server, token


def setup_browser(path: Path) -> None:
    if path.exists():
        raise CompanionError('CONFIG_EXISTS_EDIT_LOCALLY_WITH_BACKUP')
    server, token = make_server(path)
    try:
        if not webbrowser.open('http://' + server.expected_host + '/#' + token):
            raise CompanionError('SETUP_BROWSER_UNAVAILABLE_USE_SETUP_CONSOLE')
        deadline = time.monotonic() + 600
        while not server.saved and time.monotonic() < deadline:
            server.handle_request()
        if not server.saved:
            raise CompanionError('SETUP_EXPIRED')
    finally:
        server.server_close()
