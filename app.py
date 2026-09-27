import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Set

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pywebpush import WebPushException, webpush

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("SVOI_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "svoi.db"
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
INLINE_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
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

app = FastAPI(title="Свои", version="0.1.0")
connections: Dict[int, Set[WebSocket]] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
    CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      body TEXT NOT NULL,
      created_at TEXT NOT NULL
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
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_group_messages
      ON group_messages(group_id, id);
    CREATE TABLE IF NOT EXISTS uploads (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      stored_name TEXT NOT NULL UNIQUE,
      original_name TEXT NOT NULL,
      mime_type TEXT NOT NULL,
      size INTEGER NOT NULL,
      created_at TEXT NOT NULL
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
        if table == "messages":
            if "delivered_at" not in columns:
                conn.execute(
                    "ALTER TABLE messages ADD COLUMN delivered_at TEXT"
                )
            if "read_at" not in columns:
                conn.execute(
                    "ALTER TABLE messages ADD COLUMN read_at TEXT"
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
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
    }


def get_user_from_token(conn, token: str):
    row = conn.execute(
        """SELECT u.id,u.username,u.display_name
           FROM sessions s JOIN users u ON u.id=s.user_id
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


class GroupCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    member_ids: list[int] = Field(default_factory=list)


class GroupMessageIn(BaseModel):
    body: str = Field(default="", max_length=4000)
    attachment_id: int | None = None


class PushKeysIn(BaseModel):
    p256dh: str = Field(min_length=20, max_length=512)
    auth: str = Field(min_length=8, max_length=256)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=20, max_length=2048)
    keys: PushKeysIn


@app.get("/health")
def health():
    return {"status": "ok", "service": "svoi-chat"}


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
        "SELECT id,username,display_name FROM users WHERE id=?", (cur.lastrowid,)
    ).fetchone()
    return {"token": make_session(conn, row["id"]), "user": user_json(row)}


@app.post("/api/login")
def login(data: LoginIn, conn=Depends(db)):
    row = conn.execute(
        "SELECT * FROM users WHERE username=?", (data.username.strip().lower(),)
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


@app.get("/api/users")
def users(user=Depends(current_user), conn=Depends(db)):
    rows = conn.execute(
        "SELECT id,username,display_name FROM users WHERE id<>? ORDER BY display_name",
        (user["id"],),
    ).fetchall()
    return [
        {**user_json(r), "online": bool(connections.get(r["id"]))}
        for r in rows
    ]


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

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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


def attachment_json(row):
    if not row or row["attachment_id"] is None:
        return None
    mime = row["attachment_mime"] or "application/octet-stream"
    return {
        "id": row["attachment_id"],
        "url": f"/uploads/{row['attachment_stored_name']}",
        "name": row["attachment_name"],
        "mime_type": mime,
        "size": row["attachment_size"],
        "is_image": mime in INLINE_IMAGE_TYPES,
    }


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
    mime = (file.content_type or "application/octet-stream")[:120]
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
    if row["mime_type"] in INLINE_IMAGE_TYPES:
        return FileResponse(path, media_type=row["mime_type"])
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
                  m.delivered_at,m.read_at,
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
        attachment = {
            "id": upload_row["id"],
            "url": f"/uploads/{upload_row['stored_name']}",
            "name": upload_row["original_name"],
            "mime_type": upload_row["mime_type"],
            "size": upload_row["size"],
            "is_image": upload_row["mime_type"] in INLINE_IMAGE_TYPES,
        }
    msg = {
        "id": cur.lastrowid,
        "sender_id": user["id"],
        "recipient_id": data.recipient_id,
        "body": body,
        "created_at": created,
        "delivered_at": None,
        "read_at": None,
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
    preview = body or (
        "📎 " + attachment["name"]
        if attachment
        else "Новое сообщение"
    )
    await send_web_push(
        data.recipient_id,
        user["display_name"],
        preview,
        "/",
        f"user-{user['id']}",
    )
    return msg


def group_for_user(conn, group_id: int, user_id: int):
    return conn.execute(
        """SELECT g.id,g.name,g.owner_id,g.created_at
           FROM chat_groups g
           JOIN group_members gm ON gm.group_id=g.id
           WHERE g.id=? AND gm.user_id=?""",
        (group_id, user_id),
    ).fetchone()


@app.get("/api/groups")
def get_groups(user=Depends(current_user), conn=Depends(db)):
    rows = conn.execute(
        """SELECT g.id,g.name,g.owner_id,g.created_at,
                  COUNT(gm2.user_id) AS member_count
           FROM chat_groups g
           JOIN group_members mine
             ON mine.group_id=g.id AND mine.user_id=?
           LEFT JOIN group_members gm2 ON gm2.group_id=g.id
           GROUP BY g.id
           ORDER BY g.id DESC""",
        (user["id"],),
    ).fetchall()
    return [dict(r) for r in rows]


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
        "INSERT INTO group_members(group_id,user_id,joined_at) VALUES(?,?,?)",
        [(group_id, uid, created) for uid in member_ids],
    )
    conn.commit()
    group = {
        "id": group_id,
        "name": name,
        "owner_id": user["id"],
        "created_at": created,
        "member_count": len(member_ids),
    }
    for uid in member_ids:
        if uid != user["id"]:
            await push(uid, {"type": "group_created", "group": group})
    return group


@app.get("/api/groups/{group_id}/members")
def get_group_members(group_id: int, user=Depends(current_user), conn=Depends(db)):
    if not group_for_user(conn, group_id, user["id"]):
        raise HTTPException(404, "Группа не найдена")
    rows = conn.execute(
        """SELECT u.id,u.username,u.display_name
           FROM group_members gm
           JOIN users u ON u.id=gm.user_id
           WHERE gm.group_id=?
           ORDER BY u.display_name""",
        (group_id,),
    ).fetchall()
    return [
        {**user_json(r), "online": bool(connections.get(r["id"]))}
        for r in rows
    ]


@app.get("/api/groups/{group_id}/messages")
def get_group_messages(
    group_id: int,
    limit: int = Query(100, ge=1, le=300),
    user=Depends(current_user),
    conn=Depends(db),
):
    if not group_for_user(conn, group_id, user["id"]):
        raise HTTPException(404, "Группа не найдена")
    rows = conn.execute(
        """SELECT gm.id,gm.group_id,gm.sender_id,gm.body,gm.created_at,
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
        (group_id, limit),
    ).fetchall()
    result = []
    for row in reversed(rows):
        item = dict(row)
        item["attachment"] = attachment_json(row)
        for key in (
            "attachment_id",
            "attachment_stored_name",
            "attachment_name",
            "attachment_mime",
            "attachment_size",
        ):
            item.pop(key, None)
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
    conn.commit()
    attachment = None
    if upload_row:
        attachment = {
            "id": upload_row["id"],
            "url": f"/uploads/{upload_row['stored_name']}",
            "name": upload_row["original_name"],
            "mime_type": upload_row["mime_type"],
            "size": upload_row["size"],
            "is_image": upload_row["mime_type"] in INLINE_IMAGE_TYPES,
        }
    msg = {
        "id": cur.lastrowid,
        "group_id": group_id,
        "sender_id": user["id"],
        "sender_name": user["display_name"],
        "body": body,
        "created_at": created,
        "attachment": attachment,
    }
    member_rows = conn.execute(
        "SELECT user_id FROM group_members WHERE group_id=?",
        (group_id,),
    ).fetchall()
    preview = body or (
        "📎 " + attachment["name"]
        if attachment
        else "Новое сообщение"
    )
    for row in member_rows:
        if row["user_id"] != user["id"]:
            await push(
                row["user_id"],
                {"type": "group_message", "message": msg},
            )
            await send_web_push(
                row["user_id"],
                f"{group['name']} · {user['display_name']}",
                preview,
                "/",
                f"group-{group_id}",
            )
    return msg


CALL_SIGNAL_TYPES = {
    "call_offer",
    "call_answer",
    "ice_candidate",
    "call_reject",
    "call_end",
}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    row = get_user_from_token(conn, token)
    conn.close()
    if not row:
        await websocket.close(code=4401)
        return
    user_id = row["id"]
    display_name = row["display_name"]
    await websocket.accept()
    connections.setdefault(user_id, set()).add(websocket)
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
            if signal_type not in CALL_SIGNAL_TYPES:
                continue
            try:
                target_id = int(data.get("to_user_id"))
            except (TypeError, ValueError):
                continue
            if target_id == user_id:
                continue

            check = sqlite3.connect(DB_PATH, check_same_thread=False)
            exists = check.execute(
                "SELECT 1 FROM users WHERE id=?",
                (target_id,),
            ).fetchone()
            check.close()
            if not exists:
                continue

            payload = {
                "type": signal_type,
                "from_user_id": user_id,
                "from_name": display_name,
                "call_id": str(data.get("call_id", ""))[:80],
            }
            if signal_type in {"call_offer", "call_answer"}:
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

            delivered = await push(target_id, payload)
            if signal_type == "call_offer" and delivered == 0:
                await websocket.send_json(
                    {
                        "type": "call_unavailable",
                        "to_user_id": target_id,
                        "call_id": payload["call_id"],
                    }
                )
    except WebSocketDisconnect:
        pass
    finally:
        connections.get(user_id, set()).discard(websocket)
        if not connections.get(user_id):
            connections.pop(user_id, None)


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
