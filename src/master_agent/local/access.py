"""Owner-approved, chat-scoped host access. Model requests never grant permission."""

from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field

from master_agent.chat_repository import ChatRepository
from master_agent.ir import ClosedModel
from master_agent.repository import StateConflict, now


class AccessContext(str):
    def __new__(cls, mode, workspace, chat_id):
        value = super().__new__(cls, mode)
        value.workspace, value.chat_id = Path(workspace).resolve(), chat_id
        return value


class AccessRequired(StateConflict):
    def __init__(self, scope):
        self.scope = scope
        super().__init__("Owner access grant is required; enable it in the Local Agent panel")


class AccessRequest(ClosedModel):
    mode: Literal["workspace", "system"] = "system"
    reason: str = Field(min_length=3, max_length=500)


def request_access(tools, chat_id, data: AccessRequest):
    current = tools.permission(chat_id)
    if current["mode"] == "system" or current["mode"] == data.mode:
        return {"already_granted": True, "access": current}
    chat = ChatRepository(tools.store.repo).get(chat_id)
    user = next(m for m in reversed(chat["messages"]) if m["role"] == "user")
    for record in tools.store.list("access_requests"):
        if (
            record["chat_id"] == chat_id
            and record["message_id"] == user["id"]
            and record["status"] == "pending"
            and record["mode"] == data.mode
        ):
            return {"access_request": record, "needs_approval": True}
    record = {
        "id": str(uuid4()),
        "chat_id": chat_id,
        "message_id": user["id"],
        "mode": data.mode,
        "reason": tools.store.redact(data.reason),
        "workspace": tools.permission(chat_id).get(
            "workspace", str(tools.settings.local_workspace.resolve())
        ),
        "target": chat.get("target", "auto"),
        "status": "pending",
        "created_at": now(),
    }
    # A new request supersedes an older pending request; approval must match the task.
    for old in tools.store.list("access_requests"):
        if old["chat_id"] == chat_id and old["status"] == "pending":
            tools.store.put("access_requests", old["id"], {**old, "status": "superseded"})
    tools.store.put("access_requests", record["id"], record)
    tools.store.audit("access.request", "pending", chat_id=chat_id, mode=data.mode)
    return {"access_request": record, "needs_approval": True}
