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
    web_stats = await _original_send_web_push(
        user_id,
        title,
        body,
        url,
        tag,
        force,
        silent,
        prepared_context=prepared_context,
    )

    stats = dict(web_stats)
    stats["web_configured"] = bool(web_stats.get("configured"))
    stats["android_configured"] = firebase_configured()
    stats["web_attempted"] = int(web_stats.get("attempted", 0))
    stats["web_sent"] = int(web_stats.get("sent", 0))
    stats["android_attempted"] = 0
    stats["android_sent"] = 0
    stats["configured"] = bool(
        stats["web_configured"] or stats["android_configured"]
    )

    if not stats["android_configured"]:
        return stats

    conn = core.connect_db()
    rows = conn.execute(
        "SELECT token FROM android_push_tokens WHERE user_id=?",
        (user_id,),
    ).fetchall()
    unread_count = core.unread_count_for_user(conn, user_id)
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

    stats["android_attempted"] = len(rows)
    for row in rows:
        result = await asyncio.to_thread(
            send_fcm_one,
            row["token"],
            payload,
        )
        if result["ok"]:
            stats["android_sent"] += 1
        else:
            if result["error"]:
                stats.setdefault("errors", []).append(result["error"])
            if result["stale"]:
                stale_tokens.append(row["token"])

    if stale_tokens:
        conn = core.connect_db()
        conn.executemany(
            "DELETE FROM android_push_tokens WHERE token=?",
            [(token,) for token in stale_tokens],
        )
        conn.commit()
        conn.close()

    stats["attempted"] = (
        stats["web_attempted"] + stats["android_attempted"]
    )
    stats["sent"] = stats["web_sent"] + stats["android_sent"]
    stats["stale"] = int(stats.get("stale", 0)) + len(stale_tokens)
    stats["errors"] = stats.get("errors", [])[:3]
    return stats


core.send_web_push = send_push
app = core.app
