"""Stage 8 — принудительный блок прямых сокетов мимо egress-прокси.

Переменные `http_proxy` — это ПРОСЬБА: их уважают curl/pip/git, но вредоносный
код просто откроет сокет напрямую. Здесь барьер, который нельзя проигнорировать:

- процесс песочницы исполняется под ВЫДЕЛЕННЫМ uid (не под uid ядра);
- nftables в output-хуке по `meta skuid` пропускает от этого uid ровно один
  адрес:порт (локальный CONNECT-прокси) и режет всё остальное.

Почему skuid, а не netns+veth: не требует `ip`/iproute2 и работает на любом
ядре с nftables. Правила живут в отдельной таблице на песочницу и сносятся
вместе с ней — ничего не остаётся в firewall хоста.

Fail closed: если nft недоступен или мы не root, `available()` = False, и
рантайм ЧЕСТНО объявляет `supports_allowlist=False` — тогда политика отвергнет
ALLOWLIST, а не выпустит процесс в открытую сеть.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

from .. import obs

log = obs.get_logger("bossman.sandbox.netguard")

# Диапазон служебных uid для песочниц. Намеренно высокий и узкий: это не
# системные пользователи и не реальные аккаунты хоста.
UID_BASE = 60000
UID_RANGE = 500

_SAFE_NAME = re.compile(r"[^a-z0-9_]")


def _table_name(sandbox_id: str) -> str:
    return "bossman_sbx_" + _SAFE_NAME.sub("", sandbox_id.lower())[:24]


def _nft(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Вызов nft строго массивом аргументов — без shell."""
    return subprocess.run(["nft", *args], capture_output=True, text=True, check=check)


def sandbox_uid(sandbox_id: str) -> int:
    """Детерминированный uid песочницы: одинаковый id → одинаковый uid."""
    h = 0
    for ch in sandbox_id:
        h = (h * 131 + ord(ch)) % UID_RANGE
    return UID_BASE + h


def available() -> bool:
    """Можем ли реально применить барьер: нужен root и рабочий nft."""
    if os.name != "posix" or os.geteuid() != 0 or shutil.which("nft") is None:
        return False
    probe = "bossman_sbx_probe"
    try:
        _nft("add", "table", "inet", probe)
        _nft("delete", "table", "inet", probe)
        return True
    except (subprocess.CalledProcessError, OSError):
        return False


class EgressLockdown:
    """Правила «только через прокси» для одной песочницы."""

    def __init__(self, sandbox_id: str) -> None:
        self.sandbox_id = sandbox_id
        self.table = _table_name(sandbox_id)
        self.uid = sandbox_uid(sandbox_id)
        self.applied = False

    def apply(self, proxy_host: str, proxy_port: int) -> None:
        """Разрешить этому uid только proxy_host:proxy_port, всё прочее — reject.

        Порядок важен: сначала accept на прокси, затем reject на всё остальное.
        """
        self.remove()  # чистый старт: остатков прошлого прогона быть не должно
        _nft("add", "table", "inet", self.table)
        _nft("add", "chain", "inet", self.table, "out",
             "{ type filter hook output priority 0; policy accept; }")
        _nft("add", "rule", "inet", self.table, "out",
             "meta", "skuid", str(self.uid),
             "ip", "daddr", proxy_host, "tcp", "dport", str(proxy_port), "accept")
        # Всё остальное от этого uid — отказ (быстрый, а не таймаут).
        _nft("add", "rule", "inet", self.table, "out",
             "meta", "skuid", str(self.uid), "reject")
        self.applied = True
        log.info("egress lockdown applied: sandbox=%s uid=%s via %s:%s",
                 self.sandbox_id, self.uid, proxy_host, proxy_port)

    def remove(self) -> None:
        """Снести таблицу песочницы. Идемпотентно: отсутствие — не ошибка."""
        try:
            _nft("delete", "table", "inet", self.table, check=False)
        except OSError:
            pass
        self.applied = False

    def rules(self) -> str:
        """Текущие правила (для тестов/диагностики)."""
        try:
            return _nft("list", "table", "inet", self.table, check=False).stdout
        except OSError:
            return ""
