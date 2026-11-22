from pathlib import Path

import pytest
from test_coding import chat, grant, send

from master_agent.chat_service import ChatDecision
from master_agent.coding_context import named_files
from master_agent.coding_policy import execution_request, missing_execution


def test_complete_and_short_followups_are_execution_requests():
    request = "بیا برام ایجنتمو توی فایل agent.py تکمیل کن"
    history = [
        {"role": "user", "content": request},
        {"role": "assistant", "content": "اول فایل را می‌خوانم."},
    ]
    for followup in ["چیشد", "چی شد؟", "فایل توی پوشه uni هست", "همین پوشه که دسترسی دادم"]:
        assert execution_request([*history, {"role": "user", "content": followup}]) == request
    assert execution_request([*history, {"role": "user", "content": "تکمیل نکن"}]) is None
    assert missing_execution(request, [{"tool": "files.read", "result": {"content": "pass"}}])
    assert missing_execution(request, [{"tool": "terminal.exec", "result": {"exit_code": 0}}])
    assert named_files("در /tmp/uni/agent.py تکمیل کن") == ["/tmp/uni/agent.py"]


@pytest.mark.parametrize("target", ["auto", "python", "code"])
def test_existing_agent_is_read_before_model_and_completed_in_same_turn(
    api, tmp_path, monkeypatch, target
):
    root = tmp_path / "uni"
    root.mkdir()
    source = root / "agent.py"
    source.write_text("def answer():\n    raise NotImplementedError\n")
    cid = chat(api, target)
    grant(api, cid, root, "workspace")
    decisions = iter(
        [
            ChatDecision(action="reply", message="فایل را بفرست تا ببینم"),
            ChatDecision(
                action="tool",
                message="تکمیل",
                tool_name="code.replace",
                tool_arguments={
                    "path": str(source),
                    "old": "raise NotImplementedError",
                    "new": 'return "done"',
                },
            ),
            ChatDecision(action="reply", message="تکمیل شد. حالا تستش می‌کنم."),
        ]
    )

    async def decide(*args, **kwargs):
        reads = [
            o for o in kwargs["observations"] if o.get("tool") == "files.read" and "result" in o
        ]
        assert reads and "NotImplementedError" in reads[0]["result"]["content"]
        assert Path(reads[0]["result"]["path"]) == source
        return next(decisions)

    monkeypatch.setattr(api.app.state.chat_service, "decide", decide)
    events = send(api, cid, "بیا برام ایجنتمو توی فایل agent.py تکمیل کن")
    result = next(e["message"] for e in events if e["type"] == "complete")
    assert result["execution_complete"]
    assert "حالا" not in result["content"]
    assert "Files written" in result["content"]
    assert 'return "done"' in source.read_text()
    assert any(e.get("name") == "files.read" for e in result["activity"])
    assert api.app.state.tools.permission(cid)["mode"] == "workspace"


def test_chishod_resumes_old_unexecuted_request_without_trusting_assistant_promise(
    api, tmp_path, monkeypatch
):
    root = tmp_path / "uni"
    root.mkdir()
    (root / "agent.py").write_text("value = 0\n")
    cid = chat(api, "python")
    grant(api, cid, root, "workspace")
    history = api.app.state.history
    turn = history.start_turn(cid, "legacy-task", "ایجنتم را در agent.py تکمیل کن")
    history.finish_turn(cid, turn["assistant"]["id"], "باشه اول فایل را می‌خوانم.")
    choices = iter(
        [
            ChatDecision(
                action="tool",
                message="write",
                tool_name="code.replace",
                tool_arguments={"path": "agent.py", "old": "value = 0", "new": "value = 1"},
            ),
            ChatDecision(action="reply", message="تمام شد"),
        ]
    )

    async def decide(messages, *args, **kwargs):
        assert "تکمیل" in messages[-1]["content"] and "چیشد" in messages[-1]["content"]
        return next(choices)

    monkeypatch.setattr(api.app.state.chat_service, "decide", decide)
    result = next(e["message"] for e in send(api, cid, "چیشد") if e["type"] == "complete")
    assert result["execution_complete"] and (root / "agent.py").read_text() == "value = 1\n"
    assert (
        execution_request(history.get(cid)["messages"] + [{"role": "user", "content": "چیشد؟"}])
        is None
    )


def test_nested_unique_file_found_but_ambiguous_and_outside_paths_do_not_write(
    api, tmp_path, monkeypatch
):
    root = tmp_path / "project"
    root.mkdir()
    for name in ["one", "two"]:
        folder = root / name
        folder.mkdir()
        (folder / "agent.py").write_text("unchanged\n")
    cid = chat(api, "code")
    grant(api, cid, root, "workspace")

    async def must_not_decide(*args, **kwargs):
        raise AssertionError("Ambiguous file must be resolved by owner")

    monkeypatch.setattr(api.app.state.chat_service, "decide", must_not_decide)
    message = next(
        e["message"] for e in send(api, cid, "agent.py را تکمیل کن") if e["type"] == "complete"
    )
    assert "Multiple matching" in message["content"]
    assert not message["execution_complete"]
    assert (root / "one/agent.py").read_text() == "unchanged\n"


