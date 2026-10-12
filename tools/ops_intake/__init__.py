"""Bossman Operations - request intake pilot (stdlib only; openpyxl optional for XLSX).

incoming requests (Telegram result.json / messages.html, CSV / JSONL leads)
  -> field extraction -> duplicate linking -> table + needs_review -> daily report + DRAFT notifications.

Nothing is sent, no CRM is written, no model is called. Run from the ``tools`` directory::

    python -m ops_intake.cli run --input export.json --out OUTDIR [--window-min 1440]
"""
SCHEMA = "bossman.ops-intake/1"
__version__ = "0.1.0"
