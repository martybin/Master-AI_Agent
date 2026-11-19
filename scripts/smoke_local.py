"""Opt-in real Ollama smoke in a disposable workspace; no live Telegram/n8n writes."""

import asyncio
import json
import secrets
import tempfile
from pathlib import Path
from uuid import uuid4

import httpx

from master_agent.api import create_app
from master_agent.config import Settings


async def turn(client, chat_id, text):
    response = await client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": text, "request_id": str(uuid4())},
    )
    response.raise_for_status()
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    complete = next((e for e in events if e["type"] == "complete"), None)
    if not complete:
        stages = [e for e in events if e["type"] in {"stage", "tool", "failed"}]
        raise RuntimeError("Local model turn did not complete: " + str(stages))
    return complete


async def smoke(root):
    cfg = Settings(
        database_path=root / "smoke.sqlite3",
        local_workspace=root,
        local_vault_key=root / "vault.key",
        master_api_key=secrets.token_urlsafe(40),
        n8n_api_key="",
        local_plugin_modules=[],
    )
    app = create_app(cfg)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://local-smoke",
            headers={"Authorization": "Bearer " + cfg.master_api_key.get_secret_value()},
            timeout=600,
        ) as client:
            print("Checking real model + tool execution in a temporary workspace…", flush=True)
            chat = (await client.post("/api/v1/chats")).json()["id"]
            grant = await client.put("/api/v1/local/grants/" + chat, json={"mode": "workspace"})
            grant.raise_for_status()
            await turn(
                client,
                chat,
                "ابتدا با system.inspect محیط را بررسی کن. سپس با files.write فایل "
                "proof.txt را با متن دقیق LOCAL_AGENT_OK بساز. با files.read محتوای آن "
                "را بررسی کن و سپس نتیجه را بگو. هیچ Workflow نساز یا ارسال نکن.",
            )
            assert (root / "proof.txt").read_text() == "LOCAL_AGENT_OK"
            completed = {
                e.get("tool")
                for e in app.state.local_store.audits()
                if e["action"] == "tool.finish" and e["status"] == "complete"
            }
            assert {"system.inspect", "files.write", "files.read"} <= completed
            print("PASS: inspected, wrote and read the actual file", flush=True)

            for kind in ["telegram_bot", "telegram_user"]:
                alias = "smoke_" + kind
                response = await client.post(
                    "/api/v1/local/connections",
                    json={
                        "id": alias,
                        "label": "TEST ONLY, no real account",
                        "kind": kind,
                        "token": "TEST_ONLY_FAKE_TOKEN",
                        "api_id": 12345,
                        "api_hash": "TEST_ONLY_FAKE_HASH",
                    },
                )
                response.raise_for_status()
                chat = (await client.post("/api/v1/chats")).json()["id"]
                result = await turn(
                    client,
                    chat,
                    f"برای اتصال {alias} یک دستیار تلگرام بساز که پاسخ کوتاه فارسی "
                    "بدهد. فقط پیش‌نویس؛ هیچ چیز را ارسال یا فعال نکن.",
                )
                build = result.get("build")
                assert build and build["status"] == "draft"
                assert build["workflow"]["active"] is False
                assert alias in build["ir"].get("description", "")
                print(f"PASS: real model selected {kind} draft; no account connected", flush=True)


if __name__ == "__main__":
    Path("work").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="local-smoke-", dir="work") as temporary:
        asyncio.run(smoke(Path(temporary).resolve()))
