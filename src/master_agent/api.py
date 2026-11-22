import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import Field, StrictBool
from starlette.types import ASGIApp, Receive, Scope, Send

from master_agent import __version__
from master_agent.chat_repository import ChatRepository
from master_agent.chat_service import ChatService
from master_agent.clients import IntegrationError, N8nClient, OllamaClient
from master_agent.compiler import compile_ir, digest
from master_agent.config import Settings
from master_agent.ir import ClosedModel, WorkflowIR
from master_agent.knowledge.store import Knowledge
from master_agent.local.bridge import Bridges
from master_agent.local.connectors import Connectors
from master_agent.local.native import NativeCatalog
from master_agent.local.routes import router as local_router
from master_agent.local.store import LocalStore
from master_agent.local.tools import Tools
from master_agent.planner import Planner, PlanningFailed
from master_agent.repository import NotFound, Repository, StateConflict
from master_agent.service import BuildService
from master_agent.validator import InvalidIR, Policy, require_valid, validate_ir


class RevalidatedStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class BodyLimit:
    """Bound all request bodies, including chunked requests, before JSON parsing."""

    def __init__(self, app: ASGIApp, max_bytes: int = 262144):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        chunks = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            total += len(message.get("body", b""))
            if total > self.max_bytes:
                await JSONResponse({"detail": "Request body too large"}, status_code=413)(
                    scope, receive, send
                )
                return
            chunks.append(message)
            if not message.get("more_body", False):
                break
        index = 0

        async def replay() -> dict:
            nonlocal index
            if index < len(chunks):
                item = chunks[index]
                index += 1
                return item
            return await receive()

        await self.app(scope, replay, send)


class BuildRequest(ClosedModel):
    prompt: str = Field(min_length=10, max_length=12000)


class ChatMessageRequest(ClosedModel):
    content: str = Field(min_length=1, max_length=6000, pattern=r"\S")
    request_id: UUID


class ChatOptions(ClosedModel):
    target: Literal["auto", "n8n", "python", "code"] = "auto"


class ChatPinRequest(ClosedModel):
    pinned: StrictBool


class ResumeRequest(ClosedModel):
    access_request_id: UUID


