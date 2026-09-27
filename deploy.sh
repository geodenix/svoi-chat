#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/svoi-chat
REPO=https://github.com/geodenix/svoi-chat.git

apt-get update
apt-get install -y git python3 python3-venv

if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  rm -rf "$APP_DIR"
  git clone "$REPO" "$APP_DIR"
fi

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"
mkdir -p "$APP_DIR/data"

cp "$APP_DIR/svoi-chat.service" /etc/systemd/system/svoi-chat.service
systemctl daemon-reload
systemctl enable --now svoi-chat.service
systemctl restart svoi-chat.service
systemctl --no-pager --full status svoi-chat.service
