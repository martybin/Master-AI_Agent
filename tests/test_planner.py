import json

from master_agent.planner import EXAMPLE


def test_bounded_repair_uses_validation_feedback(api, network):
    network.plan_outputs = ["{}", json.dumps(EXAMPLE)]
    response = api.post("/api/v1/builds", json={"prompt": "Make a local assistant with a webhook"})
    assert response.status_code == 201, response.text
    assert response.json()["attempts"] == 2
    calls = [call for call in network.calls if call.url.path == "/api/chat"]
    second = json.loads(calls[1].content)
    assert "Validation errors" in second["messages"][-1]["content"]
    assert "tools" not in second
    assert "test-n8n-secret" not in calls[1].content.decode()


def test_budget_exhaustion_returns_errors_without_build_or_deploy(api, network):
    network.plan_outputs = ["not json"]
    response = api.post("/api/v1/builds", json={"prompt": "Make a local assistant with a webhook"})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["attempts"] == 3
    assert len([call for call in network.calls if call.url.path == "/api/chat"]) == 3
    assert all(not call.url.path.startswith("/api/v1") for call in network.calls)
