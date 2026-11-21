"""Real filesystem coding, optimistic edits, project checks and encrypted change history."""

import asyncio
import difflib
import hashlib
import io
import json
import re
import sys
import threading
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import Field

from master_agent.ir import ClosedModel
from master_agent.local import python_template
from master_agent.repository import StateConflict, now


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class PythonAgentArgs(ClosedModel):
    name: str = Field(min_length=1, max_length=100)
    directory: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_/-]{0,140}$")
    instruction: str = Field(min_length=3, max_length=6000)


class InspectArgs(ClosedModel):
    path: str = "."


class SearchArgs(ClosedModel):
    query: str = Field(min_length=1, max_length=300)
    path: str = "."
    glob: str = "*"


class CodeWrite(ClosedModel):
    path: str = Field(min_length=1, max_length=2000)
    content: str = Field(max_length=100000)
    expected_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    revision: str | None = Field(default=None, pattern=r"^r[a-f0-9]{8}$")


class CodeReplace(ClosedModel):
    path: str
    old: str = Field(min_length=1, max_length=50000)
    new: str = Field(max_length=100000)
    expected_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    revision: str | None = Field(default=None, pattern=r"^r[a-f0-9]{8}$")


class ProjectCheck(ClosedModel):
    project_id: UUID
    timeout: int = Field(default=60, ge=1, le=120)


class EmptyArgs(ClosedModel):
    pass


