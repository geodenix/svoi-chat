import os
import re
import secrets
import shutil
from pathlib import Path

DOMAIN = "epl-gruz.duckdns.org"
APP_PORT = 8010
LIVEKIT_PORT = 7880
LIVEKIT_TCP = 7881
LIVEKIT_UDP = 7882

ENV_FILE = Path("/etc/svoi-chat.env")
CONFIG_FILE = Path("/etc/livekit.yaml")
SERVICE_FILE = Path("/etc/systemd/system/livekit.service")
CADDY_FILE = Path("/etc/caddy/Caddyfile")
CADDY_BACKUP = Path("/etc/caddy/Caddyfile.before-livekit")

binary = shutil.which("livekit-server")
if not binary:
    raise SystemExit(
        "livekit-server не найден. Сначала установи его официальным установщиком."
    )

existing = {}
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            existing[key] = value

api_key = existing.get("LIVEKIT_API_KEY") or ("svoi_" + secrets.token_hex(10))
api_secret = existing.get("LIVEKIT_API_SECRET") or secrets.token_urlsafe(48)

config = f"""port: {LIVEKIT_PORT}
log_level: info

rtc:
  tcp_port: {LIVEKIT_TCP}
  udp_port: {LIVEKIT_UDP}
  use_external_ip: true

keys:
  {api_key}: {api_secret}
"""

CONFIG_FILE.write_text(config)
os.chmod(CONFIG_FILE, 0o600)

existing["LIVEKIT_API_KEY"] = api_key
existing["LIVEKIT_API_SECRET"] = api_secret
existing["LIVEKIT_WS_URL"] = f"wss://{DOMAIN}"
ENV_FILE.write_text(
    "".join(f"{key}={value}\n" for key, value in existing.items())
)
os.chmod(ENV_FILE, 0o600)

service = f"""[Unit]
Description=LiveKit SFU for Svoi
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={binary} --config {CONFIG_FILE}
Restart=always
RestartSec=3
LimitNOFILE=65535
User=root

[Install]
WantedBy=multi-user.target
"""
SERVICE_FILE.write_text(service)

if CADDY_FILE.exists():
    text = CADDY_FILE.read_text()
    if not CADDY_BACKUP.exists():
        CADDY_BACKUP.write_text(text)

    if "@svoi_livekit" not in text:
        marker = DOMAIN + " {"
        start = text.find(marker)
        if start < 0:
            raise SystemExit(
                f"Не найден блок {DOMAIN} в {CADDY_FILE}"
            )

        brace = text.find("{", start)
        depth = 0
        end = None
        for i in range(brace, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            raise SystemExit("Не удалось разобрать Caddyfile")

        body = text[brace + 1:end]
        insert = (
            "\n    @svoi_livekit path /rtc*\n"
            f"    reverse_proxy @svoi_livekit 127.0.0.1:{LIVEKIT_PORT}\n"
        )
        text = text[:brace + 1] + insert + body + text[end:]
        CADDY_FILE.write_text(text)

print("LiveKit configuration ready.")
print("Signal: wss://" + DOMAIN)
print("WebRTC TCP:", LIVEKIT_TCP)
print("WebRTC UDP:", LIVEKIT_UDP)
print("Secrets stored only on the VPS.")
