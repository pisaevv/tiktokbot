#!/usr/bin/env bash
# Подтягивает код из git, обновляет зависимости и yt-dlp, перезапускает сервис.
# Запускается еженедельно из cron, можно запускать руками от root.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME=tiktokbot

cd "$APP_DIR"
echo "[$(date -Is)] обновление"
if [[ -d .git ]]; then
    git pull --ff-only || echo "git pull не удался, обновляю только зависимости"
fi
.venv/bin/pip install -q --upgrade -r requirements.txt yt-dlp
systemctl restart "$SERVICE_NAME"
echo "[$(date -Is)] yt-dlp $(.venv/bin/yt-dlp --version), сервис перезапущен"
