import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from master_agent.api import create_app
from master_agent.config import Settings
from master_agent.ir import WorkflowIR
from master_agent.planner import EXAMPLE

API_KEY = "test-master-key-" + "a" * 40


@pytest.fixture
def ir():
    return WorkflowIR.model_validate(copy.deepcopy(EXAMPLE))


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        master_api_key=API_KEY,
        n8n_api_key="test-n8n-secret",
        ollama_model="qwen3.5:9b",
        database_path=tmp_path / "test.sqlite3",
        http_allowed_hosts=["httpbin.org"],
    )


class FakeNetwork:
    def __init__(self):
        self.calls = []
        self.remote = None
        self.plan_outputs = [json.dumps(EXAMPLE)]
        self.chat_outputs = [json.dumps({"action": "build", "message": "طرح را آماده می‌کنم."})]
        self.chat_calls = 0
        self.review_calls = 0
        self.review_outputs = [{"status": "complete", "missing": []}]
        self.fail_create = False
        self.fail_activate = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if handler := getattr(self, "extra_handler", None):
            result = handler(request)
            if result is not None:
                return result
        path, method = request.url.path, request.method
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3.5:9b"}]})
        if path == "/api/chat":
            body = json.loads(request.content)
            assert body["stream"] is False and body["think"] is False
            assert body["format"]["type"] == "object"
            if body["format"].get("title") == "ChatDecision":
                content = self.chat_outputs[min(self.chat_calls, len(self.chat_outputs) - 1)]
                self.chat_calls += 1
                return httpx.Response(200, json={"message": {"content": content}})
            if body["format"].get("title") == "CompletionReview":
                content = self.review_outputs[min(self.review_calls, len(self.review_outputs) - 1)]
                self.review_calls += 1
                return httpx.Response(200, json={"message": {"content": json.dumps(content)}})
            index = min(
                sum(
                    call.url.path == "/api/chat"
                    and json.loads(call.content)["format"].get("title") == "WorkflowIR"
                    for call in self.calls
                )
                - 1,
                len(self.plan_outputs) - 1,
            )
            return httpx.Response(200, json={"message": {"content": self.plan_outputs[index]}})
        assert request.headers["X-N8N-API-KEY"] == "test-n8n-secret"
        if path == "/api/v1/workflows" and method == "POST":
            body = json.loads(request.content)
            assert set(body) == {"name", "nodes", "connections", "settings"}
            self.remote = {**body, "id": "wf_1", "active": False, "versionId": "v1"}
            if self.fail_create:
                raise httpx.ReadTimeout("Ambiguous create outcome", request=request)
            return httpx.Response(201, json=self.remote)
        if path == "/api/v1/workflows/wf_1" and method == "GET":
            return httpx.Response(200, json=copy.deepcopy(self.remote))
        if path == "/api/v1/workflows/wf_1/activate":
            assert json.loads(request.content) == {"versionId": self.remote["versionId"]}
            self.remote["active"] = True
            if self.fail_activate:
                raise httpx.ReadTimeout("Ambiguous activation outcome", request=request)
            return httpx.Response(200, json=self.remote)
        if path == "/api/v1/workflows/wf_1/deactivate":
            self.remote["active"] = False
            return httpx.Response(200, json=self.remote)
        if path == "/api/v1/workflows":
            return httpx.Response(200, json={"data": [], "nextCursor": None})
        if path == "/api/v1/executions":
            return httpx.Response(200, json={"data": [], "nextCursor": None})
        return httpx.Response(404, json={"message": "Not found"})


@pytest.fixture
def network():
    return FakeNetwork()


@pytest.fixture
def api(settings, network):
    app = create_app(settings, httpx.MockTransport(network))
    with TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"}) as client:
        yield client
