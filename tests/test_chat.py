import asyncio
import copy
import json
from uuid import uuid4

import httpx
import pytest

from master_agent.api import create_app
from master_agent.chat_repository import ChatRepository
from master_agent.chat_service import ChatDecision
from master_agent.clients import IntegrationError
from master_agent.planner import EXAMPLE
from master_agent.repository import Repository


def events(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


def new_chat(api):
    response = api.post("/api/v1/chats")
    assert response.status_code == 201
    return response.json()["id"]


def send(api, chat_id, content="یک دستیار محلی با Webhook بساز", request_id=None):
    return api.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={
            "content": content,
            "request_id": request_id or str(uuid4()),
        },
    )


def test_ui_is_public_but_never_embeds_keys(api, settings, network):
    page = api.get("/", headers={"Authorization": ""})
    assert page.status_code == 200
    assert "ایده‌ات را بگو" in page.text
    assert settings.master_api_key.get_secret_value() not in page.text
    assert "script-src" in page.headers["content-security-policy"]
    for path in [
        "/assets/app.js",
        "/assets/app.css",
        "/assets/ui-utils.mjs",
        "/assets/fonts/Vazirmatn.woff2",
    ]:
        response = api.get(path)
        assert response.status_code == 200
        assert settings.master_api_key.get_secret_value().encode() not in response.content
    assert api.get("/assets/../config.py").status_code == 404
    assert not network.calls


def test_conversations_and_configuration_require_owner_auth(api):
    for path in ["/api/v1/chats", "/api/v1/ui/config", "/api/v1/builds"]:
        assert api.get(path, headers={"Authorization": ""}).status_code == 401
    assert api.post("/api/v1/chats", headers={"Authorization": ""}).status_code == 401
    config = api.get("/api/v1/ui/config").json()
    assert config["model"] == "qwen3.5:9b"
    assert not any("key" in key.lower() for key in config)


def test_greeting_replies_without_build_or_n8n_side_effect(api, network):
    network.chat_outputs = [json.dumps({"action": "reply", "message": "سلام! چه ایجنتی بسازیم؟"})]
    chat = new_chat(api)
    stream = events(send(api, chat, "سلام، خوبی؟"))
    complete = next(event for event in stream if event["type"] == "complete")
    assert complete["message"]["content"].startswith("سلام")
    assert complete["build"] is None
    assert api.get("/api/v1/builds").json()["builds"] == []
    record = api.get(f"/api/v1/chats/{chat}").json()
    assert record["status"] == "idle" and len(record["messages"]) == 2
    assert all(call.url.path in {"/api/chat", "/api/tags"} for call in network.calls)


def test_chat_build_streams_actual_pipeline_and_persists_draft(api, network):
    chat = new_chat(api)
    stream = events(send(api, chat))
    stages = [event["stage"] for event in stream if event["type"] == "stage"]
    assert stages == ["thinking", "planning", "validating", "compiling"]
    complete = next(event for event in stream if event["type"] == "complete")
    assert complete["build"]["status"] == "draft"
    assert complete["build"]["workflow"]["active"] is False
    assert stream[-1]["type"] == "done"
    history = api.get(f"/api/v1/chats/{chat}").json()
    assert history["messages"][-1]["build_id"] == complete["build"]["id"]
    assert history["title"] == complete["build"]["ir"]["name"]
    assert api.get("/api/v1/builds").json()["builds"][0]["node_count"] == 3
    assert all(not call.url.path.startswith("/api/v1") for call in network.calls)


def test_follow_up_has_previous_ir_and_full_latest_request(api, network):
    chat = new_chat(api)
    first = next(event["build"] for event in events(send(api, chat)) if event["type"] == "complete")
    changed = copy.deepcopy(EXAMPLE)
    changed["name"] = "Revised assistant"
    changed["nodes"][1]["system_prompt"] = "Reply briefly in Persian."
    network.plan_outputs = [json.dumps(EXAMPLE), json.dumps(changed)]
    content = "کمی دقیق‌ترش کن. " * 100 + "UNIQUE_LATEST_MESSAGE_END"
    stream = events(send(api, chat, content))
    latest = next(event["build"] for event in stream if event["type"] == "complete")
    assert latest["id"] != first["id"] and latest["ir"]["name"] == "Revised assistant"
    planning = [
        json.loads(call.content)
        for call in network.calls
        if call.url.path == "/api/chat"
        and json.loads(call.content)["format"]["title"] == "WorkflowIR"
    ]
    prompt = planning[-1]["messages"][1]["content"]
    assert "previous IR" in prompt and first["ir"]["name"] in prompt
    assert "UNIQUE_LATEST_MESSAGE_END" in prompt
    assert len(api.get(f"/api/v1/chats/{chat}").json()["messages"]) == 4
    assert all(not call.url.path.endswith("/activate") for call in network.calls)


