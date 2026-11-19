"""Authenticated owner control plane. Secret entry and grants are never model tools."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import Field, SecretStr, StrictBool

from master_agent.ir import ClosedModel
from master_agent.local.bridge import BindingInput
from master_agent.local.connectors import ConnectionInput
from master_agent.local.native import CatalogEntry, NativeSpec
from master_agent.local.tools import GrantInput
from master_agent.repository import StateConflict


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
