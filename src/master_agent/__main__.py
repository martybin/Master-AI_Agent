"""Native host runtime entrypoint. No frontend/node installation is needed."""

import argparse
import asyncio
import json
import shutil
from pathlib import Path

from master_agent import __version__


def main():
    parser = argparse.ArgumentParser(description="BisCode — local coding and n8n agent builder")
    parser.add_argument("--version", action="version", version=f"BisCode {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Run the local UI, tools and integration workers")
    serve.add_argument("--workspace", type=Path)
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("doctor", help="Check model, n8n authentication and Docker")
    docs = sub.add_parser("docs", help="Manage local official documentation retrieval")
    docs.add_argument("action", choices=["sync", "status", "search"])
    docs.add_argument("--family", choices=["langchain", "langgraph", "llamaindex", "n8n"])
    docs.add_argument("--target", choices=["python", "n8n"], default="python")
    docs.add_argument("--query", default="agent tools ollama")
    args = parser.parse_args()
    from master_agent.config import Settings

    settings = Settings()
    if args.command == "docs":
        from master_agent.knowledge.store import Knowledge

        knowledge = Knowledge(
            settings.knowledge_path or settings.database_path.parent / "knowledge.sqlite3"
        )
        if args.action == "sync":
            import httpx

            async def sync():
                async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
                    return await knowledge.sync(client, args.family)

            result = asyncio.run(sync())
        else:
            result = (
                knowledge.status()
                if args.action == "status"
                else knowledge.search(args.query, args.target)
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "serve":
        import uvicorn

        from master_agent.api import create_app

        if args.workspace:
            settings.local_workspace = args.workspace.resolve()
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, workers=1)
    else:
        asyncio.run(doctor(settings))


async def doctor(settings):
    import httpx

    from master_agent.clients import IntegrationError, N8nClient, OllamaClient

    result = {
        "docker_installed": bool(shutil.which("docker")),
        "workspace": str(settings.local_workspace.resolve()),
        "model": settings.ollama_model,
        "owner_key_configured": len(settings.master_api_key.get_secret_value()) >= 32,
    }
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        for name, action in [
            ("ollama", OllamaClient(settings, client).models),
            ("n8n", N8nClient(settings, client).list_workflows),
        ]:
            try:
                data = await action()
                result[name] = {"connected": True}
                if name == "ollama":
                    result[name]["model_installed"] = settings.ollama_model in data
            except IntegrationError as exc:
                result[name] = {"connected": False, "error": exc.code}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
