#!/usr/bin/env bash
# Ставит бота как systemd-сервис на Ubuntu или Debian.
#
# Запускать от root из каталога с репозиторием:
#   git clone <repo> /opt/tiktokbot && cd /opt/tiktokbot && sudo bash deploy/install.sh
#
# Повторный запуск безопасен: обновит зависимости и перезапустит сервис.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER=tiktokbot
SERVICE_NAME=tiktokbot

if [[ $EUID -ne 0 ]]; then
    echo "Запусти от root: sudo bash deploy/install.sh" >&2
    exit 1
fi

echo "==> Системные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv ffmpeg git cron >/dev/null

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "Нужен Python 3.10 или новее, в системе $(python3 --version)." >&2
    exit 1
fi

echo "==> Пользователь $SERVICE_USER"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

echo "==> Зависимости в $APP_DIR/.venv"
cd "$APP_DIR"
[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [[ ! -f .env ]]; then
    cp .env.example .env
    if [[ -t 0 ]]; then
        read -rp "Вставь BOT_TOKEN от @BotFather: " token
        sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=${token}|" .env
    else
        echo "Создал .env из .env.example. Впиши BOT_TOKEN в $APP_DIR/.env и запусти скрипт ещё раз." >&2
        exit 1
    fi
fi
if grep -q '^BOT_TOKEN=123456:ABC-DEF' .env || ! grep -q '^BOT_TOKEN=.\+' .env; then
    echo "В $APP_DIR/.env не заполнен BOT_TOKEN." >&2
    exit 1
fi
# Репозиторий остаётся у root, сервису нужно только читать код и .env.
chown root:"$SERVICE_USER" .env
chmod 640 .env

echo "==> systemd-сервис $SERVICE_NAME"
sed "s|__APP_DIR__|$APP_DIR|g" deploy/tiktokbot.service > "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"

echo "==> Еженедельное обновление yt-dlp"
cat > /etc/cron.weekly/tiktokbot-update <<CRON
#!/bin/sh
exec "$APP_DIR/deploy/update.sh" >> /var/log/tiktokbot-update.log 2>&1
CRON
chmod +x /etc/cron.weekly/tiktokbot-update
chmod +x "$APP_DIR/deploy/update.sh"

sleep 2
systemctl --no-pager --lines=5 status "$SERVICE_NAME" || true
cat <<MSG

Готово. Полезные команды:
  journalctl -u $SERVICE_NAME -f          логи в реальном времени
  systemctl restart $SERVICE_NAME         перезапуск
  $APP_DIR/deploy/update.sh               обновить код и yt-dlp прямо сейчас
MSG
