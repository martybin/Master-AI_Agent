import json

import pytest
from fastapi.testclient import TestClient

from master_agent.api import create_app


def draft(api, ir):
    response = api.post("/api/v1/builds/from-ir", json=ir.model_dump())
    assert response.status_code == 201, response.text
    return response.json()


def deployed(api, ir):
    record = draft(api, ir)
    response = api.post(f"/api/v1/builds/{record['id']}/deploy")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "deployed"
    return record


def review(api, record):
    response = api.post(f"/api/v1/builds/{record['id']}/activation-review")
    assert response.status_code == 200, response.text
    result = response.json()
    return {
        "confirm": True,
        "workflow_sha256": result["workflow_sha256"],
        "review_sha256": result["review_sha256"],
    }


def activation_calls(network):
    return [call for call in network.calls if call.url.path.endswith("/activate")]


def test_requires_master_key(api, network):
    assert api.get("/healthz", headers={"Authorization": ""}).status_code == 200
    for path in ["/api/v1/ir/schema", "/api/v1/system", "/api/v1/builds/missing"]:
        assert api.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert not network.calls


def test_build_does_not_contact_n8n(api, network):
    response = api.post("/api/v1/builds", json={"prompt": "Build a local webhook assistant"})
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["workflow"]["active"] is False and record["status"] == "draft"
    assert record["attempts"] == 1
    assert all(call.url.path in {"/api/tags", "/api/chat"} for call in network.calls)
    exported = api.get(f"/api/v1/builds/{record['id']}/workflow?format=api").json()
    assert "active" not in exported


def test_from_ir_offline_validate_and_export(api, network, ir):
    record = draft(api, ir)
    response = api.post("/api/v1/ir/validate", json=ir.model_dump())
    assert response.json()["valid"] is True
    assert api.get(f"/api/v1/builds/{record['id']}").json() == record
    assert api.get(f"/api/v1/builds/{record['id']}/workflow").json()["active"] is False
    assert not network.calls


def test_invalid_build_never_persists_or_deploys(api, network, ir):
    data = ir.model_dump()
    data["nodes"][1]["system_prompt"] = "{{evil()}}"
    assert api.post("/api/v1/builds/from-ir", json=data).status_code == 422
    assert not network.calls


def test_deploy_is_inactive_and_idempotent(api, network, ir):
    record = deployed(api, ir)
    assert api.post(f"/api/v1/builds/{record['id']}/deploy").status_code == 200
    creates = [call for call in network.calls if call.method == "POST"]
    assert len(creates) == 1 and creates[0].url.path == "/api/v1/workflows"
    assert "active" not in json.loads(creates[0].content)
    assert network.remote["active"] is False
    assert not activation_calls(network)


@pytest.mark.parametrize("confirmation", [None, False, "true", 1])
def test_no_activation_without_strict_explicit_confirmation(api, network, ir, confirmation):
    record = deployed(api, ir)
    body = review(api, record)
    if confirmation is None:
        body.pop("confirm")
    else:
        body["confirm"] = confirmation
    assert api.post(f"/api/v1/builds/{record['id']}/activate", json=body).status_code in {409, 422}
    assert not activation_calls(network)


def test_no_activation_without_review(api, network, ir):
    record = deployed(api, ir)
    body = {
        "confirm": True,
        "workflow_sha256": record["workflow_sha256"],
        "review_sha256": "a" * 64,
    }
    assert api.post(f"/api/v1/builds/{record['id']}/activate", json=body).status_code == 409
    assert not activation_calls(network)


def test_wrong_compiled_hash_rejected(api, network, ir):
    record = deployed(api, ir)
    body = {**review(api, record), "workflow_sha256": "0" * 64}
    assert api.post(f"/api/v1/builds/{record['id']}/activate", json=body).status_code == 409
    assert not activation_calls(network)


def test_inconsistent_published_state_cannot_be_reviewed(api, network, ir):
    record = deployed(api, ir)
    network.remote["activeVersionId"] = "published-version"
    assert api.post(f"/api/v1/builds/{record['id']}/activation-review").status_code == 409
    assert not activation_calls(network)


def test_known_server_derived_settings_are_reviewable(api, network, ir):
    record = deployed(api, ir)
    network.remote["settings"].update(binaryMode="separate", engineType="v1")
    body = review(api, record)
    assert body["review_sha256"]
    assert not activation_calls(network)


