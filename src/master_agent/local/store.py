"""Encrypted connections, execution grants, audit records and durable delivery state."""

import json
import os
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from master_agent.repository import NotFound, Repository, StateConflict, now


class LocalStore:
    def __init__(self, repo: Repository, key_path: Path):
        self.repo, self.key_path = repo, key_path
        self.cipher: Fernet | None = None
        self.sensitive_values: list[str] = []

    def initialize(self):
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.key_path.exists():
            with self.repo.connect() as db:
                tables = {
                    row[0]
                    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                existing = (
                    db.execute(
                        "SELECT count(*) FROM local_records "
                        "WHERE bucket IN ('connections','code_changes')"
                    ).fetchone()[0]
                    if "local_records" in tables
                    else 0
                )
                inbox = (
                    db.execute("SELECT count(*) FROM local_inbox").fetchone()[0]
                    if "local_inbox" in tables
                    else 0
                )
            if existing or inbox:
                raise StateConflict("Vault key is missing; restore it before starting the agent")
        # Exclusive creation prevents two startup attempts from replacing an existing key.
        try:
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(Fernet.generate_key())
        self.key_path.chmod(0o600)
        self.cipher = Fernet(self.key_path.read_bytes())
        self.sensitive_values.append(self.key_path.read_text().strip())
        with self.repo.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS local_records (bucket TEXT, id TEXT, data "
                "TEXT NOT NULL, PRIMARY KEY(bucket,id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS local_audit (seq INTEGER PRIMARY KEY "
                "AUTOINCREMENT, data TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS local_inbox (id TEXT PRIMARY KEY, connection "
                "TEXT NOT NULL, event_key TEXT NOT NULL, data TEXT NOT NULL, "
                "UNIQUE(connection,event_key))"
            )
            sample = db.execute(
                "SELECT data FROM local_records WHERE bucket IN ('connections','code_changes') "
                "UNION ALL SELECT data FROM local_inbox LIMIT 1"
            ).fetchone()
            if sample:
                self.unseal(json.loads(sample[0])["sealed"])
            for record_id, raw in db.execute("SELECT id,data FROM local_inbox").fetchall():
                record = json.loads(raw)
                if record["status"] == "delivering":
                    record.update(status="uncertain", phase="interrupted_delivery")
                    db.execute(
                        "UPDATE local_inbox SET data=? WHERE id=?", (json.dumps(record), record_id)
                    )
            for record_id, raw in db.execute(
                "SELECT id,data FROM local_records WHERE bucket='code_runs'"
            ).fetchall():
                record = json.loads(raw)
                if record["status"] == "running":
                    record.update(status="interrupted", finished_at=now())
                    db.execute(
                        "UPDATE local_records SET data=? WHERE bucket='code_runs' AND id=?",
                        (json.dumps(record), record_id),
                    )

    def seal(self, value: dict) -> str:
        return self.cipher.encrypt(json.dumps(value).encode()).decode()

    def unseal(self, value: str) -> dict:
        try:
            return json.loads(self.cipher.decrypt(value.encode()))
        except InvalidToken as exc:
            raise StateConflict(
                "Vault key does not match encrypted data; restore the original key"
            ) from exc

    def redact(self, value: object) -> object:
        secrets_to_hide = [s for s in self.sensitive_values if s]
        for record in self.list("connections"):
            secrets = self.unseal(record["sealed"])
            for secret in secrets.values():
                if isinstance(secret, str) and len(secret) >= 6:
                    secrets_to_hide.append(secret)
        secrets_to_hide.sort(key=len, reverse=True)

        def visit(item):
            if isinstance(item, str):
                for secret in secrets_to_hide:
                    item = item.replace(secret, "[REDACTED]")
                return item
            if isinstance(item, list):
                return [visit(v) for v in item]
            if isinstance(item, dict):
                return {visit(k): visit(v) for k, v in item.items()}
            return item

        return visit(json.loads(json.dumps(value, ensure_ascii=False, default=str)))

    def put(self, bucket: str, record_id: str, data: dict):
        with self.repo.connect() as db:
            db.execute(
                "INSERT INTO local_records(bucket,id,data) VALUES(?,?,?) ON "
                "CONFLICT(bucket,id) DO UPDATE SET data=excluded.data",
                (bucket, record_id, json.dumps(data)),
            )
        return data

    def get(self, bucket: str, record_id: str) -> dict:
        with self.repo.connect() as db:
            row = db.execute(
                "SELECT data FROM local_records WHERE bucket=? AND id=?", (bucket, record_id)
            ).fetchone()
        if not row:
            raise NotFound(f"{bucket} record not found")
        return json.loads(row[0])

    def list(self, bucket: str) -> list[dict]:
        with self.repo.connect() as db:
            rows = db.execute(
                "SELECT data FROM local_records WHERE bucket=? ORDER BY rowid DESC", (bucket,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def audit(self, action: str, status: str, **details):
        record = self.redact({"at": now(), "action": action, "status": status, **details})
        with self.repo.connect() as db:
            db.execute("INSERT INTO local_audit(data) VALUES(?)", (json.dumps(record),))
        return record

    def audits(self, limit: int = 100):
        with self.repo.connect() as db:
            rows = db.execute(
                "SELECT data FROM local_audit ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def capture(self, connection: str, event_key: str, payload: dict, build_id=None) -> dict | None:
        record = {
            "id": str(uuid4()),
            "connection": connection,
            "event_key": event_key,
            "status": "pending",
            "build_id": build_id,
            "created_at": now(),
            "sealed": self.seal(payload),
        }
        with self.repo.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO local_inbox(id,connection,event_key,data) VALUES(?,?,?,?)",
                (record["id"], connection, event_key, json.dumps(record)),
            )
            return record if cursor.rowcount else None

    def update_delivery(self, record: dict, status: str, **details):
        record = {**record, "status": status, "updated_at": now(), **details}
        with self.repo.connect() as db:
            db.execute(
                "UPDATE local_inbox SET data=? WHERE id=?", (json.dumps(record), record["id"])
            )
        return record

    def inbox(self, limit=100):
        with self.repo.connect() as db:
            rows = db.execute(
                "SELECT data FROM local_inbox ORDER BY rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {k: v for k, v in json.loads(row[0]).items() if not k.startswith("sealed")}
            for row in rows
        ]

    def pending(self, alias):
        with self.repo.connect() as db:
            rows = db.execute(
                "SELECT data FROM local_inbox WHERE connection=? AND "
                "json_extract(data,'$.status')='pending' ORDER BY rowid LIMIT 50",
                (alias,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def delivery(self, record_id):
        with self.repo.connect() as db:
            row = db.execute("SELECT data FROM local_inbox WHERE id=?", (record_id,)).fetchone()
        if not row:
            raise NotFound("Delivery not found")
        return json.loads(row[0])

    def claim_delivery(self, record_id, allow_uncertain=False):
        with self.repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM local_inbox WHERE id=?", (record_id,)).fetchone()
            if not row:
                raise NotFound("Delivery not found")
            record = json.loads(row[0])
            allowed = {"pending", "uncertain"} if allow_uncertain else {"pending"}
            if record["status"] not in allowed:
                return None
            record.update(status="delivering", updated_at=now())
            db.execute("UPDATE local_inbox SET data=? WHERE id=?", (json.dumps(record), record_id))
        return record
