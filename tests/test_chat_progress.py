import json
from uuid import uuid4

from master_agent.local.chat_policy import personal_birthday_request

BIRTHDAY_REQUEST = (
    "خب میخام که برام یه اتوماسیون تلگرام طراحی کنی که بتونم بیام و به تمام "
    "کانتکتام روز تولدشون تبریک ارسال کنم"
)


def tool(name, arguments=None):
    return json.dumps(
        {
            "action": "tool",
            "message": "بررسی درخواست",
            "tool_name": name,
            "tool_arguments": arguments or {},
        }
    )


def send(api, content):
    cid = api.post("/api/v1/chats").json()["id"]
    response = api.post(
        f"/api/v1/chats/{cid}/messages",
        json={"content": content, "request_id": str(uuid4())},
    )
    assert response.status_code == 200
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    return cid, events


def tool_calls(api, cid):
    return [
        row["tool"]
        for row in reversed(api.app.state.local_store.audits())
        if row.get("chat_id") == cid and row["action"] == "tool.start"
    ]


def personal_connection(api):
    response = api.post(
        "/api/v1/local/connections",
        json={
            "id": "personal",
            "label": "Test only",
            "kind": "telegram_user",
            "api_id": 123,
            "api_hash": "TEST_ONLY_HASH_NOT_A_REAL_LOGIN",
        },
    )
    assert response.status_code == 201


def test_exact_birthday_request_stops_at_real_missing_inputs_without_model_loop(api, network):
    network.chat_outputs = [tool("connections.list")]
    cid, events = send(api, BIRTHDAY_REQUEST)
    complete = next(e for e in events if e["type"] == "complete")
    assert complete["build"] is None
    assert complete["message"]["error_code"] is None
    text = complete["message"]["content"]
    assert "اکانت شخصی" in text and "CSV/JSON" in text
    assert "طرح پیشنهادی" in text and "تکراری" in text and "مخفی" in text
    assert "تعداد مراحل" not in text
    assert tool_calls(api, cid) == ["connections.list"]
    assert network.chat_calls == network.review_calls == 0
    assert not network.calls
    saved = api.get(f"/api/v1/chats/{cid}").json()
    assert saved["status"] == "idle" and saved["messages"][-1]["content"] == text
    assert api.get("/api/v1/builds").json()["builds"] == []


def test_general_empty_connection_discovery_requests_configuration_once(api, network):
    network.chat_outputs = [tool("connections.list")]
    cid, events = send(api, "یک بات تلگرام برای پاسخ کوتاه بساز")
    result = next(e for e in events if e["type"] == "complete")
    assert "هیچ اتصالی ثبت نشده" in result["message"]["content"]
    assert tool_calls(api, cid) == ["connections.list"]
    assert network.chat_calls == 1 and network.review_calls == 0


def test_bot_does_not_replace_personal_contact_access(api, network):
    api.post(
        "/api/v1/local/connections",
        json={"id": "bot", "label": "Test bot", "kind": "telegram_bot", "token": "FAKE_BOT_TOKEN"},
    ).raise_for_status()
    _, events = send(api, BIRTHDAY_REQUEST)
    text = next(e for e in events if e["type"] == "complete")["message"]["content"]
    assert "بات به فهرست مخاطبان حساب شخصی دسترسی ندارد" in text
    assert network.chat_calls == 0


def test_named_birthday_source_is_preserved_while_requesting_missing_login(api, network):
    personal_connection(api)
    _, events = send(api, BIRTHDAY_REQUEST + "؛ تاریخ‌ها در birthdays.csv هستند.")
    text = next(e for e in events if e["type"] == "complete")["message"]["content"]
    assert "ورودش کامل نشده" in text
    assert "تاریخ تولد مخاطب‌ها را" not in text
    assert network.chat_calls == 0


def test_connected_personal_account_still_needs_birthday_source(api, network):
    personal_connection(api)
    store = api.app.state.local_store
    record = store.get("connections", "personal")
    store.put("connections", "personal", {**record, "status": "connected"})
    _, events = send(api, BIRTHDAY_REQUEST)
    text = next(e for e in events if e["type"] == "complete")["message"]["content"]
    assert "CSV/JSON" in text and "اکانت شخصی را اضافه کن" not in text
    assert network.chat_calls == 0


