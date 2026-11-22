"""Controller-owned file discovery; permissions apply before every actual read."""

import asyncio
import json
import os
import re
from pathlib import Path

from master_agent.repository import StateConflict

SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}


def named_files(request):
    return list(
        dict.fromkeys(
            re.findall(
                r"(?<![\w./-])/?(?:[\w.-]+/)*[\w.-]+\.(?:py|js|ts|tsx|jsx|md|json|yaml|yml|toml)\b",
                request,
            )
        )
    )[:4]


def discover(tools, chat_id, requested):
    ctx = tools.authorize(chat_id, "workspace")
    root = Path(ctx.workspace)
    direct = tools.resolve(requested, ctx)
    if direct.is_file():
        return str(direct)
    matches, scanned = [], 0
    for parent, directories, files in os.walk(root, followlinks=False):
        directories[:] = [
            d
            for d in sorted(directories)
            if d not in SKIP and not d.startswith(".") and not Path(parent, d).is_symlink()
        ]
        scanned += 1
        if scanned > 500:
            raise StateConflict(
                "Folder search limit reached; choose a more specific project folder"
            )
        if Path(requested).name in files:
            candidate = Path(parent, Path(requested).name)
            if not candidate.is_symlink():
                matches.append(str(tools.resolve(str(candidate), ctx)))
        if len(matches) > 1:
            raise StateConflict(
                "Multiple matching source files; choose the intended path: " + json.dumps(matches)
            )
    return matches[0] if matches else None


async def inspect_task(tools, chat_id, task, emit):
    observations = []

    async def call(name, arguments):
        emit({"type": "stage", "stage": "tool_running", "tool": name, "path": arguments["path"]})
        result = await tools.call(chat_id, name, arguments)
        if isinstance(result, dict) and isinstance(result.get("content"), str):
            result = {
                **result,
                "content": result["content"][:9000],
                "truncated": len(result["content"]) > 9000,
            }
        observations.append({"tool": name, "arguments": arguments, "result": result})
        emit(
            {
                "type": "tool",
                "name": name,
                "status": "complete",
                "path": str(result.get("path", arguments["path"])),
            }
        )

    try:
        await call("project.inspect", {"path": "."})
    except (ValueError, OSError) as exc:
        return observations, "Cannot inspect the granted folder: " + str(exc)
    for requested in named_files(task):
        try:
            path = await asyncio.to_thread(discover, tools, chat_id, requested)
        except StateConflict as exc:
            return observations, str(exc)
        if path:
            try:
                await call("files.read", {"path": path})
            except (ValueError, OSError) as exc:
                return observations, "Cannot read the requested source file: " + str(exc)
        else:
            if re.search(r"تکمیل|ویرایش|اصلاح|\b(fix|edit|complete|finish)\b", task.lower()):
                return (
                    observations,
                    "The requested existing file was not found in the granted folder: " + requested,
                )
            observations.append(
                {
                    "tool": "files.read",
                    "path": requested,
                    "missing_file": True,
                    "error": "File does not exist in the selected workspace. "
                    "Create it only if the owner requested a new file.",
                }
            )
    return observations, None
