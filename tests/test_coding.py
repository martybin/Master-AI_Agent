import asyncio
import io
import json
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from master_agent.api import create_app
from master_agent.coding_policy import target_for


def chat(api, target="python"):
    return api.post("/api/v1/chats", json={"target": target}).json()["id"]


def call(api, cid, name, arguments):
    return api.post(
        "/api/v1/local/tools/execute",
        json={
            "chat_id": cid,
            "name": name,
            "arguments": arguments,
        },
    )


def grant(api, cid, root, mode="system"):
    response = api.put(
        f"/api/v1/local/grants/{cid}",
        json={
            "mode": mode,
            "confirm": True,
            "workspace": str(root),
        },
    )
    assert response.status_code == 200, response.text


def send(api, cid, content="یک ایجنت Python بساز"):
    response = api.post(
        f"/api/v1/chats/{cid}/messages",
        json={
            "content": content,
            "request_id": str(uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


def project(api, cid):
    response = call(
        api,
        cid,
        "project.create_python_agent",
        {
            "name": "Test Agent",
            "directory": "agents/test-agent",
            "instruction": "پاسخ کوتاه فارسی بده.",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]["project"]


def test_target_selection_keeps_n8n_default_and_respects_owner_choice():
    assert target_for({}, "یک ایجنت بساز") == "n8n"
    assert target_for({}, "یک ایجنت با پایتون بساز") == "python"
    assert target_for({}, "کدنویسی روی فایل انجام بده") == "code"
    assert target_for({}, "در n8n با کدنویسی یک گره Python بساز") == "n8n"
    assert target_for({"target": "python"}, "یک ایجنت بساز") == "python"


def test_coding_request_asks_access_without_touching_files_or_using_model(api, network):
    cid = chat(api)
    result = next(e for e in send(api, cid) if e["type"] == "complete")
    request = result["message"]["access_request"]
    assert request["status"] == "pending" and request["mode"] == "system"
    assert api.app.state.tools.permission(cid)["mode"] == "off"
    assert not api.app.state.tools.coding.projects(cid)
    assert not network.calls
    assert (
        api.get(f"/api/v1/chats/{cid}").json()["messages"][-1]["access_request"]["id"]
        == request["id"]
    )
    assert (
        api.get(
            f"/api/v1/local/access-requests/{request['id']}", headers={"Authorization": ""}
        ).status_code
        == 401
    )


@pytest.mark.parametrize("confirm", [False, "true", 1])
def test_access_approval_requires_owner_strict_confirmation(api, tmp_path, confirm):
    cid = chat(api)
    req = next(e for e in send(api, cid) if e["type"] == "complete")["message"]["access_request"]
    response = api.post(
        f"/api/v1/local/access-requests/{req['id']}/approve",
        json={
            "mode": "system",
            "workspace": str(tmp_path),
            "confirm": confirm,
        },
    )
    assert response.status_code in {409, 422}
    assert api.app.state.tools.permission(cid)["mode"] == "off"


def test_per_chat_workspace_is_used_and_does_not_change_other_grants(api, tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    a, b = chat(api), chat(api)
    grant(api, a, one, "workspace")
    grant(api, b, two, "workspace")
    for cid in [a, b]:
        assert (
            call(
                api, cid, "code.write_file", {"path": "proof.py", "content": "value = 1\n"}
            ).status_code
            == 200
        )
    assert (one / "proof.py").exists() and (two / "proof.py").exists()
    assert call(api, a, "files.read", {"path": str(two / "proof.py")}).status_code == 409
    assert call(api, a, "system.inspect", {}).json()["result"]["workspace"] == str(one)


def test_missing_vault_key_preserves_encrypted_code_history(api, tmp_path):
    from master_agent.local.store import LocalStore
    from master_agent.repository import StateConflict

    cid = chat(api)
    grant(api, cid, tmp_path, "workspace")
    call(
        api, cid, "code.write_file", {"path": "main.py", "content": "print(1)\n"}
    ).raise_for_status()
    store = api.app.state.local_store
    store.key_path.unlink()
    with pytest.raises(StateConflict, match="missing"):
        LocalStore(store.repo, store.key_path).initialize()
    assert not store.key_path.exists()


def test_interrupted_checks_are_not_left_running_on_restart(api):
    store = api.app.state.local_store
    store.put("code_runs", "interrupted-test", {"id": "interrupted-test", "status": "running"})
    store.initialize()
    assert store.get("code_runs", "interrupted-test")["status"] == "interrupted"


def test_optimistic_code_edit_conflicts_and_diff_history(api, tmp_path):
    cid = chat(api)
    grant(api, cid, tmp_path, "workspace")
    response = call(api, cid, "code.write_file", {"path": "main.py", "content": "value = 1\n"})
    digest = response.json()["result"]["sha256"]
    assert (
        call(api, cid, "code.write_file", {"path": "main.py", "content": "lost update"}).status_code
        == 409
    )
    response = call(
        api,
        cid,
        "code.replace",
        {"path": "main.py", "old": "value = 1", "new": "value = 2", "expected_sha256": digest},
    )
    assert response.status_code == 200, response.text
    assert (
        call(
            api,
            cid,
            "code.replace",
            {"path": "main.py", "old": "value = 2", "new": "value = 3", "expected_sha256": digest},
        ).status_code
        == 409
    )
    changes = api.get(f"/api/v1/local/code-changes?chat_id={cid}").json()["changes"]
    assert len(changes) == 1 and "+value = 2" in changes[0]["diff"]
    raw = api.app.state.local_store.list("code_changes")[0]
    assert "value = 2" not in json.dumps(raw) and "sealed" in raw
    assert (tmp_path / "main.py").read_text() == "value = 2\n"


def test_short_revision_is_scoped_to_chat_path_and_content(api, tmp_path):
    cid, other = chat(api), chat(api)
    grant(api, cid, tmp_path, "workspace")
    grant(api, other, tmp_path, "workspace")
    (tmp_path / "one.py").write_text("value = 1\n")
    (tmp_path / "two.py").write_text("value = 1\n")
    read = call(api, cid, "files.read", {"path": "one.py"}).json()["result"]
    assert len(read["revision"]) == 9
    args = {"path": "one.py", "old": "value = 1", "new": "value = 2", "revision": read["revision"]}
    assert call(api, other, "code.replace", args).status_code == 409
    assert call(api, cid, "code.replace", {**args, "path": "two.py"}).status_code == 409
    assert call(api, cid, "code.replace", args).status_code == 200
    assert (
        call(
            api,
            cid,
            "code.write_file",
            {"path": "one.py", "content": "stale", "revision": read["revision"]},
        ).status_code
        == 409
    )


def test_implicit_revision_still_rejects_blind_and_stale_writes(api, tmp_path):
    cid = chat(api)
    grant(api, cid, tmp_path, "workspace")
    (tmp_path / "source.py").write_text("value = 1\n")
    args = {"path": "source.py", "content": "value = 2\n"}
    assert call(api, cid, "code.write_file", args).status_code == 409
    call(api, cid, "files.read", {"path": "source.py"}).raise_for_status()
    assert call(api, cid, "code.write_file", args).status_code == 200
    (tmp_path / "source.py").write_text("owner_change = True\n")
    assert call(api, cid, "code.write_file", args).status_code == 409
    assert (tmp_path / "source.py").read_text() == "owner_change = True\n"


def test_truncated_source_observations_keep_revision_and_hash(api, network, tmp_path):
    cid = chat(api, "code")
    grant(api, cid, tmp_path, "workspace")
    (tmp_path / "large.py").write_text("#" + "a" * 20000)
    network.chat_outputs = [
        json.dumps(
            {
                "action": "tool",
                "message": "خواندن",
                "tool_name": "files.read",
                "tool_arguments": {"path": "large.py"},
            }
        ),
        json.dumps({"action": "reply", "message": "فایل بررسی شد."}),
    ]
    events = send(api, cid, "فایل large.py را بخوان")
    assert any(e["type"] == "complete" for e in events)
    decisions = [
        json.loads(r.content)
        for r in network.calls
        if r.url.path == "/api/chat" and json.loads(r.content)["format"]["title"] == "ChatDecision"
    ]
    evidence = decisions[1]["messages"][-1]["content"]
    assert '"revision": "r' in evidence and '"sha256":' in evidence
    assert '"truncated": true' in evidence and len(evidence) < 14000


def test_wrong_vault_key_is_rejected_before_reading_or_overwriting_code_history(api, tmp_path):
    from cryptography.fernet import Fernet

    from master_agent.local.store import LocalStore
    from master_agent.repository import StateConflict

    cid = chat(api)
    grant(api, cid, tmp_path, "workspace")
    call(
        api, cid, "code.write_file", {"path": "source.py", "content": "print(1)\n"}
    ).raise_for_status()
    store = api.app.state.local_store
    store.key_path.write_bytes(Fernet.generate_key())
    with pytest.raises(StateConflict, match="does not match"):
        LocalStore(store.repo, store.key_path).initialize()


def test_source_search_skips_symlinks_and_private_files(api, tmp_path):
    cid = chat(api)
    grant(api, cid, tmp_path, "workspace")
    (tmp_path / "app.py").write_text("needle\n")
    (tmp_path / ".env").write_text("needle=secret\n")
    (tmp_path / "alias.py").symlink_to(tmp_path / "app.py")
    result = call(api, cid, "files.search", {"query": "needle"}).json()["result"]
    assert [m["path"] for m in result["matches"]] == ["app.py"]


def test_python_factory_real_files_checks_zip_and_idempotence(api, tmp_path):
    cid = chat(api)
    grant(api, cid, tmp_path)
    record = project(api, cid)
    root = Path(record["path"])
    assert root.is_relative_to(tmp_path) and (root / "agent.py").is_file()
    assert len(record["files"]) == 5
    # A repeated creation cannot wipe subsequent owner edits.
    read = call(api, cid, "files.read", {"path": str(root / "README.md")}).json()["result"]
    call(
        api,
        cid,
        "code.write_file",
        {
            "path": str(root / "README.md"),
            "content": "Owner changes",
            "expected_sha256": read["sha256"],
        },
    ).raise_for_status()
    assert project(api, cid)["id"] == record["id"]
    assert (root / "README.md").read_text() == "Owner changes"
    checked = call(api, cid, "project.check", {"project_id": record["id"]})
    assert checked.status_code == 200, checked.text
    run = checked.json()["result"]["run"]
    assert run["status"] == "passed" and run["tests_run"] == 2 and run["result"]["exit_code"] == 0
    (root / ".env").write_text("NEVER_EXPORT_SECRET")
    archive = api.get(f"/api/v1/local/projects/{record['id']}/archive")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        assert ".env" not in zipped.namelist() and "agent.py" in zipped.namelist()
    listed = api.get(f"/api/v1/local/projects?chat_id={cid}").json()["projects"][0]
    assert listed["checks_current"] is True
    (root / "agent.py").write_text("# changed after test\n")
    assert (
        api.get(f"/api/v1/local/projects?chat_id={cid}").json()["projects"][0]["checks_current"]
        is False
    )


def test_project_checks_need_system_grant_and_zero_tests_are_not_success(api, tmp_path):
    cid = chat(api)
    grant(api, cid, tmp_path, "workspace")
    record = project(api, cid)
    assert call(api, cid, "project.check", {"project_id": record["id"]}).status_code == 409
    grant(api, cid, tmp_path)
    (Path(record["path"]) / "tests/test_agent.py").write_text("# no tests\n")
    run = call(api, cid, "project.check", {"project_id": record["id"]}).json()["result"]["run"]
    assert run["status"] == "failed" and run["tests_run"] == 0


def test_existing_directory_is_not_overwritten_and_zip_rejects_escaped_files(api, tmp_path):
    cid = chat(api)
    grant(api, cid, tmp_path)
    root = tmp_path / "agents/test-agent"
    root.mkdir(parents=True)
    (root / "keep.txt").write_text("preserve")
    r = call(
        api,
        cid,
        "project.create_python_agent",
        {"name": "Test", "directory": "agents/test-agent", "instruction": "Hello"},
    )
    assert r.status_code == 409 and (root / "keep.txt").read_text() == "preserve"
    record = call(
        api,
        cid,
        "project.create_python_agent",
        {"name": "Test", "directory": "other", "instruction": "Hello"},
    ).json()["result"]["project"]
    file = Path(record["path"]) / "agent.py"
    file.unlink()
    file.symlink_to(root / "keep.txt")
    assert api.get(f"/api/v1/local/projects/{record['id']}/archive").status_code == 409


def test_access_cannot_be_approved_after_task_or_target_changes(api):
    cid = chat(api)
    record = next(e for e in send(api, cid) if e["type"] == "complete")["message"]["access_request"]
    api.put(f"/api/v1/chats/{cid}/target", json={"target": "n8n"}).raise_for_status()
    response = api.post(
        f"/api/v1/local/access-requests/{record['id']}/approve",
        json={"mode": "system", "confirm": True},
    )
    assert response.status_code == 409
    assert api.app.state.tools.permission(cid)["mode"] == "off"


def test_approve_and_resume_builds_and_checks_real_project_without_n8n(api, network, tmp_path):
    cid = chat(api)
    record = next(e for e in send(api, cid) if e["type"] == "complete")["message"]["access_request"]

    # A fake planner chooses tools; filesystem and subprocess execution stay real.
    def planner(request):
        if request.url.path != "/api/chat":
            return None
        payload = json.loads(request.content)
        if payload["format"]["title"] != "ChatDecision":
            return None
        assert payload["format"]["properties"]["action"]["enum"] in [["tool"], ["reply", "tool"]]
        projects = api.app.state.tools.coding.projects(cid)
        runs = api.app.state.local_store.list("code_runs")
        if not projects:
            decision = {
                "action": "tool",
                "message": "ساخت",
                "tool_name": "project.create_python_agent",
                "tool_arguments": {
                    "name": "My agent",
                    "directory": "my-agent",
                    "instruction": "Answer briefly",
                },
            }
        elif not runs:
            decision = {
                "action": "tool",
                "message": "تست",
                "tool_name": "project.check",
                "tool_arguments": {"project_id": projects[0]["id"]},
            }
        else:
            decision = {"action": "reply", "message": "پروژه ساخته و تست شد."}
        return httpx.Response(200, json={"message": {"content": json.dumps(decision)}})

    network.extra_handler = planner
    response = api.post(
        f"/api/v1/local/access-requests/{record['id']}/approve",
        json={"mode": "system", "confirm": True, "workspace": str(tmp_path)},
    )
    assert response.status_code == 200
    response = api.post(f"/api/v1/chats/{cid}/resume", json={"access_request_id": record["id"]})
    assert response.status_code == 200, response.text
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    complete = next(e for e in events if e["type"] == "complete")
    assert complete["message"]["project_id"] and complete["build"] is None
    assert (tmp_path / "my-agent/agent.py").exists()
    assert api.app.state.local_store.list("code_runs")[0]["status"] == "passed"
    count = len(network.calls)
    replay = api.post(f"/api/v1/chats/{cid}/resume", json={"access_request_id": record["id"]})
    assert replay.json()["replayed"] is True and len(network.calls) == count
    assert all(not c.url.path.startswith("/api/v1/workflows") for c in network.calls)


@pytest.mark.asyncio
async def test_cancelled_project_check_records_cancellation(settings, network, tmp_path):
    from master_agent.local.access import AccessContext
    from master_agent.local.coding import ProjectCheck, PythonAgentArgs

    app = create_app(settings, httpx.MockTransport(network))
    async with app.router.lifespan_context(app):
        cid = app.state.history.create("python")["id"]
        ctx = AccessContext("system", tmp_path, cid)
        record = app.state.tools.coding.create(
            PythonAgentArgs(name="Test", directory="agent", instruction="Hello"), ctx
        )["project"]
        entered = asyncio.Event()

        async def wait(*args):
            entered.set()
            await asyncio.Event().wait()

        app.state.tools.command = wait
        task = asyncio.create_task(
            app.state.tools.coding.check(ProjectCheck(project_id=record["id"]), ctx)
        )
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert app.state.local_store.list("code_runs")[0]["status"] == "cancelled"
