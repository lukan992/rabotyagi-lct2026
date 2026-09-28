#!/usr/bin/env bash
# Шлюз видео для разработки: mediamtx с настройками СтройКонтроля (infra/mediamtx/mediamtx.yml).
# Потоки камер заводит сам сервер (нужен запущенный бэкенд на :8100), браузер смотрит по WebRTC на :8889.
#
# Запуск:   bash tools/video-gateway.sh        Останов: Ctrl-C
set -euo pipefail
cd "$(dirname "$0")/.."
command -v mediamtx >/dev/null || { echo "Нет mediamtx. Установите: brew install mediamtx"; exit 1; }
exec mediamtx infra/mediamtx/mediamtx.yml
