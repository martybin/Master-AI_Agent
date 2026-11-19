import asyncio
import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field, StrictBool
from starlette.types import ASGIApp, Receive, Scope, Send

from master_agent.clients import IntegrationError, N8nClient, OllamaClient
from master_agent.compiler import compile_ir, digest
from master_agent.config import Settings
from master_agent.ir import ClosedModel, WorkflowIR
from master_agent.planner import Planner, PlanningFailed
from master_agent.repository import NotFound, Repository, StateConflict
from master_agent.service import BuildService
from master_agent.validator import InvalidIR, Policy, require_valid, validate_ir


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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        key = cfg.master_api_key.get_secret_value()
        if len(key) < 32 or key == "replace-with-a-long-random-secret":
            raise RuntimeError("Generate MASTER_API_KEY first: uv run python scripts/bootstrap.py")
        repo.initialize()
        async with httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ) as client:
            ollama = OllamaClient(cfg, client)
            n8n = N8nClient(cfg, client)
            app.state.ollama, app.state.n8n = ollama, n8n
            app.state.planner = Planner(cfg, ollama)
            app.state.service = BuildService(repo, n8n)
            yield

    app = FastAPI(title="Master AI Agent Builder", version="0.1.0", lifespan=lifespan)
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

    @app.exception_handler(NotFound)
    async def missing(_request: Request, exc: NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

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
