import asyncio
import json
import platform
import shutil
import sys

import httpx

from master_agent.clients import IntegrationError, N8nClient, OllamaClient
from master_agent.config import Settings


async def main() -> int:
    settings = Settings()
    result = {
        "python": platform.python_version(),
        "docker_installed": bool(shutil.which("docker")),
        "configured_model": settings.ollama_model,
        "master_key_configured": len(settings.master_api_key.get_secret_value()) >= 32,
    }
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        try:
            names = await OllamaClient(settings, client).models()
            result["ollama"] = {
                "reachable": True,
                "models": names,
                "model_installed": settings.ollama_model in names,
            }
        except IntegrationError as exc:
            result["ollama"] = {"reachable": False, "error": exc.code}
        try:
            await N8nClient(settings, client).list_workflows()
            result["n8n"] = {"api_authenticated": True}
        except IntegrationError as exc:
            result["n8n"] = {"api_authenticated": False, "error": exc.code}
    print(json.dumps(result, indent=2))
    return (
        0
        if (
            result["docker_installed"]
            and result["master_key_configured"]
            and result["ollama"].get("model_installed")
            and result["n8n"].get("api_authenticated")
        )
        else 1
    )


sys.exit(asyncio.run(main()))
