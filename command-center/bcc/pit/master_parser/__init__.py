"""Jeff Master Parser: every conversation Jeff collected -> one corpus -> passports.

``bossman pit master-parse [--participant X] [--since 7d|ISO] [--dry-run]``;
the Bossman window page «Jeff · паспорта» and the пульт command ``/parse`` start
the same run. See ``engine.py`` for the contract and ``sources.py`` for the
list of sources.
"""
from .engine import (  # noqa: F401
    AlreadyRunning,
    Options,
    is_running,
    parse_since,
    parser_home,
    read_report,
    read_status,
    resolve_settings,
    revert_run,
    run_master_parse,
)
