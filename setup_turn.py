import os
import secrets
from pathlib import Path

TURN_CONF = Path("/etc/turnserver.conf")
DEFAULTS = Path("/etc/default/coturn")
ENV_FILE = Path("/etc/svoi-chat.env")

HOST = "epl-gruz.duckdns.org"
PUBLIC_IP = "89.19.212.248"
MIN_PORT = 49160
MAX_PORT = 49200

existing = {}
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            existing[key] = value

secret = existing.get("TURN_SHARED_SECRET") or secrets.token_urlsafe(48)

turn_conf = f"""listening-port=3478
fingerprint
use-auth-secret
static-auth-secret={secret}
realm={HOST}
server-name={HOST}
external-ip={PUBLIC_IP}
min-port={MIN_PORT}
max-port={MAX_PORT}
stale-nonce
no-cli
no-multicast-peers
"""

TURN_CONF.write_text(turn_conf)
os.chmod(TURN_CONF, 0o600)

DEFAULTS.write_text('TURNSERVER_ENABLED=1\n')

existing["TURN_SHARED_SECRET"] = secret
existing["TURN_HOST"] = HOST
ENV_FILE.write_text(
    "".join(f"{key}={value}\n" for key, value in existing.items())
)
os.chmod(ENV_FILE, 0o600)

print("TURN configured.")
print("Host:", HOST)
print("UDP/TCP port: 3478")
print(f"Relay ports: {MIN_PORT}-{MAX_PORT}")
print("Secret stored only in /etc/svoi-chat.env and /etc/turnserver.conf")
