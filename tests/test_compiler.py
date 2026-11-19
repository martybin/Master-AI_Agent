import copy
import json
from pathlib import Path

import pytest

from master_agent.compiler import compile_ir, create_payload, digest
from master_agent.ir import WorkflowIR
from master_agent.validator import InvalidIR


def test_compiler_deterministic_and_inactive(ir, settings):
    first = compile_ir(ir, settings)
    other = ir.model_dump()
    other["nodes"].reverse()
    other["edges"].reverse()
    assert compile_ir(WorkflowIR.model_validate(other), settings) == first
    assert first["active"] is False
    assert "active" not in create_payload(first)
    assert len({node["id"] for node in first["nodes"]}) == len(first["nodes"])
    assert digest(first) == digest(json.loads(json.dumps(first)))


def test_ollama_wire_protocol_and_input_expression(ir, settings):
    workflow = compile_ir(ir, settings)
    node = next(node for node in workflow["nodes"] if node["name"] == "answer")
    assert node["type"] == "n8n-nodes-base.httpRequest"
    assert node["typeVersion"] == 4.2
    assert node["parameters"]["url"] == "http://127.0.0.1:11434/api/chat"
    assert '"stream":false' in node["parameters"]["jsonBody"]
    assert '$json["body"]["message"]' in node["parameters"]["jsonBody"]
    assert node["parameters"]["options"]["redirect"]["redirect"]["followRedirects"] is False


def test_if_output_indexes_and_schedule(settings):
    ir = WorkflowIR.model_validate(
        {
            "name": "Branching schedule",
            "nodes": [
                {"id": "start", "kind": "schedule", "every_minutes": 15},
                {"id": "check", "kind": "if_equals", "input_path": "kind", "equals": "ok"},
                {
                    "id": "yes",
                    "kind": "set",
                    "assignments": [
                        {
                            "name": "ok",
                            "value": {"kind": "literal", "value": True},
                            "value_type": "boolean",
                        }
                    ],
                },
                {
                    "id": "no",
                    "kind": "set",
                    "assignments": [
                        {
                            "name": "ok",
                            "value": {"kind": "literal", "value": False},
                            "value_type": "boolean",
                        }
                    ],
                },
            ],
            "edges": [
                {"source": "start", "target": "check"},
                {"source": "check", "target": "yes", "port": "true"},
                {"source": "check", "target": "no", "port": "false"},
            ],
        }
    )
    workflow = compile_ir(ir, settings)
    assert workflow["connections"]["check"]["main"] == [
        [{"node": "yes", "type": "main", "index": 0}],
        [{"node": "no", "type": "main", "index": 0}],
    ]
    start = next(node for node in workflow["nodes"] if node["name"] == "start")
    assert start["parameters"]["rule"]["interval"][0]["minutesInterval"] == 15


def test_invalid_ir_cannot_bypass_validation(ir, settings):
    data = ir.model_dump()
    data["nodes"][1]["system_prompt"] = "={{ evil() }}"
    with pytest.raises(InvalidIR):
        compile_ir(WorkflowIR.model_validate(data), settings)


def test_post_json_escapes_literals(ir, settings):
    data = copy.deepcopy(ir.model_dump())
    data["nodes"][1] = {
        "id": "answer",
        "kind": "http_request",
        "url": "https://httpbin.org/post",
        "method": "POST",
        "body": {"text": {"kind": "literal", "value": '"; evil(); "'}},
    }
    workflow = compile_ir(WorkflowIR.model_validate(data), settings)
    node = next(node for node in workflow["nodes"] if node["name"] == "answer")
    expr = node["parameters"]["jsonBody"]
    literal_object = expr.removeprefix("={{ JSON.stringify(").removesuffix(") }}")
    assert json.loads(literal_object) == {"text": '"; evil(); "'}


@pytest.mark.parametrize("path", sorted(Path("examples").glob("*.ir.json")))
def test_shipped_examples_compile(path, settings):
    workflow = compile_ir(WorkflowIR.model_validate_json(path.read_text()), settings)
    assert workflow["active"] is False


def test_documented_schema_matches_runtime():
    assert (
        json.loads(Path("docs/workflow-ir.schema.json").read_text())
        == WorkflowIR.model_json_schema()
    )