def test_repeated_identical_read_executes_once_and_stops_before_budget(api, network):
    network.chat_outputs = [tool("system.inspect")]
    cid, events = send(api, "وضعیت سیستم را بررسی کن")
    failed = next(e for e in events if e["type"] == "failed")
    assert "same input" in failed["message"]["content"]
    assert "تعداد مراحل" not in failed["message"]["content"]
    assert tool_calls(api, cid) == ["system.inspect"]
    assert network.chat_calls == 3


def test_model_can_recover_from_duplicate_read_using_existing_observation(api, network):
    network.chat_outputs = [
        tool("system.inspect"),
        tool("system.inspect"),
        json.dumps({"action": "reply", "message": "وضعیت سیستم بررسی شد."}),
    ]
    cid, events = send(api, "وضعیت سیستم را بررسی کن")
    assert any(e["type"] == "complete" for e in events)
    assert tool_calls(api, cid) == ["system.inspect"]
    assert network.chat_calls == 3


def test_read_can_run_again_after_file_changes(api, network, tmp_path):
    api.app.state.tools.settings.local_workspace = tmp_path
    (tmp_path / "proof.txt").write_text("old")
    cid = api.post("/api/v1/chats").json()["id"]
    api.put(f"/api/v1/local/grants/{cid}", json={"mode": "workspace"}).raise_for_status()
    network.chat_outputs = [
        tool("files.read", {"path": "proof.txt"}),
        tool("files.write", {"path": "proof.txt", "content": "new"}),
        tool("files.read", {"path": "proof.txt"}),
        json.dumps({"action": "reply", "message": "تغییر فایل بررسی شد."}),
    ]
    response = api.post(
        f"/api/v1/chats/{cid}/messages",
        json={"content": "فایل را بخوان، تغییر بده و دوباره بخوان", "request_id": str(uuid4())},
    )
    assert '"type": "complete"' in response.text
    assert tool_calls(api, cid).count("files.read") == 2
    assert (tmp_path / "proof.txt").read_text() == "new"


def test_empty_registry_does_not_block_an_unrelated_file_task(api, network, tmp_path):
    api.app.state.tools.settings.local_workspace = tmp_path
    cid = api.post("/api/v1/chats").json()["id"]
    api.put(f"/api/v1/local/grants/{cid}", json={"mode": "workspace"}).raise_for_status()
    network.chat_outputs = [
        tool("connections.list"),
        tool("files.write", {"path": "status.txt", "content": "No connections configured"}),
        json.dumps({"action": "reply", "message": "وضعیت اتصال‌ها در فایل ذخیره شد."}),
    ]
    response = api.post(
        f"/api/v1/chats/{cid}/messages",
        json={
            "content": "فهرست اتصال‌ها را بخوان و در status.txt بنویس",
            "request_id": str(uuid4()),
        },
    )
    assert '"type": "complete"' in response.text
    assert (tmp_path / "status.txt").read_text() == "No connections configured"
    assert tool_calls(api, cid) == ["connections.list", "files.write"]


def test_birthday_requests_cannot_silently_use_incoming_reply_template(api, network):
    personal_connection(api)
    store = api.app.state.local_store
    record = store.get("connections", "personal")
    store.put("connections", "personal", {**record, "status": "connected"})
    network.chat_outputs = [
        tool(
            "workflow.telegram_assistant",
            {
                "connection": "personal",
                "name": "Birthday",
                "instruction": "Reply to incoming messages",
            },
        )
    ]
    _, events = send(api, BIRTHDAY_REQUEST + "؛ birthdays.csv منبع تاریخ‌هاست.")
    assert any(e["type"] == "failed" for e in events)
    assert api.get("/api/v1/builds").json()["builds"] == []
    schemas = [json.loads(r.content)["format"] for r in network.calls if r.url.path == "/api/chat"]
    assert schemas[0]["properties"]["action"]["enum"] == ["reply", "tool"]
    assert "workflow.telegram_assistant" not in schemas[0]["properties"]["tool_name"]["enum"]


def test_email_birthdays_are_not_routed_to_telegram():
    assert not personal_birthday_request("Email birthday greetings to all contacts")
    assert not personal_birthday_request("Explain Telegram contact birthday privacy settings")
    assert personal_birthday_request("Design Telegram birthday automation for all contacts")
