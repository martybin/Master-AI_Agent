"""Create private local configuration without overwriting existing settings."""

import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / ".env"
if target.exists():
    print(".env already exists; preserved existing settings.")
else:
    text = (root / ".env.example").read_text()
    text = text.replace("replace-with-a-long-random-secret", secrets.token_urlsafe(48))
    with target.open("x") as stream:
        stream.write(text)
    target.chmod(0o600)
    print("Created private .env with a random MASTER_API_KEY. No secrets printed.")
