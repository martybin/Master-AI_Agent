"""Owner folder selection never implicitly grants the model access."""

import subprocess
from types import SimpleNamespace

import pytest
from test_coding import call, chat


def test_external_unicode_folder_creation_selection_and_scoped_write(api, tmp_path):
    root = tmp_path / "پروژه‌های من"
    root.mkdir()
    (root / ".hidden").mkdir()
    (root / "existing").mkdir()
    (root / "source.py").write_text("value = 1\n")
    cid = chat(api, "code")
    response = api.post("/api/v1/local/folders", json={"parent": str(root), "name": "ایجنت تازه"})
    assert response.status_code == 201
    chosen = response.json()["path"]
    assert api.app.state.tools.permission(cid)["mode"] == "off"
    assert (
        call(
            api, cid, "code.write_file", {"path": "main.py", "content": "print('سلام')\n"}
        ).status_code
        == 409
    )
    listed = api.get("/api/v1/local/browse", params={"path": str(root)}).json()
    assert {e["name"] for e in listed["entries"]} == {"existing", "ایجنت تازه"}
    assert all(e["directory"] and e["path"].startswith(str(root)) for e in listed["entries"])
    shown = api.get("/api/v1/local/browse", params={"path": str(root), "hidden": True}).json()
    assert ".hidden" in {e["name"] for e in shown["entries"]}
    assert (
        api.put(
            f"/api/v1/local/grants/{cid}", json={"mode": "workspace", "workspace": chosen}
        ).status_code
        == 200
    )
    assert (
        call(
            api, cid, "code.write_file", {"path": "main.py", "content": "print('سلام')\n"}
        ).status_code
        == 200
    )
    assert (root / "ایجنت تازه" / "main.py").read_text() == "print('سلام')\n"
    assert (
        call(api, cid, "code.write_file", {"path": "../escape.py", "content": "no"}).status_code
        == 409
    )
    assert api.app.state.tools.permission(chat(api, "code"))["mode"] == "off"


@pytest.mark.parametrize("name", ["..", ".", "../escape", "a/b", "a\\b", "\x00", "  "])
def test_create_folder_rejects_path_traversal(api, tmp_path, name):
    assert (
        api.post("/api/v1/local/folders", json={"parent": str(tmp_path), "name": name}).status_code
        == 409
    )


def test_folder_controls_are_owner_only_and_do_not_overwrite(api, tmp_path):
    for method, path, kwargs in [
        ("get", "/api/v1/local/browse", {}),
        ("post", "/api/v1/local/pick-folder", {}),
        ("post", "/api/v1/local/folders", {"json": {"parent": str(tmp_path), "name": "new"}}),
    ]:
        assert (
            getattr(api, method)(path, headers={"Authorization": ""}, **kwargs).status_code == 401
        )
    existing = tmp_path / "existing"
    existing.mkdir()
    assert (
        api.post(
            "/api/v1/local/folders", json={"parent": str(tmp_path), "name": "existing"}
        ).status_code
        == 409
    )
    assert (
        api.post(
            "/api/v1/local/folders", json={"parent": str(existing / "missing"), "name": "new"}
        ).status_code
        == 409
    )
    assert api.get("/api/v1/local/browse", params={"path": "\x00"}).status_code == 409


def test_native_dialog_uses_directory_mode_and_never_grants_access(api, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("master_agent.local.tools.shutil.which", lambda _: "/usr/bin/zenity")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=str(tmp_path) + "\n")

    monkeypatch.setattr("master_agent.local.tools.subprocess.run", run)
    cid = chat(api, "code")
    picked = api.post("/api/v1/local/pick-folder", params={"path": str(tmp_path)}).json()
    assert picked == {"available": True, "cancelled": False, "path": str(tmp_path.resolve())}
    assert "--directory" in calls[0][0]
    assert not calls[0][1].get("shell")
    assert api.app.state.tools.permission(cid)["mode"] == "off"


@pytest.mark.parametrize(
    "outcome, expected",
    [
        (1, {"available": True, "cancelled": True}),
        (255, {"available": False}),
        ("timeout", {"available": False}),
    ],
)
def test_native_cancel_and_unavailable_are_distinct(api, tmp_path, monkeypatch, outcome, expected):
    monkeypatch.setattr("master_agent.local.tools.shutil.which", lambda _: "/usr/bin/zenity")

    def run(*args, **kwargs):
        if outcome == "timeout":
            raise subprocess.TimeoutExpired("zenity", 180)
        return SimpleNamespace(returncode=outcome, stdout="")

    monkeypatch.setattr("master_agent.local.tools.subprocess.run", run)
    assert api.post("/api/v1/local/pick-folder", params={"path": str(tmp_path)}).json() == expected
