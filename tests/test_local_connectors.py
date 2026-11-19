import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from telethon import errors
from telethon.crypto import AuthKey
from telethon.tl.types import User

from master_agent.local.store import LocalStore
from master_agent.repository import StateConflict

BOT_TOKEN = "12345:TEST_ONLY_BOT_TOKEN_abcdef"
API_HASH = "TEST_ONLY_API_HASH_0123456789abcdef"


def add_bot(api, alias="telegram_support"):
    response = api.post(
        "/api/v1/local/connections",
        json={"id": alias, "label": "Support Bot", "kind": "telegram_bot", "token": BOT_TOKEN},
    )
    assert response.status_code == 201, response.text
    return response.json()


def build_bot(api, alias="telegram_support"):
    cid = api.post("/api/v1/chats").json()["id"]
    response = api.post(
        "/api/v1/local/tools/execute",
        json={
            "chat_id": cid,
            "name": "workflow.telegram_assistant",
            "arguments": {
                "connection": alias,
                "name": "Telegram Assistant",
                "instruction": "پاسخ کوتاه فارسی بده.",
            },
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]["build_id"]


def approve_mock(api, build):
    assert api.post(f"/api/v1/builds/{build}/deploy").status_code == 200
    review = api.post(f"/api/v1/builds/{build}/activation-review").json()
    result = api.post(
        f"/api/v1/builds/{build}/activate",
        json={
            "confirm": True,
            "workflow_sha256": review["workflow_sha256"],
            "review_sha256": review["review_sha256"],
        },
    )
    assert result.status_code == 200, result.text


class TelegramNetwork:
    def __init__(self):
        self.sent = []
        self.webhook = ""
        self.fail_send = False
        self.fail_webhook = False
        self.updates = [
            {"update_id": 1, "message": {"chat": {"id": 77}, "text": "سلام", "from": {"id": 88}}}
        ]

    def __call__(self, request):
        if request.url.host == "api.telegram.org":
            method = request.url.path.rsplit("/", 1)[-1]
            body = json.loads(request.content)
            if method == "getMe":
                result = {"id": 12345, "username": "test_bot"}
            elif method == "getWebhookInfo":
                result = {"url": self.webhook}
            elif method == "getUpdates":
                result = [row for row in self.updates if row["update_id"] >= body["offset"]]
            elif method == "sendMessage":
                self.sent.append(body)
                if self.fail_send:
                    raise httpx.ReadTimeout("ambiguous send")
                result = {"message_id": 99}
            else:
                raise AssertionError(method)
            return httpx.Response(200, json={"ok": True, "result": result})
        if request.url.path.startswith("/webhook/"):
            if self.fail_webhook:
                raise httpx.ReadTimeout("ambiguous workflow")
            return httpx.Response(200, json={"message": {"content": "پاسخ محلی"}})
        return None


def test_connections_are_encrypted_and_never_echo_tokens(api, settings):
    record = add_bot(api)
    assert "sealed" not in record and BOT_TOKEN not in json.dumps(record)
    assert BOT_TOKEN not in api.get("/api/v1/local/status").text
    with api.app.state.local_store.repo.connect() as db:
        raw = db.execute("SELECT data FROM local_records WHERE bucket='connections'").fetchone()[0]
    assert BOT_TOKEN not in raw
    store = api.app.state.local_store
    assert store.unseal(json.loads(raw)["sealed"])["token"] == BOT_TOKEN
    assert store.key_path.stat().st_mode & 0o777 == 0o600
    assert (
        api.post(
            "/api/v1/local/connections",
            json={
                "id": "telegram_support",
                "label": "duplicate",
                "kind": "telegram_bot",
                "token": BOT_TOKEN,
            },
        ).status_code
        == 409
    )
    encrypted = store.seal({"api_hash": API_HASH})
    restarted = LocalStore(store.repo, store.key_path)
    restarted.initialize()
    assert restarted.unseal(encrypted)["api_hash"] == API_HASH


def test_bot_verification_and_authenticated_http_origin_boundary(api, network):
    network.extra_handler = TelegramNetwork()
    add_bot(api)
    response = api.post("/api/v1/local/connections/telegram_support/verify")
    assert response.status_code == 200 and response.json()["identity"]["username"] == "test_bot"
    assert BOT_TOKEN not in response.text
    secret = "Bearer API_TEST_SECRET_abcdefgh"
    assert (
        api.post(
            "/api/v1/local/connections",
            json={
                "id": "api_test",
                "label": "test",
                "kind": "http",
                "base_url": "https://api.example.com",
                "token": secret,
            },
        ).status_code
        == 201
    )
    cid = api.post("/api/v1/chats").json()["id"]
    api.put("/api/v1/local/grants/" + cid, json={"mode": "workspace"})
    result = api.post(
        "/api/v1/local/tools/execute",
        json={
            "chat_id": cid,
            "name": "http.request",
            "arguments": {"connection": "api_test", "path": "https://evil.example.com/"},
        },
    )
    assert result.status_code == 409
    assert all(request.url.host != "evil.example.com" for request in network.calls)


def test_real_telegram_template_requires_no_raw_credential_in_workflow(api):
    add_bot(api)
    build = build_bot(api)
    record = api.get("/api/v1/builds/" + build).json()
    assert record["status"] == "draft"
    assert BOT_TOKEN not in json.dumps(record)
    assert (
        "telegram-support"
        in next(n for n in record["workflow"]["nodes"] if n["type"].endswith(".webhook"))[
            "parameters"
        ]["path"]
    )


def test_bridge_cannot_start_before_owner_activation(api, network):
    network.extra_handler = TelegramNetwork()
    add_bot(api)
    build = build_bot(api)
    binding = {"build_id": build, "allowed_chat_ids": [77]}
    assert api.put("/api/v1/local/bindings/telegram_support", json=binding).status_code == 200
    assert (
        api.post(
            "/api/v1/local/bindings/telegram_support/start", json={"confirm": False}
        ).status_code
        == 409
    )
    assert (
        api.post(
            "/api/v1/local/bindings/telegram_support/start", json={"confirm": True}
        ).status_code
        == 409
    )
    assert not network.extra_handler.sent
    assert all(not req.url.path.endswith("/activate") for req in network.calls)


def test_bridge_detects_conflicting_telegram_webhook(api, network):
    network.extra_handler = TelegramNetwork()
    network.extra_handler.webhook = "https://existing.example/hook"
    add_bot(api)
    build = build_bot(api)
    approve_mock(api, build)
    api.put(
        "/api/v1/local/bindings/telegram_support",
        json={"build_id": build, "allowed_chat_ids": [77]},
    )
    result = api.post("/api/v1/local/bindings/telegram_support/start", json={"confirm": True})
    assert result.status_code == 409
    assert all(not req.url.path.endswith("/deleteWebhook") for req in network.calls)


@pytest.mark.asyncio
async def test_durable_bridge_deduplicates_and_binds_reply_to_original_chat(settings, network):
    from master_agent.api import create_app

    app = create_app(settings, httpx.MockTransport(network))
    tg = TelegramNetwork()
    network.extra_handler = tg
    async with app.router.lifespan_context(app):
        from master_agent.local.bridge import BindingInput
        from master_agent.local.connectors import ConnectionInput
        from master_agent.local.tools import TelegramBuildArgs

        state = app.state
        state.connectors.create(
            ConnectionInput(
                id="telegram_support", label="test", kind="telegram_bot", token=BOT_TOKEN
            )
        )
        bid = state.tools.telegram_workflow(
            TelegramBuildArgs(connection="telegram_support", name="test", instruction="Reply")
        )["build_id"]
        await state.service.deploy(bid)
        review = await state.service.review(bid)
        await state.service.activate(
            bid,
            confirmed=True,
            workflow_sha256=review["workflow_sha256"],
            review_sha256=review["review_sha256"],
        )
        binding = state.bridges.bind(
            "telegram_support", BindingInput(build_id=bid, allowed_chat_ids=[77])
        )
        captured = await state.bridges.poll("telegram_support", binding)
        assert len(captured) == 1
        await state.bridges.deliver(captured[0])
        assert tg.sent == [{"chat_id": 77, "text": "پاسخ محلی"}]
        binding = state.local_store.get("bindings", "telegram_support")
        assert not await state.bridges.poll("telegram_support", binding)
        assert len(state.local_store.inbox()) == 1
        assert state.local_store.inbox()[0]["status"] == "complete"
        assert "سلام" not in json.dumps(state.local_store.inbox(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_ambiguous_reply_is_not_automatically_retried(settings, network):
    from master_agent.api import create_app
    from master_agent.local.bridge import BindingInput
    from master_agent.local.connectors import ConnectionInput
    from master_agent.local.tools import TelegramBuildArgs

    tg = TelegramNetwork()
    tg.fail_send = True
    network.extra_handler = tg
    app = create_app(settings, httpx.MockTransport(network))
    async with app.router.lifespan_context(app):
        state = app.state
        state.connectors.create(
            ConnectionInput(id="bot", label="test", kind="telegram_bot", token=BOT_TOKEN)
        )
        bid = state.tools.telegram_workflow(
            TelegramBuildArgs(connection="bot", name="test", instruction="Reply")
        )["build_id"]
        await state.service.deploy(bid)
        review = await state.service.review(bid)
        await state.service.activate(
            bid,
            confirmed=True,
            workflow_sha256=review["workflow_sha256"],
            review_sha256=review["review_sha256"],
        )
        binding = state.bridges.bind("bot", BindingInput(build_id=bid, allowed_chat_ids=[77]))
        records = await state.bridges.poll("bot", binding)
        await state.bridges.deliver(records[0])
        assert len(tg.sent) == 1
        assert state.local_store.inbox()[0]["status"] == "uncertain"
        assert state.local_store.pending("bot") == []
        assert not await state.bridges.poll("bot", state.local_store.get("bindings", "bot"))
        assert len(tg.sent) == 1


class PersonalClient:
    created = []

    def __init__(self, session, *args, **kwargs):
        self.session = session
        self.authorized = bool(session.auth_key)
        self.disconnected = False
        self.created.append(self)

    async def connect(self):
        pass

    async def disconnect(self):
        self.disconnected = True

    async def is_user_authorized(self):
        return self.authorized

    async def send_code_request(self, phone):
        return SimpleNamespace(phone_code_hash="EPHEMERAL_HASH_ONLY")

    async def sign_in(self, **kwargs):
        if "password" not in kwargs:
            raise errors.SessionPasswordNeededError(None)
        assert kwargs["password"] == "TEST_2FA_PASSWORD"
        self.authorized = True
        self.session.set_dc(2, "149.154.167.50", 443)
        self.session.auth_key = AuthKey(bytes(range(256)))

    async def get_me(self):
        return SimpleNamespace(id=42, username="owner")

    async def get_dialogs(self, limit):
        return [SimpleNamespace(entity=User(id=77, first_name="Chosen chat"), name="Chosen chat")]


def test_personal_account_login_handles_2fa_without_storing_codes(api):
    api.app.state.connectors.factory = PersonalClient
    data = {
        "id": "personal",
        "label": "My account",
        "kind": "telegram_user",
        "api_id": 123,
        "api_hash": API_HASH,
    }
    response = api.post("/api/v1/local/connections", json=data)
    assert response.status_code == 201 and API_HASH not in response.text
    start = api.post(
        "/api/v1/local/connections/personal/login/start", json={"phone": "+989000000000"}
    )
    assert start.json()["state"] == "code_required"
    result = api.post("/api/v1/local/connections/personal/login/finish", json={"code": "12345"})
    assert result.json()["state"] == "password_required"
    result = api.post(
        "/api/v1/local/connections/personal/login/finish", json={"password": "TEST_2FA_PASSWORD"}
    )
    assert result.json()["state"] == "authorized"
    record = api.app.state.local_store.get("connections", "personal")
    assert api.app.state.local_store.unseal(record["sealed"])["session"]
    assert "TEST_2FA_PASSWORD" not in json.dumps(record)
    assert "12345" not in api.get("/api/v1/local/status").text
    assert api.get("/api/v1/local/connections/personal/dialogs").json()["dialogs"][0]["id"] == 77
    assert api.app.state.connectors.pending == {}


@pytest.mark.asyncio
async def test_concurrent_personal_login_and_session_reuse_are_serialized(settings, network):
    from master_agent.api import create_app
    from master_agent.local.connectors import ConnectionInput, Connectors

    app = create_app(settings, httpx.MockTransport(network))
    async with app.router.lifespan_context(app):
        connectors = app.state.connectors
        connectors.factory = PersonalClient
        connectors.create(
            ConnectionInput(
                id="personal",
                label="Owner",
                kind="telegram_user",
                api_id=123,
                api_hash=API_HASH,
            )
        )
        before = len(PersonalClient.created)
        results = await asyncio.gather(
            connectors.begin_login("personal", "+989000000000"),
            connectors.begin_login("personal", "+989000000000"),
            return_exceptions=True,
        )
        assert results[0]["state"] == "code_required"
        assert isinstance(results[1], StateConflict)
        assert len(PersonalClient.created) == before + 1
        assert (await connectors.complete_login("personal", "12345"))["state"] == (
            "password_required"
        )
        results = await asyncio.gather(
            connectors.complete_login("personal", password="TEST_2FA_PASSWORD"),
            connectors.complete_login("personal", password="TEST_2FA_PASSWORD"),
            return_exceptions=True,
        )
        assert results[0]["state"] == "authorized"
        assert isinstance(results[1], StateConflict)
        restarted = Connectors(connectors.store, connectors.http, factory=PersonalClient)
        first, second = await asyncio.gather(restarted.user("personal"), restarted.user("personal"))
        assert first is second
        assert len(PersonalClient.created) == before + 2
        await restarted.close()


def test_personal_listener_requires_selected_chats(api):
    api.post(
        "/api/v1/local/connections",
        json={
            "id": "personal",
            "label": "Me",
            "kind": "telegram_user",
            "api_id": 123,
            "api_hash": API_HASH,
        },
    )
    build = build_bot(api, "personal")
    assert (
        api.put(
            "/api/v1/local/bindings/personal", json={"build_id": build, "allowed_chat_ids": []}
        ).status_code
        == 409
    )


def test_bot_failure_does_not_echo_token(api, network):
    add_bot(api)

    def failure(request):
        if request.url.host == "api.telegram.org":
            return httpx.Response(401, json={"ok": False, "description": BOT_TOKEN})
        return None

    network.extra_handler = failure
    response = api.post("/api/v1/local/connections/telegram_support/verify")
    assert response.status_code == 503 and BOT_TOKEN not in response.text


def test_delivery_claim_is_atomic_and_restart_never_retries_inflight(api):
    from concurrent.futures import ThreadPoolExecutor

    store = api.app.state.local_store
    record = store.capture("test", "event", {"message": "private"})
    with ThreadPoolExecutor(max_workers=3) as pool:
        claimed = list(pool.map(lambda _: store.claim_delivery(record["id"]), range(3)))
    assert sum(value is not None for value in claimed) == 1
    restarted = LocalStore(store.repo, store.key_path)
    restarted.initialize()
    assert restarted.delivery(record["id"])["status"] == "uncertain"
    assert restarted.pending("test") == []


def test_missing_vault_key_does_not_replace_existing_secrets(api):
    add_bot(api)
    store = api.app.state.local_store
    original = store.key_path.read_bytes()
    store.key_path.unlink()
    try:
        with pytest.raises(StateConflict, match="key is missing"):
            LocalStore(store.repo, store.key_path).initialize()
        assert not store.key_path.exists()
    finally:
        store.key_path.write_bytes(original)
        store.key_path.chmod(0o600)


def test_redaction_handles_quoted_secrets_without_invalid_json(api):
    secret = 'secret-with-"quotes"-and-\\backslash'
    api.post(
        "/api/v1/local/connections",
        json={
            "id": "quoted",
            "label": "test",
            "kind": "http",
            "base_url": "https://example.com",
            "token": secret,
        },
    )
    cleaned = api.app.state.local_store.redact({"body": secret})
    assert cleaned == {"body": "[REDACTED]"}


@pytest.mark.asyncio
async def test_owner_retry_reuses_cached_reply_without_reexecuting_workflow(settings, network):
    from master_agent.api import create_app
    from master_agent.local.bridge import BindingInput
    from master_agent.local.connectors import ConnectionInput
    from master_agent.local.tools import TelegramBuildArgs

    tg = TelegramNetwork()
    tg.fail_send = True
    network.extra_handler = tg
    app = create_app(settings, httpx.MockTransport(network))
    async with app.router.lifespan_context(app):
        state = app.state
        state.connectors.create(
            ConnectionInput(id="bot", label="test", kind="telegram_bot", token=BOT_TOKEN)
        )
        bid = state.tools.telegram_workflow(
            TelegramBuildArgs(connection="bot", name="test", instruction="Reply")
        )["build_id"]
        await state.service.deploy(bid)
        review = await state.service.review(bid)
        await state.service.activate(
            bid,
            confirmed=True,
            workflow_sha256=review["workflow_sha256"],
            review_sha256=review["review_sha256"],
        )
        binding = state.bridges.bind("bot", BindingInput(build_id=bid, allowed_chat_ids=[77]))
        record = (await state.bridges.poll("bot", binding))[0]
        await state.bridges.deliver(record)
        before = sum(request.url.path.startswith("/webhook/") for request in network.calls)
        tg.fail_send = False
        await state.bridges.deliver(state.local_store.delivery(record["id"]), allow_uncertain=True)
        assert sum(request.url.path.startswith("/webhook/") for request in network.calls) == before
        assert state.local_store.delivery(record["id"])["status"] == "complete"
        assert "sealed_reply" not in json.dumps(state.local_store.inbox())
        assert len(tg.sent) == 2  # The first send's response was lost; only the owner may retry.


@pytest.mark.asyncio
async def test_personal_poll_starts_at_new_messages_and_ignores_outgoing(settings, network):
    from telethon.sessions import StringSession

    from master_agent.api import create_app
    from master_agent.local.bridge import BindingInput
    from master_agent.local.connectors import ConnectionInput
    from master_agent.local.tools import TelegramBuildArgs

    app = create_app(settings, httpx.MockTransport(network))
    async with app.router.lifespan_context(app):
        state = app.state
        state.connectors.create(
            ConnectionInput(
                id="personal", label="Me", kind="telegram_user", api_id=123, api_hash=API_HASH
            )
        )
        client = PersonalClient(StringSession())
        client.authorized = True

        async def recent(chat_id, limit):
            return [SimpleNamespace(id=10)]

        async def iterate(chat_id, min_id, **_):
            for mid, out in [(11, False), (12, True)]:
                if mid > min_id:
                    yield SimpleNamespace(
                        id=mid,
                        out=out,
                        raw_text="incoming" if not out else "outgoing",
                        sender_id=88,
                    )

        client.get_messages = recent
        client.iter_messages = iterate
        state.connectors.clients["personal"] = client
        bid = state.tools.telegram_workflow(
            TelegramBuildArgs(
                connection="personal", name="Personal automation", instruction="Reply"
            )
        )["build_id"]
        binding = state.bridges.bind("personal", BindingInput(build_id=bid, allowed_chat_ids=[77]))
        assert await state.bridges.poll("personal", binding) == []
        binding = state.local_store.get("bindings", "personal")
        records = await state.bridges.poll("personal", binding)
        assert len(records) == 1
        payload = state.local_store.unseal(records[0]["sealed"])
        assert payload["message"] == "incoming" and payload["chat_id"] == 77
        assert state.local_store.get("bindings", "personal")["cursor"]["77"] == 12


def test_retry_endpoint_requires_explicit_confirmation_and_hides_ciphertext(api):
    store = api.app.state.local_store
    record = store.capture("missing", "event", {"message": "private"})
    result = api.post("/api/v1/local/inbox/" + record["id"] + "/retry", json={"confirm": False})
    assert result.status_code == 409
    assert store.delivery(record["id"])["status"] == "pending"


def test_secret_request_validation_does_not_echo_input(api):
    response = api.post(
        "/api/v1/local/connections",
        json={
            "id": "bad",
            "label": "bad",
            "kind": "telegram_user",
            "api_id": API_HASH,
            "api_hash": API_HASH,
        },
    )
    assert response.status_code == 422 and API_HASH not in response.text
