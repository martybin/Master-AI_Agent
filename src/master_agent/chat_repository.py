"""Owner-local conversation history. Workflow lifecycle remains in BuildService."""

import json
from uuid import uuid4

from master_agent.repository import NotFound, Repository, StateConflict, now


class ChatRepository:
    def __init__(self, builds: Repository):
        self.builds = builds

    def initialize(self) -> None:
        with self.builds.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, request_id TEXT NOT NULL,
                role TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(chat_id,request_id,role))""")
            db.execute("CREATE INDEX IF NOT EXISTS chat_messages_chat ON chat_messages(chat_id)")
            for chat_id, raw in db.execute("SELECT id,data FROM chats").fetchall():
                record = json.loads(raw)
                if record["status"] == "busy":
                    record.update(status="idle", updated_at=now())
                    db.execute("UPDATE chats SET data=? WHERE id=?", (json.dumps(record), chat_id))
                    rows = db.execute(
                        "SELECT id,data FROM chat_messages WHERE chat_id=? AND role=?",
                        (chat_id, "assistant"),
                    ).fetchall()
                    for message_id, data in rows:
                        message = json.loads(data)
                        if message["status"] == "pending":
                            message.update(
                                status="failed",
                                error_code="interrupted",
                                content=(
                                    "The application restarted while processing this request. "
                                    "Please send your request again."
                                ),
                            )
                            db.execute(
                                "UPDATE chat_messages SET data=? WHERE id=?",
                                (json.dumps(message), message_id),
                            )

    @staticmethod
    def present(record):
        record.setdefault("pinned", False)
        if record["title"] == "گفتگوی تازه":
            record["title"] = "New chat"
        return record

    def create(self, target="auto") -> dict:
        record = {
            "id": str(uuid4()),
            "title": "New chat",
            "status": "idle",
            "target": target,
            "pinned": False,
            "created_at": now(),
            "updated_at": now(),
        }
        with self.builds.connect() as db:
            db.execute("INSERT INTO chats(id,data) VALUES(?,?)", (record["id"], json.dumps(record)))
        return {**record, "messages": []}

    def set_target(self, chat_id, target):
        if target not in {"auto", "n8n", "python", "code"}:
            raise StateConflict("Invalid build target")
        with self.builds.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM chats WHERE id=?", (chat_id,)).fetchone()
            if not row:
                raise NotFound("Chat not found")
            chat = json.loads(row[0])
            if chat["status"] != "idle":
                raise StateConflict("Wait for the current task before changing build target")
            chat.update(target=target, updated_at=now())
            db.execute("UPDATE chats SET data=? WHERE id=?", (json.dumps(chat), chat_id))
        return self.get(chat_id)

    def list(self) -> list[dict]:
        with self.builds.connect() as db:
            rows = db.execute(
                "SELECT data FROM chats ORDER BY "
                "COALESCE(json_extract(data,'$.pinned'),0) DESC, "
                "json_extract(data,'$.updated_at') DESC, id"
            ).fetchall()
        return [self.present(json.loads(row[0])) for row in rows]

    def get(self, chat_id: str) -> dict:
        with self.builds.connect() as db:
            row = db.execute("SELECT data FROM chats WHERE id=?", (chat_id,)).fetchone()
            if not row:
                raise NotFound("Chat not found")
            messages = db.execute(
                "SELECT data FROM chat_messages WHERE chat_id=? ORDER BY rowid",
                (chat_id,),
            ).fetchall()
        return {
            **self.present(json.loads(row[0])),
            "messages": [json.loads(row[0]) for row in messages],
        }

    def set_pinned(self, chat_id: str, pinned: bool) -> dict:
        with self.builds.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM chats WHERE id=?", (chat_id,)).fetchone()
            if not row:
                raise NotFound("Chat not found")
            chat = json.loads(row[0])
            # Pinning must not change conversation recency.
            chat["pinned"] = pinned
            db.execute("UPDATE chats SET data=? WHERE id=?", (json.dumps(chat), chat_id))
        return self.present(chat)

    def delete(self, chat_id: str) -> None:
        with self.builds.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM chats WHERE id=?", (chat_id,)).fetchone()
            if not row:
                raise NotFound("Chat not found")
            if json.loads(row[0])["status"] != "idle":
                raise StateConflict("Stop the current task before deleting this chat")
            db.execute("DELETE FROM chat_messages WHERE chat_id=?", (chat_id,))
            # Discard access capabilities with the conversation. Built projects, files,
            # workflows and their audit records are independent owner artifacts.
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_records'"
            ).fetchone():
                db.execute(
                    "DELETE FROM local_records WHERE (bucket='grants' AND id=?) OR "
                    "(bucket IN ('access_requests','code_revisions') "
                    "AND json_extract(data,'$.chat_id')=?)",
                    (chat_id, chat_id),
                )
            db.execute("DELETE FROM chats WHERE id=?", (chat_id,))

    def find_turn(self, chat_id: str, request_id: str, content: str) -> dict | None:
        with self.builds.connect() as db:
            rows = db.execute(
                "SELECT data FROM chat_messages WHERE chat_id=? AND request_id=?",
                (chat_id, request_id),
            ).fetchall()
        if not rows:
            return None
        messages = [json.loads(row[0]) for row in rows]
        user = next(message for message in messages if message["role"] == "user")
        if user["content"] != content:
            raise StateConflict("Request id was already used for a different message")
        return {message["role"]: message for message in messages}

    def start_turn(self, chat_id: str, request_id: str, content: str) -> dict:
        with self.builds.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM chats WHERE id=?", (chat_id,)).fetchone()
            if not row:
                raise NotFound("Chat not found")
            chat = json.loads(row[0])
            if chat["status"] != "idle":
                raise StateConflict("Wait for the current message to finish")
            user = {
                "id": str(uuid4()),
                "role": "user",
                "content": content,
                "status": "complete",
                "created_at": now(),
                "build_id": None,
            }
            assistant = {
                "id": str(uuid4()),
                "role": "assistant",
                "content": "",
                "status": "pending",
                "created_at": now(),
                "build_id": None,
            }
            for message in (user, assistant):
                db.execute(
                    "INSERT INTO chat_messages(id,chat_id,request_id,role,data) VALUES(?,?,?,?,?)",
                    (message["id"], chat_id, request_id, message["role"], json.dumps(message)),
                )
            if chat["title"] in {"New chat", "گفتگوی تازه"}:
                chat["title"] = content[:48]
            chat.update(status="busy", updated_at=now())
            db.execute("UPDATE chats SET data=? WHERE id=?", (json.dumps(chat), chat_id))
        return {"user": user, "assistant": assistant}

    def finish_turn(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        build_id: str | None = None,
        error_code: str | None = None,
        title: str | None = None,
        project_id: str | None = None,
        access_request: dict | None = None,
    ) -> dict:
        with self.builds.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT data FROM chat_messages WHERE id=? AND chat_id=?", (message_id, chat_id)
            ).fetchone()
            if not row:
                raise NotFound("Message not found")
            message = json.loads(row[0])
            message.update(
                content=content,
                build_id=build_id,
                error_code=error_code,
                status="failed" if error_code else "complete",
                project_id=project_id,
                access_request=access_request,
            )
            db.execute(
                "UPDATE chat_messages SET data=? WHERE id=?", (json.dumps(message), message_id)
            )
            chat = json.loads(
                db.execute("SELECT data FROM chats WHERE id=?", (chat_id,)).fetchone()[0]
            )
            chat.update(status="idle", updated_at=now())
            if title:
                chat["title"] = title[:80]
            db.execute("UPDATE chats SET data=? WHERE id=?", (json.dumps(chat), chat_id))
        return message