def test_duplicate_request_replays_without_another_inference(api, network):
    chat, request_id = new_chat(api), str(uuid4())
    events(send(api, chat, request_id=request_id))
    count = len(network.calls)
    replay = send(api, chat, request_id=request_id)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert len(network.calls) == count
    assert send(api, chat, "درخواست متفاوت برای همین شناسه", request_id).status_code == 409
    assert len(api.get(f"/api/v1/chats/{chat}").json()["messages"]) == 2


@pytest.mark.parametrize("content", ["", "   ", "x" * 6001])
def test_invalid_messages_never_call_model(api, network, content):
    assert send(api, new_chat(api), content).status_code == 422
    assert not network.calls


def test_unknown_chat_is_not_created_implicitly(api, network):
    assert send(api, str(uuid4())).status_code == 404
    assert not network.calls


def test_failed_plan_persists_error_and_releases_chat(api, network):
    network.plan_outputs = ["{}"]
    chat = new_chat(api)
    stream = events(send(api, chat))
    failed = next(event["message"] for event in stream if event["type"] == "failed")
    assert failed["status"] == "failed" and failed["error_code"] == "planning_failed"
    assert api.get(f"/api/v1/chats/{chat}").json()["status"] == "idle"
    network.plan_outputs = [json.dumps(EXAMPLE)]
    assert any(
        event["type"] == "complete" for event in events(send(api, chat, "یک دستیار ساده بساز"))
    )


def test_upstream_failure_is_saved_without_exposing_body(api, network, monkeypatch):
    async def failed(*args, **kwargs):
        raise IntegrationError("ollama", "generation_failed", "PRIVATE_UPSTREAM_DETAIL")

    monkeypatch.setattr(api.app.state.chat_service.ollama, "generate", failed)
    chat = new_chat(api)
    response = send(api, chat)
    assert "PRIVATE_UPSTREAM_DETAIL" not in response.text
    assert any(event["type"] == "failed" for event in events(response))
    assert api.get(f"/api/v1/chats/{chat}").json()["status"] == "idle"


def test_chat_history_survives_repository_restart(settings):
    repo = Repository(settings.database_path)
    repo.initialize()
    history = ChatRepository(repo)
    history.initialize()
    chat = history.create()
    turn = history.start_turn(chat["id"], str(uuid4()), "سلام")
    history.finish_turn(chat["id"], turn["assistant"]["id"], "سلام، چه بسازیم؟")
    restarted = ChatRepository(repo)
    restarted.initialize()
    assert restarted.get(chat["id"])["messages"][-1]["content"] == "سلام، چه بسازیم؟"


def test_interrupted_turn_recovers_as_failed_not_duplicate_build(settings):
    repo = Repository(settings.database_path)
    repo.initialize()
    history = ChatRepository(repo)
    history.initialize()
    chat = history.create()
    history.start_turn(chat["id"], str(uuid4()), "بساز")
    history.initialize()
    recovered = history.get(chat["id"])
    assert recovered["status"] == "idle"
    assert recovered["messages"][-1]["error_code"] == "interrupted"
    assert repo.list_summaries() == []


@pytest.mark.asyncio
async def test_chat_and_old_build_api_share_inference_lock(settings, network):
    app = create_app(settings, httpx.MockTransport(network))
    entered, release = asyncio.Event(), asyncio.Event()
    async with app.router.lifespan_context(app):

        async def slow_decide(*_):
            entered.set()
            await release.wait()
            return ChatDecision(action="reply", message="سلام")

        app.state.chat_service.decide = slow_decide
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://test",
            headers={"Authorization": "Bearer " + settings.master_api_key.get_secret_value()},
        ) as client:
            chat = (await client.post("/api/v1/chats")).json()["id"]
            active = asyncio.create_task(
                client.post(
                    f"/api/v1/chats/{chat}/messages",
                    json={"content": "سلام", "request_id": str(uuid4())},
                )
            )
            await entered.wait()
            blocked = await client.post(
                "/api/v1/builds", json={"prompt": "Build a local assistant"}
            )
            assert blocked.status_code == 429
            release.set()
            assert (await active).status_code == 200