@pytest.mark.parametrize(
    "change", ["version", "parameters", "extra_node", "credentials", "settings"]
)
def test_remote_changes_invalidate_approval(api, network, ir, change):
    record = deployed(api, ir)
    body = review(api, record)
    if change == "version":
        network.remote["versionId"] = "v2"
    elif change == "parameters":
        network.remote["nodes"][0]["parameters"]["url"] = "https://evil.example/"
    elif change == "extra_node":
        network.remote["nodes"].append({"id": "extra", "type": "n8n-nodes-base.code"})
    elif change == "credentials":
        network.remote["nodes"][0]["credentials"] = {"httpBasicAuth": {"id": "sensitive"}}
    else:
        network.remote["settings"]["timezone"] = "UTC"
    assert api.post(f"/api/v1/builds/{record['id']}/activate", json=body).status_code == 409
    assert api.get(f"/api/v1/builds/{record['id']}").json()["status"] == "deployed"
    assert not activation_calls(network)


def test_mock_approved_activation_then_deactivation(api, network, ir):
    # Entirely mocked network: never activates a real n8n workflow.
    record = deployed(api, ir)
    body = review(api, record)
    result = api.post(f"/api/v1/builds/{record['id']}/activate", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "active"
    assert len(activation_calls(network)) == 1
    assert api.post(f"/api/v1/builds/{record['id']}/activate", json=body).status_code == 409
    assert len(activation_calls(network)) == 1
    assert api.post(f"/api/v1/builds/{record['id']}/deactivate").json()["status"] == "deployed"
    events = api.get(f"/api/v1/builds/{record['id']}/events").json()["events"]
    assert "activation_confirmed" in [event["action"] for event in events]


def test_ambiguous_create_never_blindly_retries(api, network, ir):
    network.fail_create = True
    record = draft(api, ir)
    assert api.post(f"/api/v1/builds/{record['id']}/deploy").status_code == 503
    assert api.post(f"/api/v1/builds/{record['id']}/deploy").status_code == 409
    assert api.get(f"/api/v1/builds/{record['id']}").json()["status"] == "deployment_unknown"
    assert len([call for call in network.calls if call.method == "POST"]) == 1
    result = api.post(f"/api/v1/builds/{record['id']}/reconcile", json={"workflow_id": "wf_1"})
    assert result.status_code == 200 and result.json()["status"] == "deployed"
    assert not activation_calls(network)


def test_ambiguous_activation_requires_read_only_reconciliation(api, network, ir):
    network.fail_activate = True
    record = deployed(api, ir)
    body = review(api, record)
    assert api.post(f"/api/v1/builds/{record['id']}/activate", json=body).status_code == 503
    assert api.get(f"/api/v1/builds/{record['id']}").json()["status"] == "activation_unknown"
    assert api.post(f"/api/v1/builds/{record['id']}/activate", json=body).status_code == 409
    assert (
        api.post(f"/api/v1/builds/{record['id']}/reconcile", json={}).json()["status"] == "active"
    )
    assert len(activation_calls(network)) == 1


def test_prompt_and_body_bounds(api, network):
    assert api.post("/api/v1/builds", json={"prompt": "x" * 12001}).status_code == 422
    assert api.post("/api/v1/builds", content=b"x" * 262145).status_code == 413
    assert not network.calls


def test_unknown_build_and_undeployed_executions(api, ir):
    assert api.get("/api/v1/builds/missing").status_code == 404
    record = draft(api, ir)
    assert api.get(f"/api/v1/builds/{record['id']}/executions").status_code == 409


def test_configuration_without_n8n_key_still_builds(settings, network, ir):
    settings.n8n_api_key = ""
    import httpx

    with TestClient(
        create_app(settings, httpx.MockTransport(network)),
        headers={"Authorization": "Bearer " + settings.master_api_key.get_secret_value()},
    ) as api:
        record = draft(api, ir)
        assert api.post(f"/api/v1/builds/{record['id']}/deploy").status_code == 503
        assert api.get(f"/api/v1/builds/{record['id']}").json()["status"] == "draft"
        assert not network.calls


def test_project_env_overrides_stale_shell_model(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OLLAMA_MODEL=mimo-v2.6:9b\n")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")
    from master_agent.config import Settings

    assert Settings(_env_file=env_file).ollama_model == "mimo-v2.6:9b"
    assert Settings(_env_file=env_file, ollama_model="explicit:1").ollama_model == "explicit:1"
