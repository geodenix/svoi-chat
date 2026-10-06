#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR=/opt/svoi-chat
REPO=https://github.com/geodenix/svoi-chat.git
GITHUB_REPO=geodenix/svoi-chat
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
  "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.lock.txt"
}

ensure_runtime_permissions() {
  if ! id daemon >/dev/null 2>&1; then
    log "Required runtime user daemon is missing"
    return 1
  fi

  chown -R daemon:daemon "$APP_DIR/data"

  local secret
  for secret in     /etc/svoi-chat.env     /etc/svoi-vapid-private.pem     "$APP_DIR/firebase-admin.json"
  do
    if [ -f "$secret" ]; then
      chown root:daemon "$secret"
      chmod 640 "$secret"
    fi
  done
}

verify_remote_commit() {
  local sha="$1"

  if [ "${SVOI_ALLOW_UNVERIFIED_DEPLOY:-0}" = "1" ]; then
    log "WARNING: CI/PR verification bypassed by SVOI_ALLOW_UNVERIFIED_DEPLOY=1"
    return 0
  fi

  local pulls_file checks_file state
  pulls_file="$(mktemp /tmp/svoi-pulls.XXXXXX.json)"
  checks_file="$(mktemp /tmp/svoi-checks.XXXXXX.json)"

  if ! curl -fsS --max-time 10       -H "Accept: application/vnd.github+json"       "https://api.github.com/repos/$GITHUB_REPO/commits/$sha/pulls"       -o "$pulls_file"; then
    rm -f "$pulls_file" "$checks_file"
    log "Unable to verify merged PR for $sha"
    return 1
  fi

  if ! python3 - "$sha" "$pulls_file" <<'PY'
import json
import sys

sha, path = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    pulls = json.load(handle)

valid = any(
    item.get("merged_at")
    and item.get("merge_commit_sha") == sha
    for item in pulls
)
raise SystemExit(0 if valid else 1)
PY
  then
    rm -f "$pulls_file" "$checks_file"
    log "Refusing deploy: $sha is not the merge commit of a merged PR"
    return 1
  fi

  for _ in $(seq 1 18); do
    if ! curl -fsS --max-time 10         -H "Accept: application/vnd.github+json"         "https://api.github.com/repos/$GITHUB_REPO/commits/$sha/check-runs"         -o "$checks_file"; then
      rm -f "$pulls_file" "$checks_file"
      log "Unable to verify CI checks for $sha"
      return 1
    fi

    state="$(
      python3 - "$checks_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    checks = json.load(handle).get("check_runs", [])

smoke = [item for item in checks if item.get("name") == "smoke"]
if any(
    item.get("status") == "completed"
    and item.get("conclusion") == "success"
    for item in smoke
):
    print("success")
elif any(
    item.get("status") == "completed"
    and item.get("conclusion") not in (None, "success")
    for item in smoke
):
    print("failed")
else:
    print("pending")
PY
    )"

    case "$state" in
      success)
        rm -f "$pulls_file" "$checks_file"
        log "Verified merged PR and successful smoke CI for $sha"
        return 0
        ;;
      failed)
        rm -f "$pulls_file" "$checks_file"
        log "Refusing deploy: smoke CI failed for $sha"
        return 1
        ;;
    esac

    sleep 5
  done

  rm -f "$pulls_file" "$checks_file"
  log "Refusing deploy: smoke CI did not finish successfully for $sha"
  return 1
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
  ensure_runtime_permissions || true
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

TARGET_SHA="$(git rev-parse origin/main)"
if [ "$TARGET_SHA" != "$PREVIOUS_SHA" ]; then
  if ! verify_remote_commit "$TARGET_SHA"; then
    log "Remote commit verification failed; no service changes were applied"
    exit 1
  fi
fi

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

if ! ensure_venv || ! validate_release || ! ensure_runtime_permissions; then
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
