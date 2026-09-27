import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Set

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("SVOI_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "svoi.db"

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
    """)
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
    body: str = Field(min_length=1, max_length=4000)


class GroupCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    member_ids: list[int] = Field(default_factory=list)


class GroupMessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


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


@app.get("/api/messages/{other_id}")
def history(
    other_id: int,
    limit: int = Query(100, ge=1, le=300),
    user=Depends(current_user),
    conn=Depends(db),
):
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (other_id,)).fetchone():
        raise HTTPException(404, "Пользователь не найден")
    rows = conn.execute(
        """SELECT id,sender_id,recipient_id,body,created_at
           FROM messages
           WHERE (sender_id=? AND recipient_id=?)
              OR (sender_id=? AND recipient_id=?)
           ORDER BY id DESC LIMIT ?""",
        (user["id"], other_id, other_id, user["id"], limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


async def push(user_id: int, payload: dict):
    dead = []
    for ws in list(connections.get(user_id, set())):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connections.get(user_id, set()).discard(ws)


@app.post("/api/messages")
async def send_message(data: MessageIn, user=Depends(current_user), conn=Depends(db)):
    if data.recipient_id == user["id"]:
        raise HTTPException(400, "Нельзя отправить сообщение самому себе")
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (data.recipient_id,)).fetchone():
        raise HTTPException(404, "Пользователь не найден")
    body = data.body.strip()
    if not body:
        raise HTTPException(400, "Пустое сообщение")
    created = now_iso()
    cur = conn.execute(
        "INSERT INTO messages(sender_id,recipient_id,body,created_at) VALUES(?,?,?,?)",
        (user["id"], data.recipient_id, body, created),
    )
    conn.commit()
    msg = {
        "id": cur.lastrowid,
        "sender_id": user["id"],
        "recipient_id": data.recipient_id,
        "body": body,
        "created_at": created,
    }
    await push(data.recipient_id, {"type": "message", "message": msg})
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
                  u.display_name AS sender_name
           FROM group_messages gm
           JOIN users u ON u.id=gm.sender_id
           WHERE gm.group_id=?
           ORDER BY gm.id DESC LIMIT ?""",
        (group_id, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


@app.post("/api/groups/{group_id}/messages")
async def send_group_message(
    group_id: int,
    data: GroupMessageIn,
    user=Depends(current_user),
    conn=Depends(db),
):
    if not group_for_user(conn, group_id, user["id"]):
        raise HTTPException(404, "Группа не найдена")
    body = data.body.strip()
    if not body:
        raise HTTPException(400, "Пустое сообщение")
    created = now_iso()
    cur = conn.execute(
        "INSERT INTO group_messages(group_id,sender_id,body,created_at) VALUES(?,?,?,?)",
        (group_id, user["id"], body, created),
    )
    conn.commit()
    msg = {
        "id": cur.lastrowid,
        "group_id": group_id,
        "sender_id": user["id"],
        "sender_name": user["display_name"],
        "body": body,
        "created_at": created,
    }
    member_rows = conn.execute(
        "SELECT user_id FROM group_members WHERE group_id=?",
        (group_id,),
    ).fetchall()
    for row in member_rows:
        if row["user_id"] != user["id"]:
            await push(row["user_id"], {"type": "group_message", "message": msg})
    return msg


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
    await websocket.accept()
    connections.setdefault(user_id, set()).add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        connections.get(user_id, set()).discard(websocket)
        if not connections.get(user_id):
            connections.pop(user_id, None)


@app.get("/")
def root():
    return FileResponse(BASE_DIR / "index.html")
