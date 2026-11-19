"""Opt-in actual local inference. Never contacts n8n or activates a workflow."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from master_agent.clients import OllamaClient
from master_agent.compiler import digest
from master_agent.config import Settings
from master_agent.planner import Planner


async def main() -> None:
    settings = Settings()
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        result = await Planner(settings, OllamaClient(settings, client)).plan(
            "یک دستیار محلی بساز: Webhook با متد POST و مسیر persian-assistant، "
            "متن را از body.message بگیرد، با Ollama پاسخ فارسی بدهد و نتیجه را "
            "با پاسخ JSON برگرداند. فقط همین سه گره و بدون ابزار و حافظه."
        )
    artifact = {
        "checked_at": datetime.now(UTC).isoformat(),
        "model": settings.ollama_model,
        "attempts": result["attempts"],
        "ir": result["ir"].model_dump(),
        "workflow": result["workflow"],
        "workflow_sha256": digest(result["workflow"]),
        "warnings": result["warnings"],
        "n8n_contacted": False,
        "activated": False,
    }
    folder = Path("outputs")
    await asyncio.to_thread(folder.mkdir, exist_ok=True)
    path = folder / "live-ollama-smoke.json"
    await asyncio.to_thread(path.write_text, json.dumps(artifact, ensure_ascii=False, indent=2))
    print(
        f"PASS: actual {settings.ollama_model} -> valid IR -> inactive n8n JSON; "
        f"attempts={result['attempts']}; saved {path}"
    )


asyncio.run(main())
