"""Authenticated owner control plane. Secret entry and grants are never model tools."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import Field, SecretStr, StrictBool

from master_agent.ir import ClosedModel
from master_agent.local.bridge import BindingInput
from master_agent.local.connectors import ConnectionInput
from master_agent.local.native import CatalogEntry, NativeSpec
from master_agent.local.tools import GrantInput
from master_agent.repository import StateConflict, now


class LoginStart(ClosedModel):
    phone: SecretStr


class LoginFinish(ClosedModel):
    code: SecretStr = SecretStr("")
    password: SecretStr = SecretStr("")


class ToolInput(ClosedModel):
    chat_id: UUID
    name: str
    arguments: dict = Field(default_factory=dict)


class Confirmation(ClosedModel):
    confirm: StrictBool = False


class AccessApproval(GrantInput):
    confirm: StrictBool = False


def router(authenticate):
    routes = APIRouter(prefix="/api/v1/local", dependencies=[Depends(authenticate)])

    @routes.get("/status")
    async def status(request: Request, chat_id: str | None = None):
        local = request.app.state
        return {
            "workspace": str(local.tools.settings.local_workspace.resolve()),
            "access": local.tools.permission(chat_id) if chat_id else {"mode": "off"},
            "connections": local.connectors.list(),
            "bindings": local.local_store.list("bindings"),
            "tools": [
                {"name": item["name"], "description": item["description"], "scope": item["scope"]}
                for item in local.tools.manifest()
            ],
            "catalog_count": len(local.local_store.list("catalog")),
            "inbox": local.local_store.inbox(30),
            "audit": local.local_store.audits(30),
            "access_requests": [
                r
                for r in local.local_store.list("access_requests")
                if r["status"] == "pending" and (not chat_id or r["chat_id"] == chat_id)
            ],
        }

    @routes.get("/tools")
    async def tools(request: Request):
        return {"tools": request.app.state.tools.manifest()}

    @routes.put("/grants/{chat_id}")
    async def grant(chat_id: str, data: GrantInput, request: Request):
        request.app.state.history.get(chat_id)
        return request.app.state.tools.grant(chat_id, data)

    @routes.post("/tools/execute")
    async def execute(data: ToolInput, request: Request):
        request.app.state.history.get(str(data.chat_id))
        return {
            "result": await request.app.state.tools.call(
                str(data.chat_id), data.name, data.arguments
            )
        }

    @routes.post("/access-requests/{request_id}/approve")
    async def approve_access(request_id: str, data: AccessApproval, request: Request):
        if data.confirm is not True:
            raise StateConflict("Explicit owner confirmation is required")
        if data.mode == "off":
            raise StateConflict("Choose workspace/system access or reject the request")
        state = request.app.state
        record = state.local_store.get("access_requests", request_id)
        chat = state.history.get(record["chat_id"])
        if record["status"] == "approved":
            return record
        if record["status"] != "pending" or chat["status"] != "idle":
            raise StateConflict("This access request is no longer pending")
        user = next(m for m in reversed(chat["messages"]) if m["role"] == "user")
        if user["id"] != record["message_id"]:
            raise StateConflict("The chat has a newer task; request access for that task instead")
        if chat.get("target", "auto") != record.get("target", "auto"):
            raise StateConflict("Build target changed; request access for the current task")
        grant = state.tools.grant(record["chat_id"], data)
        record.update(status="approved", approved_at=now(), grant=grant)
        state.local_store.put("access_requests", request_id, record)
        state.local_store.audit(
            "access.approve", "complete", chat_id=record["chat_id"], mode=grant["mode"]
        )
        return record

    @routes.get("/access-requests/{request_id}")
    async def get_access(request_id: str, request: Request):
        return request.app.state.local_store.get("access_requests", request_id)

    @routes.post("/access-requests/{request_id}/reject")
    async def reject_access(request_id: str, request: Request):
        store = request.app.state.local_store
        record = store.get("access_requests", request_id)
        if record["status"] != "pending":
            raise StateConflict("This request is no longer pending")
        record.update(status="rejected")
        store.put("access_requests", request_id, record)
        return record

    @routes.get("/projects")
    async def projects(request: Request, chat_id: str | None = None):
        state = request.app.state
        runs = state.local_store.list("code_runs")
        result = {
            "projects": [
                p | {"last_run": next((r for r in runs if r["project_id"] == p["id"]), None)}
                for p in state.tools.coding.projects(chat_id)
            ]
        }
        for project in result["projects"]:
            try:
                project["checks_current"] = bool(project["last_run"]) and (
                    project["last_run"]["fingerprint"] == state.tools.coding.fingerprint(project)
                )
            except (StateConflict, OSError):
                project["checks_current"] = False
        return result

    @routes.get("/code-changes")
    async def changes(request: Request, chat_id: str):
        request.app.state.history.get(chat_id)
        return {"changes": request.app.state.tools.coding.changes(chat_id)}

    @routes.get("/projects/{project_id}/archive")
    async def archive(project_id: str, request: Request):
        return Response(
            request.app.state.tools.coding.archive(project_id),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="biscode-{UUID(project_id)}.zip"'
            },
        )

    @routes.get("/connections")
    async def connections(request: Request):
        return {"connections": request.app.state.connectors.list()}

    @routes.post("/connections", status_code=201)
    async def create(data: ConnectionInput, request: Request):
        return request.app.state.connectors.create(data)

    @routes.post("/connections/{alias}/verify")
    async def verify(alias: str, request: Request):
        return await request.app.state.connectors.verify(alias)

    @routes.post("/connections/{alias}/login/start")
    async def login_start(alias: str, data: LoginStart, request: Request):
        return await request.app.state.connectors.begin_login(alias, data.phone.get_secret_value())

    @routes.post("/connections/{alias}/login/finish")
    async def login_finish(alias: str, data: LoginFinish, request: Request):
        return await request.app.state.connectors.complete_login(
            alias, data.code.get_secret_value(), data.password.get_secret_value()
        )

    @routes.post("/connections/{alias}/login/cancel")
    async def login_cancel(alias: str, request: Request):
        return await request.app.state.connectors.cancel_login(alias)

    @routes.get("/connections/{alias}/dialogs")
    async def dialogs(alias: str, request: Request):
        return {"dialogs": await request.app.state.connectors.dialogs(alias)}

    @routes.put("/bindings/{alias}")
    async def bind(alias: str, data: BindingInput, request: Request):
        return request.app.state.bridges.bind(alias, data)

    @routes.post("/bindings/{alias}/start")
    async def start(alias: str, data: Confirmation, request: Request):
        if data.confirm is not True:
            raise StateConflict("Confirm starting automatic processing and replies")
        return await request.app.state.bridges.start(alias)

    @routes.post("/bindings/{alias}/stop")
    async def stop(alias: str, request: Request):
        return await request.app.state.bridges.stop(alias)

    @routes.get("/catalog")
    async def catalog(request: Request, query: str = ""):
        return {"nodes": request.app.state.catalog.search(query)}

    @routes.get("/catalog/describe")
    async def describe(request: Request, node_type: str):
        return request.app.state.catalog.describe(node_type)

    @routes.post("/catalog", status_code=201)
    async def install(data: CatalogEntry, request: Request):
        return request.app.state.catalog.install(data)

    @routes.post("/native-builds", status_code=201)
    async def native(data: NativeSpec, request: Request):
        return request.app.state.tools.native(data)

    @routes.post("/inbox/{record_id}/retry")
    async def retry(record_id: str, data: Confirmation, request: Request):
        if data.confirm is not True:
            raise StateConflict("Confirm possible repeat side effects before retrying")
        record = request.app.state.local_store.delivery(record_id)
        if record["status"] not in {"pending", "uncertain"}:
            raise StateConflict("Only pending or uncertain deliveries may be retried")
        request.app.state.local_store.audit("bridge.retry", "owner_confirmed", delivery=record_id)
        await request.app.state.bridges.deliver(record, allow_uncertain=True)
        return {
            k: v
            for k, v in request.app.state.local_store.delivery(record_id).items()
            if not k.startswith("sealed")
        }

    return routes
