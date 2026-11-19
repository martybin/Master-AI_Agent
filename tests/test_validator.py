import copy

import pytest
from pydantic import ValidationError

from master_agent.ir import WorkflowIR
from master_agent.planner import EXAMPLE
from master_agent.validator import Policy, validate_ir


def codes(data, policy=None):
    return {
        issue.code
        for issue in validate_ir(WorkflowIR.model_validate(data), policy or Policy()).errors
    }


def test_valid_assistant(ir):
    report = validate_ir(ir)
    assert report.valid
    assert "webhook_public" in {issue.code for issue in report.warnings}


@pytest.mark.parametrize("extra", [{"active": True}, {"credentials": {}}, {"code": "evil()"}])
def test_closed_schema_rejects_lifecycle_and_code(extra):
    with pytest.raises(ValidationError):
        WorkflowIR.model_validate({**EXAMPLE, **extra})


def test_unknown_node_type():
    data = copy.deepcopy(EXAMPLE)
    data["nodes"][1] = {"id": "answer", "kind": "execute_command", "command": "ls"}
    with pytest.raises(ValidationError):
        WorkflowIR.model_validate(data)


@pytest.mark.parametrize("value", ["={{ $env.SECRET }}", "prefix {{ foo() }}", "  =1", "oops }}"])
def test_expression_injection_is_rejected(value):
    data = copy.deepcopy(EXAMPLE)
    data["nodes"][1]["system_prompt"] = value
    assert "expression" in codes(data)


def test_dangerous_path():
    data = copy.deepcopy(EXAMPLE)
    data["nodes"][1]["input_path"] = "body.constructor.prototype"
    assert "unsafe_path" in codes(data)


def test_duplicates_and_unknown_edges():
    data = copy.deepcopy(EXAMPLE)
    data["nodes"][1]["id"] = "start"
    assert "duplicate_id" in codes(data)
    data = copy.deepcopy(EXAMPLE)
    data["edges"][0]["target"] = "missing"
    assert {"missing_node", "unreachable"} <= codes(data)


def test_cycles_and_trigger_inputs():
    data = copy.deepcopy(EXAMPLE)
    data["edges"].append({"source": "answer", "target": "start"})
    assert {"cycle", "trigger_input"} <= codes(data)


def test_webhook_must_always_respond():
    data = copy.deepcopy(EXAMPLE)
    data["nodes"] = data["nodes"][:2]
    data["edges"] = data["edges"][:1]
    assert "webhook_response" in codes(data)


def test_manual_cannot_respond():
    data = copy.deepcopy(EXAMPLE)
    data["nodes"][0] = {"id": "start", "kind": "manual"}
    assert "response_without_webhook" in codes(data)


def test_branches_must_be_complete():
    data = copy.deepcopy(EXAMPLE)
    data["nodes"][1] = {
        "id": "answer",
        "kind": "if_equals",
        "input_path": "body.kind",
        "equals": "ok",
    }
    assert {"branch", "port"} <= codes(data)


@pytest.mark.parametrize(
    "url",
    [
        "http://httpbin.org/get",
        "https://127.0.0.1/",
        "https://localhost/",
        "https://httpbin.org:123/get",
        "https://user:pass@httpbin.org/",
        "https://httpbin.org.evil.com/get",
        "https://httpbin.org/#fragment",
        "https://httpbin.org:bad/",
    ],
)
def test_http_destinations_are_limited(url):
    data = copy.deepcopy(EXAMPLE)
    data["nodes"][1] = {"id": "answer", "kind": "http_request", "url": url}
    assert codes(data, Policy(frozenset({"httpbin.org", "127.0.0.1", "localhost"})))


def test_allowlisted_http_and_no_get_body():
    data = copy.deepcopy(EXAMPLE)
    data["nodes"][1] = {"id": "answer", "kind": "http_request", "url": "https://httpbin.org/get"}
    policy = Policy(frozenset({"httpbin.org"}))
    assert not codes(data, policy)
    data["nodes"][1]["body"] = {"message": {"kind": "literal", "value": "hello"}}
    assert "http_body" in codes(data, policy)


def test_literal_assignment_type_checked():
    data = copy.deepcopy(EXAMPLE)
    data["nodes"][1] = {
        "id": "answer",
        "kind": "set",
        "assignments": [
            {"name": "text", "value": {"kind": "literal", "value": True}, "value_type": "number"}
        ],
    }
    assert "value_type" in codes(data)


def test_schedule_respects_n8n_minute_interval_limits():
    data = copy.deepcopy(EXAMPLE)
    data["nodes"][0] = {"id": "start", "kind": "schedule", "every_minutes": 60}
    with pytest.raises(ValidationError):
        WorkflowIR.model_validate(data)
