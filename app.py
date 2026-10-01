import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Set

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field
from pywebpush import WebPushException, webpush
from livekit import api as livekit_api
from livekit.protocol.room import RoomConfiguration

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("SVOI_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "svoi.db"
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
THUMB_DIR = UPLOAD_DIR / "_thumbs"
THUMB_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
VIDEO_OPTIMIZE_TRIGGER_BYTES = 5 * 1024 * 1024
VIDEO_OPTIMIZE_MIN_SAVING_RATIO = 0.90
AVATAR_MAX_BYTES = 5 * 1024 * 1024
INLINE_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
AVATAR_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
VAPID_PRIVATE_KEY_FILE = os.getenv(
    "VAPID_PRIVATE_KEY_FILE",
    "/etc/svoi-vapid-private.pem",
)
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "").strip()
VAPID_SUBJECT = os.getenv(
    "VAPID_SUBJECT",
    "mailto:admin@epl-gruz.duckdns.org",
).strip()
TURN_SHARED_SECRET = os.getenv("TURN_SHARED_SECRET", "").strip()
TURN_HOST = os.getenv(
    "TURN_HOST",
    "epl-gruz.duckdns.org",
).strip()
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "").strip()
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "").strip()
LIVEKIT_WS_URL = os.getenv(
    "LIVEKIT_WS_URL",
    "wss://epl-gruz.duckdns.org",
).strip()
SERVER_ADMIN_USERNAMES = {
    value.strip().lower()
    for value in os.getenv("SVOI_SERVER_ADMINS", "").split(",")
    if value.strip()
}
SERVER_ADMIN_IDS = {
    int(value.strip())
    for value in os.getenv("SVOI_SERVER_ADMIN_IDS", "").split(",")
    if value.strip().isdigit()
}
APP_STARTED_AT = time.time()

