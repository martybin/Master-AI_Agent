import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest


def chat(api):
    return api.post("/api/v1/chats").json()["id"]


def execute(api, chat_id, name, arguments):
    return api.post(
        "/api/v1/local/tools/execute",
        json={"chat_id": chat_id, "name": name, "arguments": arguments},
    )


def grant(api, chat_id, mode="workspace", confirm=False):
    return api.put("/api/v1/local/grants/" + chat_id, json={"mode": mode, "confirm": confirm})


def test_local_control_plane_is_authenticated(api):
    for path in [
        "/api/v1/local/status",
        "/api/v1/local/connections",
        "/api/v1/local/tools",
        "/api/v1/local/catalog",
    ]:
        assert api.get(path, headers={"Authorization": ""}).status_code == 401
    status = api.get("/api/v1/local/status").json()
    assert status["access"]["mode"] == "off" and status["catalog_count"] >= 12
    assert "sealed" not in json.dumps(status)


def test_workspace_grants_enforce_paths_and_expire(api, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    api.app.state.tools.settings.local_workspace = root
    cid = chat(api)
    assert (
        execute(api, cid, "files.write", {"path": "hello.txt", "content": "سلام"}).status_code
        == 409
    )
    assert grant(api, cid).status_code == 200
    assert (
        execute(
            api, cid, "files.write", {"path": "nested/hello.txt", "content": "سلام"}
        ).status_code
        == 200
    )
    assert (
        execute(api, cid, "files.read", {"path": "nested/hello.txt"}).json()["result"]["content"]
        == "سلام"
    )
    assert execute(api, cid, "files.read", {"path": "../outside.txt"}).status_code == 409
    (root / "escape").symlink_to(tmp_path)
    assert (
        execute(
            api, cid, "files.write", {"path": "escape/outside.txt", "content": "bad"}
        ).status_code
        == 409
    )
    assert not (tmp_path / "outside.txt").exists()
    assert (
        execute(api, cid, "terminal.exec", {"argv": [sys.executable, "-c", "print(1)"]}).status_code
        == 409
    )
    store = api.app.state.local_store
    current = store.get("grants", cid)
    current["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    store.put("grants", cid, current)
    assert execute(api, cid, "files.read", {"path": "nested/hello.txt"}).status_code == 409


@pytest.mark.parametrize("confirm", [False, "true", 1])
def test_system_access_needs_strict_explicit_confirmation(api, confirm):
    result = grant(api, chat(api), "system", confirm)
    assert result.status_code in {409, 422}


def test_real_subprocess_execution_and_redaction(api, tmp_path):
    api.app.state.tools.settings.local_workspace = tmp_path
    cid = chat(api)
    assert grant(api, cid, "system", True).status_code == 200
    script = (
        "from pathlib import Path; Path('proof.txt').write_text('executed'); "
        "print('real local tool')"
    )
    result = execute(api, cid, "terminal.exec", {"argv": [sys.executable, "-c", script]}).json()[
        "result"
    ]
    assert result["exit_code"] == 0 and "real local tool" in result["stdout"]
    assert (tmp_path / "proof.txt").read_text() == "executed"
    secret = api.app.state.tools.settings.master_api_key.get_secret_value()
    response = execute(
        api, cid, "terminal.exec", {"argv": [sys.executable, "-c", f"print({secret!r})"]}
    )
    assert secret not in response.text and "[REDACTED]" in response.text
    assert execute(api, cid, "files.read", {"path": str(Path(".env").resolve())}).status_code == 409


def test_subprocess_output_is_bounded(api, tmp_path):
    api.app.state.tools.settings.local_workspace = tmp_path
    cid = chat(api)
    grant(api, cid, "system", True)
    result = execute(
        api, cid, "terminal.exec", {"argv": [sys.executable, "-c", 'print("x"*200000)']}
    ).json()["result"]
    assert result["truncated"] is True and len(result["stdout"]) <= 32000


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group behavior")
def test_subprocess_timeout_kills_descendants_holding_pipes(api, tmp_path):
    api.app.state.tools.settings.local_workspace = tmp_path
    cid = chat(api)
    grant(api, cid, "system", True)
    script = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'])"
    )
    result = execute(
        api, cid, "terminal.exec", {"argv": [sys.executable, "-c", script], "timeout": 1}
    ).json()["result"]
    assert result["timed_out"] is True


def test_catalog_is_extensible_and_native_graphs_support_joins(api):
    entry = {
        "type": "custom.local.transform",
        "label": "Custom transform",
        "versions": [1],
        "parameter_schema": {
            "type": "object",
            "properties": {"mode": {"enum": ["upper", "lower"]}},
            "required": ["mode"],
            "additionalProperties": False,
        },
    }
    assert api.post("/api/v1/local/catalog", json=entry).status_code == 201
    spec = {
        "name": "Native automation",
        "nodes": [
            {"name": "Start", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1},
            {
                "name": "First",
                "type": "custom.local.transform",
                "typeVersion": 1,
                "parameters": {"mode": "upper"},
            },
            {
                "name": "Second",
                "type": "custom.local.transform",
                "typeVersion": 1,
                "parameters": {"mode": "lower"},
            },
            {"name": "Join", "type": "n8n-nodes-base.merge", "typeVersion": 3.2},
        ],
        "connections": {
            "Start": {"main": [[{"node": "First"}, {"node": "Second"}]]},
            "First": {"main": [[{"node": "Join"}]]},
            "Second": {"main": [[{"node": "Join", "index": 1}]]},
        },
    }
    result = api.post("/api/v1/local/native-builds", json=spec)
    assert result.status_code == 201, result.text
    build = api.get("/api/v1/builds/" + result.json()["build_id"]).json()
    assert build["ir"]["native_version"] == "1.0" and build["workflow"]["active"] is False
    spec["nodes"][1]["parameters"]["mode"] = "invalid"
    assert api.post("/api/v1/local/native-builds", json=spec).status_code == 409
    assert (
        api.get("/api/v1/local/catalog?query=custom.local").json()["nodes"][0]["type"]
        == "custom.local.transform"
    )


def test_catalog_rejects_remote_schema_references(api):
    entry = {
        "type": "custom.bad.node",
        "label": "bad",
        "versions": [1],
        "parameter_schema": {"$ref": "https://example.com/schema"},
    }
    assert api.post("/api/v1/local/catalog", json=entry).status_code == 409


def test_native_credential_names_are_display_metadata_but_id_drift_blocks_activation(api, network):
    spec = {
        "name": "Existing credential",
        "nodes": [
            {"name": "Start", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1},
            {
                "name": "Send",
                "type": "n8n-nodes-base.telegram",
                "typeVersion": 1.2,
                "parameters": {"chatId": 77, "text": "Reviewed text"},
                "credentials": {"telegramApi": "existing-credential-id"},
            },
        ],
        "connections": {"Start": {"main": [[{"node": "Send"}]]}},
    }
    response = api.post("/api/v1/local/native-builds", json=spec)
    build_id = response.json()["build_id"]
    assert api.post(f"/api/v1/builds/{build_id}/deploy").status_code == 200
    credentials = network.remote["nodes"][1]["credentials"]["telegramApi"]
    credentials["name"] = "Owner's bot"
    response = api.post(f"/api/v1/builds/{build_id}/activation-review")
    assert response.status_code == 200, response.text
    review = response.json()
    credentials["id"] = "different-credential-id"
    response = api.post(
        f"/api/v1/builds/{build_id}/activate",
        json={
            "confirm": True,
            "workflow_sha256": review["workflow_sha256"],
            "review_sha256": review["review_sha256"],
        },
    )
    assert response.status_code == 409
    assert all(not call.url.path.endswith("/activate") for call in network.calls)


def test_native_ai_model_memory_and_tools_attach_into_reachable_agent(api):
    # n8n AI connections point from auxiliary nodes into the agent, opposite the
    # trigger's execution path. A forward-only reachability check rejects them.
    for node_type in ["agent", "lmChatOllama", "memoryBufferWindow", "toolCalculator"]:
        response = api.post(
            "/api/v1/local/catalog",
            json={
                "type": "@n8n/n8n-nodes-langchain." + node_type,
                "label": node_type,
                "versions": [1],
                "outputs": 1,
            },
        )
        assert response.status_code == 201
    spec = {
        "name": "Agent with attached capabilities",
        "nodes": [
            {"name": "Start", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1},
            *[
                {
                    "name": name,
                    "type": "@n8n/n8n-nodes-langchain." + kind,
                    "typeVersion": 1,
                }
                for name, kind in [
                    ("Agent", "agent"),
                    ("Model", "lmChatOllama"),
                    ("Memory", "memoryBufferWindow"),
                    ("Calculator", "toolCalculator"),
                ]
            ],
        ],
        "connections": {
            "Start": {"main": [[{"node": "Agent"}]]},
            **{
                name: {channel: [[{"node": "Agent", "type": channel}]]}
                for name, channel in [
                    ("Model", "ai_languageModel"),
                    ("Memory", "ai_memory"),
                    ("Calculator", "ai_tool"),
                ]
            },
        },
    }
    response = api.post("/api/v1/local/native-builds", json=spec)
    assert response.status_code == 201, response.text
    first = api.get("/api/v1/builds/" + response.json()["build_id"]).json()["workflow"]
    response = api.post("/api/v1/local/native-builds", json=spec)
    second = api.get("/api/v1/builds/" + response.json()["build_id"]).json()["workflow"]
    assert first == second and first["active"] is False
    assert first["connections"]["Memory"]["ai_memory"][0][0]["node"] == "Agent"
    del spec["connections"]["Memory"]
    assert api.post("/api/v1/local/native-builds", json=spec).status_code == 409


def test_import_n8n_export_requires_grant_and_validates_metadata(api, tmp_path):
    api.app.state.tools.settings.local_workspace = tmp_path
    metadata = [
        {
            "name": "n8n-nodes-base.testApi",
            "displayName": "Test API",
            "version": [1, 2],
            "group": ["transform"],
            "inputs": ["main"],
            "outputs": ["main"],
            "properties": [{"name": "endpoint", "type": "string", "required": True}],
        }
    ]
    (tmp_path / "nodes.json").write_text(json.dumps(metadata))
    cid = chat(api)
    assert execute(api, cid, "catalog.import_file", {"path": "nodes.json"}).status_code == 409
    grant(api, cid)
    assert (
        execute(api, cid, "catalog.import_file", {"path": "nodes.json"}).json()["result"][
            "imported"
        ]
        == 1
    )
    description = api.get("/api/v1/local/catalog/describe?node_type=n8n-nodes-base.testApi").json()
    assert "endpoint" in description["parameter_schema"]["required"]


def test_chat_can_execute_tools_and_create_native_draft(api, network, tmp_path):
    api.app.state.tools.settings.local_workspace = tmp_path
    cid = chat(api)
    spec = {
        "name": "From tool",
        "nodes": [{"name": "Start", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1}],
        "connections": {},
    }
    network.chat_outputs = [
        json.dumps(
            {
                "action": "tool",
                "message": "بررسی سیستم",
                "tool_name": "system.inspect",
                "tool_arguments": {},
            }
        ),
        json.dumps(
            {
                "action": "tool",
                "message": "ساخت طرح",
                "tool_name": "workflow.create_native",
                "tool_arguments": spec,
            }
        ),
    ]
    response = api.post(
        "/api/v1/chats/" + cid + "/messages",
        json={"content": "یک Workflow دستی بساز", "request_id": str(uuid4())},
    )
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert any(event.get("tool") == "system.inspect" for event in events)
    complete = next(event for event in events if event["type"] == "complete")
    assert complete["build"]["ir"]["name"] == "From tool"
    assert complete["build"]["workflow"]["active"] is False
    assert all(not call.url.path.endswith("/activate") for call in network.calls)


def test_completion_review_continues_when_model_claims_an_unexecuted_action(api, network, tmp_path):
    api.app.state.tools.settings.local_workspace = tmp_path
    cid = chat(api)
    grant(api, cid)
    network.chat_outputs = [
        json.dumps({"action": "tool", "message": "بررسی", "tool_name": "system.inspect"}),
        json.dumps({"action": "reply", "message": "فایل نوشته شد."}),
        json.dumps(
            {
                "action": "tool",
                "message": "نوشتن فایل",
                "tool_name": "files.write",
                "tool_arguments": {"path": "proof.txt", "content": "REAL_RESULT"},
            }
        ),
        json.dumps({"action": "reply", "message": "فایل نوشته شد."}),
    ]
    network.review_outputs = [
        {"status": "incomplete", "missing": ["نوشتن فایل proof.txt"]},
        {"status": "complete", "missing": []},
    ]
    response = api.post(
        f"/api/v1/chats/{cid}/messages",
        json={"content": "سیستم را بررسی کن و فایل proof.txt بساز", "request_id": str(uuid4())},
    )
    assert network.review_calls == 2 and network.chat_calls == 4
    assert (tmp_path / "proof.txt").read_text() == "REAL_RESULT"
    assert '"type": "complete"' in response.text
    review_requests = [
        json.loads(call.content)
        for call in network.calls
        if call.url.path == "/api/chat"
        and json.loads(call.content)["format"].get("title") == "CompletionReview"
    ]
    final_evidence = json.loads(review_requests[-1]["messages"][-1]["content"])
    assert final_evidence["observations"][-1]["arguments"]["path"] == "proof.txt"
    decisions = [
        json.loads(call.content)
        for call in network.calls
        if call.url.path == "/api/chat"
        and json.loads(call.content)["format"].get("title") == "ChatDecision"
    ]
    assert decisions[2]["format"]["properties"]["action"]["enum"] == ["tool"]


def test_failed_completion_correction_never_accepts_another_unexecuted_success(api, network):
    cid = chat(api)
    network.chat_outputs = [
        json.dumps({"action": "tool", "message": "بررسی", "tool_name": "system.inspect"}),
        json.dumps({"action": "reply", "message": "فایل نوشته شد."}),
    ]
    network.review_outputs = [{"status": "incomplete", "missing": ["نوشتن فایل"]}]
    response = api.post(
        f"/api/v1/chats/{cid}/messages",
        json={"content": "یک فایل بساز", "request_id": str(uuid4())},
    )
    assert '"type": "failed"' in response.text
    assert '"type": "complete"' not in response.text
    assert network.chat_calls == 4


@pytest.mark.parametrize("failure", ["timeout", "server_error"])
def test_uncertain_http_mutation_stops_chat_without_model_retry(api, network, failure):
    calls = []

    def remote(request):
        if request.url.host == "mutation.example.com":
            calls.append(request)
            if failure == "timeout":
                raise httpx.ReadTimeout("uncertain mutation")
            return httpx.Response(500, json={"error": "unknown outcome"})

    network.extra_handler = remote
    response = api.post(
        "/api/v1/local/connections",
        json={
            "id": "external",
            "label": "API",
            "kind": "http",
            "base_url": "https://mutation.example.com",
        },
    )
    assert response.status_code == 201
    cid = chat(api)
    grant(api, cid)
    network.chat_outputs = [
        json.dumps(
            {
                "action": "tool",
                "message": "درخواست ثبت",
                "tool_name": "http.request",
                "tool_arguments": {
                    "connection": "external",
                    "method": "POST",
                    "path": "/records",
                    "body": {"x": 1},
                },
            }
        )
    ]
    response = api.post(
        f"/api/v1/chats/{cid}/messages",
        json={"content": "رکورد را در API ثبت کن", "request_id": str(uuid4())},
    )
    assert len(calls) == 1 and network.chat_calls == 1
    assert "uncertain outcome" in response.text


def test_tool_budget_is_bounded(api, network):
    network.chat_outputs = [
        json.dumps(
            {
                "action": "tool",
                "message": "بررسی",
                "tool_name": "catalog.search",
                "tool_arguments": {"query": f"integration_{index}"},
            }
        )
        for index in range(api.app.state.tools.settings.local_max_steps)
    ]
    cid = chat(api)
    response = api.post(
        "/api/v1/chats/" + cid + "/messages",
        json={"content": "سیستم را بررسی کن", "request_id": str(uuid4())},
    )
    assert network.chat_calls == api.app.state.tools.settings.local_max_steps
    assert "planning_failed" in response.text


@pytest.mark.asyncio
async def test_cancelled_deploy_is_marked_unknown_immediately(settings, ir):
    import httpx

    from master_agent.clients import N8nClient
    from master_agent.compiler import compile_ir, digest
    from master_agent.repository import Repository
    from master_agent.service import BuildService

    repo = Repository(settings.database_path)
    repo.initialize()
    workflow = compile_ir(ir, settings)
    record = repo.create(
        prompt=None,
        ir=ir.model_dump(),
        workflow=workflow,
        sha256=digest(workflow),
        warnings=[],
        attempts=0,
    )
    async with httpx.AsyncClient() as client:
        n8n = N8nClient(settings, client)

        async def cancelled(_):
            raise asyncio.CancelledError()

        n8n.create_workflow = cancelled
        with pytest.raises(asyncio.CancelledError):
            await BuildService(repo, n8n).deploy(record["id"])
    assert repo.get(record["id"])["status"] == "deployment_unknown"


def test_plugin_registration_is_extensible_but_cannot_duplicate_tools(api, tmp_path, monkeypatch):
    module = tmp_path / "demo_local_plugin.py"
    module.write_text(
        "from master_agent.ir import ClosedModel\n"
        "class Args(ClosedModel):\n    text: str\n"
        "def register(registry):\n"
        '    registry.register("demo.upper", "Uppercase text", Args, "workspace",\n'
        '                      lambda args: {"value": args.text.upper()})\n'
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    api.app.state.tools.load_plugins(["demo_local_plugin"])
    cid = chat(api)
    assert execute(api, cid, "demo.upper", {"text": "hello"}).status_code == 409
    grant(api, cid)
    assert execute(api, cid, "demo.upper", {"text": "hello"}).json()["result"] == {"value": "HELLO"}
    with pytest.raises(ValueError):
        api.app.state.tools.load_plugins(["demo_local_plugin"])


@pytest.mark.asyncio
async def test_cancelling_chat_stops_inflight_local_command(settings, network, tmp_path):
    import httpx

    from master_agent.api import create_app

    settings.local_workspace = tmp_path
    code = (
        "import os,time;from pathlib import Path;"
        "Path('running.pid').write_text(str(os.getpid()));time.sleep(20)"
    )
    network.chat_outputs = [
        json.dumps(
            {
                "action": "tool",
                "message": "اجرای تست",
                "tool_name": "terminal.exec",
                "tool_arguments": {"argv": [sys.executable, "-c", code]},
            }
        )
    ]
    app = create_app(settings, httpx.MockTransport(network))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://test",
            headers={"Authorization": "Bearer " + settings.master_api_key.get_secret_value()},
        ) as client:
            cid = (await client.post("/api/v1/chats")).json()["id"]
            await client.put(
                "/api/v1/local/grants/" + cid, json={"mode": "system", "confirm": True}
            )
            task = asyncio.create_task(
                client.post(
                    "/api/v1/chats/" + cid + "/messages",
                    json={"content": "فرمان آزمایشی را اجرا کن", "request_id": str(uuid4())},
                )
            )
            pid_file = tmp_path / "running.pid"
            for _ in range(100):
                if await asyncio.to_thread(pid_file.exists):
                    break
                await asyncio.sleep(0.02)
            assert await asyncio.to_thread(pid_file.exists)
            response = await client.post("/api/v1/chats/" + cid + "/cancel")
            assert (
                response.status_code == 200
                and response.json()["messages"][-1]["error_code"] == "interrupted"
            )
            await asyncio.wait_for(task, timeout=5)
            pid = int(await asyncio.to_thread(pid_file.read_text))
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)


def test_english_catalog_keeps_persian_lookup_and_owner_labels(api):
    state = api.app.state
    key = "n8n-nodes-base.scheduleTrigger"
    original = state.local_store.get("catalog", key)
    state.local_store.put("catalog", key, {**original, "label": "زمان‌بندی", "source": "builtin"})
    state.catalog.initialize()
    assert state.local_store.get("catalog", key)["label"] == "Schedule"
    assert any(row["type"] == key for row in state.catalog.search("زمان بندی"))
    assert any(row["type"] == key for row in state.catalog.search("schedule"))
    state.local_store.put("catalog", key, {**original, "label": "گره سفارشی من", "source": "owner"})
    state.catalog.initialize()
    assert state.local_store.get("catalog", key)["label"] == "گره سفارشی من"
