"""Owner-granted local tools, bounded subprocesses and an extensible registration API."""

import asyncio
import importlib
import inspect
import json
import os
import platform
import shutil
import signal
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, StrictBool, ValidationError

from master_agent.compiler import compile_ir, digest
from master_agent.config import Settings
from master_agent.ir import ClosedModel, WorkflowIR
from master_agent.local.access import AccessContext, AccessRequest, AccessRequired, request_access
from master_agent.local.native import NativeCatalog, NativeSpec
from master_agent.local.store import LocalStore
from master_agent.repository import StateConflict
from master_agent.service import BuildService


class GrantInput(ClosedModel):
    mode: Literal["off", "workspace", "system"] = "workspace"
    confirm: StrictBool = False
    hours: int = Field(default=4, ge=1, le=24)
    workspace: str | None = None


class PathArgs(ClosedModel):
    path: str = Field(min_length=1, max_length=2000)


class WriteArgs(PathArgs):
    content: str = Field(max_length=100000)


class CommandArgs(ClosedModel):
    argv: list[str] = Field(min_length=1, max_length=80)
    cwd: str = "."
    timeout: int = Field(default=30, ge=1, le=120)


class HttpArgs(ClosedModel):
    connection: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    path: str = "/"
    body: dict | None = None


class SearchArgs(ClosedModel):
    query: str = ""


class DescribeArgs(ClosedModel):
    node_type: str


class TelegramBuildArgs(ClosedModel):
    connection: str
    name: str = Field(min_length=1, max_length=100)
    instruction: str = Field(min_length=1, max_length=6000)


class EmptyArgs(ClosedModel):
    pass


class BuildArgs(ClosedModel):
    build_id: UUID


class RemoteWorkflowArgs(ClosedModel):
    workflow_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")


