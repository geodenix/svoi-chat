import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Set

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
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
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
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

app = FastAPI(title="Свои", version="0.1.0")
connections: Dict[int, Set[WebSocket]] = {}
active_calls: dict[str, dict] = {}


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
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sessions (
      token_hash TEXT PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL
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
      client_message_id TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_messages_pair
      ON messages(sender_id, recipient_id, id);
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
    CREATE TABLE IF NOT EXISTS group_messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      group_id INTEGER NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
      sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      body TEXT NOT NULL,
      created_at TEXT NOT NULL,
      client_message_id TEXT
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
    """)
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


def make_session(conn, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions(token_hash,user_id,created_at) VALUES(?,?,?)",
        (token_hash(token), user_id, now_iso()),
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


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=6, max_length=128)


class LoginIn(BaseModel):
    username: str
    password: str


class MessageIn(BaseModel):
    recipient_id: int
    body: str = Field(default="", max_length=4000)
    attachment_id: int | None = None
    client_message_id: str | None = Field(default=None, max_length=80)


class GroupCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    member_ids: list[int] = Field(default_factory=list)


class GroupRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class GroupMemberAddIn(BaseModel):
    tag: str = Field(min_length=1, max_length=33)


class GroupMessageIn(BaseModel):
    body: str = Field(default="", max_length=4000)
    attachment_id: int | None = None
    client_message_id: str | None = Field(default=None, max_length=80)


class ForwardMessageIn(BaseModel):
    source_type: str = Field(pattern=r"^(user|group)$")
    source_message_id: int
    target_type: str = Field(pattern=r"^(user|group)$")
    target_chat_id: int


class PushKeysIn(BaseModel):
    p256dh: str = Field(min_length=20, max_length=512)
    auth: str = Field(min_length=8, max_length=256)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=20, max_length=2048)
    keys: PushKeysIn


class GroupCallIn(BaseModel):
    video: bool = False
    invite: bool = False


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
def register(data: RegisterIn, conn=Depends(db)):
    username = data.username.strip().lower()
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        raise HTTPException(409, "Такой логин уже занят")
    password_hash, salt = hash_password(data.password)
    cur = conn.execute(
        """INSERT INTO users(username,display_name,password_hash,salt,created_at)
           VALUES(?,?,?,?,?)""",
        (username, data.display_name.strip(), password_hash, salt, now_iso()),
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
    return {"token": make_session(conn, row["id"]), "user": user_json(row)}


@app.post("/api/login")
def login(data: LoginIn, conn=Depends(db)):
    row = conn.execute(
        """SELECT u.*, a.stored_name AS avatar_stored_name
           FROM users u
           LEFT JOIN uploads a ON a.id=u.avatar_id
           WHERE u.username=?""",
        (data.username.strip().lower(),),
    ).fetchone()
    if not row or not verify_password(data.password, row["password_hash"], row["salt"]):
        raise HTTPException(401, "Неверный логин или пароль")
    return {"token": make_session(conn, row["id"]), "user": user_json(row)}


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
    return user_json(user)


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


@app.get("/api/users")
def users(user=Depends(current_user), conn=Depends(db)):
    rows = conn.execute(
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
           WHERE u.id<>?
             AND (
               EXISTS(
                 SELECT 1 FROM contacts c
                 WHERE c.user_id=? AND c.contact_user_id=u.id
               )
               OR EXISTS(
                 SELECT 1 FROM messages m
                 WHERE (m.sender_id=? AND m.recipient_id=u.id)
                    OR (m.sender_id=u.id AND m.recipient_id=?)
               )
             )
           ORDER BY u.display_name""",
        (
            user["id"],
            user["id"],
            user["id"],
            user["id"],
            user["id"],
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


async def send_web_push(
    user_id: int,
    title: str,
    body: str,
    url: str = "/",
    tag: str = "svoi",
    force: bool = False,
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

    conn = connect_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT endpoint,p256dh,auth
           FROM push_subscriptions WHERE user_id=?""",
        (user_id,),
    ).fetchall()
    conn.close()

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
        },
        ensure_ascii=False,
    )

    stale = []
    for row in rows:
        subscription = {
            "endpoint": row["endpoint"],
            "keys": {
                "p256dh": row["p256dh"],
                "auth": row["auth"],
            },
        }
        result = await asyncio.to_thread(
            _webpush_one,
            subscription,
            payload,
        )
        if result["ok"]:
            stats["sent"] += 1
        else:
            if result["error"]:
                stats["errors"].append(result["error"])
            if result["stale"]:
                stale.append(row["endpoint"])

    stats["stale"] = len(stale)
    stats["errors"] = stats["errors"][:3]

    if stale:
        conn = connect_db()
        conn.executemany(
            "DELETE FROM push_subscriptions WHERE endpoint=?",
            [(endpoint,) for endpoint in stale],
        )
        conn.commit()
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
    return {
        "configured": push_configured(),
        "subscriptions": count,
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


@app.post("/api/uploads")
async def upload(
    file: UploadFile = File(...),
    user=Depends(current_user),
    conn=Depends(db),
):
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Файл больше 20 МБ")
    original = Path(file.filename or "file").name[:255] or "file"
    suffix = Path(original).suffix.lower()
    if len(suffix) > 12 or not all(ch.isalnum() or ch == "." for ch in suffix):
        suffix = ""
    stored = secrets.token_hex(24) + suffix
    mime = effective_media_mime(
        (file.content_type or "application/octet-stream")[:120],
        original,
    )
    (UPLOAD_DIR / stored).write_bytes(content)
    cur = conn.execute(
        """INSERT INTO uploads(
             owner_id,stored_name,original_name,mime_type,size,created_at
           ) VALUES(?,?,?,?,?,?)""",
        (user["id"], stored, original, mime, len(content), now_iso()),
    )
    conn.commit()
    return {
        "id": cur.lastrowid,
        "url": f"/uploads/{stored}",
        "name": original,
        "mime_type": mime,
        "size": len(content),
        "is_image": mime in INLINE_IMAGE_TYPES,
        "is_audio": mime.startswith("audio/"),
        "is_video": mime.startswith("video/"),
    }


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
    if (
        mime in INLINE_IMAGE_TYPES
        or mime.startswith("audio/")
        or mime.startswith("video/")
    ):
        return FileResponse(path, media_type=mime)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=row["original_name"],
    )


@app.get("/api/messages/{other_id}")
async def history(
    other_id: int,
    limit: int = Query(100, ge=1, le=300),
    user=Depends(current_user),
    conn=Depends(db),
):
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (other_id,)).fetchone():
        raise HTTPException(404, "Пользователь не найден")
    unread_rows = conn.execute(
        """SELECT id FROM messages
           WHERE sender_id=? AND recipient_id=? AND read_at IS NULL""",
        (other_id, user["id"]),
    ).fetchall()
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
                "message_ids": [row["id"] for row in unread_rows],
                "read_at": seen_at,
            },
        )
    rows = conn.execute(
        """SELECT m.id,m.sender_id,m.recipient_id,m.body,m.created_at,
                  m.delivered_at,m.read_at,m.forwarded,
                  up.id AS attachment_id,
                  up.stored_name AS attachment_stored_name,
                  up.original_name AS attachment_name,
                  up.mime_type AS attachment_mime,
                  up.size AS attachment_size
           FROM messages m
           LEFT JOIN uploads up ON up.id=m.attachment_id
           WHERE (m.sender_id=? AND m.recipient_id=?)
              OR (m.sender_id=? AND m.recipient_id=?)
           ORDER BY m.id DESC LIMIT ?""",
        (user["id"], other_id, other_id, user["id"], limit),
    ).fetchall()
    result = []
    for row in reversed(rows):
        item = {
            "id": row["id"],
            "sender_id": row["sender_id"],
            "recipient_id": row["recipient_id"],
            "body": row["body"],
            "created_at": row["created_at"],
            "delivered_at": row["delivered_at"],
            "read_at": row["read_at"],
            "forwarded": bool(row["forwarded"]),
            "attachment": attachment_json(row),
        }
        result.append(item)
    return result


async def push(user_id: int, payload: dict) -> int:
    dead = []
    sent = 0
    for ws in list(connections.get(user_id, set())):
        try:
            await ws.send_json(payload)
            sent += 1
        except Exception:
            dead.append(ws)
    for ws in dead:
        connections.get(user_id, set()).discard(ws)
    return sent


@app.post("/api/messages")
async def send_message(data: MessageIn, user=Depends(current_user), conn=Depends(db)):
    if data.recipient_id == user["id"]:
        raise HTTPException(400, "Нельзя отправить сообщение самому себе")
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (data.recipient_id,)).fetchone():
        raise HTTPException(404, "Пользователь не найден")
    if users_blocked(conn, user["id"], data.recipient_id):
        raise HTTPException(403, "Личное общение с этим пользователем недоступно")
    body = data.body.strip()
    upload_row = owned_upload(conn, data.attachment_id, user["id"])
    if not body and not upload_row:
        raise HTTPException(400, "Пустое сообщение")
    created = now_iso()
    cur = conn.execute(
        """INSERT INTO messages(
             sender_id,recipient_id,body,created_at,attachment_id,
             delivered_at,read_at
           ) VALUES(?,?,?,?,?,?,?)""",
        (
            user["id"],
            data.recipient_id,
            body,
            created,
            data.attachment_id,
            None,
            None,
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
        "delivered_at": None,
        "read_at": None,
        "forwarded": False,
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
    await send_web_push(
        data.recipient_id,
        user["display_name"],
        preview,
        "/",
        f"user-{user['id']}",
    )
    return msg


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
        await send_web_push(
            data.target_chat_id,
            user["display_name"],
            "↪ " + preview,
            "/",
            f"user-{user['id']}",
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
        "attachment": attachment,
        "deleted": False,
        "deleted_at": None,
        "forwarded": True,
        "mentioned_me": user["id"] in mention_ids,
        "has_mentions": bool(mention_ids),
        "can_delete": True,
        "can_restore": False,
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

    for member in members:
        if member["user_id"] == user["id"]:
            continue
        recipient_id = int(member["user_id"])
        live_msg = {
            **msg,
            "mentioned_me": recipient_id in mention_ids,
            "can_delete": bool(member["is_admin"]),
        }
        await push(
            recipient_id,
            {"type": "group_message", "message": live_msg},
        )
        should_notify = (
            recipient_id in mention_ids
            if mention_ids
            else True
        )
        if should_notify:
            await send_web_push(
                recipient_id,
                (
                    f"Упоминание · {group['name']}"
                    if mention_ids
                    else f"{group['name']} · {user['display_name']}"
                ),
                (
                    f"{user['display_name']}: ↪ {preview}"
                    if mention_ids
                    else "↪ " + preview
                ),
                "/",
                f"group-{data.target_chat_id}",
            )

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


@app.get("/api/groups")
def get_groups(user=Depends(current_user), conn=Depends(db)):
    rows = conn.execute(
        """SELECT g.id,g.name,g.owner_id,g.created_at,
                  ga.stored_name AS avatar_stored_name,
                  mine.is_admin AS is_admin,
                  COUNT(gm2.user_id) AS member_count
           FROM chat_groups g
           JOIN group_members mine
             ON mine.group_id=g.id AND mine.user_id=?
           LEFT JOIN group_members gm2 ON gm2.group_id=g.id
           LEFT JOIN uploads ga ON ga.id=g.avatar_id
           GROUP BY g.id
           ORDER BY g.id DESC""",
        (user["id"],),
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
def get_group_messages(
    group_id: int,
    limit: int = Query(100, ge=1, le=300),
    user=Depends(current_user),
    conn=Depends(db),
):
    group = group_for_user(conn, group_id, user["id"])
    if not group:
        raise HTTPException(404, "Группа не найдена")
    rows = conn.execute(
        """SELECT gm.id,gm.group_id,gm.sender_id,gm.body,gm.created_at,
                  gm.deleted_at,gm.deleted_by,gm.forwarded,
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
           ORDER BY gm.id DESC LIMIT ?""",
        (user["id"], group_id, limit),
    ).fetchall()
    result = []
    is_admin = bool(group["is_admin"])
    for row in reversed(rows):
        deleted = bool(row["deleted_at"])
        item = {
            "id": row["id"],
            "group_id": row["group_id"],
            "sender_id": row["sender_id"],
            "sender_name": row["sender_name"],
            "body": "" if deleted else row["body"],
            "created_at": row["created_at"],
            "deleted": deleted,
            "deleted_at": row["deleted_at"],
            "forwarded": bool(row["forwarded"]),
            "mentioned_me": bool(row["mentioned_me"]) and not deleted,
            "has_mentions": bool(row["has_mentions"]) and not deleted,
            "can_delete": (
                not deleted
                and (is_admin or row["sender_id"] == user["id"])
            ),
            "can_restore": deleted and is_admin,
            "attachment": None if deleted else attachment_json(row),
        }
        result.append(item)
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
    body = data.body.strip()
    upload_row = owned_upload(conn, data.attachment_id, user["id"])
    if not body and not upload_row:
        raise HTTPException(400, "Пустое сообщение")
    created = now_iso()
    cur = conn.execute(
        """INSERT INTO group_messages(
             group_id,sender_id,body,created_at,attachment_id
           ) VALUES(?,?,?,?,?)""",
        (
            group_id,
            user["id"],
            body,
            created,
            data.attachment_id,
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
        "mentioned_me": user["id"] in mention_ids,
        "has_mentions": bool(mention_ids),
        "can_delete": True,
        "can_restore": False,
    }
    member_rows = conn.execute(
        "SELECT user_id,is_admin FROM group_members WHERE group_id=?",
        (group_id,),
    ).fetchall()
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
    for row in member_rows:
        if row["user_id"] != user["id"]:
            recipient_id = int(row["user_id"])
            live_msg = {
                **msg,
                "mentioned_me": recipient_id in mention_ids,
                "can_delete": bool(row["is_admin"]),
            }
            await push(
                recipient_id,
                {"type": "group_message", "message": live_msg},
            )
            should_notify = (
                recipient_id in mention_ids
                if mention_ids
                else True
            )
            if should_notify:
                await send_web_push(
                    recipient_id,
                    (
                        f"Упоминание · {group['name']}"
                        if mention_ids
                        else f"{group['name']} · {user['display_name']}"
                    ),
                    (
                        f"{user['display_name']}: {preview}"
                        if mention_ids
                        else preview
                    ),
                    "/",
                    f"group-{group_id}",
                )
    return msg


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
        (user["id"], message_id, group_id),
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
        "SELECT user_id FROM group_members WHERE group_id=?",
        (group_id,),
    ).fetchall()
    event = {
        "type": "group_message_deleted",
        "group_id": group_id,
        "message_id": message_id,
        "deleted_at": deleted_at,
    }
    for member in members:
        await push(int(member["user_id"]), event)

    return {
        "ok": True,
        "group_id": group_id,
        "message_id": message_id,
        "deleted_at": deleted_at,
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
        """SELECT gm.id,gm.group_id,gm.sender_id,gm.body,gm.created_at,
                  gm.deleted_at,gm.deleted_by,gm.forwarded,
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
        (message_id, group_id),
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
        "attachment": attachment_json(row),
        "deleted": False,
        "deleted_at": None,
        "forwarded": bool(row["forwarded"]),
        "mentioned_me": bool(row["mentioned_me"]),
        "has_mentions": bool(row["has_mentions"]),
        "can_delete": True,
        "can_restore": False,
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
    for member in members:
        payload = {
            **restored,
            "mentioned_me": int(member["user_id"]) in mentioned_ids,
            "can_delete": (
                bool(member["is_admin"])
                or int(member["user_id"]) == int(row["sender_id"])
            ),
        }
        await push(
            int(member["user_id"]),
            {
                "type": "group_message_restored",
                "group_id": group_id,
                "message": payload,
            },
        )

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
        for member in members:
            member_id = member["user_id"]
            await push(member_id, invite_payload)
            await send_web_push(
                member_id,
                kind,
                f"{user['display_name']} зовёт в «{group['name']}»",
                f"/?group_call={group_id}&video={1 if data.video else 0}",
                f"group-call-{group_id}",
                False,
            )

    return {
        "server_url": LIVEKIT_WS_URL,
        "participant_token": token,
        "room_name": room_name,
        "group_id": group_id,
        "group_name": group["name"],
        "video": bool(data.video),
        "max_participants": 10,
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
        "video": call["video"],
        "sdp": call["offer_sdp"],
        "ice_candidates": call.get("caller_ice", []),
    }


CALL_SIGNAL_TYPES = {
    "call_offer",
    "call_answer",
    "call_video_offer",
    "call_video_answer",
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
    for peer_id in presence_peer_ids(user_id):
        await push(peer_id, payload)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    conn = connect_db()
    conn.row_factory = sqlite3.Row
    row = get_user_from_token(conn, token)
    conn.close()
    if not row:
        await websocket.close(code=4401)
        return

    user_id = row["id"]
    display_name = row["display_name"]
    await websocket.accept()
    was_offline = not bool(connections.get(user_id))
    connections.setdefault(user_id, set()).add(websocket)

    if was_offline:
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
            await websocket.send_json(
                {
                    "type": "call_offer",
                    "from_user_id": call["caller_id"],
                    "from_name": call["caller_name"],
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
                    check.close()
                    if not exists:
                        continue
                    check = connect_db()
                    blocked = users_blocked(check, user_id, chat_id)
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
                    for member_row in members:
                        await push(int(member_row["user_id"]), payload)
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
                    "callee_id": target_id,
                    "video": bool(data.get("video", False)),
                    "offer_sdp": payload["sdp"],
                    "caller_ice": [],
                    "started_at": now_iso(),
                    "answered": False,
                    "missed_notified": False,
                }
                active_calls[call_id] = call
                save_call_started(call)
                await push(target_id, payload)

                kind = "Входящий видеозвонок" if call["video"] else "Входящий звонок"
                await send_web_push(
                    target_id,
                    kind,
                    f"Звонит {display_name}",
                    f"/?incoming_call={call_id}",
                    f"incoming-call-{call_id}",
                    False,
                )

                asyncio.create_task(expire_call(call_id))
                continue

            call = active_calls.get(call_id)

            if signal_type in {"call_video_offer", "call_video_answer"}:
                if not call or not call.get("answered"):
                    continue
                participants = {call["caller_id"], call["callee_id"]}
                if user_id not in participants or target_id not in participants:
                    continue
                if user_id == target_id:
                    continue
                if signal_type == "call_video_offer":
                    call["video"] = True
                    mark_call_video(call_id)
                payload["video"] = True
                await push(target_id, payload)
                continue

            if signal_type == "call_answer":
                if call:
                    call["answered"] = True
                    call["answered_at"] = now_iso()
                    mark_call_answered(call_id)
                await push(target_id, payload)
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
        if not connections.get(user_id):
            connections.pop(user_id, None)
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
                finish_call_history(call["call_id"], "completed")
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
                        "from_name": display_name,
                        "call_id": call["call_id"],
                    },
                )
                active_calls.pop(call["call_id"], None)


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