class Coding:
    def __init__(self, tools):
        self.tools, self.store = tools, tools.store
        self.lock = threading.RLock()

    def register(self):
        for name, description, schema, scope, handler in [
            (
                "project.create_python_agent",
                "Create a REAL standalone Python/Ollama agent "
                "project with CLI, HTTP endpoint and tests. This is a starting point: customize "
                "code for the requested capabilities, then project.check before claiming done.",
                PythonAgentArgs,
                "workspace",
                self.create,
            ),
            (
                "project.inspect",
                "Inspect a granted local project folder and tracked Python "
                "projects; read files before editing.",
                InspectArgs,
                "workspace",
                self.inspect,
            ),
            (
                "files.search",
                "Literal text search in granted source files, with line numbers; "
                "skips private/large/generated files.",
                SearchArgs,
                "workspace",
                self.search,
            ),
            (
                "code.write_file",
                "Create a source file or replace it. Existing files REQUIRE "
                "revision (short handle) OR expected_sha256 from files.read; saves a diff.",
                CodeWrite,
                "workspace",
                self.write,
            ),
            (
                "code.replace",
                "Replace one unique exact snippet in an existing file. Requires "
                "revision OR current SHA256 from files.read; rejects ambiguous/conflicting edits.",
                CodeReplace,
                "workspace",
                self.replace,
            ),
            (
                "code.changes",
                "Review this chat's actual managed file changes and diffs.",
                EmptyArgs,
                "workspace",
                lambda a, ctx: self.changes(ctx.chat_id),
            ),
            (
                "project.check",
                "Actually run the generated Python project's unittest suite; "
                "reports real output and exit code. Requires owner system permission.",
                ProjectCheck,
                "system",
                self.check,
            ),
        ]:
            self.tools.register(name, description, schema, scope, handler, contextual=True)

    @staticmethod
    def private(path):
        return (
            path.name == ".env"
            or path.name.startswith(".env.")
            and path.name != ".env.example"
            or path.suffix in {".key", ".session", ".sqlite3"}
        )

    def path(self, value, ctx):
        path = self.tools.resolve(value, ctx)
        if self.private(path):
            raise StateConflict("Use the connections panel for credentials; choose a source file")
        return path

    def projects(self, chat_id=None):
        return [p for p in self.store.list("projects") if not chat_id or p["chat_id"] == chat_id]

    def inspect(self, args, ctx):
        root = self.path(args.path, ctx)
        if not root.is_dir():
            raise StateConflict("Project folder does not exist")
        return {
            "path": str(root),
            "entries": [
                {"name": p.name, "directory": p.is_dir()}
                for p in sorted(root.iterdir())
                if not self.private(p)
            ][:150],
            "projects": self.projects(ctx.chat_id),
        }

    def search(self, args, ctx):
        root = self.path(args.path, ctx)
        if not root.is_dir():
            raise StateConflict("Search path must be a directory")
        hits, scanned = [], 0
        ignored = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "data"}
        # No regex execution and no symlink traversal. Work is bounded even in large repos.
        import os

        for parent, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d not in ignored and not Path(parent, d).is_symlink()]
            for name in sorted(names):
                path = Path(parent, name)
                if path.is_symlink() or self.private(path) or not path.match(args.glob):
                    continue
                try:
                    self.path(str(path), ctx)
                    if path.stat().st_size > 100000:
                        continue
                    scanned += 1
                    for line_no, line in enumerate(path.read_text().splitlines(), 1):
                        if args.query in line:
                            hits.append(
                                {
                                    "path": str(path.relative_to(root)),
                                    "line": line_no,
                                    "text": line[:500],
                                }
                            )
                            if len(hits) >= 80:
                                return {"matches": hits, "truncated": True}
                except (OSError, UnicodeError, StateConflict):
                    continue
                if scanned >= 1500:
                    return {"matches": hits, "truncated": True}
        return {"matches": hits, "truncated": False}

    def _write(self, path, content, expected, ctx):
        from master_agent.local.tools import WriteArgs

        with self.lock:
            exists = path.exists()
            if exists and (not path.is_file() or path.stat().st_size > 100000):
                raise StateConflict("Choose a text source file under 100 KB")
            before = path.read_bytes().decode() if exists else None
            current = sha(path.read_bytes()) if exists else None
            if expected != current:
                raise StateConflict(
                    "File changed or already exists; read it again and use its SHA256"
                )
            self.tools.write_file(WriteArgs(path=str(path), content=content), ctx)
            key = sha((ctx.chat_id + str(path)).encode())
            try:
                previous = self.store.get("code_changes", key)
                initial = self.store.unseal(previous["sealed"])["before"]
            except LookupError:
                initial = before
            record = {
                "id": key,
                "chat_id": ctx.chat_id,
                "path": str(path),
                "before_sha256": sha(initial.encode()) if initial is not None else None,
                "after_sha256": sha(content.encode()),
                "updated_at": now(),
                "sealed": self.store.seal({"before": initial, "after": content}),
            }
            self.store.put("code_changes", key, record)
            for project in self.projects(ctx.chat_id):
                root = Path(project["path"])
                if path.is_relative_to(root):
                    name = path.relative_to(root).as_posix()
                    project["files"] = sorted(set(project["files"]) | {name})
                    project["updated_at"] = now()
                    self.store.put("projects", project["id"], project)
            return {
                "path": str(path),
                "sha256": record["after_sha256"],
                "created": not exists,
                "written": len(content),
            }

    def write(self, args, ctx):
        path = self.path(args.path, ctx)
        return self._write(path, args.content, self.expected(args, path, ctx), ctx)

    def expected(self, args, path, ctx):
        if args.revision:
            reference = self.store.get("code_revisions", args.revision)
            if reference["chat_id"] != ctx.chat_id or reference["path"] != str(path):
                raise StateConflict("Read this file in the current chat to get its revision")
            if args.expected_sha256 and args.expected_sha256 != reference["sha256"]:
                raise StateConflict("Revision and SHA256 disagree; read the current file")
            return reference["sha256"]
        if args.expected_sha256:
            return args.expected_sha256
        # Bind to the last actual read in this chat. The LLM need not copy a
        # 64-character hash correctly; the same optimistic comparison still runs.
        for reference in self.store.list("code_revisions"):
            if reference["chat_id"] == ctx.chat_id and reference["path"] == str(path):
                return reference["sha256"]
        return None

    def replace(self, args, ctx):
        with self.lock:
            path = self.path(args.path, ctx)
            if not path.is_file() or path.stat().st_size > 100000:
                raise StateConflict("Choose an existing source file under 100 KB")
            content = path.read_bytes().decode()
            if content.count(args.old) != 1:
                raise StateConflict(
                    "Old snippet must match exactly once; read/search before editing"
                )
            return self._write(
                path, content.replace(args.old, args.new, 1), self.expected(args, path, ctx), ctx
            )

    def changes(self, chat_id):
        result = []
        for record in self.store.list("code_changes"):
            if record["chat_id"] != chat_id:
                continue
            content = self.store.unseal(record["sealed"])
            diff = "".join(
                difflib.unified_diff(
                    (content["before"] or "").splitlines(keepends=True),
                    content["after"].splitlines(keepends=True),
                    fromfile="before/" + Path(record["path"]).name,
                    tofile="after/" + Path(record["path"]).name,
                )
            )
            result.append(
                {k: v for k, v in record.items() if k != "sealed"}
                | {"diff": self.store.redact(diff[:20000]), "truncated": len(diff) > 20000}
            )
        return result[:100]

    def create(self, args, ctx):
        with self.lock:
            path = self.path(args.directory, ctx)
            signature = sha(json.dumps(args.model_dump(), sort_keys=True).encode())
            for project in self.projects(ctx.chat_id):
                if project["path"] == str(path) and project["signature"] == signature:
                    if project["status"] == "incomplete":
                        raise StateConflict(
                            "A partial project exists; inspect and repair its files"
                        )
                    return {"project_id": project["id"], "project": project, "reused": True}
            if path.exists():
                raise StateConflict(
                    "Folder already exists; inspect/edit it or choose a new directory"
                )
            path.mkdir(parents=True, exist_ok=False)
            record = {
                "id": str(uuid4()),
                "chat_id": ctx.chat_id,
                "name": args.name,
                "path": str(path),
                "kind": "python",
                "signature": signature,
                "status": "created",
                "files": [],
                "created_at": now(),
                "updated_at": now(),
            }
            self.store.put("projects", record["id"], record)
            try:
                for name, content in python_template.files(
                    args.name,
                    self.store.redact(args.instruction),
                    self.tools.settings.ollama_model,
                    self.tools.settings.ollama_base_url,
                ).items():
                    self._write(path / name, content, None, ctx)
            except BaseException:
                record = self.store.get("projects", record["id"])
                self.store.put("projects", record["id"], {**record, "status": "incomplete"})
                raise
            return {
                "project_id": record["id"],
                "project": self.store.get("projects", record["id"]),
                "next": "Customize requested behavior, then project.check; service stays stopped",
            }

    def project_path(self, record, ctx=None):
        path = Path(record["path"])
        if path.resolve() != path or not path.is_dir():
            raise StateConflict(
                "Project folder moved or became a symlink; inspect before continuing"
            )
        if ctx is not None:
            self.path(str(path), ctx)
        return path

    def fingerprint(self, project):
        root = self.project_path(project)
        hashes = {}
        for name in project["files"]:
            path = (root / name).resolve()
            if (
                not path.is_relative_to(root)
                or not path.is_file()
                or path.stat().st_size > 1_000_000
            ):
                raise StateConflict(
                    "Tracked project files changed; inspect before running/exporting"
                )
            hashes[name] = sha(path.read_bytes())
        return sha(json.dumps(hashes, sort_keys=True).encode())

    async def check(self, args, ctx):
        from master_agent.local.tools import CommandArgs

        project = self.store.get("projects", str(args.project_id))
        if project["chat_id"] != ctx.chat_id:
            raise StateConflict("Select a project belonging to this chat")
        root = self.project_path(project, ctx)
        before = self.fingerprint(project)
        run = {
            "id": str(uuid4()),
            "project_id": project["id"],
            "chat_id": ctx.chat_id,
            "status": "running",
            "created_at": now(),
            "fingerprint": before,
        }
        self.store.put("code_runs", run["id"], run)
        try:
            result = await self.tools.command(
                CommandArgs(
                    argv=[sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                    cwd=str(root),
                    timeout=args.timeout,
                ),
                ctx,
            )
            result = self.store.redact(result)
            same = before == self.fingerprint(self.store.get("projects", project["id"]))
            count = re.search(r"Ran (\d+) tests?", result["stdout"] + result["stderr"])
            tests_run = int(count[1]) if count else 0
            passed = result["exit_code"] == 0 and not result["timed_out"] and same and tests_run > 0
            run.update(
                result=result,
                status="passed" if passed else "failed",
                finished_at=now(),
                files_unchanged=same,
                tests_run=tests_run,
            )
        except asyncio.CancelledError:
            run.update(status="cancelled", finished_at=now())
            raise
        except Exception:
            run.update(status="failed", finished_at=now())
            raise
        finally:
            self.store.put("code_runs", run["id"], run)
        return {"project_id": project["id"], "run": run}

    def archive(self, project_id):
        project = self.store.get("projects", project_id)
        root = self.project_path(project)
        self.fingerprint(project)
        buffer, total = io.BytesIO(), 0
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in project["files"]:
                path = (root / name).resolve()
                if self.private(path):
                    continue
                total += path.stat().st_size
                if total > 5_000_000:
                    raise StateConflict("Project archive exceeds 5 MB")
                archive.writestr(name, path.read_bytes())
        return buffer.getvalue()