class Tools:
    def __init__(
        self, store: LocalStore, settings: Settings, connectors, catalog: NativeCatalog, n8n
    ):
        self.store, self.settings, self.connectors, self.catalog, self.n8n = (
            store,
            settings,
            connectors,
            catalog,
            n8n,
        )
        self.entries = {}
        self.build_service = BuildService(store.repo, n8n)
        for name, description, schema, scope, handler in [
            (
                "system.inspect",
                "Inspect local OS, workspace, Docker and configured services",
                EmptyArgs,
                "none",
                self.inspect,
            ),
            (
                "connections.list",
                "List configured integrations and their login status; no secrets",
                EmptyArgs,
                "none",
                lambda _: self.connectors.list(),
            ),
            (
                "catalog.search",
                "Discover registered n8n node types; search before native workflow construction",
                SearchArgs,
                "none",
                lambda a: self.catalog.search(a.query),
            ),
            (
                "catalog.describe",
                "Read parameter schema and supported versions for a node type",
                DescribeArgs,
                "none",
                lambda a: self.catalog.describe(a.node_type),
            ),
            (
                "catalog.import_file",
                "Import n8n export:nodes metadata JSON from a local workspace file",
                PathArgs,
                "workspace",
                self.import_catalog,
            ),
            (
                "files.list",
                "List a folder in the permitted scope",
                PathArgs,
                "workspace",
                self.list_files,
            ),
            (
                "files.read",
                "Read a UTF-8 file in the permitted scope",
                PathArgs,
                "workspace",
                self.read_file,
            ),
            (
                "files.write",
                "Write a UTF-8 file in the permitted scope",
                WriteArgs,
                "workspace",
                self.write_file,
            ),
            (
                "terminal.exec",
                "Execute argv as the OS user; requires system access grant; no automatic "
                "privilege escalation",
                CommandArgs,
                "system",
                self.command,
            ),
            (
                "http.request",
                "Call an owner-configured HTTP API connection; authentication injected privately",
                HttpArgs,
                "workspace",
                self.http,
            ),
            (
                "n8n.workflows",
                "Read workflows from configured n8n; does not execute or activate",
                EmptyArgs,
                "none",
                lambda _: self.n8n.list_workflows(),
            ),
            (
                "workflow.create_native",
                "Create a validated inactive local draft using catalog node types and "
                "existing credential IDs",
                NativeSpec,
                "none",
                self.native,
            ),
            (
                "workflow.telegram_assistant",
                "Build an INCOMING TEXT REPLY Telegram bot/personal draft; NOT scheduled "
                "outbound/birthday/contact automation. Binding requires owner activation.",
                TelegramBuildArgs,
                "none",
                self.telegram_workflow,
            ),
        ] + [
            (
                "n8n.inspect_workflow",
                "Read a remote workflow for the current task",
                RemoteWorkflowArgs,
                "none",
                lambda a: self.n8n.get_workflow(a.workflow_id),
            ),
            (
                "n8n.executions",
                "Read recent execution results; never executes a workflow",
                RemoteWorkflowArgs,
                "none",
                lambda a: self.n8n.executions(a.workflow_id),
            ),
            (
                "n8n.deploy_build",
                "Deploy an existing draft to n8n INACTIVE when requested",
                BuildArgs,
                "none",
                self.deploy,
            ),
        ]:
            self.register(
                name,
                description,
                schema,
                scope,
                handler,
                contextual=name.startswith("files.")
                or name in {"terminal.exec", "catalog.import_file", "system.inspect"},
            )
        self.register(
            "access.request",
            "Request owner permission for files/commands; NEVER "
            "grants access. Pause until owner approves in UI.",
            AccessRequest,
            "none",
            lambda a, ctx: request_access(self, ctx.chat_id, a),
            contextual=True,
        )
        from master_agent.local.coding import Coding

        self.coding = Coding(self)
        self.coding.register()

    def register(self, name, description, schema, scope, handler, *, contextual=False):
        if name in self.entries or scope not in {"none", "workspace", "system"}:
            raise ValueError("Invalid or duplicate tool registration")
        self.entries[name] = {
            "name": name,
            "description": description,
            "schema": schema,
            "scope": scope,
            "handler": handler,
            "contextual": contextual,
        }

    def load_plugins(self, modules):
        for module in modules:
            importlib.import_module(module).register(self)
            self.store.audit("plugin.load", "complete", module=module)

    def manifest(self, target=None):
        return [
            {
                "name": e["name"],
                "description": e["description"],
                "scope": e["scope"],
                "parameters": e["schema"].model_json_schema(),
            }
            for e in self.entries.values()
            if target not in {"python", "code"} or e["name"] != "files.write"
            if target not in {"python", "code"}
            or not e["name"].startswith(("catalog.", "workflow.", "n8n."))
        ]

    def grant(self, chat_id: str, data: GrantInput):
        if data.mode == "system" and data.confirm is not True:
            raise StateConflict("Explicit owner confirmation is required for system access")
        workspace = Path(data.workspace or self.settings.local_workspace).expanduser().resolve()
        if data.mode != "off" and not workspace.is_dir():
            raise StateConflict("Choose an existing local workspace folder")
        record = {
            "chat_id": chat_id,
            "mode": data.mode,
            "expires_at": (datetime.now(UTC) + timedelta(hours=data.hours)).isoformat(),
            "workspace": str(workspace),
        }
        self.store.put("grants", chat_id, record)
        self.store.audit("grant.set", "complete", chat_id=chat_id, mode=data.mode)
        return record

    def permission(self, chat_id):
        try:
            grant = self.store.get("grants", chat_id)
        except LookupError:
            return {"mode": "off"}
        if datetime.fromisoformat(grant["expires_at"]) <= datetime.now(UTC):
            return {"mode": "off"}
        return grant

    def authorize(self, chat_id, scope):
        mode = self.permission(chat_id)["mode"]
        if (
            scope == "system"
            and mode != "system"
            or scope == "workspace"
            and mode not in {"workspace", "system"}
        ):
            raise AccessRequired(scope)
        return AccessContext(
            mode, self.permission(chat_id).get("workspace", self.settings.local_workspace), chat_id
        )

    def resolve(self, path, mode):
        root = Path(getattr(mode, "workspace", self.settings.local_workspace)).resolve()
        target = (root / Path(path)).resolve()
        if mode != "system" and not target.is_relative_to(root):
            raise StateConflict("Path is outside the granted workspace")
        protected = {
            Path(".env").resolve(),
            self.settings.database_path.resolve(),
            self.store.key_path.resolve(),
        }
        if (
            target in protected
            or target.name == ".env"
            or target.name.startswith(".env.")
            and target.name != ".env.example"
            or target.suffix in {".key", ".session"}
            or target.suffix in {".sqlite3", ".sqlite"}
            or ".git" in target.parts
        ):
            raise StateConflict("Use the connection/settings interface for private state")
        return target

    async def call(self, chat_id, name, arguments):
        entry = self.entries.get(name)
        if not entry:
            raise StateConflict("Unknown tool; inspect the tool registry")
        mode = self.authorize(chat_id, entry["scope"])
        try:
            args = entry["schema"].model_validate(arguments)
        except ValidationError as exc:
            errors = [
                {"field": ".".join(map(str, e["loc"])), "reason": e["type"]} for e in exc.errors()
            ]
            raise StateConflict(
                "Tool argument fields do not match the schema: " + json.dumps(errors)
            ) from None
        self.store.audit(
            "tool.start", "running", chat_id=chat_id, tool=name, argument_keys=list(arguments)
        )
        started = time.monotonic()
        try:
            params = (args, mode) if entry["contextual"] else (args,)
            if inspect.iscoroutinefunction(entry["handler"]):
                result = await entry["handler"](*params)
            else:
                result = await asyncio.to_thread(entry["handler"], *params)
            if hasattr(result, "__await__"):
                result = await result
            result = self.store.redact(result)
            self.store.audit(
                "tool.finish",
                "complete",
                chat_id=chat_id,
                tool=name,
                seconds=round(time.monotonic() - started, 3),
            )
            return result
        except asyncio.CancelledError:
            self.store.audit("tool.finish", "cancelled", chat_id=chat_id, tool=name)
            raise
        except Exception as exc:
            self.store.audit(
                "tool.finish",
                "failed",
                chat_id=chat_id,
                tool=name,
                error_type=type(exc).__name__,
                detail=str(exc)[:500] if isinstance(exc, StateConflict) else "Tool failed",
            )
            raise

    def inspect(self, _, ctx=None):
        return {
            "os": platform.system(),
            "python": platform.python_version(),
            "workspace": str(getattr(ctx, "workspace", self.settings.local_workspace).resolve()),
            "docker": bool(shutil.which("docker")),
            "n8n_configured": bool(self.settings.n8n_api_key.get_secret_value()),
            "connectors": self.connectors.list(),
        }

    def list_files(self, args, mode):
        path = self.resolve(args.path, mode)
        return {
            "path": str(path),
            "entries": [
                {"name": p.name, "directory": p.is_dir()} for p in sorted(path.iterdir())[:200]
            ],
        }

    def read_file(self, args, mode):
        path = self.resolve(args.path, mode)
        if path.stat().st_size > 100000:
            raise StateConflict("File is too large for one tool response")
        import hashlib

        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        revision = "r" + uuid4().hex[:8]
        self.store.put(
            "code_revisions",
            revision,
            {
                "id": revision,
                "chat_id": getattr(mode, "chat_id", None),
                "path": str(path),
                "sha256": digest,
            },
        )
        return {
            "path": str(path),
            "revision": revision,
            "sha256": digest,
            "content": raw.decode(),
        }

    def write_file(self, args, mode):
        path = self.resolve(args.path, mode)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(args.content)
            temporary.replace(path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        return {"path": str(path), "written": len(args.content)}

    def import_catalog(self, args, mode):
        path = self.resolve(args.path, mode)
        if path.stat().st_size > 30000000:
            raise StateConflict("Node metadata file exceeds 30 MB")
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            data = data.get("nodes")
        if not isinstance(data, list):
            raise StateConflict("Expected an n8n node description array")
        return self.catalog.import_n8n(data)

    async def command(self, args, mode):
        cwd = self.resolve(args.cwd, mode)
        env = {
            key: value
            for key, value in os.environ.items()
            if not any(
                part in key.upper()
                for part in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "API_HASH")
            )
        }
        process = await asyncio.create_subprocess_exec(
            *args.argv,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name == "posix",
        )

        buffers = [[bytearray(), 0], [bytearray(), 0]]

        async def drain(stream, buffer):
            while chunk := await stream.read(65536):
                buffer[1] += len(chunk)
                if len(buffer[0]) < 32000:
                    buffer[0].extend(chunk[: 32000 - len(buffer[0])])

        readers = [
            asyncio.create_task(drain(process.stdout, buffers[0])),
            asyncio.create_task(drain(process.stderr, buffers[1])),
        ]
        waiter = asyncio.create_task(process.wait())
        completion = asyncio.gather(waiter, *readers)
        timed_out = False
        try:
            await asyncio.wait_for(asyncio.shield(completion), timeout=args.timeout)
        except (TimeoutError, asyncio.CancelledError) as exc:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            await process.wait()
            for task in readers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            await asyncio.gather(completion, return_exceptions=True)
            if isinstance(exc, asyncio.CancelledError):
                raise
            timed_out = True
        result = {
            "exit_code": process.returncode,
            "stdout": buffers[0][0].decode(errors="replace"),
            "stderr": buffers[1][0].decode(errors="replace"),
            "truncated": any(buffer[1] > 32000 for buffer in buffers),
            "timed_out": timed_out,
        }
        for secret in (
            self.settings.master_api_key.get_secret_value(),
            self.settings.n8n_api_key.get_secret_value(),
        ):
            if secret:
                result = {
                    k: v.replace(secret, "[REDACTED]") if isinstance(v, str) else v
                    for k, v in result.items()
                }
        return result

    async def http(self, args):
        return await self.connectors.http_request(
            args.connection, args.method, args.path, args.body
        )

    async def deploy(self, args):
        record = await self.build_service.deploy(str(args.build_id))
        return {
            "build_id": record["id"],
            "status": record["status"],
            "n8n_workflow_id": record["n8n_workflow_id"],
        }

    def native(self, args):
        ir, workflow, warnings = self.catalog.compile(args, self.settings)
        record = self.store.repo.create(
            prompt=None,
            ir=ir,
            workflow=workflow,
            sha256=digest(workflow),
            warnings=warnings,
            attempts=0,
        )
        return {"build_id": record["id"], "name": ir["name"], "status": "draft"}

    def telegram_workflow(self, args):
        connection = self.store.get("connections", args.connection)
        if connection["kind"] not in {"telegram_bot", "telegram_user"}:
            raise StateConflict("Choose a Telegram bot or personal-account connection")
        path = (
            "master-telegram-"
            + args.connection.replace("_", "-")
            + "-"
            + digest(args.model_dump())[:12]
        )
        ir = WorkflowIR.model_validate(
            {
                "name": args.name,
                "description": "Telegram automation via Master local connector: " + args.connection,
                "nodes": [
                    {"id": "start", "kind": "webhook", "path": path, "method": "POST"},
                    {
                        "id": "answer",
                        "kind": "ollama_chat",
                        "system_prompt": args.instruction,
                        "input_path": "body.message",
                    },
                    {"id": "reply", "kind": "respond", "status_code": 200},
                ],
                "edges": [
                    {"source": "start", "target": "answer"},
                    {"source": "answer", "target": "reply"},
                ],
            }
        )
        workflow = compile_ir(ir, self.settings)
        record = self.store.repo.create(
            prompt=None,
            ir=ir.model_dump(),
            workflow=workflow,
            sha256=digest(workflow),
            warnings=[
                {
                    "code": "telegram_bridge",
                    "message": (
                        "After owner activation, bind this workflow to the Telegram "
                        "connection in Local Agent."
                    ),
                }
            ],
            attempts=0,
        )
        return {
            "build_id": record["id"],
            "name": args.name,
            "connection": args.connection,
            "status": "draft",
            "next": "owner_deploy_activate_then_bind",
        }
