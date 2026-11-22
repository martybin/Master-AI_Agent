import json

import httpx
import pytest

from master_agent.knowledge.store import Knowledge, clean


def populate(knowledge):
    for family in ("langchain", "langgraph", "llamaindex", "n8n"):
        host = "docs.n8n.io" if family == "n8n" else "docs.langchain.com"
        knowledge.put(
            family,
            family + " agents",
            f"https://{host}/{family}",
            (family + " agent tools retrieval memory StateGraph Ollama documented example\n") * 15,
        )


def test_retrieval_scopes_reindex_and_persistence(tmp_path):
    path = tmp_path / "knowledge.sqlite3"
    k = Knowledge(path)
    assert k.context("agent", "python") == ("", [])
    populate(k)
    assert {h["family"] for h in k.search("agent", "n8n")} == {"n8n"}
    assert all(h["family"] != "n8n" for h in k.search("agent", "python"))
    assert k.search("agent", "code") == []
    before = k.status()["passages"]
    populate(k)
    assert k.status()["passages"] == before
    context, refs = Knowledge(path).context("ایجنت با حافظه", "python")
    assert "UNTRUSTED REFERENCE DATA" in context and refs
    assert all(r["sha256"] and r["synced_at"] for r in refs)
    assert len(k.search('" OR * ; DROP TABLE sources', "python", 100)) <= 8
    assert len(context) < 14000
    assert "hidden-secret" not in clean(
        "<nav>hidden-secret</nav><script>bad</script><p>agent example</p>", True
    )


@pytest.mark.asyncio
async def test_sync_retains_last_good_page_and_blocks_redirect_escape(tmp_path, monkeypatch):
    k = Knowledge(tmp_path / "docs.sqlite3")
    source = ("langgraph", "Graph", "https://docs.langchain.com/graph.md")
    monkeypatch.setattr("master_agent.knowledge.store.SOURCES", [source])
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(
            200, headers={"content-type": "text/markdown"}, text="Graph memory agent tools\n" * 20
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await k.sync(client)
    assert result["results"][0]["status"] == "synced"
    previous = k.status()["sources"][0]

    def redirect(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as client:
        result = await k.sync(client)
    assert result["results"][0]["status"] == "failed"
    assert k.status()["sources"][0] == previous
    assert all("127.0.0.1" not in url for url in seen)


@pytest.mark.asyncio
async def test_sync_bounds_size_and_requires_text(tmp_path, monkeypatch):
    k = Knowledge(tmp_path / "docs.sqlite3")
    monkeypatch.setattr(
        "master_agent.knowledge.store.SOURCES", [("n8n", "AI", "https://docs.n8n.io/ai.md")]
    )
    for headers, content in [
        ({"content-type": "application/octet-stream"}, b"bad"),
        ({"content-type": "text/plain"}, b"x" * 1_000_001),
    ]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request, headers=headers, content=content: httpx.Response(
                    200, headers=headers, content=content
                )
            )
        ) as client:
            assert (await k.sync(client))["results"][0]["status"] == "failed"
    assert not k.status()["ready"]


def test_model_uses_only_target_docs_and_keeps_sources_in_history(api, network, tmp_path):
    populate(api.app.state.knowledge)
    for target in ["python", "n8n", "code"]:
        cid = api.post("/api/v1/chats", json={"target": target}).json()["id"]
        network.chat_outputs = [json.dumps({"action": "reply", "message": "سلام"})]
        network.chat_calls = 0
        network.calls.clear()
        api.post(
            f"/api/v1/chats/{cid}/messages",
            json={
                "content": "درباره agent memory توضیح بده",
                "request_id": __import__("uuid").uuid4().hex,
            },
        ).raise_for_status()
        calls = [json.loads(r.content) for r in network.calls if r.url.path == "/api/chat"]
        prompts = "\n".join(m["content"] for c in calls for m in c["messages"])
        record = api.get(f"/api/v1/chats/{cid}").json()["messages"][-1]
        if target == "code":
            assert "Official documentation excerpts" not in prompts
            assert not record.get("sources")
        else:
            assert "Official documentation excerpts" in prompts
            assert record["sources"]
            assert all((s["family"] == "n8n") == (target == "n8n") for s in record["sources"])
    assert api.get("/api/v1/knowledge/status", headers={"Authorization": ""}).status_code == 401
    assert api.post("/api/v1/knowledge/sync", headers={"Authorization": ""}).status_code == 401
    assert (
        api.get("/api/v1/knowledge/search", params={"target": "code", "query": "agent"}).status_code
        == 422
    )
    response = api.post(
        "/api/v1/local/tools/execute",
        json={
            "chat_id": cid,
            "name": "docs.search",
            "arguments": {"query": "agent", "target": "python"},
        },
    )
    assert response.status_code == 200 and response.json()["result"]["results"]


def test_direct_ir_planner_gets_n8n_references_without_changing_compiler_contract(api, network):
    populate(api.app.state.knowledge)
    response = api.post("/api/v1/builds", json={"prompt": "Make a webhook agent with Ollama"})
    assert response.status_code == 201
    requests = [json.loads(r.content) for r in network.calls if r.url.path == "/api/chat"]
    assert any(
        "Official documentation excerpts" in m["content"] for r in requests for m in r["messages"]
    )
    assert response.json()["status"] == "draft"
    assert not any(r.method == "POST" and "/api/v1/workflows" in r.url.path for r in network.calls)
