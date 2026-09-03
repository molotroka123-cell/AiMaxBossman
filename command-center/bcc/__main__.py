"""``python -m bcc`` — тот же запуск, что и консольный скрипт ``bcc``.

Без этого файла модуль не запускался как пакет, и владельцу оставался только
установленный entry point; если пакет не установлен (`pip install -e`), войти
было нечем.
"""
from __future__ import annotations

from .app import main

if __name__ == "__main__":
    main()
