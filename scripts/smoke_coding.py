"""Opt-in: real Qwen creates, edits and checks a Python agent in a temporary folder."""

import ast
import asyncio
import json
import secrets
import tempfile
from pathlib import Path
from uuid import uuid4

import httpx

from master_agent.api import create_app
from master_agent.config import Settings


def complete(response):
    response.raise_for_status()
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    result = next((e for e in events if e["type"] == "complete"), None)
    if not result:
        raise RuntimeError(str([e for e in events if e["type"] in {"stage", "tool", "failed"}]))
    return result


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
        original_call = app.state.tools.call

        async def trace(chat_id, name, arguments):
            print("TOOL:", name, flush=True)
            try:
                return await original_call(chat_id, name, arguments)
            except Exception as exc:
                print("TOOL FAILED:", type(exc).__name__, str(exc)[:300], flush=True)
                raise

        app.state.tools.call = trace
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://test",
            headers={"Authorization": "Bearer " + cfg.master_api_key.get_secret_value()},
            timeout=600,
        ) as client:
            cid = (await client.post("/api/v1/chats", json={"target": "python"})).json()["id"]
            print("Checking owner permission request…", flush=True)
            first = complete(
                await client.post(
                    f"/api/v1/chats/{cid}/messages",
                    json={
                        "content": "یک ایجنت Python مستقل در پوشه demo-agent بساز که "
                        "با Ollama پاسخ "
                        "کوتاه فارسی بدهد. فایل‌ها را واقعاً بساز و تست کن؛ سرویس را روشن نکن.",
                        "request_id": str(uuid4()),
                    },
                )
            )
            access = first["message"]["access_request"]
            assert access and not app.state.tools.coding.projects(cid)
            approved = await client.post(
                f"/api/v1/local/access-requests/{access['id']}/approve",
                json={"mode": "system", "confirm": True, "workspace": str(root)},
            )
            approved.raise_for_status()
            print("Running real model: create and check project…", flush=True)
            built = complete(
                await client.post(
                    f"/api/v1/chats/{cid}/resume", json={"access_request_id": access["id"]}
                )
            )
            project = app.state.local_store.get("projects", built["message"]["project_id"])
            assert await asyncio.to_thread(Path(project["path"], "agent.py").is_file)
            assert app.state.local_store.list("code_runs")[0]["status"] == "passed"
            print("PASS: real project and subprocess tests", flush=True)
            print("Running real model: modify existing source…", flush=True)
            result = complete(
                await client.post(
                    f"/api/v1/chats/{cid}/messages",
                    json={
                        "content": "در همین پروژه، agent.py را بخوان و با ابزار ویرایش کد، یک تابع "
                        "بدون ورودی به نام identify_project اضافه کن که رشته دقیق BisCode "
                        "را برگرداند. رفتار قبلی را حفظ کن و دوباره project.check را اجرا کن.",
                        "request_id": str(uuid4()),
                    },
                )
            )
            assert result["message"]["project_id"] == project["id"]
            source = await asyncio.to_thread(Path(project["path"], "agent.py").read_text)
            tree = ast.parse(source)
            fn = next(
                n
                for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "identify_project"
            )
            assert any(
                isinstance(n, ast.Return)
                and isinstance(n.value, ast.Constant)
                and n.value.value == "BisCode"
                for n in ast.walk(fn)
            )
            assert app.state.local_store.list("code_runs")[0]["status"] == "passed"
            assert len(app.state.local_store.list("code_runs")) >= 2
            print(
                "PASS: source edited, reviewable diff saved, tests rerun; no service started",
                flush=True,
            )


if __name__ == "__main__":
    Path("work").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="biscode-smoke-", dir="work") as directory:
        asyncio.run(smoke(Path(directory).resolve()))
