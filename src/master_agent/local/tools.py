"""Owner-granted local tools, bounded subprocesses and an extensible registration API."""

import asyncio
import importlib
import inspect
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
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


class FolderInput(ClosedModel):
    parent: str = Field(min_length=1, max_length=2000)
    name: str = Field(min_length=1, max_length=150)


class PathArgs(ClosedModel):
    path: str = Field(min_length=1, max_length=2000)


class WriteArgs(PathArgs):
    content: str = Field(max_length=100000)


class CommandArgs(ClosedModel):
    argv: list[str] = Field(
        min_length=1,
        max_length=80,
        description="Executable and arguments, directly without a shell. Do not use cd, "
        "&&, pipes or redirection here; set cwd instead. For Python tests use "
        "['python','-m','unittest','discover','-s','tests','-v'].",
    )
    cwd: str = Field(
        default=".",
        description="Working directory relative to the granted workspace. For source in "
        "my-project and tests in my-project/tests, set cwd='my-project' and use -s tests. "
        "Do not repeat the project path inside -s when cwd already points there.",
    )
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


class DocumentationQuery(ClosedModel):
    query: str = Field(min_length=1, max_length=2000)
    target: Literal["python", "n8n"]


class EmptyArgs(ClosedModel):
    pass


class BuildArgs(ClosedModel):
    build_id: UUID


class RemoteWorkflowArgs(ClosedModel):
    workflow_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")


