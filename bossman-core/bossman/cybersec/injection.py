"""Prompt Injection Firewall — фильтр НЕДОВЕРЕННОГО текста на границе контекста.

Модель — не периметр. Недоверенный текст (веб-страница, репозиторий, память,
вывод инструмента, сообщение в чате) проходит через этот фильтр ДО того, как
попадёт в контекст, и НИКОГДА не получает авторитет от собственного содержания.

Что делает firewall:
* классифицирует попытки перехвата авторитета (игнорируй инструкции, ты теперь…,
  запусти shell, отдай ключ, одобри за меня, повысь права);
* понижает результат до данных: возвращает обезвреженный текст, обёрнутый как
  недоверенный, + вердикт;
* НИКОГДА не исполняет и не «выполняет» найденное.

Firewall не заменяет Policy/Approval: он лишь не даёт тексту притвориться ими.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .trust import TrustLevel


@dataclass(frozen=True)
class InjectionFinding:
    pattern_id: str
    severity: str          # low | medium | high | critical
    excerpt: str


@dataclass(frozen=True)
class FirewallVerdict:
    safe: bool
    findings: tuple[InjectionFinding, ...] = ()
    sanitized: str = ""
    effective_trust: TrustLevel = TrustLevel.UNTRUSTED

    @property
    def severity(self) -> str:
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        if not self.findings:
            return "none"
        return max((f.severity for f in self.findings), key=lambda s: order.get(s, 0))


# (id, regex, severity). Порядок не важен — считаем все совпадения.
_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("ignore_previous", re.compile(
        r"\b(ignore|disregard|forget)\b[^.\n]{0,40}\b(previous|prior|earlier|above|all)\b"
        r"[^.\n]{0,30}\b(instruction|prompt|rule|context)", re.S | re.I), "high"),
    ("role_override", re.compile(
        r"\byou\s+are\s+now\b|\bfrom\s+now\s+on\s+you\b|\bact\s+as\s+(?:an?\s+)?"
        r"(?:admin|root|owner|developer\s+mode)", re.S | re.I), "high"),
    ("authority_spoof", re.compile(
        r"\b(owner|admin|system|policy)\b[^.\n]{0,20}\b(say|says|said|approved|authorou?ized|grants?)\b"
        r"|\bthis\s+is\s+(?:the\s+)?(?:owner|admin|system)\b", re.S | re.I), "critical"),
    ("approval_bypass", re.compile(
        r"\b(skip|bypass|no\s+need\s+for|without)\b[^.\n]{0,25}\b(approval|confirmation|permission)\b",
        re.S | re.I), "critical"),
    ("secret_exfil", re.compile(
        r"\b(reveal|show|print|send|leak|exfiltrate|give\s+me)\b[^.\n]{0,30}"
        r"(?:\b(?:api[_\s-]?key|secret|token|password|credential|private\s+key)\b|(?<![\w.])\.env\b)",
        re.S | re.I), "critical"),
    ("shell_request", re.compile(
        r"\b(run|execute|exec|spawn)\b[^.\n]{0,20}\b(shell|bash|sh|cmd|powershell|command)\b"
        r"|\brm\s+-rf\b|\bcurl\b[^\n]{0,40}\|\s*(?:sh|bash)\b", re.S | re.I), "critical"),
    ("scope_escalation", re.compile(
        r"\b(grant|give|elevate|escalate|raise)\b[^.\n]{0,25}\b(scope|privilege|permission|admin|root)\b",
        re.S | re.I), "critical"),
    ("tool_result_spoof", re.compile(
        r"\b(tool|function|system)\s*(?:output|result|response)\s*[:=]\s*\{?\s*[\"']?"
        r"(?:ok|success|approved|true)\b", re.S | re.I), "medium"),
    ("memory_poison", re.compile(
        r"\b(remember|store|save|persist)\b[^.\n]{0,30}\b(forever|permanently|as\s+a\s+rule|always)\b",
        re.S | re.I), "medium"),
    ("hidden_channel", re.compile(
        r"<!--.{0,200}?(?:ignore|instruction|system|prompt).{0,200}?-->"
        r"|[​‌‍⁠]{3,}", re.S | re.I), "medium"),
)

_MARK_OPEN = "<<<UNTRUSTED_CONTENT>>>"
_MARK_CLOSE = "<<<END_UNTRUSTED_CONTENT>>>"


def scan(text: str) -> tuple[InjectionFinding, ...]:
    """Найти попытки инъекции. Чистая функция, без сети и моделей."""
    if not text:
        return ()
    out: list[InjectionFinding] = []
    for pid, rx, sev in _RULES:
        m = rx.search(text)
        if m:
            frag = m.group(0)
            out.append(InjectionFinding(pid, sev, frag[:160]))
    return tuple(out)


def sanitize(text: str) -> str:
    """Обезвредить: пометить как недоверенные данные и снять управляющие маркеры.

    Не «чистим смысл» — модель всё равно увидит текст, но увидит его как ДАННЫЕ
    внутри явной рамки, а не как инструкции.
    """
    body = (text or "")
    body = body.replace(_MARK_OPEN, "").replace(_MARK_CLOSE, "")
    body = re.sub(r"[​‌‍⁠]", "", body)          # zero-width каналы
    body = re.sub(r"(?is)<!--(.*?)-->", r"[html-comment-removed]", body)
    return f"{_MARK_OPEN}\n{body}\n{_MARK_CLOSE}"


def inspect(text: str, *, source_trust: TrustLevel = TrustLevel.UNTRUSTED) -> FirewallVerdict:
    """Главная точка входа. Возвращает вердикт и ОБЕЗВРЕЖЕННЫЙ текст.

    Инвариант: `effective_trust` никогда не поднимается выше `source_trust`.
    Содержимое не может повысить собственный авторитет.
    """
    findings = scan(text)
    safe = not any(f.severity in {"high", "critical"} for f in findings)
    return FirewallVerdict(safe=safe, findings=findings, sanitized=sanitize(text),
                           effective_trust=source_trust)
