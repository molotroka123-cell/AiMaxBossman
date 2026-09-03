#!/bin/sh
# Однократная установка ярлыка BOSSMAN (Linux/macOS).
set -e
cd "$(dirname "$0")/../.."
python3 -m bcc.desktop --install-shortcut
