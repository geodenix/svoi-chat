import asyncio
import os
from datetime import timedelta
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, messaging

import app as core


FIREBASE_CREDENTIALS_FILE = os.getenv(
    "FIREBASE_CREDENTIALS_FILE",
    "/opt/svoi-chat/firebase-admin.json",
).strip()

_original_send_web_push = core.send_web_push
_firebase_app = None


def firebase_configured() -> bool:
    return bool(
        FIREBASE_CREDENTIALS_FILE
        and Path(FIREBASE_CREDENTIALS_FILE).is_file()
    )


def get_firebase_app():
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app
    if not firebase_configured():
        raise RuntimeError("Firebase credentials are not configured")
    try:
        _firebase_app = firebase_admin.get_app("svoi")
    except ValueError:
        cred = credentials.Certificate(FIREBASE_CREDENTIALS_FILE)
        _firebase_app = firebase_admin.initialize_app(cred, name="svoi")
    return _firebase_app


def send_fcm_one(token: str, payload: dict) -> dict:
    try:
        message = messaging.Message(
            token=token,
            data={
                "title": str(payload.get("title") or "Свои"),
                "body": str(payload.get("body") or "")[:180],
                "url": str(payload.get("url") or "/"),
                "tag": str(payload.get("tag") or "svoi"),
                "force": "true" if payload.get("force") else "false",
                "silent": "true" if payload.get("silent") else "false",
                "unread_count": str(int(payload.get("unread_count") or 0)),
                "type": (
                    "call"
                    if str(payload.get("tag") or "").startswith(
                        "incoming-call-"
                    )
                    else "message"
                ),
            },
            android=messaging.AndroidConfig(
                priority="high",
                ttl=timedelta(seconds=120),
            ),
        )
        messaging.send(message, app=get_firebase_app())
        return {"ok": True, "stale": False, "error": None}
    except Exception as exc:
        error = str(exc)[:180]
        lowered = error.lower()
        name = exc.__class__.__name__.lower()
        stale = (
            "unregistered" in name
            or "registration-token-not-registered" in lowered
            or "requested entity was not found" in lowered
        )
        return {"ok": False, "stale": stale, "error": error}


async def _send_android_push(
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
        "configured": firebase_configured(),
        "attempted": 0,
        "sent": 0,
        "stale": 0,
        "errors": [],
    }
    if not stats["configured"]:
        return stats

    conn = core.connect_db()
    try:
        rows = conn.execute(
            "SELECT token FROM android_push_tokens WHERE user_id=?",
            (user_id,),
        ).fetchall()
        unread_count = core.unread_count_for_user(conn, user_id)
    finally:
        conn.close()

    stale_tokens = []
    payload = {
        "title": title,
        "body": body,
        "url": url,
        "tag": tag,
        "force": force,
        "silent": silent,
        "unread_count": unread_count,
    }

    stats["attempted"] = len(rows)
    for row in rows:
        result = await asyncio.to_thread(
            send_fcm_one,
            row["token"],
            payload,
        )
        if result["ok"]:
            stats["sent"] += 1
        else:
            if result["error"]:
                stats.setdefault("errors", []).append(result["error"])
            if result["stale"]:
                stale_tokens.append(row["token"])

    if stale_tokens:
        conn = core.connect_db()
        try:
            conn.executemany(
                "DELETE FROM android_push_tokens WHERE token=?",
                [(token,) for token in stale_tokens],
            )
            conn.commit()
        finally:
            conn.close()

    stats["stale"] = len(stale_tokens)
    stats["errors"] = stats["errors"][:3]
    return stats


async def send_push(
    user_id: int,
    title: str,
    body: str,
    url: str = "/",
    tag: str = "svoi",
    force: bool = False,
    silent: bool = False,
    prepared_context: dict | None = None,
):
    # Neither provider should prevent the other channel from starting.
    results = await asyncio.gather(
        _original_send_web_push(
            user_id, title, body, url, tag, force, silent,
            prepared_context=prepared_context,
        ),
        _send_android_push(user_id, title, body, url, tag, force, silent),
        return_exceptions=True,
    )
    channels = []
    for name, result in zip(("web", "android"), results):
        if isinstance(result, Exception):
            result = {
                "configured": (
                    core.push_configured() if name == "web"
                    else firebase_configured()
                ),
                "attempted": 0,
                "sent": 0,
                "stale": 0,
                "errors": [name + ": " + str(result)[:180]],
            }
        channels.append(result)

    web, android = channels
    stats = dict(web)
    for name, channel in zip(("web", "android"), channels):
        stats[name + "_configured"] = bool(channel.get("configured"))
        stats[name + "_attempted"] = int(channel.get("attempted", 0))
        stats[name + "_sent"] = int(channel.get("sent", 0))
    stats["configured"] = stats["web_configured"] or stats["android_configured"]
    for key in ("attempted", "sent", "stale"):
        stats[key] = int(web.get(key, 0)) + int(android.get(key, 0))
    stats["errors"] = (list(web.get("errors", [])) + list(android.get("errors", [])))[:3]
    return stats


core.send_web_push = send_push
app = core.app