app = FastAPI(title="Свои", version="0.1.0")
app.add_middleware(GZipMiddleware, minimum_size=1024)
connections: Dict[int, Set[WebSocket]] = {}
ws_connection_state: dict[WebSocket, dict] = {}
user_event_seq: dict[int, int] = {}
user_event_buffer: dict[int, deque] = {}
WS_EVENT_BUFFER_LIMIT = 500
WS_EVENT_TTL_SECONDS = 180.0
WS_BATCH_DELAY_SECONDS = 0.025
WS_BATCH_MAX_EVENTS = 24
WS_IMMEDIATE_TYPES = {
    "call_offer", "call_answer", "call_video_offer", "call_video_answer",
    "call_video_state", "call_mute", "ice_candidate", "call_reject",
    "call_end", "call_unavailable", "private_call_room_upgrade",
    "group_call_invite", "group_force_mute", "group_force_mute_sent",
}
active_calls: dict[str, dict] = {}
call_invite_links: dict[str, dict] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect_db():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=5.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def db():
    conn = connect_db()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    conn = connect_db()
    conn.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT NOT NULL UNIQUE,
      display_name TEXT NOT NULL,
      password_hash TEXT NOT NULL,
      salt TEXT NOT NULL,
      recovery_code_hash TEXT,
      phone_hash TEXT,
      phone_last4 TEXT,
      phone_linked_at TEXT,
      phone_verified_at TEXT,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sessions (
      token_hash TEXT PRIMARY KEY,
      session_id TEXT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      device_label TEXT,
      user_agent TEXT,
      created_at TEXT NOT NULL,
      last_seen_at TEXT
    );
    CREATE TABLE IF NOT EXISTS contacts (
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      contact_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL,
      PRIMARY KEY(user_id, contact_user_id),
      CHECK(user_id <> contact_user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_contacts_contact
      ON contacts(contact_user_id);
    CREATE TABLE IF NOT EXISTS phone_verifications (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      phone_hash TEXT NOT NULL,
      phone_last4 TEXT NOT NULL,
      code_hash TEXT NOT NULL,
      code_salt TEXT NOT NULL,
      expires_at TEXT NOT NULL,
      sent_at TEXT NOT NULL,
      attempts INTEGER NOT NULL DEFAULT 0,
      used_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_phone_verifications_user
      ON phone_verifications(user_id, id DESC);
    CREATE INDEX IF NOT EXISTS idx_phone_verifications_sent
      ON phone_verifications(user_id, sent_at);
    CREATE TABLE IF NOT EXISTS user_blocks (
      blocker_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      blocked_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL,
      PRIMARY KEY(blocker_id, blocked_id),
      CHECK(blocker_id <> blocked_id)
    );
    CREATE INDEX IF NOT EXISTS idx_user_blocks_blocked
      ON user_blocks(blocked_id);
    CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      body TEXT NOT NULL,
      created_at TEXT NOT NULL,
      edited_at TEXT,
      client_message_id TEXT,
      reply_to_message_id INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_messages_pair
      ON messages(sender_id, recipient_id, id);
    CREATE INDEX IF NOT EXISTS idx_messages_recipient_pair
      ON messages(recipient_id, sender_id, id);
    CREATE TABLE IF NOT EXISTS chat_groups (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS group_members (
      group_id INTEGER NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      joined_at TEXT NOT NULL,
      PRIMARY KEY(group_id, user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_group_members_user
      ON group_members(user_id, group_id);
    CREATE TABLE IF NOT EXISTS group_messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      group_id INTEGER NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
      sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      body TEXT NOT NULL,
      created_at TEXT NOT NULL,
      edited_at TEXT,
      client_message_id TEXT,
      reply_to_message_id INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_group_messages
      ON group_messages(group_id, id);
    CREATE TABLE IF NOT EXISTS group_message_mentions (
      message_id INTEGER NOT NULL REFERENCES group_messages(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      PRIMARY KEY(message_id, user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_group_message_mentions_user
      ON group_message_mentions(user_id, message_id);
    CREATE TABLE IF NOT EXISTS message_hidden_by_user (
      message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      hidden_at TEXT NOT NULL,
      PRIMARY KEY(message_id, user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_message_hidden_user
      ON message_hidden_by_user(user_id, message_id);
    CREATE TABLE IF NOT EXISTS group_message_hidden_by_user (
      message_id INTEGER NOT NULL REFERENCES group_messages(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      hidden_at TEXT NOT NULL,
      PRIMARY KEY(message_id, user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_group_message_hidden_user
      ON group_message_hidden_by_user(user_id, message_id);
    CREATE TABLE IF NOT EXISTS group_message_reads (
      message_id INTEGER NOT NULL REFERENCES group_messages(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      read_at TEXT NOT NULL,
      PRIMARY KEY(message_id, user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_group_message_reads_message
      ON group_message_reads(message_id, read_at);
    CREATE TABLE IF NOT EXISTS group_message_receipts (
      message_id INTEGER NOT NULL REFERENCES group_messages(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      delivered_at TEXT,
      read_at TEXT,
      PRIMARY KEY(message_id, user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_group_message_receipts_message
      ON group_message_receipts(message_id, delivered_at, read_at);
    CREATE INDEX IF NOT EXISTS idx_group_message_receipts_user
      ON group_message_receipts(user_id, message_id);
    CREATE TABLE IF NOT EXISTS uploads (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      stored_name TEXT NOT NULL UNIQUE,
      original_name TEXT NOT NULL,
      mime_type TEXT NOT NULL,
      size INTEGER NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS chat_backgrounds (
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      chat_type TEXT NOT NULL,
      chat_id INTEGER NOT NULL,
      upload_id INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
      updated_at TEXT NOT NULL,
      PRIMARY KEY(user_id, chat_type, chat_id),
      CHECK(chat_type IN ('user','group'))
    );
    CREATE TABLE IF NOT EXISTS chat_mutes (
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      chat_type TEXT NOT NULL,
      chat_id INTEGER NOT NULL,
      muted_until TEXT,
      updated_at TEXT NOT NULL,
      PRIMARY KEY(user_id, chat_type, chat_id),
      CHECK(chat_type IN ('user','group'))
    );
    CREATE INDEX IF NOT EXISTS idx_chat_mutes_user
      ON chat_mutes(user_id, chat_type, chat_id);
    CREATE TABLE IF NOT EXISTS android_push_tokens (
      token TEXT PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_android_push_tokens_user
      ON android_push_tokens(user_id);
    CREATE TABLE IF NOT EXISTS push_subscriptions (
      endpoint TEXT PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      p256dh TEXT NOT NULL,
      auth TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user
      ON push_subscriptions(user_id);
    CREATE TABLE IF NOT EXISTS call_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      call_id TEXT NOT NULL UNIQUE,
      caller_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      callee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      video INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL,
      started_at TEXT NOT NULL,
      answered_at TEXT,
      ended_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_call_history_caller
      ON call_history(caller_id, id DESC);
    CREATE INDEX IF NOT EXISTS idx_call_history_callee
      ON call_history(callee_id, id DESC);
    CREATE TABLE IF NOT EXISTS call_quality_samples (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      call_key TEXT NOT NULL,
      call_type TEXT NOT NULL,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      peer_id INTEGER,
      group_id INTEGER,
      rtt_ms REAL,
      packet_loss_pct REAL,
      jitter_ms REAL,
      available_outgoing_bitrate REAL,
      video_bitrate_bps REAL,
      video_width INTEGER,
      video_height INTEGER,
      video_fps REAL,
      connection_quality TEXT,
      end_reason TEXT,
      recorded_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_call_quality_key
      ON call_quality_samples(call_key, recorded_at);
    CREATE INDEX IF NOT EXISTS idx_call_quality_time
      ON call_quality_samples(recorded_at DESC);
    CREATE INDEX IF NOT EXISTS idx_call_quality_user
      ON call_quality_samples(user_id, recorded_at DESC);
    CREATE TABLE IF NOT EXISTS client_errors (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      kind TEXT NOT NULL,
      message TEXT NOT NULL,
      source TEXT,
      line_no INTEGER,
      column_no INTEGER,
      stack TEXT,
      page_path TEXT,
      client_type TEXT,
      app_version TEXT,
      app_version_code INTEGER,
      user_agent TEXT,
      network_type TEXT,
      effective_type TEXT,
      downlink_mbps REAL,
      network_rtt_ms REAL,
      online INTEGER NOT NULL DEFAULT 1,
      context TEXT,
      fingerprint TEXT,
      recorded_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_client_errors_time
      ON client_errors(recorded_at DESC);
    CREATE INDEX IF NOT EXISTS idx_client_errors_user
      ON client_errors(user_id, recorded_at DESC);
    CREATE INDEX IF NOT EXISTS idx_client_errors_fingerprint
      ON client_errors(user_id, fingerprint, recorded_at DESC);
    CREATE TABLE IF NOT EXISTS online_samples (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      recorded_at TEXT NOT NULL,
      online_count INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_online_samples_time
      ON online_samples(recorded_at);
    """)
    session_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
    }
    if "session_id" not in session_columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN session_id TEXT")
    if "device_label" not in session_columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN device_label TEXT")
    if "user_agent" not in session_columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN user_agent TEXT")
    if "last_seen_at" not in session_columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN last_seen_at TEXT")

    legacy_sessions = conn.execute(
        "SELECT token_hash FROM sessions WHERE session_id IS NULL OR session_id=''"
    ).fetchall()
    for legacy in legacy_sessions:
        conn.execute(
            "UPDATE sessions SET session_id=?,last_seen_at=COALESCE(last_seen_at,created_at) WHERE token_hash=?",
            (secrets.token_urlsafe(12), legacy["token_hash"]),
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_user_seen ON sessions(user_id,last_seen_at DESC)"
    )

    for table in ("messages", "group_messages"):
        columns = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "attachment_id" not in columns:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN attachment_id INTEGER"
            )
        if "client_message_id" not in columns:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN client_message_id TEXT"
            )
        if "reply_to_message_id" not in columns:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN reply_to_message_id INTEGER"
            )
        if "edited_at" not in columns:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN edited_at TEXT"
            )
        if table == "messages":
            if "delivered_at" not in columns:
                conn.execute(
                    "ALTER TABLE messages ADD COLUMN delivered_at TEXT"
                )
            if "read_at" not in columns:
                conn.execute(
                    "ALTER TABLE messages ADD COLUMN read_at TEXT"
                )
            if "forwarded" not in columns:
                conn.execute(
                    "ALTER TABLE messages ADD COLUMN forwarded INTEGER NOT NULL DEFAULT 0"
                )
        if table == "group_messages":
            if "deleted_at" not in columns:
                conn.execute(
                    "ALTER TABLE group_messages ADD COLUMN deleted_at TEXT"
                )
            if "deleted_by" not in columns:
                conn.execute(
                    "ALTER TABLE group_messages ADD COLUMN deleted_by INTEGER"
                )
            if "forwarded" not in columns:
                conn.execute(
                    "ALTER TABLE group_messages ADD COLUMN forwarded INTEGER NOT NULL DEFAULT 0"
                )

    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_messages_unread_recipient
           ON messages(recipient_id, sender_id, id)
           WHERE read_at IS NULL"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_client_message
           ON messages(sender_id, client_message_id)
           WHERE client_message_id IS NOT NULL"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_group_messages_client_message
           ON group_messages(sender_id, client_message_id)
           WHERE client_message_id IS NOT NULL"""
    )

    user_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(users)").fetchall()
    }
    if "avatar_id" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN avatar_id INTEGER")
    if "last_seen_at" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN last_seen_at TEXT")
    if "recovery_code_hash" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN recovery_code_hash TEXT")
    if "phone_hash" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN phone_hash TEXT")
    if "phone_last4" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN phone_last4 TEXT")
    if "phone_linked_at" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN phone_linked_at TEXT")
    if "phone_verified_at" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN phone_verified_at TEXT")

    # Legacy phone links created before SMS verification are not trusted.
    # Pending SMS verification is stored in phone_verifications instead.
    conn.execute(
        """UPDATE users
           SET phone_hash=NULL,
               phone_last4=NULL,
               phone_linked_at=NULL
           WHERE phone_verified_at IS NULL
             AND phone_hash IS NOT NULL"""
    )

    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_hash_unique
           ON users(phone_hash)
           WHERE phone_hash IS NOT NULL"""
    )

    group_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(chat_groups)").fetchall()
    }
    if "avatar_id" not in group_columns:
        conn.execute("ALTER TABLE chat_groups ADD COLUMN avatar_id INTEGER")

    member_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(group_members)").fetchall()
    }
    if "is_admin" not in member_columns:
        conn.execute(
            "ALTER TABLE group_members ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute(
        """UPDATE group_members
           SET is_admin=1
           WHERE EXISTS(
             SELECT 1 FROM chat_groups g
             WHERE g.id=group_members.group_id
               AND g.owner_id=group_members.user_id
           )"""
    )

    conn.commit()
    conn.close()


@app.on_event("startup")
def startup():
    init_db()


def hash_password(password: str, salt_hex: str | None = None):
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return digest.hex(), salt.hex()


def verify_password(password: str, expected: str, salt: str) -> bool:
    digest, _ = hash_password(password, salt)
    return secrets.compare_digest(digest, expected)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_recovery_code(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value or "").upper()


def recovery_code_hash(value: str) -> str:
    normalized = normalize_recovery_code(value)
    return hashlib.sha256(
        ("svoi-recovery-v1:" + normalized).encode()
    ).hexdigest()


def make_recovery_code() -> str:
    raw = secrets.token_hex(12).upper()
    return "-".join(
        raw[index:index + 4]
        for index in range(0, len(raw), 4)
    )


def normalize_phone_digits(value: str) -> str:
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)

    if raw.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        # Default for the current Russian-language deployment.
        digits = "7" + digits

    if not 8 <= len(digits) <= 15:
        raise HTTPException(
            400,
            "Укажи номер в международном формате, например +79991234567",
        )
    return digits


def phone_lookup_hash(value: str) -> tuple[str, str]:
    digits = normalize_phone_digits(value)
    digest = hashlib.sha256(
        ("svoi-phone-v1:" + digits).encode()
    ).hexdigest()
    return digest, digits[-4:]


def request_session_meta(request: Request | None) -> tuple[str, str]:
    if request is None:
        return "Неизвестное устройство", ""
    raw_label = str(request.headers.get("x-svoi-device") or "").strip()[:120]
    user_agent = str(request.headers.get("user-agent") or "").strip()[:500]
    platform, separator, mode = raw_label.partition("|")
    platform = re.sub(r"[^A-Za-z0-9 ./_-]", "", platform).strip()[:60]
    mode = re.sub(r"[^A-Za-z0-9_-]", "", mode).strip().lower()[:24] if separator else ""
    mode_labels = {
        "app": "приложение",
        "pwa": "PWA",
        "browser": "браузер",
    }
    if platform:
        device_label = platform
        if mode in mode_labels:
            device_label += " · " + mode_labels[mode]
    else:
        device_label = "Браузер"
    return device_label, user_agent


def make_session(
    conn,
    user_id: int,
    request: Request | None = None,
) -> str:
    token = secrets.token_urlsafe(32)
    now = now_iso()
    device_label, user_agent = request_session_meta(request)
    conn.execute(
        """INSERT INTO sessions(
             token_hash,session_id,user_id,device_label,user_agent,created_at,last_seen_at
           ) VALUES(?,?,?,?,?,?,?)""",
        (
            token_hash(token),
            secrets.token_urlsafe(12),
            user_id,
            device_label,
            user_agent,
            now,
            now,
        ),
    )
    conn.commit()
    return token


def user_json(row):
    keys = set(row.keys())
    stored = row["avatar_stored_name"] if "avatar_stored_name" in keys else None
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "avatar_url": f"/uploads/{stored}" if stored else None,
    }


def group_json(row):
    keys = set(row.keys())
    stored = row["avatar_stored_name"] if "avatar_stored_name" in keys else None
    data = {
        "id": row["id"],
        "name": row["name"],
        "owner_id": row["owner_id"],
        "created_at": row["created_at"],
        "avatar_url": f"/uploads/{stored}" if stored else None,
    }
    if "member_count" in keys:
        data["member_count"] = row["member_count"]
    if "is_admin" in keys:
        data["is_admin"] = bool(row["is_admin"])
    if "unread_count" in keys:
        data["unread_count"] = int(row["unread_count"] or 0)
    if "muted" in keys:
        data["muted"] = bool(row["muted"])
    if "muted_until" in keys:
        data["muted_until"] = row["muted_until"]
    return data


def get_user_from_token(conn, token: str):
    row = conn.execute(
        """SELECT u.id,u.username,u.display_name,
                  a.stored_name AS avatar_stored_name
           FROM sessions s
           JOIN users u ON u.id=s.user_id
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE s.token_hash=?""",
        (token_hash(token),),
    ).fetchone()
    return row


def current_user(
    authorization: str | None = Header(default=None),
    conn=Depends(db),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Нужна авторизация")
    token = authorization[7:]
    row = get_user_from_token(conn, token)
    if not row:
        raise HTTPException(401, "Сессия недействительна")
    return row


def is_server_admin(user) -> bool:
    try:
        user_id = int(user["id"])
        username = str(user["username"]).strip().lower()
    except Exception:
        return False
    return (
        user_id in SERVER_ADMIN_IDS
        or username in SERVER_ADMIN_USERNAMES
    )


def require_server_admin(user=Depends(current_user)):
    if not is_server_admin(user):
        raise HTTPException(403, "Доступ только для администратора сервера")
    return user


def _memory_snapshot() -> dict:
    values = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            parts = raw.strip().split()
            if parts:
                values[key] = int(parts[0]) * 1024
    except Exception:
        pass

    total = int(values.get("MemTotal", 0))
    available = int(values.get("MemAvailable", 0))
    used = max(0, total - available) if total else 0
    return {
        "total": total,
        "used": used,
        "available": available,
        "percent": round((used * 100 / total), 1) if total else None,
    }


def _process_rss_bytes() -> int:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except Exception:
        pass
    return 0


def _service_state(name: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        state = (result.stdout or result.stderr or "unknown").strip()
        return state or "unknown"
    except Exception:
        return "unknown"


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=6, max_length=128)


class LoginIn(BaseModel):
    username: str
    password: str


class RecoveryCodeCreateIn(BaseModel):
    current_password: str = Field(min_length=6, max_length=128)


class PasswordRecoverIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    recovery_code: str = Field(min_length=12, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class PhoneLinkIn(BaseModel):
    phone: str = Field(min_length=8, max_length=40)
    current_password: str = Field(min_length=6, max_length=128)


class PhoneUnlinkIn(BaseModel):
    current_password: str = Field(min_length=6, max_length=128)


class PhoneContactSyncIn(BaseModel):
    hashes: list[str] = Field(default_factory=list)


class MessageIn(BaseModel):
    recipient_id: int
    body: str = Field(default="", max_length=4000)
    attachment_id: int | None = None
    client_message_id: str | None = Field(default=None, max_length=80)
    reply_to_message_id: int | None = None


class GroupCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    member_ids: list[int] = Field(default_factory=list)


class GroupRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class AdminUserRenameIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=60)


class AdminUsernameRenameIn(BaseModel):
    username: str = Field(min_length=3, max_length=33)


class GroupMemberAddIn(BaseModel):
    tag: str = Field(min_length=1, max_length=33)


class GroupMessageIn(BaseModel):
    body: str = Field(default="", max_length=4000)
    attachment_id: int | None = None
    client_message_id: str | None = Field(default=None, max_length=80)
    reply_to_message_id: int | None = None


class EditMessageIn(BaseModel):
    body: str = Field(default="", max_length=4000)


class ForwardMessageIn(BaseModel):
    source_type: str = Field(pattern=r"^(user|group)$")
    source_message_id: int
    target_type: str = Field(pattern=r"^(user|group)$")
    target_chat_id: int


class ChatMuteIn(BaseModel):
    chat_type: str = Field(pattern=r"^(user|group)$")
    chat_id: int = Field(gt=0)
    duration: str = Field(pattern=r"^(1h|8h|forever|off)$")


class AndroidPushTokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=4096)


class PushKeysIn(BaseModel):
    p256dh: str = Field(min_length=20, max_length=512)
    auth: str = Field(min_length=8, max_length=256)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=20, max_length=2048)
    keys: PushKeysIn


class GroupCallIn(BaseModel):
    video: bool = False
    invite: bool = False


class CallInviteActivateIn(BaseModel):
    invite_token: str = Field(min_length=16, max_length=160)


class CallQualityIn(BaseModel):
    call_key: str = Field(min_length=1, max_length=120)
    call_type: str = Field(default="private", max_length=16)
    peer_id: int | None = None
    group_id: int | None = None
    rtt_ms: float | None = Field(default=None, ge=0, le=60000)
    packet_loss_pct: float | None = Field(default=None, ge=0, le=100)
    jitter_ms: float | None = Field(default=None, ge=0, le=60000)
    available_outgoing_bitrate: float | None = Field(default=None, ge=0)
    video_bitrate_bps: float | None = Field(default=None, ge=0)
    video_width: int | None = Field(default=None, ge=0, le=16384)
    video_height: int | None = Field(default=None, ge=0, le=16384)
    video_fps: float | None = Field(default=None, ge=0, le=240)
    connection_quality: str | None = Field(default=None, max_length=24)
    end_reason: str | None = Field(default=None, max_length=64)


class ClientErrorIn(BaseModel):
    kind: str = Field(pattern=r"^(js_error|promise_rejection|manual)$")
    message: str = Field(min_length=1, max_length=1200)
    source: str | None = Field(default=None, max_length=500)
    line_no: int | None = Field(default=None, ge=0, le=10000000)
    column_no: int | None = Field(default=None, ge=0, le=10000000)
    stack: str | None = Field(default=None, max_length=8000)
    page_path: str | None = Field(default=None, max_length=500)
    client_type: str | None = Field(default=None, max_length=64)
    app_version: str | None = Field(default=None, max_length=64)
    app_version_code: int | None = Field(default=None, ge=0, le=2147483647)
    user_agent: str | None = Field(default=None, max_length=1200)
    network_type: str | None = Field(default=None, max_length=64)
    effective_type: str | None = Field(default=None, max_length=32)
    downlink_mbps: float | None = Field(default=None, ge=0, le=100000)
    network_rtt_ms: float | None = Field(default=None, ge=0, le=60000)
    online: bool = True
    context: str | None = Field(default=None, max_length=1000)
    fingerprint: str | None = Field(default=None, max_length=160)


@app.get("/health")
def health():
    try:
        conn = connect_db()
        conn.execute("SELECT 1").fetchone()
        conn.close()
    except Exception:
        raise HTTPException(503, "database unavailable")
    return {
        "status": "ok",
        "service": "svoi-chat",
        "database": "ok",
    }


@app.post("/api/register")
def register(data: RegisterIn, request: Request, conn=Depends(db)):
    username = data.username.strip().lower()
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        raise HTTPException(409, "Такой логин уже занят")
    password_hash, salt = hash_password(data.password)
    recovery_code = make_recovery_code()
    cur = conn.execute(
        """INSERT INTO users(
             username,display_name,password_hash,salt,recovery_code_hash,created_at
           ) VALUES(?,?,?,?,?,?)""",
        (
            username,
            data.display_name.strip(),
            password_hash,
            salt,
            recovery_code_hash(recovery_code),
            now_iso(),
        ),
    )
    conn.commit()
    row = conn.execute(
        """SELECT u.id,u.username,u.display_name,
                  a.stored_name AS avatar_stored_name
           FROM users u
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE u.id=?""",
        (cur.lastrowid,),
    ).fetchone()
    return {
        "token": make_session(conn, row["id"], request),
        "user": user_json(row),
        "recovery_code": recovery_code,
    }


@app.post("/api/login")
def login(data: LoginIn, request: Request, conn=Depends(db)):
    row = conn.execute(
        """SELECT u.*, a.stored_name AS avatar_stored_name
           FROM users u
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE u.username=?""",
        (data.username.strip().lower(),),
    ).fetchone()
    if not row or not verify_password(data.password, row["password_hash"], row["salt"]):
        raise HTTPException(401, "Неверный логин или пароль")
    return {"token": make_session(conn, row["id"], request), "user": user_json(row)}


@app.get("/api/account/recovery")
def recovery_status(
    user=Depends(current_user),
    conn=Depends(db),
):
    row = conn.execute(
        "SELECT recovery_code_hash FROM users WHERE id=?",
        (user["id"],),
    ).fetchone()
    return {
        "configured": bool(
            row and row["recovery_code_hash"]
        )
    }


@app.post("/api/account/recovery-code")
def create_recovery_code(
    data: RecoveryCodeCreateIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    row = conn.execute(
        """SELECT id,password_hash,salt
           FROM users
           WHERE id=?""",
        (user["id"],),
    ).fetchone()
    if not row or not verify_password(
        data.current_password,
        row["password_hash"],
        row["salt"],
    ):
        raise HTTPException(401, "Неверный текущий пароль")

    code = make_recovery_code()
    conn.execute(
        "UPDATE users SET recovery_code_hash=? WHERE id=?",
        (recovery_code_hash(code), user["id"]),
    )
    conn.commit()
    return {
        "recovery_code": code,
        "message": "Новый код восстановления создан",
    }


@app.post("/api/password/recover")
async def recover_password(
    data: PasswordRecoverIn,
    request: Request,
    conn=Depends(db),
):
    username = data.username.strip().lower().lstrip("@")
    code_hash = recovery_code_hash(data.recovery_code)
    row = conn.execute(
        """SELECT u.*, a.stored_name AS avatar_stored_name
           FROM users u
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE u.username=?""",
        (username,),
    ).fetchone()

    valid = bool(
        row
        and row["recovery_code_hash"]
        and secrets.compare_digest(
            str(row["recovery_code_hash"]),
            code_hash,
        )
    )
    if not valid:
        raise HTTPException(
            401,
            "Неверный логин или код восстановления",
        )

    password_hash, salt = hash_password(data.new_password)
    next_recovery_code = make_recovery_code()

    conn.execute(
        """UPDATE users
           SET password_hash=?,
               salt=?,
               recovery_code_hash=?
           WHERE id=?""",
        (
            password_hash,
            salt,
            recovery_code_hash(next_recovery_code),
            row["id"],
        ),
    )
    conn.execute(
        "DELETE FROM sessions WHERE user_id=?",
        (row["id"],),
    )
    conn.commit()

    new_token = make_session(conn, row["id"], request)

    for websocket in list(connections.get(row["id"], set())):
        try:
            await websocket.close(
                code=4401,
                reason="Пароль изменён",
            )
        except Exception:
            pass

    fresh = conn.execute(
        """SELECT u.id,u.username,u.display_name,
                  a.stored_name AS avatar_stored_name
           FROM users u
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE u.id=?""",
        (row["id"],),
    ).fetchone()

    return {
        "token": new_token,
        "user": user_json(fresh),
        "recovery_code": next_recovery_code,
        "message": "Пароль изменён",
    }


def authorization_token_hash(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return token_hash(authorization[7:])


def session_json(row, current_hash: str | None = None) -> dict:
    ua = str(row["user_agent"] or "")
    return {
        "id": row["session_id"],
        "device_label": row["device_label"] or "Неизвестное устройство",
        "created_at": row["created_at"],
        "last_seen_at": row["last_seen_at"] or row["created_at"],
        "current": bool(current_hash and row["token_hash"] == current_hash),
        "client": (
            "Android"
            if "Android" in ua
            else "iPhone/iPad"
            if "iPhone" in ua or "iPad" in ua
            else "Windows"
            if "Windows" in ua
            else "Web"
        ),
    }


@app.get("/api/account/sessions")
def list_account_sessions(
    request: Request,
    authorization: str | None = Header(default=None),
    user=Depends(current_user),
    conn=Depends(db),
):
    current_hash = authorization_token_hash(authorization)
    if current_hash:
        device_label, user_agent = request_session_meta(request)
        conn.execute(
            """UPDATE sessions
               SET device_label=?,user_agent=?,last_seen_at=?
               WHERE token_hash=?""",
            (device_label, user_agent, now_iso(), current_hash),
        )
        conn.commit()
    rows = conn.execute(
        """SELECT token_hash,session_id,device_label,user_agent,created_at,last_seen_at
           FROM sessions
           WHERE user_id=?
           ORDER BY COALESCE(last_seen_at,created_at) DESC,created_at DESC""",
        (user["id"],),
    ).fetchall()
    return {
        "sessions": [session_json(row, current_hash) for row in rows],
        "count": len(rows),
    }


async def close_revoked_session_sockets(user_id: int, hashes: set[str]) -> None:
    if not hashes:
        return
    for websocket in list(connections.get(user_id, set())):
        state = ws_connection_state.get(websocket) or {}
        if state.get("token_hash") not in hashes:
            continue
        try:
            await websocket.close(code=4401, reason="Сессия завершена")
        except Exception:
            pass


@app.delete("/api/account/sessions/{session_id}")
async def revoke_account_session(
    session_id: str,
    authorization: str | None = Header(default=None),
    user=Depends(current_user),
    conn=Depends(db),
):
    current_hash = authorization_token_hash(authorization)
    row = conn.execute(
        """SELECT token_hash,session_id
           FROM sessions
           WHERE user_id=? AND session_id=?""",
        (user["id"], session_id[:160]),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Сессия не найдена")
    if current_hash and row["token_hash"] == current_hash:
        raise HTTPException(409, "Текущую сессию заверши через «Выйти»")
    revoked_hash = str(row["token_hash"])
    conn.execute(
        "DELETE FROM sessions WHERE user_id=? AND session_id=?",
        (user["id"], row["session_id"]),
    )
    conn.commit()
    await close_revoked_session_sockets(user["id"], {revoked_hash})
    return {"ok": True, "session_id": row["session_id"]}


@app.post("/api/account/sessions/revoke-others")
async def revoke_other_account_sessions(
    authorization: str | None = Header(default=None),
    user=Depends(current_user),
    conn=Depends(db),
):
    current_hash = authorization_token_hash(authorization)
    if not current_hash:
        raise HTTPException(401, "Нужна авторизация")
    rows = conn.execute(
        "SELECT token_hash FROM sessions WHERE user_id=? AND token_hash<>?",
        (user["id"], current_hash),
    ).fetchall()
    revoked_hashes = {str(row["token_hash"]) for row in rows}
    conn.execute(
        "DELETE FROM sessions WHERE user_id=? AND token_hash<>?",
        (user["id"], current_hash),
    )
    conn.commit()
    await close_revoked_session_sockets(user["id"], revoked_hashes)
    return {"ok": True, "revoked": len(revoked_hashes)}


@app.post("/api/logout")
def logout(
    authorization: str | None = Header(default=None),
    user=Depends(current_user),
    conn=Depends(db),
):
    if authorization:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(authorization[7:]),))
        conn.commit()
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    data = user_json(user)
    data["is_server_admin"] = is_server_admin(user)
    return data


@app.get("/api/account/phone")
def account_phone(
    user=Depends(current_user),
    conn=Depends(db),
):
    row = conn.execute(
        """SELECT phone_last4,phone_linked_at,phone_verified_at
           FROM users
           WHERE id=?""",
        (user["id"],),
    ).fetchone()
    verified = bool(row and row["phone_verified_at"])
    return {
        "linked": verified,
        "verified": verified,
        "last4": row["phone_last4"] if verified else None,
        "linked_at": row["phone_linked_at"] if verified else None,
        "verified_at": row["phone_verified_at"] if verified else None,
    }


@app.post("/api/account/phone/link")
@app.post("/api/account/phone/request")
def link_account_phone(
    data: PhoneLinkIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    account = conn.execute(
        """SELECT id,password_hash,salt
           FROM users
           WHERE id=?""",
        (user["id"],),
    ).fetchone()
    if not account or not verify_password(
        data.current_password,
        account["password_hash"],
        account["salt"],
    ):
        raise HTTPException(401, "Неверный текущий пароль")

    phone_digits = normalize_phone_digits(data.phone)
    phone_hash, last4 = phone_lookup_hash(phone_digits)

    owner = conn.execute(
        """SELECT id
           FROM users
           WHERE phone_hash=?
             AND id<>?""",
        (phone_hash, user["id"]),
    ).fetchone()
    if owner:
        raise HTTPException(
            409,
            "Этот номер уже привязан к другому аккаунту",
        )

    linked_at = now_iso()
    try:
        conn.execute(
            """UPDATE users
               SET phone_hash=?,
                   phone_last4=?,
                   phone_linked_at=?,
                   phone_verified_at=?
               WHERE id=?""",
            (
                phone_hash,
                last4,
                linked_at,
                linked_at,
                user["id"],
            ),
        )
    except sqlite3.IntegrityError as error:
        raise HTTPException(
            409,
            "Этот номер уже привязан к другому аккаунту",
        ) from error

    conn.execute(
        "DELETE FROM phone_verifications WHERE user_id=?",
        (user["id"],),
    )
    conn.commit()

    return {
        "linked": True,
        "verified": True,
        "last4": last4,
        "linked_at": linked_at,
        "verified_at": linked_at,
    }


@app.delete("/api/account/phone")
def unlink_account_phone(
    data: PhoneUnlinkIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    account = conn.execute(
        """SELECT id,password_hash,salt
           FROM users
           WHERE id=?""",
        (user["id"],),
    ).fetchone()
    if not account or not verify_password(
        data.current_password,
        account["password_hash"],
        account["salt"],
    ):
        raise HTTPException(401, "Неверный текущий пароль")

    conn.execute(
        """UPDATE users
           SET phone_hash=NULL,
               phone_last4=NULL,
               phone_linked_at=NULL,
               phone_verified_at=NULL
           WHERE id=?""",
        (user["id"],),
    )
    conn.commit()

    return {
        "linked": False,
        "verified": False,
        "last4": None,
    }


@app.post("/api/phone-contacts/sync")
async def sync_phone_contacts(
    data: PhoneContactSyncIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    if len(data.hashes) > 5000:
        raise HTTPException(400, "Слишком много контактов за один раз")

    hashes = []
    seen = set()
    for value in data.hashes:
        item = str(value or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", item):
            continue
        if item in seen:
            continue
        seen.add(item)
        hashes.append(item)

    if not hashes:
        return {"matched": 0, "users": []}

    rows = []
    for offset in range(0, len(hashes), 400):
        chunk = hashes[offset:offset + 400]
        placeholders = ",".join("?" for _ in chunk)
        query = f"""
            SELECT u.id,u.username,u.display_name,u.last_seen_at,u.phone_hash,
                   a.stored_name AS avatar_stored_name
            FROM users u
            LEFT JOIN uploads a ON a.id=u.avatar_id
            WHERE u.id<>?
              AND u.phone_verified_at IS NOT NULL
              AND u.phone_hash IN ({placeholders})
        """
        rows.extend(
            conn.execute(
                query,
                [user["id"], *chunk],
            ).fetchall()
        )

    matched = {}
    for row in rows:
        if row["id"] in matched:
            continue
        matched[row["id"]] = row
        conn.execute(
            """INSERT OR IGNORE INTO contacts(
                 user_id,contact_user_id,created_at
               ) VALUES(?,?,?)""",
            (user["id"], row["id"], now_iso()),
        )
    conn.commit()

    result = [
        {
            **user_json(row),
            "phone_hash": row["phone_hash"],
            "online": bool(connections.get(row["id"])),
            "last_seen_at": row["last_seen_at"],
            "in_contacts": True,
            "blocked_by_me": blocked_by_user(
                conn,
                user["id"],
                row["id"],
            ),
        }
        for row in matched.values()
    ]

    if result:
        await push(user["id"], {"type": "contacts_updated"})

    return {
        "matched": len(result),
        "users": result,
    }


def record_online_sample(conn=None) -> None:
    owns_conn = conn is None
    if owns_conn:
        conn = connect_db()
    try:
        online_count = sum(
            1 for sockets in connections.values() if sockets
        )
        recorded_at = now_iso()
        conn.execute(
            "INSERT INTO online_samples(recorded_at,online_count) VALUES(?,?)",
            (recorded_at, online_count),
        )
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=31)
        ).isoformat()
        conn.execute(
            "DELETE FROM online_samples WHERE recorded_at<?",
            (cutoff,),
        )
        conn.commit()
    finally:
        if owns_conn:
            conn.close()


def _parse_stat_time(value: str | None):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _bucket_counts(rows, start, step, bucket_count):
    values = [0 for _ in range(bucket_count)]
    seconds = step.total_seconds()
    for row in rows:
        value = row[0] if not isinstance(row, sqlite3.Row) else row[0]
        dt = _parse_stat_time(value)
        if dt is None:
            continue
        index = int((dt - start).total_seconds() // seconds)
        if 0 <= index < bucket_count:
            values[index] += 1
    return values


@app.get("/api/admin/overview")
def admin_overview(
    user=Depends(require_server_admin),
    conn=Depends(db),
):
    now = datetime.now(timezone.utc)
    since_24h = (now - timedelta(hours=24)).isoformat()

    total_users = int(
        conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    )
    total_groups = int(
        conn.execute("SELECT COUNT(*) FROM chat_groups").fetchone()[0]
    )
    private_messages = int(
        conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    )
    group_messages = int(
        conn.execute("SELECT COUNT(*) FROM group_messages").fetchone()[0]
    )
    uploads_row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(size),0) FROM uploads"
    ).fetchone()
    active_sessions = int(
        conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    )

    registrations_24h = int(
        conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at>=?",
            (since_24h,),
        ).fetchone()[0]
    )
    private_24h = int(
        conn.execute(
            "SELECT COUNT(*) FROM messages WHERE created_at>=?",
            (since_24h,),
        ).fetchone()[0]
    )
    group_24h = int(
        conn.execute(
            "SELECT COUNT(*) FROM group_messages WHERE created_at>=?",
            (since_24h,),
        ).fetchone()[0]
    )
    calls_24h = int(
        conn.execute(
            "SELECT COUNT(*) FROM call_history WHERE started_at>=?",
            (since_24h,),
        ).fetchone()[0]
    )

    memory = _memory_snapshot()
    try:
        disk_usage = shutil.disk_usage(DATA_DIR)
        disk = {
            "total": int(disk_usage.total),
            "used": int(disk_usage.used),
            "free": int(disk_usage.free),
            "percent": round(
                disk_usage.used * 100 / disk_usage.total,
                1,
            ) if disk_usage.total else None,
        }
    except Exception:
        disk = {
            "total": 0,
            "used": 0,
            "free": 0,
            "percent": None,
        }

    try:
        load_1m, load_5m, load_15m = os.getloadavg()
    except Exception:
        load_1m = load_5m = load_15m = 0.0

    service_names = (
        "svoi-chat",
        "livekit",
        "coturn",
        "caddy",
        "svoi-watchdog.timer",
    )
    services = {
        name: _service_state(name)
        for name in service_names
    }

    online_users = sum(
        1 for sockets in connections.values() if sockets
    )
    websocket_connections = sum(
        len(sockets) for sockets in connections.values()
    )

    return {
        "generated_at": now.isoformat(),
        "app_uptime_seconds": max(0, int(time.time() - APP_STARTED_AT)),
        "online_users": online_users,
        "websocket_connections": websocket_connections,
        "active_calls": len(active_calls),
        "counts": {
            "users": total_users,
            "groups": total_groups,
            "private_messages": private_messages,
            "group_messages": group_messages,
            "uploads": int(uploads_row[0]),
            "uploads_bytes": int(uploads_row[1] or 0),
            "sessions": active_sessions,
        },
        "activity_24h": {
            "registrations": registrations_24h,
            "private_messages": private_24h,
            "group_messages": group_24h,
            "calls": calls_24h,
        },
        "resources": {
            "cpu_count": int(os.cpu_count() or 1),
            "load_1m": round(float(load_1m), 2),
            "load_5m": round(float(load_5m), 2),
            "load_15m": round(float(load_15m), 2),
            "memory": memory,
            "disk": disk,
            "database_bytes": (
                DB_PATH.stat().st_size if DB_PATH.is_file() else 0
            ),
            "process_rss_bytes": _process_rss_bytes(),
        },
        "services": services,
    }


@app.get("/api/admin/stats")
def admin_stats(
    period: str = Query("day", max_length=12),
    user=Depends(require_server_admin),
    conn=Depends(db),
):
    period = period.strip().lower()
    if period not in {"day", "week"}:
        raise HTTPException(400, "Период должен быть day или week")

    now = datetime.now(timezone.utc)
    if period == "day":
        bucket_count = 24
        step = timedelta(hours=1)
        current = now.replace(minute=0, second=0, microsecond=0)
        start = current - step * (bucket_count - 1)
    else:
        bucket_count = 7
        step = timedelta(days=1)
        current = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = current - step * (bucket_count - 1)

    end = current + step
    start_iso = start.isoformat()
    end_iso = end.isoformat()

    registrations = _bucket_counts(
        conn.execute(
            "SELECT created_at FROM users WHERE created_at>=? AND created_at<?",
            (start_iso, end_iso),
        ).fetchall(),
        start,
        step,
        bucket_count,
    )

    message_rows = conn.execute(
        """SELECT created_at FROM messages
           WHERE created_at>=? AND created_at<?
           UNION ALL
           SELECT created_at FROM group_messages
           WHERE created_at>=? AND created_at<?""",
        (start_iso, end_iso, start_iso, end_iso),
    ).fetchall()
    messages = _bucket_counts(
        message_rows,
        start,
        step,
        bucket_count,
    )

    calls = _bucket_counts(
        conn.execute(
            """SELECT started_at FROM call_history
               WHERE started_at>=? AND started_at<?""",
            (start_iso, end_iso),
        ).fetchall(),
        start,
        step,
        bucket_count,
    )

    # Add a fresh point so the graph always reflects current live online.
    record_online_sample(conn)

    previous = conn.execute(
        """SELECT recorded_at,online_count
           FROM online_samples
           WHERE recorded_at<?
           ORDER BY recorded_at DESC
           LIMIT 1""",
        (start_iso,),
    ).fetchone()
    samples = conn.execute(
        """SELECT recorded_at,online_count
           FROM online_samples
           WHERE recorded_at>=? AND recorded_at<?
           ORDER BY recorded_at""",
        (start_iso, end_iso),
    ).fetchall()

    current_online = int(previous["online_count"]) if previous else 0
    online_peak = []
    sample_index = 0
    parsed_samples = [
        (_parse_stat_time(row["recorded_at"]), int(row["online_count"]))
        for row in samples
    ]
    parsed_samples = [
        item for item in parsed_samples if item[0] is not None
    ]

    points = []
    for index in range(bucket_count):
        bucket_start = start + step * index
        bucket_end = bucket_start + step
        peak = current_online

        while sample_index < len(parsed_samples):
            sample_time, sample_count = parsed_samples[sample_index]
            if sample_time >= bucket_end:
                break
            if sample_time >= bucket_start:
                current_online = sample_count
                peak = max(peak, sample_count)
            sample_index += 1

        online_peak.append(peak)
        points.append(
            {
                "start": bucket_start.isoformat(),
                "online": peak,
                "messages": messages[index],
                "registrations": registrations[index],
                "calls": calls[index],
            }
        )

    return {
        "period": period,
        "generated_at": now.isoformat(),
        "online_tracking_since": (
            conn.execute(
                "SELECT MIN(recorded_at) FROM online_samples"
            ).fetchone()[0]
        ),
        "points": points,
        "totals": {
            "messages": sum(messages),
            "registrations": sum(registrations),
            "calls": sum(calls),
            "peak_online": max(online_peak, default=0),
        },
    }


@app.get("/api/admin/users")
def admin_users(
    limit: int = Query(50, ge=1, le=200),
    user=Depends(require_server_admin),
    conn=Depends(db),
):
    rows = conn.execute(
        """SELECT u.id,u.username,u.display_name,u.created_at,u.last_seen_at,
                  COUNT(s.token_hash) AS session_count
           FROM users u
           LEFT JOIN sessions s ON s.user_id=u.id
           GROUP BY u.id
           ORDER BY u.id DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "username": row["username"],
            "display_name": row["display_name"],
            "created_at": row["created_at"],
            "last_seen_at": row["last_seen_at"],
            "online": bool(connections.get(int(row["id"]))),
            "session_count": int(row["session_count"] or 0),
        }
        for row in rows
    ]


@app.patch("/api/admin/users/{target_user_id}/display-name")
async def admin_rename_user(
    target_user_id: int,
    data: AdminUserRenameIn,
    user=Depends(require_server_admin),
    conn=Depends(db),
):
    display_name = data.display_name.strip()
    if not display_name:
        raise HTTPException(400, "Имя не может быть пустым")

    target = conn.execute(
        "SELECT id FROM users WHERE id=?",
        (target_user_id,),
    ).fetchone()
    if not target:
        raise HTTPException(404, "Пользователь не найден")

    conn.execute(
        "UPDATE users SET display_name=? WHERE id=?",
        (display_name, target_user_id),
    )
    conn.commit()

    row = conn.execute(
        """SELECT u.id,u.username,u.display_name,
                  a.stored_name AS avatar_stored_name
           FROM users u
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE u.id=?""",
        (target_user_id,),
    ).fetchone()
    user_data = user_json(row)
    await _broadcast_profile(user_data)
    return user_data


@app.patch("/api/admin/users/{target_user_id}/username")
async def admin_change_username(
    target_user_id: int,
    data: AdminUsernameRenameIn,
    user=Depends(require_server_admin),
    conn=Depends(db),
):
    username = data.username.strip()
    if username.startswith("@"):
        username = username[1:]
    username = username.strip().lower()

    if not re.fullmatch(r"[a-z0-9_.-]{3,32}", username):
        raise HTTPException(
            400,
            "Ник должен содержать 3–32 символа: латинские буквы, цифры, _, . или -",
        )

    target = conn.execute(
        "SELECT id,username FROM users WHERE id=?",
        (target_user_id,),
    ).fetchone()
    if not target:
        raise HTTPException(404, "Пользователь не найден")

    current_username = str(target["username"]).strip().lower()
    if (
        int(target["id"]) not in SERVER_ADMIN_IDS
        and current_username in SERVER_ADMIN_USERNAMES
    ):
        raise HTTPException(
            409,
            "Этот админ привязан к нику. Сначала закрепи права администратора по ID.",
        )

    existing = conn.execute(
        "SELECT id FROM users WHERE username=? AND id<>?",
        (username, target_user_id),
    ).fetchone()
    if existing:
        raise HTTPException(409, "Такой ник уже занят")

    conn.execute(
        "UPDATE users SET username=? WHERE id=?",
        (username, target_user_id),
    )
    conn.commit()

    row = conn.execute(
        """SELECT u.id,u.username,u.display_name,
                  a.stored_name AS avatar_stored_name
           FROM users u
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE u.id=?""",
        (target_user_id,),
    ).fetchone()
    user_data = user_json(row)
    await _broadcast_profile(user_data)
    return user_data


@app.get("/api/turn")
def turn_credentials(user=Depends(current_user)):
    if not TURN_SHARED_SECRET:
        return {
            "configured": False,
            "ice_servers": [
                {"urls": ["stun:stun.l.google.com:19302"]},
            ],
        }

    expires = int(time.time()) + 3600
    username = f"{expires}:{user['id']}"
    digest = hmac.new(
        TURN_SHARED_SECRET.encode(),
        username.encode(),
        hashlib.sha1,
    ).digest()
    password = base64.b64encode(digest).decode()

    return {
        "configured": True,
        "expires_at": expires,
        "ice_servers": [
            {
                "urls": [
                    "stun:stun.l.google.com:19302",
                    "stun:stun1.l.google.com:19302",
                ],
            },
            {
                "urls": [
                    f"turn:{TURN_HOST}:3478?transport=udp",
                    f"turn:{TURN_HOST}:3478?transport=tcp",
                ],
                "username": username,
                "credential": password,
            },
        ],
    }


def chat_mute_state(conn, user_id: int, chat_type: str, chat_id: int) -> dict:
    row = conn.execute(
        """SELECT muted_until FROM chat_mutes
           WHERE user_id=? AND chat_type=? AND chat_id=?""",
        (user_id, chat_type, chat_id),
    ).fetchone()
    if not row:
        return {"muted": False, "muted_until": None}

    muted_until = row["muted_until"]
    if muted_until is not None and muted_until <= now_iso():
        conn.execute(
            "DELETE FROM chat_mutes WHERE user_id=? AND chat_type=? AND chat_id=?",
            (user_id, chat_type, chat_id),
        )
        conn.commit()
        return {"muted": False, "muted_until": None}

    return {"muted": True, "muted_until": muted_until}


def is_chat_muted(conn, user_id: int, chat_type: str, chat_id: int) -> bool:
    return bool(
        conn.execute(
            """SELECT 1 FROM chat_mutes
               WHERE user_id=? AND chat_type=? AND chat_id=?
                 AND (muted_until IS NULL OR muted_until>?)
               LIMIT 1""",
            (user_id, chat_type, chat_id, now_iso()),
        ).fetchone()
    )


def unread_count_for_user(conn, user_id: int) -> int:
    direct = conn.execute(
        """SELECT COUNT(*) FROM messages m
           WHERE m.recipient_id=? AND m.read_at IS NULL
             AND NOT EXISTS(
               SELECT 1 FROM message_hidden_by_user mh
               WHERE mh.message_id=m.id AND mh.user_id=?
             )""",
        (user_id, user_id),
    ).fetchone()[0]

    groups = conn.execute(
        """SELECT COUNT(*)
           FROM group_messages gm
           JOIN group_members member
             ON member.group_id=gm.group_id AND member.user_id=?
           WHERE gm.sender_id<>?
             AND gm.deleted_at IS NULL
             AND gm.created_at>=member.joined_at
             AND NOT EXISTS(
               SELECT 1 FROM group_message_reads gr
               WHERE gr.message_id=gm.id AND gr.user_id=?
             )
             AND NOT EXISTS(
               SELECT 1 FROM group_message_hidden_by_user gh
               WHERE gh.message_id=gm.id AND gh.user_id=?
             )""",
        (user_id, user_id, user_id, user_id),
    ).fetchone()[0]
    return int(direct or 0) + int(groups or 0)


def users_blocked(conn, user_a: int, user_b: int) -> bool:
    return bool(
        conn.execute(
            """SELECT 1 FROM user_blocks
               WHERE (blocker_id=? AND blocked_id=?)
                  OR (blocker_id=? AND blocked_id=?)
               LIMIT 1""",
            (user_a, user_b, user_b, user_a),
        ).fetchone()
    )


def blocked_by_user(conn, blocker_id: int, blocked_id: int) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM user_blocks WHERE blocker_id=? AND blocked_id=?",
            (blocker_id, blocked_id),
        ).fetchone()
    )


@app.post("/api/chat-mute")
def set_chat_mute(
    data: ChatMuteIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    if data.chat_type == "user":
        if data.chat_id == user["id"]:
            raise HTTPException(400, "Нельзя изменить уведомления для самого себя")
        if not conn.execute(
            "SELECT 1 FROM users WHERE id=?",
            (data.chat_id,),
        ).fetchone():
            raise HTTPException(404, "Пользователь не найден")
    else:
        if not group_for_user(conn, data.chat_id, user["id"]):
            raise HTTPException(404, "Группа не найдена")

    if data.duration == "off":
        conn.execute(
            "DELETE FROM chat_mutes WHERE user_id=? AND chat_type=? AND chat_id=?",
            (user["id"], data.chat_type, data.chat_id),
        )
    else:
        muted_until = None
        if data.duration == "1h":
            muted_until = (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat()
        elif data.duration == "8h":
            muted_until = (
                datetime.now(timezone.utc) + timedelta(hours=8)
            ).isoformat()

        conn.execute(
            """INSERT INTO chat_mutes(
                 user_id,chat_type,chat_id,muted_until,updated_at
               ) VALUES(?,?,?,?,?)
               ON CONFLICT(user_id,chat_type,chat_id) DO UPDATE SET
                 muted_until=excluded.muted_until,
                 updated_at=excluded.updated_at""",
            (
                user["id"],
                data.chat_type,
                data.chat_id,
                muted_until,
                now_iso(),
            ),
        )
    conn.commit()
    return {
        "chat_type": data.chat_type,
        "chat_id": data.chat_id,
        **chat_mute_state(
            conn,
            user["id"],
            data.chat_type,
            data.chat_id,
        ),
    }


@app.get("/api/users")
def users(user=Depends(current_user), conn=Depends(db)):
    now = now_iso()
    rows = conn.execute(
        """WITH peer_ids(id) AS (
             SELECT contact_user_id
             FROM contacts
             WHERE user_id=?
             UNION
             SELECT recipient_id
             FROM messages
             WHERE sender_id=?
             UNION
             SELECT sender_id
             FROM messages
             WHERE recipient_id=?
           )
           SELECT u.id,u.username,u.display_name,u.last_seen_at,
                  a.stored_name AS avatar_stored_name,
                  EXISTS(
                    SELECT 1 FROM contacts c
                    WHERE c.user_id=? AND c.contact_user_id=u.id
                  ) AS in_contacts,
                  EXISTS(
                    SELECT 1 FROM user_blocks b
                    WHERE b.blocker_id=? AND b.blocked_id=u.id
                  ) AS blocked_by_me,
                  (
                    SELECT COUNT(*) FROM messages incoming
                    WHERE incoming.sender_id=u.id
                      AND incoming.recipient_id=?
                      AND incoming.read_at IS NULL
                      AND NOT EXISTS(
                        SELECT 1 FROM message_hidden_by_user mh
                        WHERE mh.message_id=incoming.id AND mh.user_id=?
                      )
                  ) AS unread_count,
                  EXISTS(
                    SELECT 1 FROM chat_mutes cm
                    WHERE cm.user_id=? AND cm.chat_type='user' AND cm.chat_id=u.id
                      AND (cm.muted_until IS NULL OR cm.muted_until>?)
                  ) AS muted,
                  (
                    SELECT cm.muted_until FROM chat_mutes cm
                    WHERE cm.user_id=? AND cm.chat_type='user' AND cm.chat_id=u.id
                      AND (cm.muted_until IS NULL OR cm.muted_until>?)
                    LIMIT 1
                  ) AS muted_until
           FROM peer_ids peers
           JOIN users u ON u.id=peers.id
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE u.id<>?
           ORDER BY u.display_name""",
        (
            user["id"],
            user["id"],
            user["id"],
            user["id"],
            user["id"],
            user["id"],
            user["id"],
            user["id"],
            now,
            user["id"],
            now,
            user["id"],
        ),
    ).fetchall()
    return [
        {
            **user_json(r),
            "online": bool(connections.get(r["id"])),
            "last_seen_at": r["last_seen_at"],
            "in_contacts": bool(r["in_contacts"]),
            "blocked_by_me": bool(r["blocked_by_me"]),
            "unread_count": int(r["unread_count"] or 0),
            "muted": bool(r["muted"]),
            "muted_until": r["muted_until"],
        }
        for r in rows
    ]

@app.get("/api/users/search")
def search_user(
    tag: str = Query(..., min_length=1, max_length=33),
    user=Depends(current_user),
    conn=Depends(db),
):
    username = tag.strip().lower()
    if username.startswith("@"):
        username = username[1:]
    if not username:
        raise HTTPException(400, "Укажи тег пользователя")

    row = conn.execute(
        """SELECT u.id,u.username,u.display_name,u.last_seen_at,
                  a.stored_name AS avatar_stored_name,
                  EXISTS(
                    SELECT 1 FROM contacts c
                    WHERE c.user_id=? AND c.contact_user_id=u.id
                  ) AS in_contacts,
                  EXISTS(
                    SELECT 1 FROM user_blocks b
                    WHERE b.blocker_id=? AND b.blocked_id=u.id
                  ) AS blocked_by_me
           FROM users u
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE u.username=? AND u.id<>?""",
        (user["id"], user["id"], username, user["id"]),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Пользователь с таким тегом не найден")

    return {
        **user_json(row),
        "online": bool(connections.get(row["id"])),
        "last_seen_at": row["last_seen_at"],
        "in_contacts": bool(row["in_contacts"]),
        "blocked_by_me": bool(row["blocked_by_me"]),
    }


@app.post("/api/contacts/{other_id}")
async def add_contact(
    other_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    if other_id == user["id"]:
        raise HTTPException(400, "Нельзя добавить себя")
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (other_id,)).fetchone():
        raise HTTPException(404, "Пользователь не найден")

    conn.execute(
        """INSERT OR IGNORE INTO contacts(user_id,contact_user_id,created_at)
           VALUES(?,?,?)""",
        (user["id"], other_id, now_iso()),
    )
    conn.commit()

    row = conn.execute(
        """SELECT u.id,u.username,u.display_name,u.last_seen_at,
                  a.stored_name AS avatar_stored_name
           FROM users u
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE u.id=?""",
        (other_id,),
    ).fetchone()
    await push(user["id"], {"type": "contacts_updated"})
    return {
        **user_json(row),
        "online": bool(connections.get(other_id)),
        "last_seen_at": row["last_seen_at"],
        "in_contacts": True,
        "blocked_by_me": blocked_by_user(conn, user["id"], other_id),
    }


@app.get("/api/blocks")
def get_blocks(user=Depends(current_user), conn=Depends(db)):
    rows = conn.execute(
        """SELECT u.id,u.username,u.display_name,u.last_seen_at,
                  a.stored_name AS avatar_stored_name,
                  b.created_at AS blocked_at
           FROM user_blocks b
           JOIN users u ON u.id=b.blocked_id
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE b.blocker_id=?
           ORDER BY b.created_at DESC""",
        (user["id"],),
    ).fetchall()
    return [
        {
            **user_json(row),
            "last_seen_at": row["last_seen_at"],
            "blocked_at": row["blocked_at"],
            "blocked_by_me": True,
        }
        for row in rows
    ]


@app.post("/api/blocks/{other_id}")
async def block_user(
    other_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    if other_id == user["id"]:
        raise HTTPException(400, "Нельзя заблокировать самого себя")
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (other_id,)).fetchone():
        raise HTTPException(404, "Пользователь не найден")

    conn.execute(
        """INSERT OR IGNORE INTO user_blocks(blocker_id,blocked_id,created_at)
           VALUES(?,?,?)""",
        (user["id"], other_id, now_iso()),
    )
    conn.commit()

    # End any active private call between these users.
    for call_id, call in list(active_calls.items()):
        participants = {call.get("caller_id"), call.get("callee_id")}
        if participants == {user["id"], other_id}:
            finish_call_history(
                call_id,
                "completed" if call.get("answered") else "rejected",
            )
            active_calls.pop(call_id, None)
            await push(
                other_id,
                {
                    "type": "call_end",
                    "from_user_id": user["id"],
                    "from_name": user["display_name"],
                    "call_id": call_id,
                },
            )

    await push(user["id"], {"type": "blocks_updated"})
    return {"ok": True, "user_id": other_id, "blocked": True}


@app.delete("/api/blocks/{other_id}")
async def unblock_user(
    other_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    conn.execute(
        "DELETE FROM user_blocks WHERE blocker_id=? AND blocked_id=?",
        (user["id"], other_id),
    )
    conn.commit()
    await push(user["id"], {"type": "blocks_updated"})
    return {"ok": True, "user_id": other_id, "blocked": False}


@app.delete("/api/contacts/{other_id}")
async def remove_contact(
    other_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    conn.execute(
        "DELETE FROM contacts WHERE user_id=? AND contact_user_id=?",
        (user["id"], other_id),
    )
    conn.commit()
    await push(user["id"], {"type": "contacts_updated"})
    return {"ok": True}


def _avatar_suffix(content: bytes, mime: str) -> str:
    if mime == "image/jpeg" and content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if mime == "image/png" and content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if (
        mime == "image/webp"
        and len(content) >= 12
        and content[:4] == b"RIFF"
        and content[8:12] == b"WEBP"
    ):
        return ".webp"
    raise HTTPException(400, "Поддерживаются только JPEG, PNG и WebP")


async def _broadcast_profile(user_data: dict):
    profile_id = int(user_data["id"])
    conn = connect_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT DISTINCT peer_id FROM (
             SELECT contact_user_id AS peer_id
             FROM contacts WHERE user_id=?
             UNION
             SELECT user_id AS peer_id
             FROM contacts WHERE contact_user_id=?
             UNION
             SELECT recipient_id AS peer_id
             FROM messages WHERE sender_id=?
             UNION
             SELECT sender_id AS peer_id
             FROM messages WHERE recipient_id=?
           )""",
        (profile_id, profile_id, profile_id, profile_id),
    ).fetchall()
    conn.close()

    recipients = {profile_id, *(int(row["peer_id"]) for row in rows)}
    for user_id in recipients:
        await push(
            user_id,
            {
                "type": "profile_updated",
                "user": user_data,
            },
        )


@app.post("/api/me/avatar")
async def set_avatar(
    file: UploadFile = File(...),
    user=Depends(current_user),
    conn=Depends(db),
):
    mime = (file.content_type or "").lower().split(";", 1)[0].strip()
    if mime not in AVATAR_IMAGE_TYPES:
        await file.close()
        raise HTTPException(400, "Для аватара выбери JPEG, PNG или WebP")

    content = await file.read(AVATAR_MAX_BYTES + 1)
    await file.close()
    if not content:
        raise HTTPException(400, "Файл пустой")
    if len(content) > AVATAR_MAX_BYTES:
        raise HTTPException(413, "Аватар больше 5 МБ")

    suffix = _avatar_suffix(content, mime)
    stored = "avatar-" + secrets.token_hex(24) + suffix
    path = UPLOAD_DIR / stored
    path.write_bytes(content)

    old = conn.execute(
        """SELECT u.avatar_id, a.stored_name
           FROM users u
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE u.id=?""",
        (user["id"],),
    ).fetchone()

    generate_media_thumbnail(path, stored, mime)

    try:
        cur = conn.execute(
            """INSERT INTO uploads(
                 owner_id,stored_name,original_name,mime_type,size,created_at
               ) VALUES(?,?,?,?,?,?)""",
            (
                user["id"],
                stored,
                f"avatar{suffix}",
                mime,
                len(content),
                now_iso(),
            ),
        )
        conn.execute(
            "UPDATE users SET avatar_id=? WHERE id=?",
            (cur.lastrowid, user["id"]),
        )
        conn.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise

    if old and old["avatar_id"]:
        old_stored = old["stored_name"]
        conn.execute(
            "DELETE FROM uploads WHERE id=? AND owner_id=?",
            (old["avatar_id"], user["id"]),
        )
        conn.commit()
        if old_stored:
            (UPLOAD_DIR / old_stored).unlink(missing_ok=True)

    row = conn.execute(
        """SELECT u.id,u.username,u.display_name,
                  a.stored_name AS avatar_stored_name
           FROM users u
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE u.id=?""",
        (user["id"],),
    ).fetchone()
    data = user_json(row)
    await _broadcast_profile(data)
    return data


@app.delete("/api/me/avatar")
async def delete_avatar(
    user=Depends(current_user),
    conn=Depends(db),
):
    old = conn.execute(
        """SELECT u.avatar_id, a.stored_name
           FROM users u
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE u.id=?""",
        (user["id"],),
    ).fetchone()

    if old and old["avatar_id"]:
        conn.execute(
            "UPDATE users SET avatar_id=NULL WHERE id=?",
            (user["id"],),
        )
        conn.execute(
            "DELETE FROM uploads WHERE id=? AND owner_id=?",
            (old["avatar_id"], user["id"]),
        )
        conn.commit()
        if old["stored_name"]:
            (UPLOAD_DIR / old["stored_name"]).unlink(missing_ok=True)

    row = conn.execute(
        """SELECT u.id,u.username,u.display_name,
                  NULL AS avatar_stored_name
           FROM users u WHERE u.id=?""",
        (user["id"],),
    ).fetchone()
    data = user_json(row)
    await _broadcast_profile(data)
    return data

async def _broadcast_group_roles(group_id: int):
    conn = connect_db()
    members = conn.execute(
        "SELECT user_id,is_admin FROM group_members WHERE group_id=?",
        (group_id,),
    ).fetchall()
    conn.close()
    for member in members:
        await push(
            int(member["user_id"]),
            {
                "type": "group_roles_updated",
                "group_id": group_id,
                "is_admin": bool(member["is_admin"]),
            },
        )


async def _broadcast_group_update(group_id: int):
    conn = connect_db()
    row = conn.execute(
        """SELECT g.id,g.name,g.owner_id,g.created_at,
                  ga.stored_name AS avatar_stored_name,
                  COUNT(gm.user_id) AS member_count
           FROM chat_groups g
           LEFT JOIN uploads ga ON ga.id=g.avatar_id
           LEFT JOIN group_members gm ON gm.group_id=g.id
           WHERE g.id=?
           GROUP BY g.id""",
        (group_id,),
    ).fetchone()
    members = conn.execute(
        "SELECT user_id,is_admin FROM group_members WHERE group_id=?",
        (group_id,),
    ).fetchall()
    conn.close()
    if not row:
        return
    base = group_json(row)
    for member in members:
        data = {**base, "is_admin": bool(member["is_admin"])}
        await push(
            int(member["user_id"]),
            {"type": "group_updated", "group": data},
        )


@app.post("/api/groups/{group_id}/avatar")
async def set_group_avatar(
    group_id: int,
    file: UploadFile = File(...),
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        await file.close()
        raise HTTPException(404, "Группа не найдена")
    if not bool(group["is_admin"]):
        await file.close()
        raise HTTPException(403, "Менять аватар группы может только администратор")

    mime = (file.content_type or "").lower().split(";", 1)[0].strip()
    if mime not in AVATAR_IMAGE_TYPES:
        await file.close()
        raise HTTPException(400, "Для аватара выбери JPEG, PNG или WebP")

    content = await file.read(AVATAR_MAX_BYTES + 1)
    await file.close()
    if not content:
        raise HTTPException(400, "Файл пустой")
    if len(content) > AVATAR_MAX_BYTES:
        raise HTTPException(413, "Аватар больше 5 МБ")

    suffix = _avatar_suffix(content, mime)
    stored = "group-avatar-" + secrets.token_hex(24) + suffix
    path = UPLOAD_DIR / stored
    path.write_bytes(content)

    old = conn.execute(
        """SELECT g.avatar_id, a.stored_name
           FROM chat_groups g
           LEFT JOIN uploads a ON a.id=g.avatar_id
           WHERE g.id=?""",
        (group_id,),
    ).fetchone()

    try:
        cur = conn.execute(
            """INSERT INTO uploads(
                 owner_id,stored_name,original_name,mime_type,size,created_at
               ) VALUES(?,?,?,?,?,?)""",
            (
                user["id"],
                stored,
                f"group-avatar{suffix}",
                mime,
                len(content),
                now_iso(),
            ),
        )
        conn.execute(
            "UPDATE chat_groups SET avatar_id=? WHERE id=?",
            (cur.lastrowid, group_id),
        )
        conn.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise

    if old and old["avatar_id"]:
        conn.execute(
            "DELETE FROM uploads WHERE id=? AND owner_id=?",
            (old["avatar_id"], user["id"]),
        )
        conn.commit()
        if old["stored_name"]:
            (UPLOAD_DIR / old["stored_name"]).unlink(missing_ok=True)

    row = conn.execute(
        """SELECT g.id,g.name,g.owner_id,g.created_at,
                  ga.stored_name AS avatar_stored_name,
                  COUNT(gm.user_id) AS member_count
           FROM chat_groups g
           LEFT JOIN uploads ga ON ga.id=g.avatar_id
           LEFT JOIN group_members gm ON gm.group_id=g.id
           WHERE g.id=?
           GROUP BY g.id""",
        (group_id,),
    ).fetchone()
    data = group_json(row)
    await _broadcast_group_update(group_id)
    return data


def push_configured() -> bool:
    return bool(
        VAPID_PUBLIC_KEY
        and Path(VAPID_PRIVATE_KEY_FILE).is_file()
    )


@app.get("/api/push/public-key")
def push_public_key(user=Depends(current_user)):
    return {
        "configured": push_configured(),
        "public_key": VAPID_PUBLIC_KEY if push_configured() else None,
    }


@app.post("/api/push/android/register")
def register_android_push(
    data: AndroidPushTokenIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    token = data.token.strip()
    now = now_iso()
    conn.execute(
        """INSERT INTO android_push_tokens(
             token,user_id,created_at,updated_at
           ) VALUES(?,?,?,?)
           ON CONFLICT(token) DO UPDATE SET
             user_id=excluded.user_id,
             updated_at=excluded.updated_at""",
        (token, user["id"], now, now),
    )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM android_push_tokens WHERE user_id=?",
        (user["id"],),
    ).fetchone()[0]
    return {"ok": True, "android_tokens": int(count)}


@app.post("/api/push/android/unregister")
def unregister_android_push(
    data: AndroidPushTokenIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    conn.execute(
        "DELETE FROM android_push_tokens WHERE token=? AND user_id=?",
        (data.token.strip(), user["id"]),
    )
    conn.commit()
    return {"ok": True}


@app.post("/api/push/subscribe")
def subscribe_push(
    data: PushSubscriptionIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    if not push_configured():
        raise HTTPException(503, "Push-уведомления пока не настроены")
    conn.execute(
        """INSERT INTO push_subscriptions(
             endpoint,user_id,p256dh,auth,created_at
           ) VALUES(?,?,?,?,?)
           ON CONFLICT(endpoint) DO UPDATE SET
             user_id=excluded.user_id,
             p256dh=excluded.p256dh,
             auth=excluded.auth,
             created_at=excluded.created_at""",
        (
            data.endpoint,
            user["id"],
            data.keys.p256dh,
            data.keys.auth,
            now_iso(),
        ),
    )
    conn.commit()
    return {"ok": True}


@app.post("/api/push/unsubscribe")
def unsubscribe_push(
    data: PushSubscriptionIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    conn.execute(
        "DELETE FROM push_subscriptions WHERE endpoint=? AND user_id=?",
        (data.endpoint, user["id"]),
    )
    conn.commit()
    return {"ok": True}


def _webpush_one(subscription: dict, payload: str) -> dict:
    try:
        webpush(
            subscription_info=subscription,
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY_FILE,
            vapid_claims={"sub": VAPID_SUBJECT},
            ttl=120,
        )
        return {"ok": True, "stale": False, "error": None}
    except WebPushException as exc:
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(
                getattr(exc, "response", None),
                "status_code",
                None,
            )
        stale = status in (404, 410)
        error = f"HTTP {status}" if status else str(exc)[:180]
        return {"ok": False, "stale": stale, "error": error}
    except Exception as exc:
        return {
            "ok": False,
            "stale": False,
            "error": str(exc)[:180],
        }


def web_push_context_for_user(conn, user_id: int) -> dict:
    rows = conn.execute(
        """SELECT endpoint,p256dh,auth
           FROM push_subscriptions WHERE user_id=?""",
        (user_id,),
    ).fetchall()
    if not rows:
        return {"rows": [], "unread_count": 0}
    return {
        "rows": [dict(row) for row in rows],
        "unread_count": unread_count_for_user(conn, user_id),
    }


def web_push_contexts_for_users(user_ids) -> dict[int, dict]:
    ids = sorted({int(user_id) for user_id in user_ids if int(user_id) > 0})
    if not ids or not push_configured():
        return {}

    marks = ",".join("?" for _ in ids)
    conn = connect_db()
    try:
        rows = conn.execute(
            f"""SELECT user_id,endpoint,p256dh,auth
                FROM push_subscriptions
                WHERE user_id IN ({marks})""",
            tuple(ids),
        ).fetchall()
        grouped: dict[int, list[dict]] = {}
        for row in rows:
            grouped.setdefault(int(row["user_id"]), []).append(
                {
                    "endpoint": row["endpoint"],
                    "p256dh": row["p256dh"],
                    "auth": row["auth"],
                }
            )

        contexts = {}
        for user_id in ids:
            subscriptions = grouped.get(user_id, [])
            contexts[user_id] = {
                "rows": subscriptions,
                "unread_count": (
                    unread_count_for_user(conn, user_id)
                    if subscriptions
                    else 0
                ),
            }
        return contexts
    finally:
        conn.close()


WEB_PUSH_CONCURRENCY = 16
web_push_semaphore = asyncio.Semaphore(WEB_PUSH_CONCURRENCY)


async def _send_web_push_subscription(row: dict, payload: str):
    subscription = {
        "endpoint": row["endpoint"],
        "keys": {
            "p256dh": row["p256dh"],
            "auth": row["auth"],
        },
    }
    async with web_push_semaphore:
        result = await asyncio.to_thread(
            _webpush_one,
            subscription,
            payload,
        )
    return row["endpoint"], result


async def send_web_push(
    user_id: int,
    title: str,
    body: str,
    url: str = "/",
    tag: str = "svoi",
    force: bool = False,
    silent: bool = False,
    prepared_context: dict | None = None,
):
    stats = {
        "configured": push_configured(),
        "attempted": 0,
        "sent": 0,
        "stale": 0,
        "errors": [],
    }
    if not stats["configured"]:
        return stats

    if prepared_context is None:
        conn = connect_db()
        try:
            prepared_context = web_push_context_for_user(conn, user_id)
        finally:
            conn.close()

    rows = prepared_context.get("rows", [])
    unread_count = int(prepared_context.get("unread_count", 0) or 0)

    stats["attempted"] = len(rows)
    if not rows:
        return stats

    payload = json.dumps(
        {
            "title": title,
            "body": body[:180],
            "url": url,
            "tag": tag,
            "force": force,
            "silent": silent,
            "unread_count": unread_count,
        },
        ensure_ascii=False,
    )

    results = await asyncio.gather(
        *(_send_web_push_subscription(row, payload) for row in rows),
        return_exceptions=True,
    )

    stale = []
    for item in results:
        if isinstance(item, Exception):
            stats["errors"].append(str(item)[:180])
            continue
        endpoint, result = item
        if result["ok"]:
            stats["sent"] += 1
        else:
            if result["error"]:
                stats["errors"].append(result["error"])
            if result["stale"]:
                stale.append(endpoint)

    stats["stale"] = len(stale)
    stats["errors"] = stats["errors"][:3]

    if stale:
        conn = connect_db()
        try:
            conn.executemany(
                "DELETE FROM push_subscriptions WHERE endpoint=?",
                [(endpoint,) for endpoint in stale],
            )
            conn.commit()
        finally:
            conn.close()

    return stats

@app.get("/api/push/status")
def push_status(
    user=Depends(current_user),
    conn=Depends(db),
):
    count = conn.execute(
        "SELECT COUNT(*) FROM push_subscriptions WHERE user_id=?",
        (user["id"],),
    ).fetchone()[0]
    android_count = conn.execute(
        "SELECT COUNT(*) FROM android_push_tokens WHERE user_id=?",
        (user["id"],),
    ).fetchone()[0]
    return {
        "configured": push_configured(),
        "subscriptions": count,
        "android_tokens": int(android_count),
    }


@app.post("/api/push/test")
async def test_push(user=Depends(current_user)):
    stats = await send_web_push(
        user["id"],
        "Свои",
        "Тестовое уведомление работает ✅",
        "/",
        f"test-{user['id']}",
        True,
    )
    if not stats["configured"]:
        raise HTTPException(
            503,
            "Push-ключи не настроены на сервере",
        )
    if stats["attempted"] == 0:
        raise HTTPException(
            409,
            "На этом аккаунте нет push-подписки",
        )
    if stats["sent"] == 0:
        detail = "Push-сервис не принял уведомление"
        if stats["errors"]:
            detail += ": " + "; ".join(stats["errors"])
        raise HTTPException(502, detail)
    return stats


def effective_media_mime(mime: str | None, name: str | None) -> str:
    value = (mime or "application/octet-stream").lower().split(";", 1)[0].strip()
    filename = (name or "").lower()

    if filename.startswith("voice-"):
        if filename.endswith(".m4a") or filename.endswith(".mp4"):
            return "audio/mp4"
        return "audio/webm"

    if filename.startswith("video-circle-"):
        if filename.endswith(".mp4"):
            return "video/mp4"
        return "video/webm"

    return value


def attachment_thumbnail_url(stored_name: str, mime: str) -> str | None:
    if mime in INLINE_IMAGE_TYPES or mime.startswith("video/"):
        return f"/uploads/thumb/{stored_name}.jpg"
    return None


def attachment_json(row):
    if not row or row["attachment_id"] is None:
        return None
    mime = effective_media_mime(
        row["attachment_mime"],
        row["attachment_name"],
    )
    return {
        "id": row["attachment_id"],
        "url": f"/uploads/{row['attachment_stored_name']}",
        "thumbnail_url": attachment_thumbnail_url(
            row["attachment_stored_name"],
            mime,
        ),
        "name": row["attachment_name"],
        "mime_type": mime,
        "size": row["attachment_size"],
        "is_image": mime in INLINE_IMAGE_TYPES,
        "is_audio": mime.startswith("audio/"),
        "is_video": mime.startswith("video/"),
    }


MENTION_RE = re.compile(r"(?<![A-Za-z0-9_.-])@([A-Za-z0-9_.-]{3,32})")


def resolve_group_mentions(conn, group_id: int, body: str) -> set[int]:
    usernames = {
        match.group(1).lower()
        for match in MENTION_RE.finditer(body or "")
    }
    if not usernames:
        return set()

    marks = ",".join("?" for _ in usernames)
    rows = conn.execute(
        f"""SELECT u.id
            FROM group_members gm
            JOIN users u ON u.id=gm.user_id
            WHERE gm.group_id=?
              AND u.username IN ({marks})""",
        (group_id, *sorted(usernames)),
    ).fetchall()
    return {int(row["id"]) for row in rows}


def store_group_mentions(conn, message_id: int, user_ids: set[int]):
    if not user_ids:
        return
    conn.executemany(
        """INSERT OR IGNORE INTO group_message_mentions(message_id,user_id)
           VALUES(?,?)""",
        [(message_id, user_id) for user_id in sorted(user_ids)],
    )


def owned_upload(conn, attachment_id: int | None, user_id: int):
    if attachment_id is None:
        return None
    row = conn.execute(
        """SELECT id,stored_name,original_name,mime_type,size
           FROM uploads WHERE id=? AND owner_id=?""",
        (attachment_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(400, "Вложение не найдено")
    return row


def validate_chat_background_target(conn, user_id: int, chat_type: str, chat_id: int):
    if chat_type == "user":
        if chat_id == user_id:
            raise HTTPException(400, "Нельзя выбрать фон для чата с самим собой")
        row = conn.execute(
            "SELECT 1 FROM users WHERE id=?",
            (chat_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Пользователь не найден")
        return
    if chat_type == "group":
        if not group_for_user(conn, chat_id, user_id):
            raise HTTPException(404, "Группа не найдена")
        return
    raise HTTPException(400, "Неизвестный тип чата")


@app.get("/api/chat-background")
def get_chat_background(
    chat_type: str,
    chat_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    validate_chat_background_target(conn, user["id"], chat_type, chat_id)
    row = conn.execute(
        """SELECT cb.upload_id,u.stored_name,u.original_name,u.mime_type,u.size
           FROM chat_backgrounds cb
           JOIN uploads u ON u.id=cb.upload_id
           WHERE cb.user_id=? AND cb.chat_type=? AND cb.chat_id=?""",
        (user["id"], chat_type, chat_id),
    ).fetchone()
    if not row:
        return {"background_url": None}
    return {
        "background_url": f"/uploads/{row['stored_name']}",
        "upload_id": row["upload_id"],
        "name": row["original_name"],
        "mime_type": row["mime_type"],
        "size": row["size"],
    }


@app.post("/api/chat-background")
async def set_chat_background(
    chat_type: str,
    chat_id: int,
    file: UploadFile = File(...),
    user=Depends(current_user),
    conn=Depends(db),
):
    validate_chat_background_target(conn, user["id"], chat_type, chat_id)

    mime = (file.content_type or "").lower().split(";", 1)[0].strip()
    if mime not in AVATAR_IMAGE_TYPES:
        await file.close()
        raise HTTPException(400, "Для фона выбери JPEG, PNG или WebP")

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if not content:
        raise HTTPException(400, "Файл пустой")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Фон больше 20 МБ")

    suffix = _avatar_suffix(content, mime)
    stored = "chat-bg-" + secrets.token_hex(24) + suffix
    path = UPLOAD_DIR / stored
    path.write_bytes(content)

    old = conn.execute(
        """SELECT cb.upload_id,u.stored_name
           FROM chat_backgrounds cb
           LEFT JOIN uploads u ON u.id=cb.upload_id
           WHERE cb.user_id=? AND cb.chat_type=? AND cb.chat_id=?""",
        (user["id"], chat_type, chat_id),
    ).fetchone()

    try:
        cur = conn.execute(
            """INSERT INTO uploads(
                 owner_id,stored_name,original_name,mime_type,size,created_at
               ) VALUES(?,?,?,?,?,?)""",
            (
                user["id"],
                stored,
                f"chat-background{suffix}",
                mime,
                len(content),
                now_iso(),
            ),
        )
        upload_id = cur.lastrowid
        conn.execute(
            """INSERT INTO chat_backgrounds(
                 user_id,chat_type,chat_id,upload_id,updated_at
               ) VALUES(?,?,?,?,?)
               ON CONFLICT(user_id,chat_type,chat_id)
               DO UPDATE SET upload_id=excluded.upload_id,updated_at=excluded.updated_at""",
            (user["id"], chat_type, chat_id, upload_id, now_iso()),
        )
        conn.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise

    if old and old["upload_id"]:
        conn.execute(
            "DELETE FROM uploads WHERE id=? AND owner_id=?",
            (old["upload_id"], user["id"]),
        )
        conn.commit()
        if old["stored_name"]:
            (UPLOAD_DIR / old["stored_name"]).unlink(missing_ok=True)

    return {
        "background_url": f"/uploads/{stored}",
        "upload_id": upload_id,
        "mime_type": mime,
        "size": len(content),
    }


@app.delete("/api/chat-background")
def delete_chat_background(
    chat_type: str,
    chat_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    validate_chat_background_target(conn, user["id"], chat_type, chat_id)
    old = conn.execute(
        """SELECT cb.upload_id,u.stored_name
           FROM chat_backgrounds cb
           LEFT JOIN uploads u ON u.id=cb.upload_id
           WHERE cb.user_id=? AND cb.chat_type=? AND cb.chat_id=?""",
        (user["id"], chat_type, chat_id),
    ).fetchone()
    if not old:
        return {"ok": True, "background_url": None}

    conn.execute(
        "DELETE FROM chat_backgrounds WHERE user_id=? AND chat_type=? AND chat_id=?",
        (user["id"], chat_type, chat_id),
    )
    conn.execute(
        "DELETE FROM uploads WHERE id=? AND owner_id=?",
        (old["upload_id"], user["id"]),
    )
    conn.commit()
    if old["stored_name"]:
        (UPLOAD_DIR / old["stored_name"]).unlink(missing_ok=True)
    return {"ok": True, "background_url": None}




def optimize_uploaded_mp4(path: Path, mime: str, original_name: str) -> bool:
    if mime != "video/mp4":
        return False
    if Path(original_name).suffix.lower() != ".mp4":
        return False
    try:
        original_size = path.stat().st_size
    except OSError:
        return False
    if original_size < VIDEO_OPTIMIZE_TRIGGER_BYTES:
        return False

    candidate = path.with_name(path.name + ".optimized.mp4")
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(path),
        "-map", "0:v:0",
        "-map", "0:a?",
        "-map_metadata", "-1",
        "-vf", "scale=1280:1280:force_original_aspect_ratio=decrease:force_divisible_by=2",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "96k",
        "-ac", "2",
        "-movflags", "+faststart",
        str(candidate),
    ]
    try:
        subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=90,
            check=True,
        )
        optimized_size = candidate.stat().st_size
        if (
            optimized_size > 0
            and optimized_size < original_size * VIDEO_OPTIMIZE_MIN_SAVING_RATIO
        ):
            os.replace(candidate, path)
            return True
    except Exception:
        pass
    finally:
        candidate.unlink(missing_ok=True)
    return False


def generate_media_thumbnail(path: Path, stored_name: str, mime: str) -> None:
    if not (mime in INLINE_IMAGE_TYPES or mime.startswith("video/")):
        return
    target = THUMB_DIR / f"{stored_name}.jpg"
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
    ]
    if mime.startswith("video/"):
        command += ["-ss", "0.35", "-i", str(path), "-frames:v", "1"]
    else:
        command += ["-i", str(path), "-frames:v", "1"]
    command += [
        "-vf",
        "scale='min(640,iw)':-2:flags=lanczos",
        "-q:v", "5",
        str(target),
    ]
    try:
        subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=12,
            check=True,
        )
    except Exception:
        target.unlink(missing_ok=True)


@app.post("/api/uploads")
async def upload(
    file: UploadFile = File(...),
    user=Depends(current_user),
    conn=Depends(db),
):
    original = Path(file.filename or "file").name[:255] or "file"
    suffix = Path(original).suffix.lower()
    if len(suffix) > 12 or not all(ch.isalnum() or ch == "." for ch in suffix):
        suffix = ""
    stored = secrets.token_hex(24) + suffix
    mime = effective_media_mime(
        (file.content_type or "application/octet-stream")[:120],
        original,
    )
    path = UPLOAD_DIR / stored
    total = 0

    try:
        with path.open("wb") as target:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Файл больше 20 МБ")
                target.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    if mime == "video/mp4":
        await asyncio.to_thread(
            optimize_uploaded_mp4,
            path,
            mime,
            original,
        )
        try:
            total = path.stat().st_size
        except OSError:
            path.unlink(missing_ok=True)
            raise HTTPException(500, "Не удалось подготовить видео")

    if mime in INLINE_IMAGE_TYPES or mime.startswith("video/"):
        await asyncio.to_thread(
            generate_media_thumbnail,
            path,
            stored,
            mime,
        )

    try:
        cur = conn.execute(
            """INSERT INTO uploads(
                 owner_id,stored_name,original_name,mime_type,size,created_at
               ) VALUES(?,?,?,?,?,?)""",
            (user["id"], stored, original, mime, total, now_iso()),
        )
        conn.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise

    return {
        "id": cur.lastrowid,
        "url": f"/uploads/{stored}",
        "thumbnail_url": attachment_thumbnail_url(stored, mime),
        "name": original,
        "mime_type": mime,
        "size": total,
        "is_image": mime in INLINE_IMAGE_TYPES,
        "is_audio": mime.startswith("audio/"),
        "is_video": mime.startswith("video/"),
    }


@app.get("/uploads/thumb/{thumb_name}")
def get_upload_thumbnail(thumb_name: str, conn=Depends(db)):
    if Path(thumb_name).name != thumb_name or not thumb_name.endswith(".jpg"):
        raise HTTPException(404, "Превью не найдено")
    stored_name = thumb_name[:-4]
    row = conn.execute(
        "SELECT 1 FROM uploads WHERE stored_name=?",
        (stored_name,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Превью не найдено")
    path = THUMB_DIR / thumb_name
    if not path.is_file():
        raise HTTPException(404, "Превью не найдено")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=604800, immutable"},
    )


@app.get("/uploads/{stored_name}")
def get_upload(stored_name: str, conn=Depends(db)):
    if Path(stored_name).name != stored_name:
        raise HTTPException(404, "Файл не найден")
    row = conn.execute(
        """SELECT stored_name,original_name,mime_type
           FROM uploads WHERE stored_name=?""",
        (stored_name,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Файл не найден")
    path = UPLOAD_DIR / row["stored_name"]
    if not path.is_file():
        raise HTTPException(404, "Файл не найден")
    mime = effective_media_mime(
        row["mime_type"],
        row["original_name"],
    )
    cache_headers = {
        "Cache-Control": "private, max-age=604800, immutable",
    }
    if (
        mime in INLINE_IMAGE_TYPES
        or mime.startswith("audio/")
        or mime.startswith("video/")
    ):
        return FileResponse(
            path,
            media_type=mime,
            headers=cache_headers,
        )
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=row["original_name"],
        headers=cache_headers,
    )


CHAT_GALLERY_KINDS = {"photos", "videos", "files", "links", "voice"}
CHAT_GALLERY_LINK_RE = re.compile(r"""https?://[^\s<>"']+""", re.IGNORECASE)


def chat_gallery_links(body: str | None) -> list[str]:
    found = []
    seen = set()
    for match in CHAT_GALLERY_LINK_RE.finditer(body or ""):
        value = match.group(0).rstrip(".,!?;:)]}")
        if not value or value in seen:
            continue
        seen.add(value)
        found.append(value)
    return found


def chat_gallery_attachment_kind(row) -> tuple[str | None, dict | None]:
    attachment = attachment_json(row)
    if not attachment:
        return None, None
    if attachment["is_image"]:
        return "photos", attachment
    if attachment["is_video"]:
        return "videos", attachment
    if attachment["is_audio"]:
        return "voice", attachment
    return "files", attachment


@app.get("/api/chat-gallery")
def chat_gallery(
    chat_type: str,
    chat_id: int,
    kind: str = Query(...),
    limit: int = Query(30, ge=1, le=60),
    before_id: int | None = Query(default=None, ge=1),
    user=Depends(current_user),
    conn=Depends(db),
):
    chat_type = (chat_type or "").strip().lower()
    kind = (kind or "").strip().lower()
    if chat_type not in {"user", "group"}:
        raise HTTPException(400, "Неизвестный тип чата")
    if kind not in CHAT_GALLERY_KINDS:
        raise HTTPException(400, "Неизвестный раздел галереи")

    if chat_type == "user":
        if chat_id == user["id"]:
            raise HTTPException(400, "Нельзя открыть чат с самим собой")
        if not conn.execute(
            "SELECT 1 FROM users WHERE id=?",
            (chat_id,),
        ).fetchone():
            raise HTTPException(404, "Пользователь не найден")
    else:
        if not group_for_user(conn, chat_id, user["id"]):
            raise HTTPException(404, "Группа не найдена")

    items = []
    cursor = before_id
    scan_limit = max(60, min(200, limit * 4))
    exhausted = False
    stopped_mid_batch = False

    while len(items) < limit and not exhausted:
        candidate_filter = (
            " AND m.body LIKE '%http%'"
            if kind == "links" and chat_type == "user"
            else " AND m.attachment_id IS NOT NULL"
            if chat_type == "user"
            else " AND gm.body LIKE '%http%'"
            if kind == "links"
            else " AND gm.attachment_id IS NOT NULL"
        )

        if chat_type == "user":
            rows = conn.execute(
                f"""SELECT m.id,m.sender_id,m.body,m.created_at,
                           sender.display_name AS sender_name,
                           up.id AS attachment_id,
                           up.stored_name AS attachment_stored_name,
                           up.original_name AS attachment_name,
                           up.mime_type AS attachment_mime,
                           up.size AS attachment_size
                    FROM messages m
                    JOIN users sender ON sender.id=m.sender_id
                    LEFT JOIN uploads up ON up.id=m.attachment_id
                    WHERE (
                         (m.sender_id=? AND m.recipient_id=?)
                         OR (m.sender_id=? AND m.recipient_id=?)
                    )
                      AND (? IS NULL OR m.id<?)
                      AND NOT EXISTS(
                        SELECT 1 FROM message_hidden_by_user mh
                        WHERE mh.message_id=m.id AND mh.user_id=?
                      )
                      {candidate_filter}
                    ORDER BY m.id DESC
                    LIMIT ?""",
                (
                    user["id"],
                    chat_id,
                    chat_id,
                    user["id"],
                    cursor,
                    cursor,
                    user["id"],
                    scan_limit,
                ),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""SELECT gm.id,gm.sender_id,gm.body,gm.created_at,
                           sender.display_name AS sender_name,
                           up.id AS attachment_id,
                           up.stored_name AS attachment_stored_name,
                           up.original_name AS attachment_name,
                           up.mime_type AS attachment_mime,
                           up.size AS attachment_size
                    FROM group_messages gm
                    JOIN users sender ON sender.id=gm.sender_id
                    LEFT JOIN uploads up ON up.id=gm.attachment_id
                    WHERE gm.group_id=?
                      AND gm.deleted_at IS NULL
                      AND (? IS NULL OR gm.id<?)
                      AND NOT EXISTS(
                        SELECT 1 FROM group_message_hidden_by_user gh
                        WHERE gh.message_id=gm.id AND gh.user_id=?
                      )
                      {candidate_filter}
                    ORDER BY gm.id DESC
                    LIMIT ?""",
                (
                    chat_id,
                    cursor,
                    cursor,
                    user["id"],
                    scan_limit,
                ),
            ).fetchall()

        if not rows:
            exhausted = True
            break

        exhausted = len(rows) < scan_limit
        for index, row in enumerate(rows):
            cursor = int(row["id"])
            if kind == "links":
                links = chat_gallery_links(row["body"])
                if not links:
                    continue
                items.append(
                    {
                        "message_id": row["id"],
                        "sender_id": row["sender_id"],
                        "sender_name": row["sender_name"],
                        "created_at": row["created_at"],
                        "body": row["body"],
                        "links": links,
                        "attachment": None,
                    }
                )
            else:
                attachment_kind, attachment = chat_gallery_attachment_kind(row)
                if attachment_kind != kind:
                    continue
                items.append(
                    {
                        "message_id": row["id"],
                        "sender_id": row["sender_id"],
                        "sender_name": row["sender_name"],
                        "created_at": row["created_at"],
                        "body": row["body"],
                        "links": [],
                        "attachment": attachment,
                    }
                )

            if len(items) >= limit:
                stopped_mid_batch = index < len(rows) - 1
                break

    next_before_id = cursor if cursor and (stopped_mid_batch or not exhausted) else None
    return {
        "items": items,
        "next_before_id": next_before_id,
    }


@app.get("/api/messages/{other_id}")
async def history(
    other_id: int,
    limit: int = Query(50, ge=1, le=300),
    before_id: int | None = Query(default=None, ge=1),
    user=Depends(current_user),
    conn=Depends(db),
):
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (other_id,)).fetchone():
        raise HTTPException(404, "Пользователь не найден")
    if before_id is None:
        unread_rows = conn.execute(
            """SELECT id FROM messages
               WHERE sender_id=? AND recipient_id=? AND read_at IS NULL""",
            (other_id, user["id"]),
        ).fetchall()
        message_ids = [int(row["id"]) for row in unread_rows]
        seen_at = None
        if unread_rows:
            seen_at = now_iso()
            conn.execute(
                """UPDATE messages
                   SET delivered_at=COALESCE(delivered_at, ?),
                       read_at=?
                   WHERE sender_id=? AND recipient_id=? AND read_at IS NULL""",
                (seen_at, seen_at, other_id, user["id"]),
            )
            conn.commit()
            await push(
                other_id,
                {
                    "type": "read_receipt",
                    "reader_id": user["id"],
                    "message_ids": message_ids,
                    "read_at": seen_at,
                },
            )

        # Send the authoritative local state even when another device
        # already read the chat earlier. This repairs stale counters after
        # a reconnect/offline period.
        await push(
            int(user["id"]),
            {
                "type": "chat_read_sync",
                "chat_type": "user",
                "chat_id": other_id,
                "message_ids": message_ids,
                "read_at": seen_at,
                "unread_count": 0,
            },
        )
    rows = conn.execute(
        """SELECT m.id,m.sender_id,m.recipient_id,m.body,m.created_at,m.edited_at,
                  m.delivered_at,m.read_at,m.forwarded,m.client_message_id,
                  m.reply_to_message_id,
                  up.id AS attachment_id,
                  up.stored_name AS attachment_stored_name,
                  up.original_name AS attachment_name,
                  up.mime_type AS attachment_mime,
                  up.size AS attachment_size
           FROM messages m
           LEFT JOIN uploads up ON up.id=m.attachment_id
           WHERE (
                (m.sender_id=? AND m.recipient_id=?)
                OR (m.sender_id=? AND m.recipient_id=?)
           )
             AND (? IS NULL OR m.id<?)
             AND NOT EXISTS(
               SELECT 1 FROM message_hidden_by_user mh
               WHERE mh.message_id=m.id AND mh.user_id=?
             )
           ORDER BY m.id DESC LIMIT ?""",
        (
            user["id"],
            other_id,
            other_id,
            user["id"],
            before_id,
            before_id,
            user["id"],
            limit,
        ),
    ).fetchall()
    result = []
    for row in reversed(rows):
        item = {
            "id": row["id"],
            "sender_id": row["sender_id"],
            "recipient_id": row["recipient_id"],
            "body": row["body"],
            "created_at": row["created_at"],
            "edited_at": row["edited_at"],
            "delivered_at": row["delivered_at"],
            "read_at": row["read_at"],
            "forwarded": bool(row["forwarded"]),
            "client_message_id": row["client_message_id"],
            "reply_to_message_id": row["reply_to_message_id"],
            "attachment": attachment_json(row),
        }
        result.append(item)
    return result


@app.post("/api/messages/{other_id}/read")
async def mark_private_messages_read(
    other_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    if not conn.execute(
        "SELECT 1 FROM users WHERE id=?",
        (other_id,),
    ).fetchone():
        raise HTTPException(404, "Пользователь не найден")

    unread_rows = conn.execute(
        """SELECT id FROM messages
           WHERE sender_id=? AND recipient_id=? AND read_at IS NULL
           ORDER BY id""",
        (other_id, user["id"]),
    ).fetchall()

    if not unread_rows:
        await push(
            int(user["id"]),
            {
                "type": "chat_read_sync",
                "chat_type": "user",
                "chat_id": other_id,
                "message_ids": [],
                "read_at": None,
                "unread_count": 0,
            },
        )
        return {"ok": True, "message_ids": [], "read_at": None}

    seen_at = now_iso()
    conn.execute(
        """UPDATE messages
           SET delivered_at=COALESCE(delivered_at, ?),
               read_at=?
           WHERE sender_id=? AND recipient_id=? AND read_at IS NULL""",
        (seen_at, seen_at, other_id, user["id"]),
    )
    conn.commit()

    message_ids = [int(row["id"]) for row in unread_rows]
    await push(
        other_id,
        {
            "type": "read_receipt",
            "reader_id": user["id"],
            "message_ids": message_ids,
            "read_at": seen_at,
        },
    )
    await push(
        int(user["id"]),
        {
            "type": "chat_read_sync",
            "chat_type": "user",
            "chat_id": other_id,
            "message_ids": message_ids,
            "read_at": seen_at,
            "unread_count": 0,
        },
    )
    return {
        "ok": True,
        "message_ids": message_ids,
        "read_at": seen_at,
    }


def next_user_event_seq(user_id: int) -> int:
    seq = int(user_event_seq.get(user_id, 0)) + 1
    user_event_seq[user_id] = seq
    return seq


def remember_user_event(user_id: int, event: dict) -> None:
    now = time.monotonic()
    buffer = user_event_buffer.setdefault(
        user_id,
        deque(maxlen=WS_EVENT_BUFFER_LIMIT),
    )
    buffer.append((int(event["_seq"]), now, event))
    while buffer and now - buffer[0][1] > WS_EVENT_TTL_SECONDS:
        buffer.popleft()


async def flush_ws_queue(websocket: WebSocket, delay: float = WS_BATCH_DELAY_SECONDS):
    current_task = asyncio.current_task()
    if delay > 0:
        await asyncio.sleep(delay)
    state = ws_connection_state.get(websocket)
    if not state:
        return
    async with state["send_lock"]:
        queue = state.get("queue") or []
        if not queue:
            if state.get("flush_task") is current_task:
                state["flush_task"] = None
            return
        events = queue[:WS_BATCH_MAX_EVENTS]
        del queue[:len(events)]
        try:
            if len(events) == 1:
                await websocket.send_json(events[0])
            else:
                await websocket.send_json(
                    {
                        "type": "ws_batch",
                        "events": events,
                        "last_seq": int(events[-1].get("_seq", 0)),
                    }
                )
            state["last_sent_seq"] = max(
                int(state.get("last_sent_seq", 0)),
                max(int(item.get("_seq", 0)) for item in events),
            )
        except Exception:
            state["dead"] = True
        finally:
            if state.get("flush_task") is current_task:
                state["flush_task"] = None
    if state.get("queue") and not state.get("dead") and not state.get("flush_task"):
        state["flush_task"] = asyncio.create_task(
            flush_ws_queue(websocket, 0)
        )


async def send_ws_direct(websocket: WebSocket, payload: dict):
    state = ws_connection_state.get(websocket)
    if not state:
        await websocket.send_json(payload)
        return
    async with state["send_lock"]:
        await websocket.send_json(payload)


async def queue_ws_event(websocket: WebSocket, event: dict, immediate: bool = False):
    state = ws_connection_state.get(websocket)
    if not state or state.get("dead"):
        raise RuntimeError("websocket unavailable")
    state["queue"].append(event)
    task = state.get("flush_task")
    if not task or task.done():
        state["flush_task"] = asyncio.create_task(
            flush_ws_queue(
                websocket,
                0 if immediate else WS_BATCH_DELAY_SECONDS,
            )
        )
    if not immediate:
        return

    target_seq = int(event.get("_seq", 0))
    while (
        int(state.get("last_sent_seq", 0)) < target_seq
        and not state.get("dead")
    ):
        task = state.get("flush_task")
        if not task or task.done():
            state["flush_task"] = asyncio.create_task(
                flush_ws_queue(websocket, 0)
            )
            task = state["flush_task"]
        try:
            await task
        except asyncio.CancelledError:
            if state.get("dead") or websocket not in ws_connection_state:
                raise RuntimeError("websocket unavailable")
    if state.get("dead"):
        raise RuntimeError("websocket send failed")


async def push(user_id: int, payload: dict) -> int:
    dead = []
    sent = 0
    event = dict(payload)
    event["_seq"] = next_user_event_seq(user_id)
    event["_sent_at"] = now_iso()
    remember_user_event(user_id, event)
    immediate = str(event.get("type", "")) in WS_IMMEDIATE_TYPES
    for ws in list(connections.get(user_id, set())):
        try:
            await queue_ws_event(ws, event, immediate=immediate)
            sent += 1
        except Exception:
            dead.append(ws)
    for ws in dead:
        connections.get(user_id, set()).discard(ws)
        state = ws_connection_state.pop(ws, None)
        task = state.get("flush_task") if state else None
        if task and not task.done():
            task.cancel()
    return sent


GROUP_DELIVERY_CONCURRENCY = 12
group_delivery_semaphore = asyncio.Semaphore(GROUP_DELIVERY_CONCURRENCY)


async def deliver_group_recipient(
    recipient_id: int,
    payload: dict,
    notification: dict | None = None,
    push_context: dict | None = None,
):
    async with group_delivery_semaphore:
        jobs = [push(recipient_id, payload)]
        if notification:
            jobs.append(
                send_web_push(
                    recipient_id,
                    notification["title"],
                    notification["body"],
                    notification.get("url", "/"),
                    notification.get("tag", "svoi"),
                    notification.get("force", False),
                    notification.get("silent", False),
                    prepared_context=push_context,
                )
            )
        await asyncio.gather(*jobs, return_exceptions=True)


async def deliver_group_batch(deliveries):
    if not deliveries:
        return

    push_contexts = web_push_contexts_for_users(
        item["recipient_id"]
        for item in deliveries
        if item.get("notification")
    )
    await asyncio.gather(
        *(
            deliver_group_recipient(
                item["recipient_id"],
                item["payload"],
                item.get("notification"),
                push_contexts.get(int(item["recipient_id"])),
            )
            for item in deliveries
        ),
        return_exceptions=True,
    )


@app.post("/api/messages")
async def send_message(data: MessageIn, user=Depends(current_user), conn=Depends(db)):
    if data.recipient_id == user["id"]:
        raise HTTPException(400, "Нельзя отправить сообщение самому себе")
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (data.recipient_id,)).fetchone():
        raise HTTPException(404, "Пользователь не найден")
    if users_blocked(conn, user["id"], data.recipient_id):
        raise HTTPException(403, "Личное общение с этим пользователем недоступно")
    if data.client_message_id:
        existing = conn.execute(
            """SELECT m.id,m.sender_id,m.recipient_id,m.body,m.created_at,m.edited_at,
                      m.delivered_at,m.read_at,m.forwarded,m.client_message_id,
                      m.reply_to_message_id,
                      up.id AS attachment_id,
                      up.stored_name AS attachment_stored_name,
                      up.original_name AS attachment_name,
                      up.mime_type AS attachment_mime,
                      up.size AS attachment_size
               FROM messages m
               LEFT JOIN uploads up ON up.id=m.attachment_id
               WHERE m.sender_id=? AND m.client_message_id=?""",
            (user["id"], data.client_message_id),
        ).fetchone()
        if existing:
            if existing["recipient_id"] != data.recipient_id:
                raise HTTPException(409, "Идентификатор сообщения уже использован")
            return {
                "id": existing["id"],
                "sender_id": existing["sender_id"],
                "recipient_id": existing["recipient_id"],
                "body": existing["body"],
                "created_at": existing["created_at"],
                "edited_at": existing["edited_at"],
                "delivered_at": existing["delivered_at"],
                "read_at": existing["read_at"],
                "forwarded": bool(existing["forwarded"]),
                "client_message_id": existing["client_message_id"],
                "reply_to_message_id": existing["reply_to_message_id"],
                "attachment": attachment_json(existing),
            }
    if data.reply_to_message_id is not None:
        reply_row = conn.execute(
            """SELECT id FROM messages
               WHERE id=?
                 AND (
                   (sender_id=? AND recipient_id=?)
                   OR (sender_id=? AND recipient_id=?)
                 )""",
            (
                data.reply_to_message_id,
                user["id"],
                data.recipient_id,
                data.recipient_id,
                user["id"],
            ),
        ).fetchone()
        if not reply_row:
            raise HTTPException(400, "Сообщение для ответа не найдено в этом чате")

    body = data.body.strip()
    upload_row = owned_upload(conn, data.attachment_id, user["id"])
    if not body and not upload_row:
        raise HTTPException(400, "Пустое сообщение")
    created = now_iso()
    cur = conn.execute(
        """INSERT INTO messages(
             sender_id,recipient_id,body,created_at,attachment_id,
             delivered_at,read_at,client_message_id,reply_to_message_id
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            user["id"],
            data.recipient_id,
            body,
            created,
            data.attachment_id,
            None,
            None,
            data.client_message_id,
            data.reply_to_message_id,
        ),
    )
    conn.commit()
    attachment = None
    if upload_row:
        attachment_mime = effective_media_mime(
            upload_row["mime_type"],
            upload_row["original_name"],
        )
        attachment = {
            "id": upload_row["id"],
            "url": f"/uploads/{upload_row['stored_name']}",
            "name": upload_row["original_name"],
            "mime_type": attachment_mime,
            "size": upload_row["size"],
            "is_image": attachment_mime in INLINE_IMAGE_TYPES,
            "is_audio": attachment_mime.startswith("audio/"),
            "is_video": attachment_mime.startswith("video/"),
        }
    msg = {
        "id": cur.lastrowid,
        "sender_id": user["id"],
        "recipient_id": data.recipient_id,
        "body": body,
        "created_at": created,
        "edited_at": None,
        "delivered_at": None,
        "read_at": None,
        "forwarded": False,
        "client_message_id": data.client_message_id,
        "reply_to_message_id": data.reply_to_message_id,
        "attachment": attachment,
    }
    sent = await push(
        data.recipient_id,
        {"type": "message", "message": msg},
    )
    if sent:
        delivered_at = now_iso()
        conn.execute(
            "UPDATE messages SET delivered_at=? WHERE id=?",
            (delivered_at, cur.lastrowid),
        )
        conn.commit()
        msg["delivered_at"] = delivered_at

    # Keep every active session of the sender in sync as well. The device
    # that made the HTTP request will receive the same event, but the client
    # deduplicates by message id.
    await push(
        int(user["id"]),
        {"type": "message", "message": msg},
    )

    if body:
        preview = body
    elif attachment and attachment.get("is_audio"):
        preview = "🎙 Голосовое сообщение"
    elif attachment and attachment.get("is_video"):
        preview = "◉ Видеокружок"
    elif attachment:
        preview = "📎 " + attachment["name"]
    else:
        preview = "Новое сообщение"
    push_context = web_push_context_for_user(conn, data.recipient_id)
    await send_web_push(
        data.recipient_id,
        user["display_name"],
        preview,
        "/",
        f"user-{user['id']}",
        silent=is_chat_muted(
            conn,
            data.recipient_id,
            "user",
            user["id"],
        ),
        prepared_context=push_context,
    )
    return msg


@app.patch("/api/messages/{message_id}")
async def edit_message(
    message_id: int,
    data: EditMessageIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    row = conn.execute(
        """SELECT m.id,m.sender_id,m.recipient_id,m.body,m.created_at,m.edited_at,
                  m.delivered_at,m.read_at,m.forwarded,m.reply_to_message_id,
                  up.id AS attachment_id,
                  up.stored_name AS attachment_stored_name,
                  up.original_name AS attachment_name,
                  up.mime_type AS attachment_mime,
                  up.size AS attachment_size
           FROM messages m
           LEFT JOIN uploads up ON up.id=m.attachment_id
           WHERE m.id=?""",
        (message_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Сообщение не найдено")
    if int(row["sender_id"]) != int(user["id"]):
        raise HTTPException(403, "Редактировать можно только свои сообщения")

    body = data.body.strip()
    if not body and row["attachment_id"] is None:
        raise HTTPException(400, "Сообщение не может быть пустым")

    edited_at = now_iso()
    conn.execute(
        "UPDATE messages SET body=?, edited_at=? WHERE id=? AND sender_id=?",
        (body, edited_at, message_id, user["id"]),
    )
    conn.commit()

    message = {
        "id": row["id"],
        "sender_id": row["sender_id"],
        "recipient_id": row["recipient_id"],
        "body": body,
        "created_at": row["created_at"],
        "edited_at": edited_at,
        "delivered_at": row["delivered_at"],
        "read_at": row["read_at"],
        "forwarded": bool(row["forwarded"]),
        "reply_to_message_id": row["reply_to_message_id"],
        "attachment": attachment_json(row),
    }
    event = {"type": "message_edited", "message": message}
    await push(int(row["recipient_id"]), event)
    await push(int(user["id"]), event)
    return message


@app.delete("/api/messages/{message_id}/me")
async def delete_message_for_me(
    message_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    row = conn.execute(
        """SELECT id,sender_id,recipient_id FROM messages
           WHERE id=? AND (sender_id=? OR recipient_id=?)""",
        (message_id, user["id"], user["id"]),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Сообщение не найдено")
    hidden_at = now_iso()
    conn.execute(
        """INSERT OR REPLACE INTO message_hidden_by_user(
             message_id,user_id,hidden_at
           ) VALUES(?,?,?)""",
        (message_id, user["id"], hidden_at),
    )
    conn.commit()
    peer_id = (
        int(row["recipient_id"])
        if int(row["sender_id"]) == int(user["id"])
        else int(row["sender_id"])
    )
    await push(
        int(user["id"]),
        {
            "type": "message_hidden_for_me",
            "chat_type": "user",
            "chat_id": peer_id,
            "message_id": message_id,
            "hidden_at": hidden_at,
        },
    )
    return {"ok": True, "message_id": message_id}


@app.delete("/api/messages/{message_id}/all")
async def delete_message_for_all(
    message_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    row = conn.execute(
        """SELECT id,sender_id,recipient_id FROM messages
           WHERE id=?""",
        (message_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Сообщение не найдено")
    if int(row["sender_id"]) != int(user["id"]):
        raise HTTPException(403, "Удалить для всех можно только своё сообщение")

    recipient_id = int(row["recipient_id"])
    conn.execute("DELETE FROM messages WHERE id=?", (message_id,))
    conn.commit()

    event = {
        "type": "message_deleted_all",
        "message_id": message_id,
    }
    await push(recipient_id, event)
    await push(int(user["id"]), event)
    return {"ok": True, "message_id": message_id}


@app.get("/api/messages/{message_id}/seen-by")
def private_message_seen_by(
    message_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    row = conn.execute(
        """SELECT m.id,m.sender_id,m.recipient_id,m.read_at,
                  u.id AS viewer_id,u.username,u.display_name,
                  a.stored_name AS avatar_stored_name
           FROM messages m
           JOIN users u ON u.id=m.recipient_id
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE m.id=? AND (m.sender_id=? OR m.recipient_id=?)""",
        (message_id, user["id"], user["id"]),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Сообщение не найдено")

    viewers = []
    if row["read_at"]:
        viewers.append(
            {
                **user_json(
                    {
                        "id": row["viewer_id"],
                        "username": row["username"],
                        "display_name": row["display_name"],
                        "avatar_stored_name": row["avatar_stored_name"],
                    }
                ),
                "read_at": row["read_at"],
            }
        )
    return {"message_id": message_id, "viewers": viewers}


@app.post("/api/messages/forward")
async def forward_message(
    data: ForwardMessageIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    source = None
    if data.source_type == "user":
        source = conn.execute(
            """SELECT m.id,m.sender_id,m.recipient_id,m.body,m.attachment_id,
                      up.id AS attachment_id,
                      up.stored_name AS attachment_stored_name,
                      up.original_name AS attachment_name,
                      up.mime_type AS attachment_mime,
                      up.size AS attachment_size
               FROM messages m
               LEFT JOIN uploads up ON up.id=m.attachment_id
               WHERE m.id=?
                 AND (m.sender_id=? OR m.recipient_id=?)""",
            (data.source_message_id, user["id"], user["id"]),
        ).fetchone()
    else:
        source = conn.execute(
            """SELECT gm.id,gm.group_id,gm.sender_id,gm.body,gm.attachment_id,
                      gm.deleted_at,
                      up.id AS attachment_id,
                      up.stored_name AS attachment_stored_name,
                      up.original_name AS attachment_name,
                      up.mime_type AS attachment_mime,
                      up.size AS attachment_size
               FROM group_messages gm
               LEFT JOIN uploads up ON up.id=gm.attachment_id
               WHERE gm.id=?""",
            (data.source_message_id,),
        ).fetchone()
        if (
            not source
            or source["deleted_at"]
            or not group_for_user(conn, source["group_id"], user["id"])
        ):
            source = None

    if not source:
        raise HTTPException(404, "Исходное сообщение недоступно")

    body = source["body"] or ""
    attachment_id = source["attachment_id"]
    if not body and not attachment_id:
        raise HTTPException(400, "Нечего пересылать")

    created = now_iso()
    attachment = attachment_json(source)

    if data.target_type == "user":
        if data.target_chat_id == user["id"]:
            raise HTTPException(400, "Нельзя переслать сообщение самому себе")
        target = conn.execute(
            "SELECT id FROM users WHERE id=?",
            (data.target_chat_id,),
        ).fetchone()
        if not target:
            raise HTTPException(404, "Получатель не найден")
        if users_blocked(conn, user["id"], data.target_chat_id):
            raise HTTPException(403, "Личное общение с этим пользователем недоступно")

        cur = conn.execute(
            """INSERT INTO messages(
                 sender_id,recipient_id,body,created_at,attachment_id,
                 delivered_at,read_at,forwarded
               ) VALUES(?,?,?,?,?,?,?,1)""",
            (
                user["id"],
                data.target_chat_id,
                body,
                created,
                attachment_id,
                None,
                None,
            ),
        )
        conn.commit()

        msg = {
            "id": cur.lastrowid,
            "sender_id": user["id"],
            "recipient_id": data.target_chat_id,
            "body": body,
            "created_at": created,
            "delivered_at": None,
            "read_at": None,
            "forwarded": True,
            "attachment": attachment,
        }

        sent = await push(
            data.target_chat_id,
            {"type": "message", "message": msg},
        )
        if sent:
            delivered_at = now_iso()
            conn.execute(
                "UPDATE messages SET delivered_at=? WHERE id=?",
                (delivered_at, cur.lastrowid),
            )
            conn.commit()
            msg["delivered_at"] = delivered_at

        preview = body or (
            "🎙 Голосовое сообщение" if attachment and attachment.get("is_audio")
            else "◉ Видеокружок" if attachment and attachment.get("is_video")
            else "📎 " + attachment["name"] if attachment
            else "Пересланное сообщение"
        )
        push_context = web_push_context_for_user(
            conn,
            data.target_chat_id,
        )
        await send_web_push(
            data.target_chat_id,
            user["display_name"],
            "↪ " + preview,
            "/",
            f"user-{user['id']}",
            silent=is_chat_muted(
                conn,
                data.target_chat_id,
                "user",
                user["id"],
            ),
            prepared_context=push_context,
        )
        return msg

    group = group_for_user(conn, data.target_chat_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа назначения недоступна")

    cur = conn.execute(
        """INSERT INTO group_messages(
             group_id,sender_id,body,created_at,attachment_id,forwarded
           ) VALUES(?,?,?,?,?,1)""",
        (
            data.target_chat_id,
            user["id"],
            body,
            created,
            attachment_id,
        ),
    )
    mention_ids = resolve_group_mentions(conn, data.target_chat_id, body)
    store_group_mentions(conn, cur.lastrowid, mention_ids)
    conn.commit()

    msg = {
        "id": cur.lastrowid,
        "group_id": data.target_chat_id,
        "sender_id": user["id"],
        "sender_name": user["display_name"],
        "body": body,
        "created_at": created,
        "edited_at": None,
        "attachment": attachment,
        "deleted": False,
        "deleted_at": None,
        "forwarded": True,
        "mentioned_me": user["id"] in mention_ids,
        "has_mentions": bool(mention_ids),
        "can_delete": True,
        "can_restore": False,
        "show_deleted_notice": False,
    }
    members = conn.execute(
        "SELECT user_id,is_admin FROM group_members WHERE group_id=?",
        (data.target_chat_id,),
    ).fetchall()

    preview = body or (
        "🎙 Голосовое сообщение" if attachment and attachment.get("is_audio")
        else "◉ Видеокружок" if attachment and attachment.get("is_video")
        else "📎 " + attachment["name"] if attachment
        else "Пересланное сообщение"
    )

    deliveries = []
    for member in members:
        if member["user_id"] == user["id"]:
            continue
        recipient_id = int(member["user_id"])
        live_msg = {
            **msg,
            "mentioned_me": recipient_id in mention_ids,
            "can_delete": bool(member["is_admin"]),
        }
        should_notify = (
            recipient_id in mention_ids
            if mention_ids
            else True
        )
        notification = None
        if should_notify:
            notification = {
                "title": (
                    f"Упоминание · {group['name']}"
                    if mention_ids
                    else f"{group['name']} · {user['display_name']}"
                ),
                "body": (
                    f"{user['display_name']}: ↪ {preview}"
                    if mention_ids
                    else "↪ " + preview
                ),
                "url": "/",
                "tag": f"group-{data.target_chat_id}",
                "silent": is_chat_muted(
                    conn,
                    recipient_id,
                    "group",
                    data.target_chat_id,
                ),
            }
        deliveries.append(
            {
                "recipient_id": recipient_id,
                "payload": {
                    "type": "group_message",
                    "message": live_msg,
                },
                "notification": notification,
            }
        )

    await deliver_group_batch(deliveries)
    return msg


def group_for_user(conn, group_id: int, user_id: int):
    return conn.execute(
        """SELECT g.id,g.name,g.owner_id,g.created_at,g.avatar_id,
                  ga.stored_name AS avatar_stored_name,
                  gm.is_admin AS is_admin
           FROM chat_groups g
           JOIN group_members gm ON gm.group_id=g.id
           LEFT JOIN uploads ga ON ga.id=g.avatar_id
           WHERE g.id=? AND gm.user_id=?""",
        (group_id, user_id),
    ).fetchone()


def group_unread_count_for_user(conn, group_id: int, user_id: int) -> int:
    row = conn.execute(
        """SELECT COUNT(*) AS unread_count
           FROM group_messages unread
           JOIN group_members mine
             ON mine.group_id=unread.group_id
            AND mine.user_id=?
           WHERE unread.group_id=?
             AND unread.sender_id<>?
             AND unread.deleted_at IS NULL
             AND unread.created_at>=mine.joined_at
             AND NOT EXISTS(
               SELECT 1 FROM group_message_reads gr
               WHERE gr.message_id=unread.id AND gr.user_id=?
             )
             AND NOT EXISTS(
               SELECT 1 FROM group_message_hidden_by_user gh
               WHERE gh.message_id=unread.id AND gh.user_id=?
             )""",
        (user_id, group_id, user_id, user_id, user_id),
    ).fetchone()
    return int(row["unread_count"] or 0) if row else 0


def ensure_group_message_receipts(
    conn,
    message_id: int,
    group_id: int,
    sender_id: int,
    created_at: str,
) -> None:
    # Snapshot current members who had already joined when the message was
    # created. New messages are snapshotted at send time; this branch mainly
    # backfills older messages created before receipt tracking existed.
    conn.execute(
        """INSERT OR IGNORE INTO group_message_receipts(
             message_id,user_id,delivered_at,read_at
           )
           SELECT ?,gm.user_id,NULL,NULL
           FROM group_members gm
           WHERE gm.group_id=?
             AND gm.user_id<>?
             AND gm.joined_at<=?""",
        (message_id, group_id, sender_id, created_at),
    )

    # Existing historical read receipts prove both delivery and read.
    conn.execute(
        """INSERT OR IGNORE INTO group_message_receipts(
             message_id,user_id,delivered_at,read_at
           )
           SELECT gmr.message_id,gmr.user_id,gmr.read_at,gmr.read_at
           FROM group_message_reads gmr
           WHERE gmr.message_id=? AND gmr.user_id<>?""",
        (message_id, sender_id),
    )
    conn.execute(
        """UPDATE group_message_receipts
           SET delivered_at=COALESCE(
                 delivered_at,
                 (
                   SELECT gmr.read_at
                   FROM group_message_reads gmr
                   WHERE gmr.message_id=group_message_receipts.message_id
                     AND gmr.user_id=group_message_receipts.user_id
                 )
               ),
               read_at=COALESCE(
                 read_at,
                 (
                   SELECT gmr.read_at
                   FROM group_message_reads gmr
                   WHERE gmr.message_id=group_message_receipts.message_id
                     AND gmr.user_id=group_message_receipts.user_id
                 )
               )
           WHERE message_id=?""",
        (message_id,),
    )


def group_receipt_summary(conn, message_id: int) -> dict:
    row = conn.execute(
        """SELECT
             COUNT(*) AS total_recipients,
             SUM(CASE WHEN delivered_at IS NOT NULL THEN 1 ELSE 0 END)
               AS delivered_count,
             SUM(CASE WHEN read_at IS NOT NULL THEN 1 ELSE 0 END)
               AS read_count
           FROM group_message_receipts
           WHERE message_id=?""",
        (message_id,),
    ).fetchone()
    return {
        "total_recipients": int(row["total_recipients"] or 0) if row else 0,
        "delivered_count": int(row["delivered_count"] or 0) if row else 0,
        "read_count": int(row["read_count"] or 0) if row else 0,
    }


async def push_group_receipt_updates(
    conn,
    group_id: int,
    message_ids,
) -> None:
    ids = sorted({int(value) for value in message_ids if int(value) > 0})
    if not ids:
        return

    grouped = {}
    for offset in range(0, len(ids), 400):
        chunk = ids[offset:offset + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""SELECT id,sender_id FROM group_messages
                WHERE group_id=? AND id IN ({placeholders})""",
            (group_id, *chunk),
        ).fetchall()
        for row in rows:
            sender_id = int(row["sender_id"])
            grouped.setdefault(sender_id, []).append(
                {
                    "message_id": int(row["id"]),
                    "summary": group_receipt_summary(
                        conn,
                        int(row["id"]),
                    ),
                }
            )

    tasks = []
    for sender_id, updates in grouped.items():
        for offset in range(0, len(updates), 100):
            tasks.append(
                push(
                    sender_id,
                    {
                        "type": "group_receipt_updates",
                        "group_id": group_id,
                        "updates": updates[offset:offset + 100],
                    },
                )
            )
    if tasks:
        await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )


async def push_group_receipt_update(
    conn,
    group_id: int,
    message_id: int,
) -> None:
    await push_group_receipt_updates(conn, group_id, [message_id])


@app.get("/api/groups")
def get_groups(user=Depends(current_user), conn=Depends(db)):
    now = now_iso()
    rows = conn.execute(
        """SELECT g.id,g.name,g.owner_id,g.created_at,
                  ga.stored_name AS avatar_stored_name,
                  mine.is_admin AS is_admin,
                  (
                    SELECT COUNT(*)
                    FROM group_members gm2
                    WHERE gm2.group_id=g.id
                  ) AS member_count,
                  (
                    SELECT COUNT(*) FROM group_messages unread
                    WHERE unread.group_id=g.id
                      AND unread.sender_id<>?
                      AND unread.deleted_at IS NULL
                      AND unread.created_at>=mine.joined_at
                      AND NOT EXISTS(
                        SELECT 1 FROM group_message_reads gr
                        WHERE gr.message_id=unread.id AND gr.user_id=?
                      )
                      AND NOT EXISTS(
                        SELECT 1 FROM group_message_hidden_by_user gh
                        WHERE gh.message_id=unread.id AND gh.user_id=?
                      )
                  ) AS unread_count,
                  EXISTS(
                    SELECT 1 FROM chat_mutes cm
                    WHERE cm.user_id=? AND cm.chat_type='group' AND cm.chat_id=g.id
                      AND (cm.muted_until IS NULL OR cm.muted_until>?)
                  ) AS muted,
                  (
                    SELECT cm.muted_until FROM chat_mutes cm
                    WHERE cm.user_id=? AND cm.chat_type='group' AND cm.chat_id=g.id
                      AND (cm.muted_until IS NULL OR cm.muted_until>?)
                    LIMIT 1
                  ) AS muted_until
           FROM group_members mine
           JOIN chat_groups g ON g.id=mine.group_id
           LEFT JOIN uploads ga ON ga.id=g.avatar_id
           WHERE mine.user_id=?
           ORDER BY g.id DESC""",
        (
            user["id"],
            user["id"],
            user["id"],
            user["id"],
            now,
            user["id"],
            now,
            user["id"],
        ),
    ).fetchall()
    return [group_json(r) for r in rows]

@app.post("/api/groups")
async def create_group(data: GroupCreateIn, user=Depends(current_user), conn=Depends(db)):
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "Название группы не может быть пустым")
    member_ids = sorted(set(data.member_ids + [user["id"]]))
    if len(member_ids) > 50:
        raise HTTPException(400, "В группе может быть не более 50 участников")
    marks = ",".join("?" for _ in member_ids)
    found = conn.execute(
        f"SELECT id FROM users WHERE id IN ({marks})", tuple(member_ids)
    ).fetchall()
    if len(found) != len(member_ids):
        raise HTTPException(400, "Один из пользователей не найден")
    created = now_iso()
    cur = conn.execute(
        "INSERT INTO chat_groups(name,owner_id,created_at) VALUES(?,?,?)",
        (name, user["id"], created),
    )
    group_id = cur.lastrowid
    conn.executemany(
        """INSERT INTO group_members(
             group_id,user_id,joined_at,is_admin
           ) VALUES(?,?,?,?)""",
        [
            (group_id, uid, created, 1 if uid == user["id"] else 0)
            for uid in member_ids
        ],
    )
    conn.commit()
    group = {
        "id": group_id,
        "name": name,
        "owner_id": user["id"],
        "created_at": created,
        "member_count": len(member_ids),
        "avatar_url": None,
        "is_admin": True,
    }
    for uid in member_ids:
        if uid != user["id"]:
            await push(
                uid,
                {
                    "type": "group_created",
                    "group": {**group, "is_admin": False},
                },
            )
    return group


@app.patch("/api/groups/{group_id}")
async def rename_group(
    group_id: int,
    data: GroupRenameIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")
    if not bool(group["is_admin"]):
        raise HTTPException(403, "Менять название группы может только администратор")

    name = data.name.strip()
    if not name:
        raise HTTPException(400, "Название группы не может быть пустым")

    conn.execute(
        "UPDATE chat_groups SET name=? WHERE id=?",
        (name, group_id),
    )
    conn.commit()

    row = conn.execute(
        """SELECT g.id,g.name,g.owner_id,g.created_at,
                  ga.stored_name AS avatar_stored_name,
                  gm_me.is_admin AS is_admin,
                  COUNT(gm.user_id) AS member_count
           FROM chat_groups g
           JOIN group_members gm_me
             ON gm_me.group_id=g.id AND gm_me.user_id=?
           LEFT JOIN uploads ga ON ga.id=g.avatar_id
           LEFT JOIN group_members gm ON gm.group_id=g.id
           WHERE g.id=?
           GROUP BY g.id""",
        (user["id"], group_id),
    ).fetchone()

    await _broadcast_group_update(group_id)
    return group_json(row)


@app.get("/api/groups/{group_id}/members")
def get_group_members(group_id: int, user=Depends(current_user), conn=Depends(db)):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")
    rows = conn.execute(
        """SELECT u.id,u.username,u.display_name,u.last_seen_at,
                  a.stored_name AS avatar_stored_name,
                  gm.is_admin,
                  CASE WHEN g.owner_id=u.id THEN 1 ELSE 0 END AS is_owner
           FROM group_members gm
           JOIN users u ON u.id=gm.user_id
           JOIN chat_groups g ON g.id=gm.group_id
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE gm.group_id=?
           ORDER BY is_owner DESC, gm.is_admin DESC, u.display_name""",
        (group_id,),
    ).fetchall()
    return {
        "group_id": group_id,
        "owner_id": group["owner_id"],
        "can_manage_admins": group["owner_id"] == user["id"],
        "can_add_members": bool(group["is_admin"]),
        "members": [
            {
                **user_json(r),
                "online": bool(connections.get(r["id"])),
                "last_seen_at": r["last_seen_at"],
                "is_admin": bool(r["is_admin"]),
                "is_owner": bool(r["is_owner"]),
            }
            for r in rows
        ],
    }


@app.post("/api/groups/{group_id}/admins/{member_id}")
async def add_group_admin(
    group_id: int,
    member_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")
    if group["owner_id"] != user["id"]:
        raise HTTPException(403, "Назначать администраторов может только владелец")

    member = conn.execute(
        """SELECT user_id,is_admin FROM group_members
           WHERE group_id=? AND user_id=?""",
        (group_id, member_id),
    ).fetchone()
    if not member:
        raise HTTPException(404, "Участник не найден")

    conn.execute(
        "UPDATE group_members SET is_admin=1 WHERE group_id=? AND user_id=?",
        (group_id, member_id),
    )
    conn.commit()
    await _broadcast_group_roles(group_id)
    return {"ok": True, "member_id": member_id, "is_admin": True}


@app.delete("/api/groups/{group_id}/admins/{member_id}")
async def remove_group_admin(
    group_id: int,
    member_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")
    if group["owner_id"] != user["id"]:
        raise HTTPException(403, "Снимать администраторов может только владелец")
    if member_id == group["owner_id"]:
        raise HTTPException(400, "Владелец группы всегда администратор")

    member = conn.execute(
        "SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",
        (group_id, member_id),
    ).fetchone()
    if not member:
        raise HTTPException(404, "Участник не найден")

    conn.execute(
        "UPDATE group_members SET is_admin=0 WHERE group_id=? AND user_id=?",
        (group_id, member_id),
    )
    conn.commit()
    await _broadcast_group_roles(group_id)
    return {"ok": True, "member_id": member_id, "is_admin": False}


@app.post("/api/groups/{group_id}/members")
async def add_group_member(
    group_id: int,
    data: GroupMemberAddIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")
    if not bool(group["is_admin"]):
        raise HTTPException(403, "Добавлять участников может только администратор")

    username = data.tag.strip().lower()
    if username.startswith("@"):
        username = username[1:]
    if not username:
        raise HTTPException(400, "Укажи @тег пользователя")

    target = conn.execute(
        """SELECT u.id,u.username,u.display_name,
                  a.stored_name AS avatar_stored_name
           FROM users u
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE u.username=?""",
        (username,),
    ).fetchone()
    if not target:
        raise HTTPException(404, "Пользователь не найден")

    exists = conn.execute(
        "SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",
        (group_id, target["id"]),
    ).fetchone()
    if exists:
        raise HTTPException(409, "Пользователь уже состоит в группе")

    count = conn.execute(
        "SELECT COUNT(*) AS c FROM group_members WHERE group_id=?",
        (group_id,),
    ).fetchone()["c"]
    if count >= 50:
        raise HTTPException(400, "В группе может быть не более 50 участников")

    conn.execute(
        """INSERT INTO group_members(
             group_id,user_id,joined_at,is_admin
           ) VALUES(?,?,?,0)""",
        (group_id, target["id"], now_iso()),
    )
    conn.commit()

    row = conn.execute(
        """SELECT g.id,g.name,g.owner_id,g.created_at,
                  ga.stored_name AS avatar_stored_name,
                  0 AS is_admin,
                  COUNT(gm.user_id) AS member_count
           FROM chat_groups g
           LEFT JOIN uploads ga ON ga.id=g.avatar_id
           LEFT JOIN group_members gm ON gm.group_id=g.id
           WHERE g.id=?
           GROUP BY g.id""",
        (group_id,),
    ).fetchone()
    group_data = group_json(row)

    await push(
        target["id"],
        {
            "type": "group_added",
            "group": group_data,
            "added_by": user["id"],
        },
    )
    await _broadcast_group_update(group_id)

    return {
        "ok": True,
        "group": group_data,
        "member": {
            **user_json(target),
            "online": bool(connections.get(target["id"])),
            "is_admin": False,
            "is_owner": False,
        },
    }


@app.delete("/api/groups/{group_id}/members/{member_id}")
async def remove_group_member(
    group_id: int,
    member_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")
    if not bool(group["is_admin"]):
        raise HTTPException(403, "Исключать участников может только администратор")
    if member_id == group["owner_id"]:
        raise HTTPException(400, "Владельца группы исключить нельзя")
    if member_id == user["id"]:
        raise HTTPException(400, "Нельзя исключить самого себя")

    target = conn.execute(
        """SELECT gm.user_id,gm.is_admin,u.display_name
           FROM group_members gm
           JOIN users u ON u.id=gm.user_id
           WHERE gm.group_id=? AND gm.user_id=?""",
        (group_id, member_id),
    ).fetchone()
    if not target:
        raise HTTPException(404, "Участник не найден")

    actor_is_owner = group["owner_id"] == user["id"]
    if bool(target["is_admin"]) and not actor_is_owner:
        raise HTTPException(
            403,
            "Исключить другого администратора может только владелец",
        )

    conn.execute(
        "DELETE FROM group_members WHERE group_id=? AND user_id=?",
        (group_id, member_id),
    )
    conn.commit()

    await push(
        member_id,
        {
            "type": "group_removed",
            "group_id": group_id,
            "group_name": group["name"],
            "removed_by": user["id"],
        },
    )
    await _broadcast_group_update(group_id)

    return {
        "ok": True,
        "group_id": group_id,
        "member_id": member_id,
        "member_name": target["display_name"],
    }


@app.get("/api/groups/{group_id}/messages")
async def get_group_messages(
    group_id: int,
    limit: int = Query(50, ge=1, le=300),
    before_id: int | None = Query(default=None, ge=1),
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")
    rows = conn.execute(
        """SELECT gm.id,gm.group_id,gm.sender_id,gm.body,gm.created_at,gm.edited_at,
                  gm.deleted_at,gm.deleted_by,gm.forwarded,gm.client_message_id,
                  gm.reply_to_message_id,
                  EXISTS(
                    SELECT 1 FROM group_message_mentions gmm
                    WHERE gmm.message_id=gm.id AND gmm.user_id=?
                  ) AS mentioned_me,
                  EXISTS(
                    SELECT 1 FROM group_message_mentions gmm_any
                    WHERE gmm_any.message_id=gm.id
                  ) AS has_mentions,
                  u.display_name AS sender_name,
                  up.id AS attachment_id,
                  up.stored_name AS attachment_stored_name,
                  up.original_name AS attachment_name,
                  up.mime_type AS attachment_mime,
                  up.size AS attachment_size
           FROM group_messages gm
           JOIN users u ON u.id=gm.sender_id
           LEFT JOIN uploads up ON up.id=gm.attachment_id
           WHERE gm.group_id=?
             AND (? IS NULL OR gm.id<?)
             AND NOT EXISTS(
               SELECT 1 FROM group_message_hidden_by_user gh
               WHERE gh.message_id=gm.id AND gh.user_id=?
             )
           ORDER BY gm.id DESC LIMIT ?""",
        (
            user["id"],
            group_id,
            before_id,
            before_id,
            user["id"],
            limit,
        ),
    ).fetchall()
    if before_id is None:
        read_at = now_iso()
        newly_read_rows = conn.execute(
            """SELECT gm.id,gm.sender_id,gm.created_at
               FROM group_messages gm
               JOIN group_members mine
                 ON mine.group_id=gm.group_id
                AND mine.user_id=?
               WHERE gm.group_id=?
                 AND gm.sender_id<>?
                 AND gm.deleted_at IS NULL
                 AND gm.created_at>=mine.joined_at
                 AND NOT EXISTS(
                   SELECT 1 FROM group_message_reads gr
                   WHERE gr.message_id=gm.id AND gr.user_id=?
                 )
                 AND NOT EXISTS(
                   SELECT 1 FROM group_message_hidden_by_user gh
                   WHERE gh.message_id=gm.id AND gh.user_id=?
                 )""",
            (
                user["id"],
                group_id,
                user["id"],
                user["id"],
                user["id"],
            ),
        ).fetchall()

        conn.execute(
            """INSERT OR IGNORE INTO group_message_reads(
                 message_id,user_id,read_at
               )
               SELECT gm.id,?,?
               FROM group_messages gm
               WHERE gm.group_id=?
                 AND gm.sender_id<>?
                 AND gm.deleted_at IS NULL
                 AND NOT EXISTS(
                   SELECT 1 FROM group_message_hidden_by_user gh
                   WHERE gh.message_id=gm.id AND gh.user_id=?
                 )""",
            (
                user["id"],
                read_at,
                group_id,
                user["id"],
                user["id"],
            ),
        )

        newly_read_ids = [
            int(receipt_row["id"])
            for receipt_row in newly_read_rows
        ]
        if newly_read_ids:
            conn.executemany(
                """INSERT OR IGNORE INTO group_message_receipts(
                     message_id,user_id,delivered_at,read_at
                   ) VALUES(?,?,?,?)""",
                [
                    (message_id, user["id"], read_at, read_at)
                    for message_id in newly_read_ids
                ],
            )
            conn.executemany(
                """UPDATE group_message_receipts
                   SET delivered_at=COALESCE(delivered_at, ?),
                       read_at=COALESCE(read_at, ?)
                   WHERE message_id=? AND user_id=?""",
                [
                    (read_at, read_at, message_id, user["id"])
                    for message_id in newly_read_ids
                ],
            )

        conn.commit()
        await push(
            int(user["id"]),
            {
                "type": "chat_read_sync",
                "chat_type": "group",
                "chat_id": group_id,
                "message_ids": [int(row["id"]) for row in rows],
                "read_at": read_at,
                "unread_count": 0,
            },
        )
        if newly_read_ids:
            await push_group_receipt_updates(
                conn,
                group_id,
                newly_read_ids,
            )

    result = []
    is_admin = bool(group["is_admin"])
    receipt_backfill_touched = False
    for row in reversed(rows):
        deleted = bool(row["deleted_at"])
        if deleted and not is_admin:
            continue
        item = {
            "id": row["id"],
            "group_id": row["group_id"],
            "sender_id": row["sender_id"],
            "sender_name": row["sender_name"],
            "body": "" if deleted else row["body"],
            "created_at": row["created_at"],
            "edited_at": row["edited_at"],
            "deleted": deleted,
            "deleted_at": row["deleted_at"],
            "forwarded": bool(row["forwarded"]),
            "client_message_id": row["client_message_id"],
            "reply_to_message_id": row["reply_to_message_id"],
            "mentioned_me": bool(row["mentioned_me"]) and not deleted,
            "has_mentions": bool(row["has_mentions"]) and not deleted,
            "can_delete": (
                not deleted
                and (is_admin or row["sender_id"] == user["id"])
            ),
            "can_restore": deleted and is_admin,
            "show_deleted_notice": deleted and is_admin,
            "attachment": None if deleted else attachment_json(row),
        }
        if int(row["sender_id"]) == int(user["id"]):
            ensure_group_message_receipts(
                conn,
                int(row["id"]),
                group_id,
                int(row["sender_id"]),
                row["created_at"],
            )
            item["receipt_summary"] = group_receipt_summary(
                conn,
                int(row["id"]),
            )
            receipt_backfill_touched = True
        result.append(item)
    if receipt_backfill_touched:
        conn.commit()
    return result

@app.post("/api/groups/{group_id}/messages")
async def send_group_message(
    group_id: int,
    data: GroupMessageIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")
    if data.client_message_id:
        existing = conn.execute(
            """SELECT gm.id,gm.group_id,gm.sender_id,gm.body,gm.created_at,gm.edited_at,
                      gm.deleted_at,gm.forwarded,gm.client_message_id,
                      gm.reply_to_message_id,
                      EXISTS(
                        SELECT 1 FROM group_message_mentions gmm
                        WHERE gmm.message_id=gm.id AND gmm.user_id=?
                      ) AS mentioned_me,
                      EXISTS(
                        SELECT 1 FROM group_message_mentions gmm_any
                        WHERE gmm_any.message_id=gm.id
                      ) AS has_mentions,
                      u.display_name AS sender_name,
                      up.id AS attachment_id,
                      up.stored_name AS attachment_stored_name,
                      up.original_name AS attachment_name,
                      up.mime_type AS attachment_mime,
                      up.size AS attachment_size
               FROM group_messages gm
               JOIN users u ON u.id=gm.sender_id
               LEFT JOIN uploads up ON up.id=gm.attachment_id
               WHERE gm.sender_id=? AND gm.client_message_id=?""",
            (user["id"], user["id"], data.client_message_id),
        ).fetchone()
        if existing:
            if existing["group_id"] != group_id:
                raise HTTPException(409, "Идентификатор сообщения уже использован")
            deleted = bool(existing["deleted_at"])
            ensure_group_message_receipts(
                conn,
                int(existing["id"]),
                group_id,
                int(existing["sender_id"]),
                existing["created_at"],
            )
            conn.commit()
            existing_receipt_summary = group_receipt_summary(
                conn,
                int(existing["id"]),
            )
            return {
                "id": existing["id"],
                "group_id": existing["group_id"],
                "sender_id": existing["sender_id"],
                "sender_name": existing["sender_name"],
                "body": "" if deleted else existing["body"],
                "created_at": existing["created_at"],
                "edited_at": existing["edited_at"],
                "attachment": None if deleted else attachment_json(existing),
                "deleted": deleted,
                "deleted_at": existing["deleted_at"],
                "forwarded": bool(existing["forwarded"]),
                "client_message_id": existing["client_message_id"],
                "reply_to_message_id": existing["reply_to_message_id"],
                "mentioned_me": bool(existing["mentioned_me"]) and not deleted,
                "has_mentions": bool(existing["has_mentions"]) and not deleted,
                "can_delete": not deleted,
                "can_restore": False,
                "show_deleted_notice": False,
                "receipt_summary": existing_receipt_summary,
            }
    if data.reply_to_message_id is not None:
        reply_row = conn.execute(
            "SELECT id FROM group_messages WHERE id=? AND group_id=?",
            (data.reply_to_message_id, group_id),
        ).fetchone()
        if not reply_row:
            raise HTTPException(400, "Сообщение для ответа не найдено в этой группе")

    body = data.body.strip()
    upload_row = owned_upload(conn, data.attachment_id, user["id"])
    if not body and not upload_row:
        raise HTTPException(400, "Пустое сообщение")
    created = now_iso()
    cur = conn.execute(
        """INSERT INTO group_messages(
             group_id,sender_id,body,created_at,attachment_id,client_message_id,
             reply_to_message_id
           ) VALUES(?,?,?,?,?,?,?)""",
        (
            group_id,
            user["id"],
            body,
            created,
            data.attachment_id,
            data.client_message_id,
            data.reply_to_message_id,
        ),
    )
    mention_ids = resolve_group_mentions(conn, group_id, body)
    store_group_mentions(conn, cur.lastrowid, mention_ids)
    conn.commit()
    attachment = None
    if upload_row:
        attachment_mime = effective_media_mime(
            upload_row["mime_type"],
            upload_row["original_name"],
        )
        attachment = {
            "id": upload_row["id"],
            "url": f"/uploads/{upload_row['stored_name']}",
            "name": upload_row["original_name"],
            "mime_type": attachment_mime,
            "size": upload_row["size"],
            "is_image": attachment_mime in INLINE_IMAGE_TYPES,
            "is_audio": attachment_mime.startswith("audio/"),
            "is_video": attachment_mime.startswith("video/"),
        }
    msg = {
        "id": cur.lastrowid,
        "group_id": group_id,
        "sender_id": user["id"],
        "sender_name": user["display_name"],
        "body": body,
        "created_at": created,
        "attachment": attachment,
        "deleted": False,
        "deleted_at": None,
        "forwarded": False,
        "client_message_id": data.client_message_id,
        "reply_to_message_id": data.reply_to_message_id,
        "mentioned_me": user["id"] in mention_ids,
        "has_mentions": bool(mention_ids),
        "can_delete": True,
        "can_restore": False,
    }
    member_rows = conn.execute(
        "SELECT user_id,is_admin FROM group_members WHERE group_id=?",
        (group_id,),
    ).fetchall()
    recipient_ids = [
        int(row["user_id"])
        for row in member_rows
        if int(row["user_id"]) != int(user["id"])
    ]
    if recipient_ids:
        conn.executemany(
            """INSERT OR IGNORE INTO group_message_receipts(
                 message_id,user_id,delivered_at,read_at
               ) VALUES(?,?,NULL,NULL)""",
            [
                (int(cur.lastrowid), recipient_id)
                for recipient_id in recipient_ids
            ],
        )
        conn.commit()
    msg["receipt_summary"] = group_receipt_summary(
        conn,
        int(cur.lastrowid),
    )
    if body:
        preview = body
    elif attachment and attachment.get("is_audio"):
        preview = "🎙 Голосовое сообщение"
    elif attachment and attachment.get("is_video"):
        preview = "◉ Видеокружок"
    elif attachment:
        preview = "📎 " + attachment["name"]
    else:
        preview = "Новое сообщение"
    deliveries = []
    for row in member_rows:
        if row["user_id"] == user["id"]:
            continue
        recipient_id = int(row["user_id"])
        live_msg = {
            **msg,
            "mentioned_me": recipient_id in mention_ids,
            "can_delete": bool(row["is_admin"]),
        }
        should_notify = (
            recipient_id in mention_ids
            if mention_ids
            else True
        )
        notification = None
        if should_notify:
            notification = {
                "title": (
                    f"Упоминание · {group['name']}"
                    if mention_ids
                    else f"{group['name']} · {user['display_name']}"
                ),
                "body": (
                    f"{user['display_name']}: {preview}"
                    if mention_ids
                    else preview
                ),
                "url": "/",
                "tag": f"group-{group_id}",
                "silent": is_chat_muted(
                    conn,
                    recipient_id,
                    "group",
                    group_id,
                ),
            }
        deliveries.append(
            {
                "recipient_id": recipient_id,
                "payload": {
                    "type": "group_message",
                    "message": live_msg,
                },
                "notification": notification,
            }
        )

    await deliver_group_batch(deliveries)

    # Keep the sender's other active devices synchronized too. The device
    # that issued the HTTP request may receive this before the HTTP response;
    # client_message_id lets the client merge it with the optimistic bubble.
    await push(
        int(user["id"]),
        {
            "type": "group_message",
            "message": msg,
        },
    )
    return msg


@app.patch("/api/groups/{group_id}/messages/{message_id}")
async def edit_group_message(
    group_id: int,
    message_id: int,
    data: EditMessageIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")

    row = conn.execute(
        """SELECT gm.id,gm.group_id,gm.sender_id,gm.body,gm.created_at,gm.edited_at,
                  gm.deleted_at,gm.forwarded,gm.reply_to_message_id,
                  u.display_name AS sender_name,
                  up.id AS attachment_id,
                  up.stored_name AS attachment_stored_name,
                  up.original_name AS attachment_name,
                  up.mime_type AS attachment_mime,
                  up.size AS attachment_size
           FROM group_messages gm
           JOIN users u ON u.id=gm.sender_id
           LEFT JOIN uploads up ON up.id=gm.attachment_id
           WHERE gm.id=? AND gm.group_id=?""",
        (message_id, group_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Сообщение не найдено")
    if int(row["sender_id"]) != int(user["id"]):
        raise HTTPException(403, "Редактировать можно только свои сообщения")
    if row["deleted_at"]:
        raise HTTPException(409, "Удалённое сообщение нельзя редактировать")

    body = data.body.strip()
    if not body and row["attachment_id"] is None:
        raise HTTPException(400, "Сообщение не может быть пустым")

    edited_at = now_iso()
    conn.execute(
        """UPDATE group_messages
           SET body=?, edited_at=?
           WHERE id=? AND group_id=? AND sender_id=?""",
        (body, edited_at, message_id, group_id, user["id"]),
    )
    conn.execute(
        "DELETE FROM group_message_mentions WHERE message_id=?",
        (message_id,),
    )
    mention_ids = resolve_group_mentions(conn, group_id, body)
    store_group_mentions(conn, message_id, mention_ids)
    conn.commit()

    base = {
        "id": row["id"],
        "group_id": row["group_id"],
        "sender_id": row["sender_id"],
        "sender_name": row["sender_name"],
        "body": body,
        "created_at": row["created_at"],
        "edited_at": edited_at,
        "attachment": attachment_json(row),
        "deleted": False,
        "deleted_at": None,
        "forwarded": bool(row["forwarded"]),
        "reply_to_message_id": row["reply_to_message_id"],
        "has_mentions": bool(mention_ids),
        "can_restore": False,
    }

    members = conn.execute(
        "SELECT user_id,is_admin FROM group_members WHERE group_id=?",
        (group_id,),
    ).fetchall()
    deliveries = []
    for member in members:
        member_id = int(member["user_id"])
        payload = {
            **base,
            "mentioned_me": member_id in mention_ids,
            "can_delete": (
                bool(member["is_admin"])
                or member_id == int(row["sender_id"])
            ),
        }
        deliveries.append(
            {
                "recipient_id": member_id,
                "payload": {
                    "type": "group_message_edited",
                    "group_id": group_id,
                    "message": payload,
                },
            }
        )
    await deliver_group_batch(deliveries)

    return {
        **base,
        "mentioned_me": int(user["id"]) in mention_ids,
        "can_delete": True,
    }


@app.delete("/api/groups/{group_id}/messages/{message_id}/me")
async def delete_group_message_for_me(
    group_id: int,
    message_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")
    row = conn.execute(
        "SELECT id FROM group_messages WHERE id=? AND group_id=?",
        (message_id, group_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Сообщение не найдено")
    hidden_at = now_iso()
    conn.execute(
        """INSERT OR REPLACE INTO group_message_hidden_by_user(
             message_id,user_id,hidden_at
           ) VALUES(?,?,?)""",
        (message_id, user["id"], hidden_at),
    )
    conn.commit()
    await push(
        int(user["id"]),
        {
            "type": "message_hidden_for_me",
            "chat_type": "group",
            "chat_id": group_id,
            "message_id": message_id,
            "hidden_at": hidden_at,
        },
    )
    return {"ok": True, "message_id": message_id}


@app.post("/api/groups/{group_id}/messages/{message_id}/read")
async def mark_group_message_read(
    group_id: int,
    message_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")
    row = conn.execute(
        """SELECT id,sender_id,created_at,deleted_at FROM group_messages
           WHERE id=? AND group_id=?""",
        (message_id, group_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Сообщение не найдено")

    read_at = None
    receipt_changed = False
    if not row["deleted_at"] and int(row["sender_id"]) != int(user["id"]):
        read_at = now_iso()
        conn.execute(
            """INSERT OR IGNORE INTO group_message_reads(
                 message_id,user_id,read_at
               ) VALUES(?,?,?)""",
            (message_id, user["id"], read_at),
        )
        receipt_row = conn.execute(
            """SELECT delivered_at,read_at
               FROM group_message_receipts
               WHERE message_id=? AND user_id=?""",
            (message_id, user["id"]),
        ).fetchone()
        if receipt_row is None:
            ensure_group_message_receipts(
                conn,
                message_id,
                group_id,
                int(row["sender_id"]),
                row["created_at"],
            )
        receipt_update = conn.execute(
            """UPDATE group_message_receipts
               SET delivered_at=COALESCE(delivered_at, ?),
                   read_at=COALESCE(read_at, ?)
               WHERE message_id=? AND user_id=? AND read_at IS NULL""",
            (read_at, read_at, message_id, user["id"]),
        )
        receipt_changed = bool(receipt_update.rowcount)
        conn.commit()

    unread_count = group_unread_count_for_user(
        conn,
        group_id,
        int(user["id"]),
    )
    await push(
        int(user["id"]),
        {
            "type": "chat_read_sync",
            "chat_type": "group",
            "chat_id": group_id,
            "message_ids": [message_id] if read_at else [],
            "read_at": read_at,
            "unread_count": unread_count,
        },
    )
    if receipt_changed:
        await push_group_receipt_update(
            conn,
            group_id,
            message_id,
        )
    return {
        "ok": True,
        "unread_count": unread_count,
        "receipt_summary": group_receipt_summary(conn, message_id),
    }


@app.post("/api/groups/{group_id}/messages/{message_id}/delivered")
async def mark_group_message_delivered(
    group_id: int,
    message_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")

    row = conn.execute(
        """SELECT id,sender_id,created_at,deleted_at
           FROM group_messages
           WHERE id=? AND group_id=?""",
        (message_id, group_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Сообщение не найдено")

    receipt_row = conn.execute(
        """SELECT delivered_at,read_at
           FROM group_message_receipts
           WHERE message_id=? AND user_id=?""",
        (message_id, user["id"]),
    ).fetchone()
    if receipt_row is None:
        ensure_group_message_receipts(
            conn,
            message_id,
            group_id,
            int(row["sender_id"]),
            row["created_at"],
        )

    changed = False
    delivered_at = None
    if int(row["sender_id"]) != int(user["id"]):
        delivered_at = now_iso()
        update = conn.execute(
            """UPDATE group_message_receipts
               SET delivered_at=COALESCE(delivered_at, ?)
               WHERE message_id=? AND user_id=?
                 AND delivered_at IS NULL""",
            (delivered_at, message_id, user["id"]),
        )
        changed = bool(update.rowcount)

    conn.commit()
    if changed:
        await push_group_receipt_update(
            conn,
            group_id,
            message_id,
        )

    return {
        "ok": True,
        "delivered_at": delivered_at,
        "receipt_summary": group_receipt_summary(conn, message_id),
    }


@app.get("/api/groups/{group_id}/messages/{message_id}/seen-by")
def group_message_seen_by(
    group_id: int,
    message_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")
    message = conn.execute(
        """SELECT id,sender_id,created_at
           FROM group_messages
           WHERE id=? AND group_id=?""",
        (message_id, group_id),
    ).fetchone()
    if not message:
        raise HTTPException(404, "Сообщение не найдено")

    if int(message["sender_id"]) != int(user["id"]):
        read_rows = conn.execute(
            """SELECT u.id,u.username,u.display_name,
                      a.stored_name AS avatar_stored_name,
                      gmr.read_at
               FROM group_message_reads gmr
               JOIN users u ON u.id=gmr.user_id
               LEFT JOIN uploads a ON a.id=u.avatar_id
               WHERE gmr.message_id=? AND gmr.user_id<>?
               ORDER BY gmr.read_at""",
            (message_id, message["sender_id"]),
        ).fetchall()
        return {
            "message_id": message_id,
            "viewers": [
                {**user_json(row), "read_at": row["read_at"]}
                for row in read_rows
            ],
        }

    ensure_group_message_receipts(
        conn,
        message_id,
        group_id,
        int(message["sender_id"]),
        message["created_at"],
    )
    conn.commit()

    rows = conn.execute(
        """SELECT u.id,u.username,u.display_name,
                  a.stored_name AS avatar_stored_name,
                  gmr.delivered_at,gmr.read_at
           FROM group_message_receipts gmr
           JOIN users u ON u.id=gmr.user_id
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE gmr.message_id=?
           ORDER BY
             CASE
               WHEN gmr.read_at IS NOT NULL THEN 0
               WHEN gmr.delivered_at IS NOT NULL THEN 1
               ELSE 2
             END,
             COALESCE(gmr.read_at,gmr.delivered_at) DESC,
             u.display_name COLLATE NOCASE""",
        (message_id,),
    ).fetchall()

    recipients = [
        {
            **user_json(row),
            "delivered_at": row["delivered_at"],
            "read_at": row["read_at"],
        }
        for row in rows
    ]
    return {
        "message_id": message_id,
        "summary": group_receipt_summary(conn, message_id),
        "recipients": recipients,
        "viewers": [
            item
            for item in recipients
            if item["read_at"]
        ],
    }

@app.delete("/api/groups/{group_id}/messages/{message_id}")
async def delete_group_message(
    group_id: int,
    message_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")

    row = conn.execute(
        """SELECT id,sender_id,deleted_at
           FROM group_messages
           WHERE id=? AND group_id=?""",
        (message_id, group_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Сообщение не найдено")
    if row["deleted_at"]:
        raise HTTPException(409, "Сообщение уже удалено")

    if row["sender_id"] != user["id"] and not bool(group["is_admin"]):
        raise HTTPException(403, "Удалить это сообщение может только автор или администратор")

    deleted_at = now_iso()
    conn.execute(
        """UPDATE group_messages
           SET deleted_at=?, deleted_by=?
           WHERE id=? AND group_id=?""",
        (deleted_at, user["id"], message_id, group_id),
    )
    conn.commit()

    members = conn.execute(
        "SELECT user_id,is_admin FROM group_members WHERE group_id=?",
        (group_id,),
    ).fetchall()
    deliveries = [
        {
            "recipient_id": int(member["user_id"]),
            "payload": {
                "type": "group_message_deleted",
                "group_id": group_id,
                "message_id": message_id,
                "deleted_at": deleted_at,
                "show_deleted_notice": bool(member["is_admin"]),
            },
        }
        for member in members
    ]
    await deliver_group_batch(deliveries)

    return {
        "ok": True,
        "group_id": group_id,
        "message_id": message_id,
        "deleted_at": deleted_at,
        "show_deleted_notice": bool(group["is_admin"]),
    }


@app.post("/api/groups/{group_id}/messages/{message_id}/restore")
async def restore_group_message(
    group_id: int,
    message_id: int,
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")
    if not bool(group["is_admin"]):
        raise HTTPException(403, "Восстанавливать сообщения может только администратор")

    row = conn.execute(
        """SELECT gm.id,gm.group_id,gm.sender_id,gm.body,gm.created_at,gm.edited_at,
                  gm.deleted_at,gm.deleted_by,gm.forwarded,gm.reply_to_message_id,
                  EXISTS(
                    SELECT 1 FROM group_message_mentions gmm
                    WHERE gmm.message_id=gm.id AND gmm.user_id=?
                  ) AS mentioned_me,
                  EXISTS(
                    SELECT 1 FROM group_message_mentions gmm_any
                    WHERE gmm_any.message_id=gm.id
                  ) AS has_mentions,
                  u.display_name AS sender_name,
                  up.id AS attachment_id,
                  up.stored_name AS attachment_stored_name,
                  up.original_name AS attachment_name,
                  up.mime_type AS attachment_mime,
                  up.size AS attachment_size
           FROM group_messages gm
           JOIN users u ON u.id=gm.sender_id
           LEFT JOIN uploads up ON up.id=gm.attachment_id
           WHERE gm.id=? AND gm.group_id=?""",
        (user["id"], message_id, group_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Сообщение не найдено")
    if not row["deleted_at"]:
        raise HTTPException(409, "Сообщение не удалено")

    conn.execute(
        """UPDATE group_messages
           SET deleted_at=NULL, deleted_by=NULL
           WHERE id=? AND group_id=?""",
        (message_id, group_id),
    )
    conn.commit()

    restored = {
        "id": row["id"],
        "group_id": row["group_id"],
        "sender_id": row["sender_id"],
        "sender_name": row["sender_name"],
        "body": row["body"],
        "created_at": row["created_at"],
        "edited_at": row["edited_at"],
        "attachment": attachment_json(row),
        "deleted": False,
        "deleted_at": None,
        "forwarded": bool(row["forwarded"]),
        "reply_to_message_id": row["reply_to_message_id"],
        "mentioned_me": bool(row["mentioned_me"]),
        "has_mentions": bool(row["has_mentions"]),
        "can_delete": True,
        "can_restore": False,
        "show_deleted_notice": False,
    }

    members = conn.execute(
        "SELECT user_id,is_admin FROM group_members WHERE group_id=?",
        (group_id,),
    ).fetchall()
    mentioned_ids = {
        int(r["user_id"])
        for r in conn.execute(
            "SELECT user_id FROM group_message_mentions WHERE message_id=?",
            (message_id,),
        ).fetchall()
    }
    deliveries = []
    for member in members:
        member_id = int(member["user_id"])
        payload = {
            **restored,
            "mentioned_me": member_id in mentioned_ids,
            "can_delete": (
                bool(member["is_admin"])
                or member_id == int(row["sender_id"])
            ),
        }
        deliveries.append(
            {
                "recipient_id": member_id,
                "payload": {
                    "type": "group_message_restored",
                    "group_id": group_id,
                    "message": payload,
                },
            }
        )
    await deliver_group_batch(deliveries)

    return restored


@app.post("/api/groups/{group_id}/call-token")
async def group_call_token(
    group_id: int,
    data: GroupCallIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise HTTPException(
            503,
            "Сервер групповых звонков пока не настроен",
        )

    room_name = f"svoi-group-{group_id}"
    identity = f"user-{user['id']}"

    grants = livekit_api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
    )
    token = (
        livekit_api.AccessToken(
            LIVEKIT_API_KEY,
            LIVEKIT_API_SECRET,
        )
        .with_identity(identity)
        .with_name(user["display_name"])
        .with_grants(grants)
        .with_room_config(
            RoomConfiguration(
                name=room_name,
                max_participants=10,
                empty_timeout=60,
                departure_timeout=20,
            )
        )
        .with_ttl(timedelta(hours=2))
        .to_jwt()
    )

    if data.invite:
        members = conn.execute(
            """SELECT gm.user_id
               FROM group_members gm
               WHERE gm.group_id=? AND gm.user_id<>?""",
            (group_id, user["id"]),
        ).fetchall()
        invite_payload = {
            "type": "group_call_invite",
            "group_id": group_id,
            "group_name": group["name"],
            "from_user_id": user["id"],
            "from_name": user["display_name"],
            "video": bool(data.video),
        }
        kind = (
            "Групповой видеозвонок"
            if data.video
            else "Групповой звонок"
        )
        deliveries = [
            {
                "recipient_id": int(member["user_id"]),
                "payload": invite_payload,
                "notification": {
                    "title": kind,
                    "body": f"{user['display_name']} зовёт в «{group['name']}»",
                    "url": f"/?group_call={group_id}&video={1 if data.video else 0}",
                    "tag": f"group-call-{group_id}",
                    "force": False,
                    "silent": False,
                },
            }
            for member in members
        ]
        await deliver_group_batch(deliveries)

    return {
        "server_url": LIVEKIT_WS_URL,
        "participant_token": token,
        "room_name": room_name,
        "group_id": group_id,
        "group_name": group["name"],
        "video": bool(data.video),
        "is_admin": bool(group["is_admin"]),
        "max_participants": 10,
    }



def call_invite_link_for_token(invite_token: str) -> dict | None:
    invite = call_invite_links.get(invite_token)
    if not invite:
        return None
    if float(invite.get("expires_at_ts", 0)) <= time.time():
        call_invite_links.pop(invite_token, None)
        return None
    return invite


async def expire_call_invite_link(invite_token: str, delay_seconds: float = 7200.0):
    await asyncio.sleep(delay_seconds)
    invite = call_invite_links.get(invite_token)
    if invite and float(invite.get("expires_at_ts", 0)) <= time.time():
        call_invite_links.pop(invite_token, None)


async def cleanup_promoted_private_call(call_id: str, delay_seconds: float = 45.0):
    await asyncio.sleep(delay_seconds)
    call = active_calls.get(call_id)
    if not call or not call.get("promoted_to_room"):
        return
    if call.get("answered"):
        finish_call_history(call_id, "completed")
    active_calls.pop(call_id, None)


@app.post("/api/calls/{call_id}/invite-link")
async def create_private_call_invite_link(
    call_id: str,
    user=Depends(current_user),
):
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise HTTPException(503, "Сервер конференций пока не настроен")

    call = active_calls.get(call_id)
    if not call or not call.get("answered"):
        raise HTTPException(404, "Активный личный звонок не найден")

    user_id = int(user["id"])
    participants = {int(call["caller_id"]), int(call["callee_id"])}
    if user_id not in participants:
        raise HTTPException(403, "Нет доступа к этому звонку")

    invite_token = str(call.get("invite_token") or "")
    invite = call_invite_link_for_token(invite_token) if invite_token else None

    if not invite:
        invite_token = secrets.token_urlsafe(32)
        room_name = "svoi-private-" + hashlib.sha256(
            f"{call_id}:{invite_token}".encode("utf-8")
        ).hexdigest()[:24]
        invite = {
            "invite_token": invite_token,
            "room_name": room_name,
            "source_call_id": call_id,
            "creator_id": user_id,
            "video": bool(call.get("video")),
            "created_at": now_iso(),
            "expires_at_ts": time.time() + 7200,
        }
        call_invite_links[invite_token] = invite
        call["invite_token"] = invite_token
        asyncio.create_task(expire_call_invite_link(invite_token))

    return {
        "invite_token": invite_token,
        "invite_path": f"/?call_invite={invite_token}",
        "video": bool(invite.get("video")),
        "expires_in_seconds": max(
            0,
            int(float(invite.get("expires_at_ts", 0)) - time.time()),
        ),
        "max_participants": 10,
    }


@app.post("/api/calls/{call_id}/activate-conference")
async def activate_private_call_conference(
    call_id: str,
    data: CallInviteActivateIn,
    user=Depends(current_user),
):
    call = active_calls.get(call_id)
    if not call or not call.get("answered"):
        raise HTTPException(404, "Активный личный звонок не найден")

    user_id = int(user["id"])
    participants = {int(call["caller_id"]), int(call["callee_id"])}
    if user_id not in participants:
        raise HTTPException(403, "Нет доступа к этому звонку")

    invite = call_invite_link_for_token(data.invite_token)
    if not invite or str(invite.get("source_call_id")) != str(call_id):
        raise HTTPException(404, "Ссылка приглашения недействительна")

    peer_id = (
        int(call["callee_id"])
        if user_id == int(call["caller_id"])
        else int(call["caller_id"])
    )
    call["promoted_to_room"] = True

    await push(
        peer_id,
        {
            "type": "private_call_room_upgrade",
            "call_id": call_id,
            "invite_token": data.invite_token,
            "video": bool(invite.get("video")),
            "from_user_id": user_id,
            "from_name": user["display_name"],
        },
    )
    asyncio.create_task(cleanup_promoted_private_call(call_id))

    return {
        "ok": True,
        "invite_path": f"/?call_invite={data.invite_token}",
    }


@app.post("/api/call-links/{invite_token}/token")
def private_call_invite_token(
    invite_token: str,
    data: GroupCallIn,
    user=Depends(current_user),
):
    invite = call_invite_link_for_token(invite_token)
    if not invite:
        raise HTTPException(410, "Ссылка на звонок истекла или недействительна")
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise HTTPException(503, "Сервер конференций пока не настроен")

    room_name = str(invite["room_name"])
    identity = f"user-{user['id']}"

    grants = livekit_api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
    )
    ttl_seconds = max(
        60,
        min(
            7200,
            int(float(invite.get("expires_at_ts", 0)) - time.time()),
        ),
    )
    token = (
        livekit_api.AccessToken(
            LIVEKIT_API_KEY,
            LIVEKIT_API_SECRET,
        )
        .with_identity(identity)
        .with_name(user["display_name"])
        .with_grants(grants)
        .with_room_config(
            RoomConfiguration(
                name=room_name,
                max_participants=10,
                empty_timeout=90,
                departure_timeout=30,
            )
        )
        .with_ttl(timedelta(seconds=ttl_seconds))
        .to_jwt()
    )

    return {
        "server_url": LIVEKIT_WS_URL,
        "participant_token": token,
        "room_name": room_name,
        "group_id": None,
        "group_name": "Личный звонок",
        "video": bool(invite.get("video")),
        "is_admin": False,
        "max_participants": 10,
        "invite_token": invite_token,
    }


def save_call_started(call: dict):
    conn = connect_db()
    conn.execute(
        """INSERT OR IGNORE INTO call_history(
             call_id,caller_id,callee_id,video,status,started_at
           ) VALUES(?,?,?,?,?,?)""",
        (
            call["call_id"],
            call["caller_id"],
            call["callee_id"],
            1 if call.get("video") else 0,
            "ringing",
            call["started_at"],
        ),
    )
    conn.commit()
    conn.close()


def mark_call_answered(call_id: str):
    conn = connect_db()
    conn.execute(
        """UPDATE call_history
           SET status='answered',
               answered_at=COALESCE(answered_at, ?)
           WHERE call_id=?""",
        (now_iso(), call_id),
    )
    conn.commit()
    conn.close()


def finish_call_history(call_id: str, status: str):
    conn = connect_db()
    conn.execute(
        """UPDATE call_history
           SET status=?, ended_at=COALESCE(ended_at, ?)
           WHERE call_id=?""",
        (status, now_iso(), call_id),
    )
    conn.commit()
    conn.close()


def mark_call_video(call_id: str):
    conn = connect_db()
    conn.execute(
        "UPDATE call_history SET video=1 WHERE call_id=?",
        (call_id,),
    )
    conn.commit()
    conn.close()


@app.post("/api/calls/quality")
def record_call_quality(
    payload: CallQualityIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    call_type = payload.call_type.strip().lower()
    if call_type not in {"private", "group", "link"}:
        raise HTTPException(400, "Неизвестный тип звонка")

    peer_id = payload.peer_id
    group_id = payload.group_id

    if call_type == "private":
        row = conn.execute(
            """SELECT caller_id,callee_id FROM call_history
               WHERE call_id=?""",
            (payload.call_key,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Звонок не найден")
        if user["id"] not in (row["caller_id"], row["callee_id"]):
            raise HTTPException(403, "Нет доступа к этому звонку")
        peer_id = (
            row["callee_id"]
            if user["id"] == row["caller_id"]
            else row["caller_id"]
        )
    elif call_type == "group":
        if not group_id:
            raise HTTPException(400, "Не указана группа")
        membership = conn.execute(
            """SELECT 1 FROM group_members
               WHERE group_id=? AND user_id=?""",
            (group_id, user["id"]),
        ).fetchone()
        if not membership:
            raise HTTPException(403, "Нет доступа к группе")

    quality = (payload.connection_quality or "").strip().lower()[:24] or None
    reason = (payload.end_reason or "").strip().lower()[:64] or None
    conn.execute(
        """INSERT INTO call_quality_samples(
             call_key,call_type,user_id,peer_id,group_id,
             rtt_ms,packet_loss_pct,jitter_ms,
             available_outgoing_bitrate,video_bitrate_bps,
             video_width,video_height,video_fps,
             connection_quality,end_reason,recorded_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            payload.call_key,
            call_type,
            user["id"],
            peer_id,
            group_id,
            payload.rtt_ms,
            payload.packet_loss_pct,
            payload.jitter_ms,
            payload.available_outgoing_bitrate,
            payload.video_bitrate_bps,
            payload.video_width,
            payload.video_height,
            payload.video_fps,
            quality,
            reason,
            now_iso(),
        ),
    )
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    conn.execute(
        "DELETE FROM call_quality_samples WHERE recorded_at<?",
        (cutoff,),
    )
    conn.commit()
    return {"ok": True}


@app.post("/api/client-errors")
def record_client_error(
    payload: ClientErrorIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    fingerprint = (payload.fingerprint or "").strip()[:160] or None
    now = now_iso()

    if fingerprint:
        recent_since = (
            datetime.now(timezone.utc) - timedelta(seconds=60)
        ).isoformat()
        duplicate = conn.execute(
            """SELECT id FROM client_errors
               WHERE user_id=? AND fingerprint=? AND recorded_at>=?
               ORDER BY id DESC LIMIT 1""",
            (user["id"], fingerprint, recent_since),
        ).fetchone()
        if duplicate:
            return {"ok": True, "duplicate": True}

    conn.execute(
        """INSERT INTO client_errors(
             user_id,kind,message,source,line_no,column_no,stack,page_path,
             client_type,app_version,app_version_code,user_agent,
             network_type,effective_type,downlink_mbps,network_rtt_ms,
             online,context,fingerprint,recorded_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            user["id"],
            payload.kind,
            payload.message.strip()[:1200],
            (payload.source or "").strip()[:500] or None,
            payload.line_no,
            payload.column_no,
            (payload.stack or "").strip()[:8000] or None,
            (payload.page_path or "").strip()[:500] or None,
            (payload.client_type or "").strip()[:64] or None,
            (payload.app_version or "").strip()[:64] or None,
            payload.app_version_code,
            (payload.user_agent or "").strip()[:1200] or None,
            (payload.network_type or "").strip()[:64] or None,
            (payload.effective_type or "").strip()[:32] or None,
            payload.downlink_mbps,
            payload.network_rtt_ms,
            1 if payload.online else 0,
            (payload.context or "").strip()[:1000] or None,
            fingerprint,
            now,
        ),
    )
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    conn.execute("DELETE FROM client_errors WHERE recorded_at<?", (cutoff,))
    conn.commit()
    return {"ok": True, "duplicate": False}


@app.get("/api/admin/client-errors")
def admin_client_errors(
    period: str = Query("day", max_length=12),
    limit: int = Query(60, ge=1, le=200),
    user=Depends(require_server_admin),
    conn=Depends(db),
):
    period = period.strip().lower()
    if period not in {"day", "week"}:
        raise HTTPException(400, "Период должен быть day или week")

    since = datetime.now(timezone.utc) - (
        timedelta(hours=24) if period == "day" else timedelta(days=7)
    )
    since_iso = since.isoformat()

    rows = conn.execute(
        """SELECT
             e.*,u.display_name,u.username
           FROM client_errors e
           JOIN users u ON u.id=e.user_id
           WHERE e.recorded_at>=?
           ORDER BY e.id DESC
           LIMIT ?""",
        (since_iso, limit),
    ).fetchall()

    summary_row = conn.execute(
        """SELECT
             COUNT(*) AS total_errors,
             COUNT(DISTINCT user_id) AS affected_users,
             SUM(CASE WHEN kind='js_error' THEN 1 ELSE 0 END) AS js_errors,
             SUM(CASE WHEN kind='promise_rejection' THEN 1 ELSE 0 END)
               AS promise_rejections
           FROM client_errors
           WHERE recorded_at>=?""",
        (since_iso,),
    ).fetchone()

    errors = []
    for row in rows:
        errors.append({
            "id": row["id"],
            "user_id": row["user_id"],
            "display_name": row["display_name"],
            "username": row["username"],
            "kind": row["kind"],
            "message": row["message"],
            "source": row["source"],
            "line_no": row["line_no"],
            "column_no": row["column_no"],
            "stack": row["stack"],
            "page_path": row["page_path"],
            "client_type": row["client_type"],
            "app_version": row["app_version"],
            "app_version_code": row["app_version_code"],
            "user_agent": row["user_agent"],
            "network_type": row["network_type"],
            "effective_type": row["effective_type"],
            "downlink_mbps": row["downlink_mbps"],
            "network_rtt_ms": row["network_rtt_ms"],
            "online": bool(row["online"]),
            "context": row["context"],
            "recorded_at": row["recorded_at"],
        })

    return {
        "period": period,
        "summary": {
            "total_errors": int(summary_row["total_errors"] or 0),
            "affected_users": int(summary_row["affected_users"] or 0),
            "js_errors": int(summary_row["js_errors"] or 0),
            "promise_rejections": int(
                summary_row["promise_rejections"] or 0
            ),
        },
        "errors": errors,
    }


@app.get("/api/admin/call-quality")
def admin_call_quality(
    period: str = Query("day", max_length=12),
    limit: int = Query(40, ge=1, le=100),
    user=Depends(require_server_admin),
    conn=Depends(db),
):
    period = period.strip().lower()
    if period not in {"day", "week"}:
        raise HTTPException(400, "Период должен быть day или week")

    since = datetime.now(timezone.utc) - (
        timedelta(hours=24) if period == "day" else timedelta(days=7)
    )
    since_iso = since.isoformat()

    rows = conn.execute(
        """SELECT
             q.call_key,
             q.call_type,
             MIN(q.recorded_at) AS started_at,
             MAX(q.recorded_at) AS last_at,
             COUNT(*) AS sample_count,
             GROUP_CONCAT(DISTINCT u.display_name) AS participants,
             AVG(q.rtt_ms) AS avg_rtt_ms,
             MAX(q.rtt_ms) AS max_rtt_ms,
             AVG(q.packet_loss_pct) AS avg_packet_loss_pct,
             MAX(q.packet_loss_pct) AS max_packet_loss_pct,
             AVG(q.jitter_ms) AS avg_jitter_ms,
             MAX(q.jitter_ms) AS max_jitter_ms,
             AVG(q.available_outgoing_bitrate) AS avg_available_outgoing_bitrate,
             AVG(q.video_bitrate_bps) AS avg_video_bitrate_bps,
             MAX(q.video_width) AS max_video_width,
             MAX(q.video_height) AS max_video_height,
             AVG(q.video_fps) AS avg_video_fps,
             MAX(CASE
               WHEN q.end_reason IS NOT NULL AND q.end_reason<>''
               THEN q.end_reason ELSE NULL END
             ) AS end_reason
           FROM call_quality_samples q
           JOIN users u ON u.id=q.user_id
           WHERE q.recorded_at>=?
           GROUP BY q.call_key,q.call_type
           ORDER BY last_at DESC
           LIMIT ?""",
        (since_iso, limit),
    ).fetchall()

    def rounded(value, digits=1):
        return round(float(value), digits) if value is not None else None

    calls = []
    for row in rows:
        calls.append({
            "call_key": row["call_key"],
            "call_type": row["call_type"],
            "started_at": row["started_at"],
            "last_at": row["last_at"],
            "sample_count": int(row["sample_count"] or 0),
            "participants": [
                value for value in str(row["participants"] or "").split(",")
                if value
            ],
            "avg_rtt_ms": rounded(row["avg_rtt_ms"]),
            "max_rtt_ms": rounded(row["max_rtt_ms"]),
            "avg_packet_loss_pct": rounded(row["avg_packet_loss_pct"], 2),
            "max_packet_loss_pct": rounded(row["max_packet_loss_pct"], 2),
            "avg_jitter_ms": rounded(row["avg_jitter_ms"]),
            "max_jitter_ms": rounded(row["max_jitter_ms"]),
            "avg_available_outgoing_bitrate": rounded(
                row["avg_available_outgoing_bitrate"], 0
            ),
            "avg_video_bitrate_bps": rounded(
                row["avg_video_bitrate_bps"], 0
            ),
            "max_video_width": row["max_video_width"],
            "max_video_height": row["max_video_height"],
            "avg_video_fps": rounded(row["avg_video_fps"]),
            "end_reason": row["end_reason"],
        })

    valid_rtt = [item["avg_rtt_ms"] for item in calls if item["avg_rtt_ms"] is not None]
    valid_loss = [
        item["avg_packet_loss_pct"]
        for item in calls
        if item["avg_packet_loss_pct"] is not None
    ]
    valid_jitter = [
        item["avg_jitter_ms"]
        for item in calls
        if item["avg_jitter_ms"] is not None
    ]

    return {
        "period": period,
        "calls": calls,
        "summary": {
            "calls_with_diagnostics": len(calls),
            "avg_rtt_ms": rounded(sum(valid_rtt) / len(valid_rtt)) if valid_rtt else None,
            "avg_packet_loss_pct": rounded(
                sum(valid_loss) / len(valid_loss), 2
            ) if valid_loss else None,
            "avg_jitter_ms": rounded(
                sum(valid_jitter) / len(valid_jitter)
            ) if valid_jitter else None,
        },
    }


@app.get("/api/calls/history")
def call_history(
    limit: int = Query(30, ge=1, le=100),
    user=Depends(current_user),
    conn=Depends(db),
):
    rows = conn.execute(
        """SELECT ch.*,
                  CASE
                    WHEN ch.caller_id=? THEN callee.id
                    ELSE caller.id
                  END AS peer_id,
                  CASE
                    WHEN ch.caller_id=? THEN callee.username
                    ELSE caller.username
                  END AS peer_username,
                  CASE
                    WHEN ch.caller_id=? THEN callee.display_name
                    ELSE caller.display_name
                  END AS peer_name
           FROM call_history ch
           JOIN users caller ON caller.id=ch.caller_id
           JOIN users callee ON callee.id=ch.callee_id
           WHERE ch.caller_id=? OR ch.callee_id=?
           ORDER BY ch.id DESC
           LIMIT ?""",
        (
            user["id"],
            user["id"],
            user["id"],
            user["id"],
            user["id"],
            limit,
        ),
    ).fetchall()

    result = []
    for row in rows:
        duration = 0
        if row["answered_at"] and row["ended_at"]:
            try:
                start = datetime.fromisoformat(row["answered_at"])
                end = datetime.fromisoformat(row["ended_at"])
                duration = max(0, int((end - start).total_seconds()))
            except Exception:
                duration = 0
        result.append(
            {
                "call_id": row["call_id"],
                "direction": (
                    "outgoing"
                    if row["caller_id"] == user["id"]
                    else "incoming"
                ),
                "peer_id": row["peer_id"],
                "peer_username": row["peer_username"],
                "peer_name": row["peer_name"],
                "video": bool(row["video"]),
                "status": row["status"],
                "started_at": row["started_at"],
                "answered_at": row["answered_at"],
                "ended_at": row["ended_at"],
                "duration_seconds": duration,
            }
        )
    return result


@app.get("/api/calls/pending/{call_id}")
def pending_call(
    call_id: str,
    user=Depends(current_user),
):
    call = active_calls.get(call_id)
    if not call or call.get("callee_id") != user["id"] or call.get("answered"):
        raise HTTPException(404, "Вызов уже завершён")
    return {
        "type": "call_offer",
        "call_id": call["call_id"],
        "from_user_id": call["caller_id"],
        "from_name": call["caller_name"],
        "from_avatar_url": call.get("caller_avatar_url"),
        "video": call["video"],
        "sdp": call["offer_sdp"],
        "ice_candidates": call.get("caller_ice", []),
    }


@app.post("/api/calls/native-action/{call_id}/reject")
async def native_reject_call(
    call_id: str,
    token: str = Query(..., min_length=16, max_length=256),
):
    call = active_calls.get(call_id)
    if not call:
        return {"ok": True, "status": "ended"}

    expected = str(call.get("action_token") or "")
    if not expected or not hmac.compare_digest(expected, token):
        raise HTTPException(403, "Недействительный токен действия")

    if call.get("answered"):
        return {"ok": False, "status": "answered"}

    finish_call_history(call_id, "rejected")
    await push(
        call["caller_id"],
        {
            "type": "call_reject",
            "from_user_id": call["callee_id"],
            "from_name": "Собеседник",
            "call_id": call_id,
        },
    )
    active_calls.pop(call_id, None)
    return {"ok": True, "status": "rejected"}


CALL_SIGNAL_TYPES = {
    "call_offer",
    "call_answer",
    "call_video_offer",
    "call_video_answer",
    "call_video_state",
    "call_mute",
    "ice_candidate",
    "call_reject",
    "call_end",
}


async def notify_missed_call(call: dict):
    if call.get("answered") or call.get("missed_notified"):
        return
    call["missed_notified"] = True
    finish_call_history(call["call_id"], "missed")
    kind = "видеозвонок" if call.get("video") else "голосовой звонок"
    await send_web_push(
        call["callee_id"],
        "Пропущенный звонок",
        f"{kind.capitalize()} от {call['caller_name']}",
        "/",
        f"missed-call-{call['call_id']}",
        True,
    )


async def expire_call(call_id: str):
    await asyncio.sleep(45)
    call = active_calls.get(call_id)
    if not call or call.get("answered"):
        return
    await notify_missed_call(call)
    await push(
        call["caller_id"],
        {
            "type": "call_end",
            "from_user_id": call["callee_id"],
            "from_name": "Система",
            "call_id": call_id,
        },
    )
    await push(
        call["callee_id"],
        {
            "type": "call_end",
            "from_user_id": call["caller_id"],
            "from_name": call["caller_name"],
            "call_id": call_id,
        },
    )
    active_calls.pop(call_id, None)


def presence_peer_ids(user_id: int) -> set[int]:
    conn = connect_db()
    rows = conn.execute(
        """SELECT DISTINCT peer_id FROM (
             SELECT contact_user_id AS peer_id
             FROM contacts WHERE user_id=?
             UNION
             SELECT user_id AS peer_id
             FROM contacts WHERE contact_user_id=?
             UNION
             SELECT recipient_id AS peer_id
             FROM messages WHERE sender_id=?
             UNION
             SELECT sender_id AS peer_id
             FROM messages WHERE recipient_id=?
           ) WHERE peer_id<>?""",
        (user_id, user_id, user_id, user_id, user_id),
    ).fetchall()
    conn.close()
    return {int(row["peer_id"]) for row in rows}


async def broadcast_presence(
    user_id: int,
    online: bool,
    last_seen_at: str | None,
):
    payload = {
        "type": "presence",
        "user_id": user_id,
        "online": bool(online),
        "last_seen_at": last_seen_at,
    }
    await deliver_group_batch(
        [
            {
                "recipient_id": peer_id,
                "payload": payload,
            }
            for peer_id in presence_peer_ids(user_id)
        ]
    )


async def finish_answered_call_after_disconnect_grace(
    call_id: str,
    user_id: int,
    delay_seconds: float = 12.0,
):
    await asyncio.sleep(delay_seconds)

    call = active_calls.get(call_id)
    if not call or not call.get("answered"):
        return
    if user_id not in (call.get("caller_id"), call.get("callee_id")):
        return
    if connections.get(user_id):
        return

    finish_call_history(call_id, "completed")
    peer_id = (
        call["callee_id"]
        if user_id == call["caller_id"]
        else call["caller_id"]
    )
    await push(
        peer_id,
        {
            "type": "call_end",
            "from_user_id": user_id,
            "call_id": call_id,
        },
    )
    active_calls.pop(call_id, None)


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
    last_seq: int = Query(default=0, ge=0),
):
    conn = connect_db()
    conn.row_factory = sqlite3.Row
    row = get_user_from_token(conn, token)
    current_session_hash = token_hash(token)
    if row:
        conn.execute(
            "UPDATE sessions SET last_seen_at=? WHERE token_hash=?",
            (now_iso(), current_session_hash),
        )
        conn.commit()
    conn.close()
    if not row:
        await websocket.close(code=4401)
        return

    user_id = row["id"]
    display_name = row["display_name"]
    caller_avatar_url = user_json(row).get("avatar_url")
    await websocket.accept()
    was_offline = not bool(connections.get(user_id))
    connections.setdefault(user_id, set()).add(websocket)
    user_event_seq[user_id] = max(
        int(user_event_seq.get(user_id, 0)),
        int(last_seq or 0),
    )
    ws_connection_state[websocket] = {
        "queue": [],
        "flush_task": None,
        "send_lock": asyncio.Lock(),
        "acked_seq": int(last_seq or 0),
        "last_sent_seq": int(last_seq or 0),
        "dead": False,
        "token_hash": current_session_hash,
    }

    replay = [
        event
        for seq, _recorded_at, event in user_event_buffer.get(user_id, ())
        if seq > int(last_seq or 0)
    ]
    for event in replay:
        try:
            await queue_ws_event(websocket, event)
        except Exception:
            break

    if was_offline:
        try:
            record_online_sample()
        except Exception:
            pass
        presence_conn = connect_db()
        last_seen_row = presence_conn.execute(
            "SELECT last_seen_at FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
        presence_conn.close()
        await broadcast_presence(
            user_id,
            True,
            last_seen_row["last_seen_at"] if last_seen_row else None,
        )

    for call in list(active_calls.values()):
        if call.get("callee_id") == user_id and not call.get("answered"):
            await send_ws_direct(
                websocket,
                {
                    "type": "call_offer",
                    "from_user_id": call["caller_id"],
                    "from_name": call["caller_name"],
                    "from_avatar_url": call.get("caller_avatar_url"),
                    "call_id": call["call_id"],
                    "video": call["video"],
                    "sdp": call["offer_sdp"],
                    "ice_candidates": call.get("caller_ice", []),
                }
            )

    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw) > 30000:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue

            signal_type = data.get("type")

            if signal_type == "ws_ack":
                try:
                    ack_seq = max(0, int(data.get("seq", 0)))
                except (TypeError, ValueError):
                    ack_seq = 0
                state = ws_connection_state.get(websocket)
                if state and ack_seq:
                    state["acked_seq"] = max(
                        int(state.get("acked_seq", 0)),
                        ack_seq,
                    )
                continue

            if signal_type == "ws_ping_probe":
                nonce = str(data.get("nonce", ""))[:120]
                if nonce:
                    await send_ws_direct(
                        websocket,
                        {
                            "type": "ws_ping_pong",
                            "nonce": nonce,
                        },
                    )
                continue

            if signal_type == "group_ping_probe":
                nonce = str(data.get("nonce", ""))[:120]
                if nonce:
                    await send_ws_direct(
                        websocket,
                        {
                            "type": "group_ping_pong",
                            "nonce": nonce,
                        }
                    )
                continue

            if signal_type == "group_force_mute":
                try:
                    group_id = int(data.get("group_id"))
                    target_user_id = int(data.get("target_user_id"))
                except (TypeError, ValueError):
                    continue
                if target_user_id == user_id:
                    continue

                check = connect_db()
                actor = check.execute(
                    """SELECT gm.is_admin
                       FROM group_members gm
                       WHERE gm.group_id=? AND gm.user_id=?""",
                    (group_id, user_id),
                ).fetchone()
                target = check.execute(
                    """SELECT 1
                       FROM group_members
                       WHERE group_id=? AND user_id=?""",
                    (group_id, target_user_id),
                ).fetchone()
                check.close()

                if not actor or not bool(actor["is_admin"]) or not target:
                    continue

                await push(
                    target_user_id,
                    {
                        "type": "group_force_mute",
                        "group_id": group_id,
                        "from_user_id": user_id,
                        "from_name": display_name,
                    },
                )
                await send_ws_direct(
                    websocket,
                    {
                        "type": "group_force_mute_sent",
                        "group_id": group_id,
                        "target_user_id": target_user_id,
                    }
                )
                continue

            if signal_type == "group_call_ping":
                try:
                    group_id = int(data.get("group_id"))
                    ping_ms = int(round(float(data.get("ping_ms"))))
                except (TypeError, ValueError):
                    continue
                ping_ms = max(1, min(5000, ping_ms))

                check = connect_db()
                member = check.execute(
                    "SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ).fetchone()
                if not member:
                    check.close()
                    continue
                members = check.execute(
                    "SELECT user_id FROM group_members WHERE group_id=? AND user_id<>?",
                    (group_id, user_id),
                ).fetchall()
                check.close()

                payload = {
                    "type": "group_call_ping",
                    "group_id": group_id,
                    "from_user_id": user_id,
                    "from_name": display_name,
                    "identity": f"user-{user_id}",
                    "ping_ms": ping_ms,
                }
                await deliver_group_batch(
                    [
                        {
                            "recipient_id": int(member_row["user_id"]),
                            "payload": payload,
                        }
                        for member_row in members
                    ]
                )
                continue

            if signal_type == "typing":
                chat_type = str(data.get("chat_type", ""))[:12]
                typing = bool(data.get("typing", False))
                try:
                    chat_id = int(data.get("chat_id"))
                except (TypeError, ValueError):
                    continue

                if chat_type == "user":
                    if chat_id == user_id:
                        continue
                    check = connect_db()
                    exists = check.execute(
                        "SELECT 1 FROM users WHERE id=?",
                        (chat_id,),
                    ).fetchone()
                    blocked = (
                        users_blocked(check, user_id, chat_id)
                        if exists
                        else True
                    )
                    check.close()
                    if blocked:
                        continue
                    await push(
                        chat_id,
                        {
                            "type": "typing",
                            "chat_type": "user",
                            "chat_id": user_id,
                            "from_user_id": user_id,
                            "from_name": display_name,
                            "typing": typing,
                        },
                    )
                    continue

                if chat_type == "group":
                    check = connect_db()
                    member = check.execute(
                        "SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",
                        (chat_id, user_id),
                    ).fetchone()
                    if not member:
                        check.close()
                        continue
                    members = check.execute(
                        "SELECT user_id FROM group_members WHERE group_id=? AND user_id<>?",
                        (chat_id, user_id),
                    ).fetchall()
                    check.close()
                    payload = {
                        "type": "typing",
                        "chat_type": "group",
                        "chat_id": chat_id,
                        "from_user_id": user_id,
                        "from_name": display_name,
                        "typing": typing,
                    }
                    await deliver_group_batch(
                        [
                            {
                                "recipient_id": int(member_row["user_id"]),
                                "payload": payload,
                            }
                            for member_row in members
                        ]
                    )
                    continue

                continue

            if signal_type not in CALL_SIGNAL_TYPES:
                continue

            try:
                target_id = int(data.get("to_user_id"))
            except (TypeError, ValueError):
                continue
            if target_id == user_id:
                continue

            call_id = str(data.get("call_id", ""))[:80]
            if not call_id:
                continue

            check = connect_db()
            exists = check.execute(
                "SELECT 1 FROM users WHERE id=?",
                (target_id,),
            ).fetchone()
            check.close()
            if not exists:
                continue

            block_check = connect_db()
            blocked = users_blocked(block_check, user_id, target_id)
            block_check.close()
            if blocked:
                if signal_type == "call_offer":
                    await push(
                        user_id,
                        {
                            "type": "call_unavailable",
                            "from_user_id": target_id,
                            "from_name": "Система",
                            "call_id": call_id,
                        },
                    )
                continue

            payload = {
                "type": signal_type,
                "from_user_id": user_id,
                "from_name": display_name,
                "from_avatar_url": caller_avatar_url,
                "call_id": call_id,
            }

            if signal_type in {
                "call_offer",
                "call_answer",
                "call_video_offer",
                "call_video_answer",
            }:
                sdp = data.get("sdp")
                if not isinstance(sdp, dict):
                    continue
                payload["sdp"] = sdp
                if signal_type in {
                    "call_offer",
                    "call_answer",
                    "call_video_offer",
                    "call_video_answer",
                }:
                    payload["video"] = bool(data.get("video", False))
            elif signal_type == "ice_candidate":
                candidate = data.get("candidate")
                if not isinstance(candidate, dict):
                    continue
                payload["candidate"] = candidate

            if signal_type == "call_offer":
                call = {
                    "call_id": call_id,
                    "caller_id": user_id,
                    "caller_name": display_name,
                    "caller_avatar_url": caller_avatar_url,
                    "callee_id": target_id,
                    "video": bool(data.get("video", False)),
                    "offer_sdp": payload["sdp"],
                    "caller_ice": [],
                    "started_at": now_iso(),
                    "answered": False,
                    "caller_muted": False,
                    "callee_muted": False,
                    "caller_video_enabled": bool(data.get("video", False)),
                    "callee_video_enabled": False,
                    "missed_notified": False,
                    "action_token": secrets.token_urlsafe(24),
                }
                active_calls[call_id] = call
                save_call_started(call)
                await push(target_id, payload)

                kind = "Входящий видеозвонок" if call["video"] else "Входящий звонок"
                await send_web_push(
                    target_id,
                    kind,
                    f"Звонит {display_name}",
                    (
                        f"/?incoming_call={call_id}"
                        f"&native_ring=1"
                        f"&action_token={call['action_token']}"
                    ),
                    f"incoming-call-{call_id}",
                    False,
                )

                asyncio.create_task(expire_call(call_id))
                continue

            call = active_calls.get(call_id)

            if signal_type == "call_video_state":
                if not call:
                    continue
                participants = {call["caller_id"], call["callee_id"]}
                if user_id not in participants or target_id not in participants:
                    continue
                if user_id == target_id:
                    continue

                enabled = bool(data.get("enabled", False))
                sync = bool(data.get("sync", False))
                if user_id == call["caller_id"]:
                    call["caller_video_enabled"] = enabled
                else:
                    call["callee_video_enabled"] = enabled

                if enabled:
                    call["video"] = True
                    mark_call_video(call_id)

                payload["enabled"] = enabled
                payload["sync"] = sync
                await push(target_id, payload)

                if sync:
                    remote_enabled = (
                        bool(call.get("caller_video_enabled", False))
                        if target_id == call["caller_id"]
                        else bool(call.get("callee_video_enabled", False))
                    )
                    await push(
                        user_id,
                        {
                            "type": "call_video_state",
                            "from_user_id": target_id,
                            "from_name": "Собеседник",
                            "call_id": call_id,
                            "enabled": remote_enabled,
                            "sync": True,
                        },
                    )
                continue

            if signal_type == "call_mute":
                if not call:
                    continue
                participants = {call["caller_id"], call["callee_id"]}
                if user_id not in participants or target_id not in participants:
                    continue
                if user_id == target_id:
                    continue

                muted = bool(data.get("muted", False))
                sync = bool(data.get("sync", False))
                if user_id == call["caller_id"]:
                    call["caller_muted"] = muted
                else:
                    call["callee_muted"] = muted

                payload["muted"] = muted
                payload["sync"] = sync
                await push(target_id, payload)

                if sync:
                    remote_muted = (
                        bool(call.get("caller_muted", False))
                        if target_id == call["caller_id"]
                        else bool(call.get("callee_muted", False))
                    )
                    await push(
                        user_id,
                        {
                            "type": "call_mute",
                            "from_user_id": target_id,
                            "from_name": "Собеседник",
                            "call_id": call_id,
                            "muted": remote_muted,
                            "sync": True,
                        },
                    )
                continue

            if signal_type in {"call_video_offer", "call_video_answer"}:
                if not call or not call.get("answered"):
                    continue
                participants = {call["caller_id"], call["callee_id"]}
                if user_id not in participants or target_id not in participants:
                    continue
                if user_id == target_id:
                    continue

                requested_video = bool(data.get("video", False))
                reconnect = bool(data.get("reconnect", False))

                if signal_type == "call_video_offer" and requested_video:
                    call["video"] = True
                    if not reconnect:
                        if user_id == call["caller_id"]:
                            call["caller_video_enabled"] = True
                        else:
                            call["callee_video_enabled"] = True
                    mark_call_video(call_id)

                if reconnect and not requested_video:
                    payload["video"] = bool(call.get("video", False))
                else:
                    payload["video"] = requested_video

                payload["reconnect"] = reconnect
                await push(target_id, payload)
                continue

            if signal_type == "call_answer":
                if call:
                    call["answered"] = True
                    call["answered_at"] = now_iso()
                    if user_id == call["caller_id"]:
                        call["caller_video_enabled"] = bool(data.get("video", False))
                    else:
                        call["callee_video_enabled"] = bool(data.get("video", False))
                    mark_call_answered(call_id)
                await push(target_id, payload)
                if call:
                    await push(
                        user_id,
                        {
                            "type": "call_mute",
                            "from_user_id": target_id,
                            "from_name": "Собеседник",
                            "call_id": call_id,
                            "muted": bool(
                                call.get(
                                    "caller_muted"
                                    if target_id == call["caller_id"]
                                    else "callee_muted",
                                    False,
                                )
                            ),
                            "sync": True,
                        },
                    )
                    await push(
                        target_id,
                        {
                            "type": "call_mute",
                            "from_user_id": user_id,
                            "from_name": display_name,
                            "call_id": call_id,
                            "muted": bool(
                                call.get(
                                    "caller_muted"
                                    if user_id == call["caller_id"]
                                    else "callee_muted",
                                    False,
                                )
                            ),
                            "sync": True,
                        },
                    )
                    await push(
                        user_id,
                        {
                            "type": "call_video_state",
                            "from_user_id": target_id,
                            "from_name": "Собеседник",
                            "call_id": call_id,
                            "enabled": bool(
                                call.get(
                                    "caller_video_enabled"
                                    if target_id == call["caller_id"]
                                    else "callee_video_enabled",
                                    False,
                                )
                            ),
                            "sync": True,
                        },
                    )
                    await push(
                        target_id,
                        {
                            "type": "call_video_state",
                            "from_user_id": user_id,
                            "from_name": display_name,
                            "call_id": call_id,
                            "enabled": bool(
                                call.get(
                                    "caller_video_enabled"
                                    if user_id == call["caller_id"]
                                    else "callee_video_enabled",
                                    False,
                                )
                            ),
                            "sync": True,
                        },
                    )
                continue

            if signal_type == "call_reject":
                finish_call_history(call_id, "rejected")
                await push(target_id, payload)
                active_calls.pop(call_id, None)
                continue

            if signal_type == "call_end":
                if call and call.get("answered"):
                    finish_call_history(call_id, "completed")
                elif call:
                    await notify_missed_call(call)
                await push(target_id, payload)
                active_calls.pop(call_id, None)
                continue

            if signal_type == "ice_candidate":
                if (
                    call
                    and user_id == call.get("caller_id")
                    and not call.get("answered")
                ):
                    candidates = call.setdefault("caller_ice", [])
                    if len(candidates) < 64:
                        candidates.append(payload["candidate"])
                await push(target_id, payload)
                continue

            await push(target_id, payload)

    except WebSocketDisconnect:
        pass
    finally:
        connections.get(user_id, set()).discard(websocket)
        state = ws_connection_state.pop(websocket, None)
        task = state.get("flush_task") if state else None
        if task and not task.done():
            task.cancel()
        if not connections.get(user_id):
            connections.pop(user_id, None)
            try:
                record_online_sample()
            except Exception:
                pass
            last_seen_at = now_iso()
            presence_conn = connect_db()
            presence_conn.execute(
                "UPDATE users SET last_seen_at=? WHERE id=?",
                (last_seen_at, user_id),
            )
            presence_conn.commit()
            presence_conn.close()
            await broadcast_presence(user_id, False, last_seen_at)

            unfinished = [
                call
                for call in list(active_calls.values())
                if call["caller_id"] == user_id and not call.get("answered")
            ]
            for call in unfinished:
                await notify_missed_call(call)
                active_calls.pop(call["call_id"], None)

            connected = [
                call
                for call in list(active_calls.values())
                if call.get("answered")
                and user_id in (call["caller_id"], call["callee_id"])
            ]
            for call in connected:
                asyncio.create_task(
                    finish_answered_call_after_disconnect_grace(
                        call["call_id"],
                        user_id,
                    )
                )


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(
        BASE_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/icon-192.svg")
def icon_192():
    return FileResponse(
        BASE_DIR / "icon-192.svg",
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/icon-512.svg")
def icon_512():
    return FileResponse(
        BASE_DIR / "icon-512.svg",
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/sw.js")
def service_worker():
    return FileResponse(
        BASE_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/")
def root():
    return FileResponse(
        BASE_DIR / "index.html",
        headers={"Cache-Control": "no-cache"},
    )