def test_unique_nested_source_is_read_and_edited_without_duplicating_folder_name(
    api, tmp_path, monkeypatch
):
    root = tmp_path / "project"
    source = root / "uni" / "agent.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 0\n")
    cid = chat(api, "python")
    grant(api, cid, root, "workspace")
    choices = iter(
        [
            ChatDecision(
                action="tool",
                message="edit",
                tool_name="code.replace",
                tool_arguments={"path": str(source), "old": "value = 0", "new": "value = 2"},
            ),
            ChatDecision(action="reply", message="done"),
        ]
    )

    async def decide(*args, **kwargs):
        assert any(o.get("result", {}).get("path") == str(source) for o in kwargs["observations"])
        return next(choices)

    monkeypatch.setattr(api.app.state.chat_service, "decide", decide)
    result = next(
        e["message"]
        for e in send(api, cid, "agent.py در پوشه uni را تکمیل کن")
        if e["type"] == "complete"
    )
    assert result["execution_complete"] and source.read_text() == "value = 2\n"


def test_preflight_rejects_symlink_escape_and_respects_workspace_permission(
    api, tmp_path, monkeypatch
):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("private source\n")
    (root / "agent.py").symlink_to(outside)
    cid = chat(api, "code")
    grant(api, cid, root, "workspace")

    async def must_not_decide(*args, **kwargs):
        raise AssertionError("Outside file must not reach model")

    monkeypatch.setattr(api.app.state.chat_service, "decide", must_not_decide)
    result = next(
        e["message"] for e in send(api, cid, "agent.py را تکمیل کن") if e["type"] == "complete"
    )
    assert "outside the granted" in result["content"]
    assert not result["execution_complete"]
    assert outside.read_text() == "private source\n"


def test_edit_without_terminal_does_not_cancel_file_work():
    task = "agent.py را تکمیل کن. تست یا فرمان اجرا نکن."
    assert execution_request([{"role": "user", "content": task}]) == task
    assert not missing_execution(
        task, [{"tool": "code.write_file", "result": {"path": "agent.py"}}]
    )
    history = [
        {"role": "user", "content": "در agent.py تابع موردنظر را تکمیل کن"},
        {"role": "assistant", "content": "باشه"},
    ]
    assert (
        execution_request(history + [{"role": "user", "content": "بنویس"}]) == history[0]["content"]
    )


def test_real_missing_requirements_question_is_allowed_only_after_inspection(
    api, tmp_path, monkeypatch
):
    from master_agent.chat_service import CompletionReview

    (tmp_path / "agent.py").write_text("def answer(text):\n    return text\n")
    cid = chat(api, "python")
    grant(api, cid, tmp_path, "workspace")

    async def decide(*args, **kwargs):
        assert kwargs["force_tool"] is False
        return ChatDecision(action="reply", message="چه قابلیت جدیدی باید به این ایجنت اضافه شود؟")

    async def review(*args):
        return CompletionReview(status="blocked", missing=["Requested feature is unspecified"])

    monkeypatch.setattr(api.app.state.chat_service, "decide", decide)
    monkeypatch.setattr(api.app.state.chat_service, "review_completion", review)
    result = next(
        e["message"] for e in send(api, cid, "agent.py را تکمیل کن") if e["type"] == "complete"
    )
    assert "Inspected:" in result["content"]
    assert not result["execution_complete"]
    assert (tmp_path / "agent.py").read_text() == "def answer(text):\n    return text\n"


@pytest.mark.asyncio
async def test_repeated_inspection_focuses_available_tools_on_the_requested_edit(
    api, network, tmp_path
):
    import json

    path = tmp_path / "agent.py"
    path.write_text("value = 0\n")
    cid = chat(api, "python")
    grant(api, cid, tmp_path, "workspace")
    observations = [
        {"tool": "project.inspect", "result": {"path": str(tmp_path)}},
        {"tool": "files.read", "result": {"path": str(path), "content": "value = 0\n"}},
        {"tool": "system.inspect", "result": {}},
        {"tool": "files.search", "result": {}},
    ]
    network.chat_outputs = [
        json.dumps(
            {
                "action": "tool",
                "message": "edit",
                "tool_name": "code.replace",
                "tool_arguments": {"path": str(path), "old": "value = 0", "new": "value = 1"},
            }
        )
    ]
    await api.app.state.chat_service.decide(
        [{"role": "user", "status": "complete", "content": "agent.py را تکمیل کن"}],
        None,
        observations=observations,
        chat_id=cid,
        target="python",
    )
    call = next(r for r in network.calls if r.url.path == "/api/chat")
    variants = json.loads(call.content)["format"]["anyOf"]
    tools = [
        v["properties"]["tool_name"]["const"]
        for v in variants
        if v["properties"]["action"].get("const") == "tool"
    ]
    assert set(tools) == {"code.replace", "code.write_file"}
    for variant in variants:
        if variant["properties"]["action"].get("const") == "tool":
            assert variant["properties"]["tool_arguments"]["properties"]["path"]["const"] == str(
                path
            )