class ActivationRequest(ClosedModel):
    confirm: StrictBool
    workflow_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReconcileRequest(ClosedModel):
    workflow_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    cfg = settings or Settings()
    repo = Repository(cfg.database_path)
    policy = Policy(frozenset(cfg.http_allowed_hosts))
    planner_lock = asyncio.Lock()
    history = ChatRepository(repo)
    chat_tasks: set[asyncio.Task] = set()
    turn_tasks: dict[str, asyncio.Task] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        key = cfg.master_api_key.get_secret_value()
        if len(key) < 32 or key == "replace-with-a-long-random-secret":
            raise RuntimeError("Generate MASTER_API_KEY first: uv run python scripts/bootstrap.py")
        repo.initialize()
        history.initialize()
        async with httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ) as client:
            ollama = OllamaClient(cfg, client)
            n8n = N8nClient(cfg, client)
            app.state.ollama, app.state.n8n = ollama, n8n
            knowledge = (
                Knowledge(cfg.knowledge_path or cfg.database_path.parent / "knowledge.sqlite3")
                if cfg.docs_enabled
                else None
            )
            app.state.knowledge = knowledge
            app.state.planner = Planner(cfg, ollama, knowledge)
            app.state.service = BuildService(repo, n8n)
            local_store = LocalStore(
                repo, cfg.local_vault_key or cfg.database_path.parent / "vault.key"
            )
            local_store.initialize()
            local_store.sensitive_values.extend([key, cfg.n8n_api_key.get_secret_value()])
            connectors = Connectors(local_store, client)
            catalog = NativeCatalog(local_store)
            catalog.initialize()
            tools = Tools(local_store, cfg, connectors, catalog, n8n, knowledge=knowledge)
            tools.load_plugins(cfg.local_plugin_modules)
            bridges = Bridges(local_store, connectors, n8n, cfg, client)
            app.state.local_store, app.state.connectors = local_store, connectors
            app.state.catalog, app.state.tools, app.state.bridges = catalog, tools, bridges
            app.state.history = history
            app.state.chat_service = ChatService(history, ollama, app.state.planner, tools=tools)
            resume_task = asyncio.create_task(bridges.resume())
            try:
                yield
            finally:
                for task in list(chat_tasks):
                    task.cancel()
                await asyncio.gather(*chat_tasks, return_exceptions=True)
                resume_task.cancel()
                await asyncio.gather(resume_task, return_exceptions=True)
                await bridges.close()
                await connectors.close()

    app = FastAPI(title="BisCode", version=__version__, lifespan=lifespan)
    app.add_middleware(BodyLimit)
    bearer = HTTPBearer(auto_error=False)

    async def authenticate(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        if (
            credentials is None
            or credentials.scheme.lower() != "bearer"
            or not secrets.compare_digest(
                credentials.credentials.encode(), cfg.master_api_key.get_secret_value().encode()
            )
        ):
            raise HTTPException(401, "Invalid API key", headers={"WWW-Authenticate": "Bearer"})

    auth = [Depends(authenticate)]
    app.include_router(local_router(authenticate))

    web = Path(__file__).parent / "web"
    app.mount("/assets", RevalidatedStaticFiles(directory=web), name="assets")

    @app.get("/api/v1/knowledge/status", dependencies=[Depends(authenticate)])
    async def knowledge_status():
        return app.state.knowledge.status() if app.state.knowledge else {"enabled": False}

    @app.get("/api/v1/knowledge/search", dependencies=[Depends(authenticate)])
    async def knowledge_search(query: str, target: Literal["python", "n8n"], limit: int = 4):
        return {
            "results": app.state.knowledge.search(query[:2000], target, limit)
            if app.state.knowledge
            else []
        }

    @app.post("/api/v1/knowledge/sync", dependencies=[Depends(authenticate)])
    async def knowledge_sync(
        family: Literal["langchain", "langgraph", "llamaindex", "n8n"] | None = None,
    ):
        if not app.state.knowledge:
            raise HTTPException(409, "Documentation retrieval is disabled")
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            try:
                return await app.state.knowledge.sync(client, family)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc

    @app.get("/", include_in_schema=False)
    async def home():
        return FileResponse(
            web / "index.html",
            headers={
                "Cache-Control": "no-cache",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
                    "frame-ancestors 'none'; form-action 'self'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/v1/ui/config", dependencies=auth)
    async def ui_config():
        return {
            "model": cfg.ollama_model,
            "n8n_editor_url": cfg.n8n_base_url,
            "http_allowed_hosts": cfg.http_allowed_hosts,
            "version": __version__,
        }

    @app.get("/api/v1/chats", dependencies=auth)
    async def chats():
        return {"chats": history.list()}

    @app.post("/api/v1/chats", dependencies=auth, status_code=201)
    async def new_chat(body: ChatOptions | None = None):
        return history.create(body.target if body else "auto")

    @app.put("/api/v1/chats/{chat_id}/target", dependencies=auth)
    async def chat_target(chat_id: str, body: ChatOptions):
        return history.set_target(chat_id, body.target)

    @app.put("/api/v1/chats/{chat_id}/pin", dependencies=auth)
    async def pin_chat(chat_id: str, body: ChatPinRequest):
        return history.set_pinned(chat_id, body.pinned)

    @app.delete("/api/v1/chats/{chat_id}", dependencies=auth)
    async def delete_chat(chat_id: str):
        history.delete(chat_id)
        return {"deleted": True, "id": chat_id}

    @app.post("/api/v1/chats/{chat_id}/resume", dependencies=auth, response_model=None)
    async def resume_chat(chat_id: str, body: ResumeRequest, request: Request):
        record = request.app.state.local_store.get("access_requests", str(body.access_request_id))
        if record["chat_id"] != chat_id or record["status"] != "approved":
            raise StateConflict("This task has no approved access request")
        chat = history.get(chat_id)
        user = next((m for m in chat["messages"] if m["id"] == record["message_id"]), None)
        if not user:
            raise StateConflict("Original task is no longer in this conversation context")
        request_id = uuid5(NAMESPACE_URL, "biscode-resume:" + record["id"])
        replay = history.find_turn(chat_id, str(request_id), user["content"])
        if replay:
            return {"replayed": True, **replay}
        latest = next(m for m in reversed(chat["messages"]) if m["role"] == "user")
        if latest["id"] != user["id"] or chat.get("target", "auto") != record.get("target", "auto"):
            raise StateConflict("The task or build target changed; request fresh access")
        if request.app.state.tools.permission(chat_id)["mode"] == "off":
            raise StateConflict("Access expired or was revoked; approve a fresh request")
        return await chat_message(
            chat_id, ChatMessageRequest(content=user["content"], request_id=request_id), request
        )

    @app.get("/api/v1/chats/{chat_id}", dependencies=auth)
    async def get_chat(chat_id: str):
        return history.get(chat_id)

    @app.post("/api/v1/chats/{chat_id}/messages", dependencies=auth, response_model=None)
    async def chat_message(chat_id: str, body: ChatMessageRequest, request: Request):
        history.get(chat_id)
        content = body.content.strip()
        request_id = str(body.request_id)
        previous = history.find_turn(chat_id, request_id, content)
        if previous:
            return {"replayed": True, **previous}
        if planner_lock.locked():
            raise HTTPException(429, "Local model is busy; retry after the current request")
        await planner_lock.acquire()
        try:
            turn = history.start_turn(chat_id, request_id, content)
        except Exception:
            planner_lock.release()
            raise
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(event: dict) -> None:
            # LangGraph runs synchronous validation/compilation nodes in a thread pool.
            loop.call_soon_threadsafe(queue.put_nowait, event)

        async def process() -> None:
            try:
                await request.app.state.chat_service.run(chat_id, turn["assistant"]["id"], emit)
            finally:
                planner_lock.release()
                turn_tasks.pop(chat_id, None)

        task = asyncio.create_task(process())
        chat_tasks.add(task)
        turn_tasks[chat_id] = task
        task.add_done_callback(chat_tasks.discard)

        async def stream():
            yield "data: " + json.dumps({"type": "started", **turn}, ensure_ascii=False) + "\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                if event["type"] == "done":
                    break

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/v1/chats/{chat_id}/cancel", dependencies=auth)
    async def cancel_chat(chat_id: str):
        history.get(chat_id)
        task = turn_tasks.get(chat_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return history.get(chat_id)

    @app.exception_handler(NotFound)
    async def missing(_request: Request, exc: NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            {
                "detail": [
                    {k: v for k, v in error.items() if k in {"loc", "msg", "type"}}
                    for error in exc.errors()
                ]
            },
            status_code=422,
        )

    @app.exception_handler(StateConflict)
    async def conflict(_request: Request, exc: StateConflict):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(InvalidIR)
    async def invalid(_request: Request, exc: InvalidIR):
        return JSONResponse({"detail": exc.report.model_dump()}, status_code=422)

    @app.exception_handler(PlanningFailed)
    async def failed(_request: Request, exc: PlanningFailed):
        return JSONResponse(
            {"detail": {"errors": exc.errors, "attempts": exc.attempts}}, status_code=422
        )

    @app.exception_handler(IntegrationError)
    async def unavailable(_request: Request, exc: IntegrationError):
        return JSONResponse(
            {"detail": {"service": exc.service, "code": exc.code, "message": str(exc)}},
            status_code=503,
        )

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/system", dependencies=auth)
    async def system(request: Request):
        result: dict = {"model": cfg.ollama_model}
        try:
            names = await request.app.state.ollama.models()
            result["ollama"] = {
                "reachable": True,
                "model_installed": cfg.ollama_model in names,
                "models": names,
            }
        except IntegrationError as exc:
            result["ollama"] = {"reachable": False, "error": exc.code}
        try:
            await request.app.state.n8n.list_workflows()
            result["n8n"] = {"reachable": True, "api_authenticated": True}
        except IntegrationError as exc:
            result["n8n"] = {"api_authenticated": False, "error": exc.code}
        return result

    @app.get("/api/v1/ir/schema", dependencies=auth)
    async def schema():
        return WorkflowIR.model_json_schema()

    @app.post("/api/v1/ir/validate", dependencies=auth)
    async def validate(ir: WorkflowIR):
        report = validate_ir(ir, policy)
        return {"valid": report.valid, **report.model_dump()}

    @app.post("/api/v1/builds/from-ir", dependencies=auth, status_code=201)
    async def from_ir(ir: WorkflowIR):
        report = require_valid(ir, policy)
        workflow = compile_ir(ir, cfg, policy)
        return repo.create(
            prompt=None,
            ir=ir.model_dump(),
            workflow=workflow,
            sha256=digest(workflow),
            attempts=0,
            warnings=[issue.model_dump() for issue in report.warnings],
        )

    @app.post("/api/v1/builds", dependencies=auth, status_code=201)
    async def build(body: BuildRequest, request: Request):
        if planner_lock.locked():
            raise HTTPException(429, "Local model is busy; retry after the current build completes")
        async with planner_lock:
            state = await request.app.state.planner.plan(body.prompt)
        return repo.create(
            prompt=body.prompt,
            ir=state["ir"].model_dump(),
            workflow=state["workflow"],
            sha256=digest(state["workflow"]),
            attempts=state["attempts"],
            warnings=state["warnings"],
        )

    @app.get("/api/v1/builds/{build_id}", dependencies=auth)
    async def get_build(build_id: str):
        return repo.get(build_id)

    @app.get("/api/v1/builds", dependencies=auth)
    async def list_builds():
        return {"builds": repo.list_summaries()}

    @app.get("/api/v1/builds/{build_id}/workflow", dependencies=auth)
    async def export_workflow(build_id: str, format: Literal["import", "api"] = "import"):
        from master_agent.compiler import create_payload

        workflow = repo.get(build_id)["workflow"]
        return create_payload(workflow) if format == "api" else workflow

    @app.get("/api/v1/builds/{build_id}/events", dependencies=auth)
    async def events(build_id: str):
        return {"events": repo.events(build_id)}

    @app.post("/api/v1/builds/{build_id}/deploy", dependencies=auth)
    async def deploy(build_id: str, request: Request):
        return await request.app.state.service.deploy(build_id)

    @app.post("/api/v1/builds/{build_id}/activation-review", dependencies=auth)
    async def review(build_id: str, request: Request):
        return await request.app.state.service.review(build_id)

    @app.post("/api/v1/builds/{build_id}/activate", dependencies=auth)
    async def activate(build_id: str, body: ActivationRequest, request: Request):
        return await request.app.state.service.activate(
            build_id,
            confirmed=body.confirm,
            workflow_sha256=body.workflow_sha256,
            review_sha256=body.review_sha256,
        )

    @app.post("/api/v1/builds/{build_id}/deactivate", dependencies=auth)
    async def deactivate(build_id: str, request: Request):
        return await request.app.state.service.deactivate(build_id)

    @app.post("/api/v1/builds/{build_id}/reconcile", dependencies=auth)
    async def reconcile(build_id: str, body: ReconcileRequest, request: Request):
        return await request.app.state.service.reconcile(build_id, body.workflow_id)

    @app.get("/api/v1/builds/{build_id}/executions", dependencies=auth)
    async def executions(build_id: str, request: Request):
        record = repo.get(build_id)
        if not record["n8n_workflow_id"]:
            raise StateConflict("Deploy the workflow before reading executions")
        return await request.app.state.n8n.executions(record["n8n_workflow_id"])

    return app


app = create_app()
