#!/usr/bin/env bash
# start-bossman.sh — та же единственная точка входа для Linux/macOS.
# Порядок и решения идентичны start-bossman.ps1: Python → .venv → пакеты →
# доктор (BLOCKED останавливает запуск) → окно Command Center.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1

DOCTOR_ONLY=0; SKIP_INSTALL=0; EVENING=0; WEB=0
for arg in "$@"; do
  case "$arg" in
    --doctor-only) DOCTOR_ONLY=1 ;;
    --skip-install) SKIP_INSTALL=1 ;;
    --evening-test) EVENING=1 ;;
    --web) WEB=1 ;;
    --port=*) export BCC_PORT="${arg#*=}" ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "неизвестный аргумент: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$1"; }

step "Python"
PY=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,11) else 1)' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
[ -n "$PY" ] || { echo "    Не найден Python 3.11 или новее." >&2; exit 1; }
echo "    $PY ($("$PY" -V 2>&1))"

VENV_PY="$REPO/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  step "Создаю виртуальное окружение .venv"
  "$PY" -m venv "$REPO/.venv"
fi

if [ "$SKIP_INSTALL" -eq 0 ]; then
  step "Устанавливаю пакеты (первый запуск занимает несколько минут)"
  "$VENV_PY" -m pip install --upgrade pip --quiet
  "$VENV_PY" -m pip install --quiet -e . -e command-center -e bossman-core
fi

step "Предполётная проверка"
if ! "$VENV_PY" scripts/bossman_doctor.py --json-out doctor.json; then
  echo "    Есть BLOCKED-проверки: запуск остановлен намеренно." >&2
  echo "    Почините их и запустите снова. Подробности: doctor.json" >&2
  exit 1
fi
[ "$DOCTOR_ONLY" -eq 0 ] || exit 0

if [ "$EVENING" -eq 1 ]; then
  # Точка входа одна — обёртка по ТОЧНОМУ SHA: канонная ветка, чистое дерево,
  # совпадение с живым origin, доктор, самопроверка обоих харнессов и каталог
  # улик этого SHA. Отката на evening_acceptance.py нет: улики, не привязанные
  # к коммиту, неотличимы от улик другого дерева.
  if [ ! -f scripts/evening_owner_run.py ]; then
    echo "    Не найден scripts/evening_owner_run.py." >&2
    echo "    Отката на старый харнесс НЕТ: улики без точного SHA недействительны." >&2
    exit 1
  fi
  step "Вечерняя приёмка владельца (точный SHA)"
  echo "    ветка:     $(git branch --show-current)"
  echo "    SHA:       $(git rev-parse HEAD)"
  echo "    платформа: $(uname -srm)"
  echo "    runtime:   $("$VENV_PY" -c 'import platform;print(platform.python_version())')"
  exec "$VENV_PY" scripts/evening_owner_run.py run
fi

step "Открываю BOSSMAN Command Center"
ARGS=(-m bcc.desktop)
[ "$WEB" -eq 0 ] || ARGS+=(--web)
exec "$VENV_PY" "${ARGS[@]}"
