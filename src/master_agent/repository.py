"""Small durable SQLite store with compare-and-set mutation claims and an audit trail."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class NotFound(LookupError):
    pass


class StateConflict(ValueError):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat()


class Repository:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS builds (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, build_id TEXT NOT NULL,
                at TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL)""")
        self.path.chmod(0o600)
        # Supported deployment is one uvicorn worker. A restarted process cannot assume POST failed.
        with self.connect() as db:
            for build_id, data in db.execute("SELECT id,data FROM builds").fetchall():
                record = json.loads(data)
                recovery = {
                    "deploying": "deployment_unknown",
                    "activating": "activation_unknown",
                    "deactivating": "activation_unknown",
                }
                if record["status"] in recovery:
                    record["status"] = recovery[record["status"]]
                    record["updated_at"] = now()
                    db.execute(
                        "UPDATE builds SET data=? WHERE id=?", (json.dumps(record), build_id)
                    )
                    db.execute(
                        "INSERT INTO events(build_id,at,action,status) VALUES(?,?,?,?)",
                        (build_id, now(), "recover_interrupted", record["status"]),
                    )

    def create(
        self,
        *,
        prompt: str | None,
        ir: dict,
        workflow: dict,
        sha256: str,
        warnings: list[dict],
        attempts: int,
    ) -> dict:
        record = {
            "id": str(uuid4()),
            "status": "draft",
            "prompt": prompt,
            "ir": ir,
            "workflow": workflow,
            "workflow_sha256": sha256,
            "warnings": warnings,
            "attempts": attempts,
            "n8n_workflow_id": None,
            "review_sha256": None,
            "created_at": now(),
            "updated_at": now(),
            "last_error": None,
        }
        with self.connect() as db:
            db.execute(
                "INSERT INTO builds(id,data) VALUES(?,?)", (record["id"], json.dumps(record))
            )
            db.execute(
                "INSERT INTO events(build_id,at,action,status) VALUES(?,?,?,?)",
                (record["id"], now(), "build", "draft"),
            )
        return record

    def get(self, build_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT data FROM builds WHERE id=?", (build_id,)).fetchone()
        if not row:
            raise NotFound("Build not found")
        return json.loads(row[0])

    def update(
        self,
        build_id: str,
        expected: set[str],
        action: str,
        review_guard: str | None = None,
        **changes: object,
    ) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM builds WHERE id=?", (build_id,)).fetchone()
            if not row:
                raise NotFound("Build not found")
            record = json.loads(row[0])
            if record["status"] not in expected:
                raise StateConflict(f"Cannot {action} while build is {record['status']}")
            if review_guard is not None and record.get("review_sha256") != review_guard:
                raise StateConflict("Approval review is stale; review the workflow again")
            record.update(changes, updated_at=now())
            db.execute("UPDATE builds SET data=? WHERE id=?", (json.dumps(record), build_id))
            db.execute(
                "INSERT INTO events(build_id,at,action,status) VALUES(?,?,?,?)",
                (build_id, now(), action, record["status"]),
            )
        return record

    def events(self, build_id: str) -> list[dict]:
        self.get(build_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT at,action,status FROM events WHERE build_id=? ORDER BY seq", (build_id,)
            ).fetchall()
        return [{"at": row[0], "action": row[1], "status": row[2]} for row in rows]
