#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR=/opt/svoi-chat
REPO=https://github.com/geodenix/svoi-chat.git
SERVICE=svoi-chat.service
HEALTH_URL=http://127.0.0.1:8010/health
BACKUP_DIR="$APP_DIR/data/backups"

log() {
  printf '[svoi-deploy] %s\n' "$*"
}

need_packages=()
command -v git >/dev/null 2>&1 || need_packages+=(git)
command -v curl >/dev/null 2>&1 || need_packages+=(curl)
command -v python3 >/dev/null 2>&1 || need_packages+=(python3 python3-venv)

if (( ${#need_packages[@]} )); then
  log "Installing missing packages: ${need_packages[*]}"
  apt-get update
  apt-get install -y "${need_packages[@]}"
fi

if [ ! -d "$APP_DIR/.git" ]; then
  if [ -e "$APP_DIR" ] && [ "$(find "$APP_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
    log "Refusing to delete non-empty $APP_DIR because it is not a git checkout."
    exit 1
  fi
  log "Cloning application"
  git clone "$REPO" "$APP_DIR"
fi

cd "$APP_DIR"
mkdir -p data "$BACKUP_DIR"

PREVIOUS_SHA="$(git rev-parse HEAD)"
BACKUP_FILE=""

backup_database() {
  local db="$APP_DIR/data/svoi.db"
  if [ ! -f "$db" ]; then
    return 0
  fi

  BACKUP_FILE="$BACKUP_DIR/svoi-$(date -u +%Y%m%d-%H%M%S).db"
  log "Creating SQLite backup: $BACKUP_FILE"
  python3 - "$db" "$BACKUP_FILE" <<'PY'
import sqlite3
import sys

source_path, backup_path = sys.argv[1], sys.argv[2]
source = sqlite3.connect(source_path, timeout=10)
backup = sqlite3.connect(backup_path, timeout=10)
try:
    source.execute("PRAGMA busy_timeout=10000")
    source.backup(backup)
    result = backup.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise RuntimeError(f"backup integrity_check failed: {result}")
finally:
    backup.close()
    source.close()
PY

  mapfile -t old_backups < <(ls -1t "$BACKUP_DIR"/svoi-*.db 2>/dev/null | tail -n +15 || true)
  if (( ${#old_backups[@]} )); then
    rm -f -- "${old_backups[@]}"
  fi
}

ensure_venv() {
  if [ ! -x "$APP_DIR/venv/bin/python" ]; then
    log "Creating Python virtual environment"
    python3 -m venv "$APP_DIR/venv"
  fi
  "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
}

check_frontend() {
  if ! command -v node >/dev/null 2>&1; then
    log "Node.js not found; skipping JavaScript syntax check"
    return 0
  fi

  local js_file
  js_file="$(mktemp /tmp/svoi-inline.XXXXXX.js)"
  python3 - "$APP_DIR/index.html" "$js_file" <<'PY'
import re
import sys
from pathlib import Path

html = Path(sys.argv[1]).read_text(encoding="utf-8")
blocks = re.findall(
    r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
    html,
    flags=re.IGNORECASE | re.DOTALL,
)
Path(sys.argv[2]).write_text("\n".join(blocks), encoding="utf-8")
PY
  if ! node --check "$js_file"; then
    rm -f "$js_file"
    return 1
  fi
  rm -f "$js_file"
  return 0
}

validate_release() {
  log "Validating Python"
  "$APP_DIR/venv/bin/python" -m py_compile "$APP_DIR/app.py"

  log "Running clean-database startup smoke test"
  local smoke_dir
  smoke_dir="$(mktemp -d)"
  if ! SVOI_DATA_DIR="$smoke_dir" "$APP_DIR/venv/bin/python" - <<'PY'
import app
app.init_db()
print("BACKEND_SMOKE_OK")
PY
  then
    rm -rf "$smoke_dir"
    return 1
  fi
  rm -rf "$smoke_dir"

  log "Validating frontend JavaScript"
  check_frontend
}

health_ok() {
  local require_database="${1:-yes}"
  local body
  for _ in $(seq 1 20); do
    body="$(curl -fsS --max-time 3 "$HEALTH_URL" 2>/dev/null || true)"
    if printf '%s' "$body" | grep -q '"status":"ok"'; then
      if [ "$require_database" = "no" ] ||
         printf '%s' "$body" | grep -q '"database":"ok"'; then
        curl -fsS --max-time 3 http://127.0.0.1:8010/ >/dev/null
        return 0
      fi
    fi
    sleep 1
  done
  return 1
}

rollback() {
  log "Deployment failed. Rolling back to $PREVIOUS_SHA"
  git reset --hard "$PREVIOUS_SHA"
  ensure_venv
  cp "$APP_DIR/svoi-chat.service" /etc/systemd/system/svoi-chat.service
  systemctl daemon-reload
  systemctl restart "$SERVICE"

  if health_ok no; then
    log "Rollback successful"
  else
    log "Rollback completed, but health check is still failing"
    journalctl -u "$SERVICE" -n 80 --no-pager || true
    exit 2
  fi
}

backup_database

log "Fetching latest main branch"
git fetch origin main

if ! git merge --ff-only origin/main; then
  log "Fast-forward update failed; no service changes were applied"
  git reset --hard "$PREVIOUS_SHA"
  exit 1
fi

FETCHED_SHA="$(git rev-parse HEAD)"
if [ "$FETCHED_SHA" = "$PREVIOUS_SHA" ]; then
  log "No code changes detected: $FETCHED_SHA"
  if health_ok; then
    log "Service is already healthy; skipping restart"
    exit 0
  fi

  log "No code changes, but health check failed; restarting service for recovery"
  if ! systemctl restart "$SERVICE"; then
    log "Recovery restart failed"
    exit 1
  fi
  if health_ok; then
    log "Service recovered successfully"
    exit 0
  fi

  journalctl -u "$SERVICE" -n 80 --no-pager || true
  log "Service is still unhealthy after recovery restart"
  exit 1
fi

if ! ensure_venv || ! validate_release; then
  rollback
  exit 1
fi

log "Installing systemd unit"
cp "$APP_DIR/svoi-chat.service" /etc/systemd/system/svoi-chat.service
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1 || true

log "Restarting service"
if ! systemctl restart "$SERVICE"; then
  rollback
  exit 1
fi

log "Waiting for health check"
if ! health_ok; then
  journalctl -u "$SERVICE" -n 80 --no-pager || true
  rollback
  exit 1
fi

NEW_SHA="$(git rev-parse HEAD)"
log "Deployment successful: $PREVIOUS_SHA -> $NEW_SHA"
if [ -n "$BACKUP_FILE" ]; then
  log "Database backup: $BACKUP_FILE"
fi
