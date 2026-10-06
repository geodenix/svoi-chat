import base64
import os
import shutil
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

PRIVATE_PATH = Path("/etc/svoi-vapid-private.pem")
ENV_PATH = Path("/etc/svoi-chat.env")
SUBJECT = "mailto:admin@epl-gruz.duckdns.org"

if PRIVATE_PATH.exists():
    private_key = serialization.load_pem_private_key(
        PRIVATE_PATH.read_bytes(),
        password=None,
    )
else:
    private_key = ec.generate_private_key(ec.SECP256R1())
    PRIVATE_PATH.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    shutil.chown(PRIVATE_PATH, user="root", group="daemon")
    os.chmod(PRIVATE_PATH, 0o640)

# Normalize permissions even when the key already existed.
shutil.chown(PRIVATE_PATH, user="root", group="daemon")
os.chmod(PRIVATE_PATH, 0o640)

numbers = private_key.public_key().public_numbers()
public_bytes = (
    b"\x04"
    + numbers.x.to_bytes(32, "big")
    + numbers.y.to_bytes(32, "big")
)
public_key = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode()

existing = {}
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            existing[key] = value

existing["VAPID_PRIVATE_KEY_FILE"] = str(PRIVATE_PATH)
existing["VAPID_PUBLIC_KEY"] = public_key
existing["VAPID_SUBJECT"] = SUBJECT

ENV_PATH.write_text(
    "".join(f"{key}={value}\n" for key, value in existing.items())
)
shutil.chown(ENV_PATH, user="root", group="daemon")
os.chmod(ENV_PATH, 0o640)

print("Push keys ready.")
print("Public VAPID key:", public_key)
