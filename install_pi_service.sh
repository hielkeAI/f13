#!/usr/bin/env bash
set -euo pipefail

# Install the Discord bot as a systemd service on a Raspberry Pi (or any Linux box).
# Run this from the bot project directory:
#   sudo ./install_pi_service.sh

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="youtube-subscriber-discord-bot"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
SERVICE_USER="${SUDO_USER:-$(id -un)}"

if [ ! -f "${BOT_DIR}/bot.py" ]; then
  echo "bot.py not found in ${BOT_DIR}" >&2
  exit 1
fi

if [ ! -f "${BOT_DIR}/.env" ]; then
  echo "Missing ${BOT_DIR}/.env" >&2
  exit 1
fi

echo "Using project directory: ${BOT_DIR}"
echo "Service user: ${SERVICE_USER}"

if [ ! -x "${BOT_DIR}/.venv/bin/python" ]; then
  echo "Creating virtual environment..."
  python3 -m venv "${BOT_DIR}/.venv"
fi

echo "Installing Python dependencies..."
"${BOT_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${BOT_DIR}/.venv/bin/python" -m pip install -r "${BOT_DIR}/requirements.txt"

sudo chown -R "${SERVICE_USER}:${SERVICE_USER}" "${BOT_DIR}" 2>/dev/null || true

echo "Writing systemd unit: ${UNIT_FILE}"
sudo tee "${UNIT_FILE}" > /dev/null <<EOF
[Unit]
Description=YouTube subscriber Discord bot
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${BOT_DIR}
EnvironmentFile=${BOT_DIR}/.env
ExecStart=${BOT_DIR}/.venv/bin/python ${BOT_DIR}/bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading systemd and enabling the service..."
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

echo
echo "Done. Check status with:"
echo "  sudo systemctl status ${SERVICE_NAME}"
echo "Follow logs with:"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
EOF