class Tools:
    def __init__(
        self,
        store: LocalStore,
        settings: Settings,
        connectors,
        catalog: NativeCatalog,
        n8n,
        knowledge=None,
    ):
        self.store, self.settings, self.connectors, self.catalog, self.n8n = (
            store,
            settings,
            connectors,
            catalog,
            n8n,
        )
        self.knowledge = knowledge
        self.entries = {}
        if knowledge:
            self.register(
                "docs.search",
                "Search locally indexed official documentation. Reference data only; "
                "does not grant permissions or add n8n node types.",
                DocumentationQuery,
                "none",
                lambda a: {"results": knowledge.search(a.query, a.target)},
            )
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
        entries = [
            {
                "name": e["name"],
                "description": e["description"],
                "scope": e["scope"],
                "parameters": e["schema"].model_json_schema(),
            }
            for e in self.entries.values()
            if target != "code" or e["name"] not in {"project.create_python_agent", "docs.search"}
            if target not in {"python", "code"} or e["name"] != "files.write"
            if target not in {"python", "code"}
            or not e["name"].startswith(("catalog.", "workflow.", "n8n."))
        ]
        if target in {"python", "n8n"}:
            for entry in entries:
                if entry["name"] == "docs.search":
                    entry["parameters"]["properties"]["target"] = {
                        "type": "string",
                        "const": target,
                    }
        if target in {"python", "code"}:
            for entry in entries:
                if entry["name"] in {"code.write_file", "code.replace"}:
                    # The controller tracks actual reads/writes and guards their hashes.
                    # Keep optional manual version handles out of the small model's task.
                    for key in ("revision", "expected_sha256"):
                        entry["parameters"]["properties"].pop(key, None)
                    entry["description"] = (
                        "Create/write source file; existing files need a prior files.read "
                        "or successful write in THIS chat. "
                        if entry["name"] == "code.write_file"
                        else "Replace one unique exact old snippet; first read the source. "
                    ) + (
                        "The controller automatically uses the last known file version and "
                        "rejects outside changes. Use ONLY the published argument fields."
                    )
        return entries

    def grant(self, chat_id: str, data: GrantInput):
        if data.mode == "system" and data.confirm is not True:
            raise StateConflict("Explicit owner confirmation is required for system access")
        workspace = Path(data.workspace or self.settings.local_workspace).expanduser().resolve()
        if data.mode != "off":
            if workspace.is_file():
                workspace = workspace.parent
            if not workspace.is_dir():
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

    def browse(self, raw: str | None, hidden: bool = False):
        """List one real folder so the owner can choose where coding runs."""
        start = Path(raw).expanduser() if raw else Path.home()
        try:
            resolved = start.resolve(strict=False)
        except (OSError, ValueError) as exc:
            raise StateConflict("Choose an existing folder") from exc
        if resolved.is_file():
            resolved = resolved.parent
        if not resolved.is_dir():
            raise StateConflict("Choose an existing folder")
        try:
            children = sorted(
                resolved.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())
            )
        except OSError as exc:
            raise StateConflict("Cannot open this folder") from exc
        entries = []
        truncated = False
        for child in children:
            if len(entries) >= 400:
                truncated = True
                break
            try:
                directory = child.is_dir()
                if not directory or (child.name.startswith(".") and not hidden):
                    continue
                entries.append({"name": child.name, "directory": True, "path": str(child)})
            except OSError:
                continue
        return {
            "path": str(resolved),
            "parent": None if resolved.parent == resolved else str(resolved.parent),
            "home": str(Path.home()),
            "entries": entries,
            "truncated": truncated,
        }

    def create_folder(self, data: FolderInput):
        """Owner-only mkdir; creates one child in an explicitly selected existing folder."""
        name = data.name.strip()
        if not name or name in {".", ".."} or any(c in name for c in "/\\\x00"):
            raise StateConflict("Choose a single folder name")
        try:
            parent = Path(data.parent).expanduser().resolve()
        except (OSError, ValueError) as exc:
            raise StateConflict("Choose an existing folder") from exc
        if not parent.is_dir():
            raise StateConflict("Choose an existing folder")
        path = parent / name
        try:
            path.mkdir()
        except FileExistsError as exc:
            raise StateConflict("This folder already exists") from exc
        except OSError as exc:
            raise StateConflict("Cannot create this folder") from exc
        self.store.audit("folder.create", "complete")
        return {"path": str(path)}

    _picker_lock = threading.Lock()

    def pick_folder(self, raw: str | None):
        if not self._picker_lock.acquire(blocking=False):
            return {"available": False}
        try:
            return self._pick_folder(raw)
        finally:
            self._picker_lock.release()

    def _pick_folder(self, raw: str | None):
        """Open a native folder chooser on the machine running BisCode."""
        if not shutil.which("zenity"):
            return {"available": False}
        start = Path(raw).expanduser() if raw else Path.home()
        try:
            if start.is_file():
                start = start.parent
            if not start.is_dir():
                start = Path.home()
            start = start.resolve()
        except OSError:
            start = Path.home()
        filename = str(start)
        if not filename.endswith(os.sep):
            filename += os.sep
        try:
            completed = subprocess.run(
                [
                    "zenity",
                    "--file-selection",
                    "--directory",
                    "--title=Choose a project folder",
                    "--filename=" + filename,
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {"available": False}
        chosen = completed.stdout.strip()
        if completed.returncode == 1:
            return {"available": True, "cancelled": True}
        if completed.returncode != 0 or not chosen:
            return {"available": False}
        path = Path(chosen).expanduser()
        try:
            if path.is_file():
                path = path.parent
            if not path.is_dir():
                raise StateConflict("Choose an existing folder")
            return {"available": True, "cancelled": False, "path": str(path.resolve())}
        except (OSError, ValueError) as exc:
            raise StateConflict("Choose an existing folder") from exc

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
            "python_executable": sys.executable,
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
        if args.argv[0] == "cd":
            raise StateConflict(
                "cd is a shell builtin, not an executable. Put the directory in cwd and "
                "the command in argv. Nothing was executed."
            )
        cwd = self.resolve(args.cwd, mode)
        env = {
            key: value
            for key, value in os.environ.items()
            if not any(
                part in key.upper()
                for part in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "API_HASH")
            )
        }
        argv = list(args.argv)
        # Desktop launches often omit the activated venv from PATH. Keep explicit
        # interpreters unchanged; supply our known Python only for a missing alias.
        if argv[0] in {"python", "python3"} and shutil.which(argv[0]) is None:
            argv[0] = sys.executable
        process = await asyncio.create_subprocess_exec(
            *argv,
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
            "executable": argv[0],
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
