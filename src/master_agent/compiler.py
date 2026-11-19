"""Deterministic compiler. This module never contacts the LLM or n8n."""

import hashlib
import json
from collections import defaultdict, deque
from typing import Any
from uuid import UUID, uuid5

from master_agent.config import Settings
from master_agent.ir import InputValue, LiteralValue, WorkflowIR
from master_agent.validator import Policy, require_valid

NAMESPACE = UUID("3ba5b171-c71b-5fe6-9f30-62ed97373033")
NODE_TYPES = {
    "manual": ("n8n-nodes-base.manualTrigger", 1),
    "webhook": ("n8n-nodes-base.webhook", 2),
    "schedule": ("n8n-nodes-base.scheduleTrigger", 1.2),
    "set": ("n8n-nodes-base.set", 3.4),
    "http_request": ("n8n-nodes-base.httpRequest", 4.2),
    "ollama_chat": ("n8n-nodes-base.httpRequest", 4.2),
    "if_equals": ("n8n-nodes-base.if", 2.2),
    "respond": ("n8n-nodes-base.respondToWebhook", 1.4),
}


def canonical_json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def input_js(path: str) -> str:
    return "$json" + "".join(f"[{json.dumps(part)}]" for part in path.split("."))


def value_js(value: LiteralValue | InputValue) -> str:
    return input_js(value.path) if isinstance(value, InputValue) else canonical_json(value.value)


def compile_ir(ir: WorkflowIR, settings: Settings, policy: Policy | None = None) -> dict[str, Any]:
    require_valid(ir, policy or Policy(frozenset(settings.http_allowed_hosts)))
    # Sorting removes list-order variability from the emitted artifact.
    normalized = ir.model_dump()
    normalized["nodes"] = sorted(normalized["nodes"], key=lambda node: node["id"])
    normalized["edges"] = sorted(normalized["edges"], key=canonical_json)
    namespace = uuid5(NAMESPACE, digest(normalized))
    ordered = sorted(ir.nodes, key=lambda node: node.id)
    depth = {}
    children = defaultdict(list)
    targets = {edge.target for edge in ir.edges}
    for edge in ir.edges:
        children[edge.source].append(edge.target)
    queue = deque((node.id, 0) for node in ordered if node.id not in targets)
    while queue:
        node_id, level = queue.popleft()
        depth[node_id] = level
        queue.extend((child, level + 1) for child in sorted(children[node_id]))
    lanes = defaultdict(int)
    emitted = []
    for node in ordered:
        uid = str(uuid5(namespace, node.id))
        node_type, version = NODE_TYPES[node.kind]
        params: dict[str, Any] = {}
        options = {"timeout": 30000, "redirect": {"redirect": {"followRedirects": False}}}
        if node.kind == "webhook":
            params = {
                "httpMethod": node.method,
                "path": node.path,
                "responseMode": "responseNode",
                "options": {},
            }
        elif node.kind == "schedule":
            params = {
                "rule": {"interval": [{"field": "minutes", "minutesInterval": node.every_minutes}]}
            }
        elif node.kind == "set":
            assignments = []
            for assignment in sorted(node.assignments, key=lambda item: item.name):
                value = assignment.value
                assignments.append(
                    {
                        "id": str(uuid5(namespace, f"{node.id}.{assignment.name}")),
                        "name": assignment.name,
                        "type": assignment.value_type,
                        "value": (
                            "={{ " + input_js(value.path) + " }}"
                            if isinstance(value, InputValue)
                            else value.value
                        ),
                    }
                )
            params = {
                "mode": "manual",
                "assignments": {"assignments": assignments},
                "includeOtherFields": node.include_input,
                "options": {},
            }
        elif node.kind == "http_request":
            params = {"method": node.method, "url": node.url, "options": options}
            if node.method == "POST":
                body = ",".join(
                    f"{json.dumps(key)}:{value_js(value)}"
                    for key, value in sorted(node.body.items())
                )
                params.update(
                    sendBody=True,
                    contentType="json",
                    specifyBody="json",
                    jsonBody="={{ JSON.stringify({" + body + "}) }}",
                )
        elif node.kind == "ollama_chat":
            # Only this compiler assembles expressions. All text is JSON-escaped.
            body = (
                '{"model":'
                + json.dumps(settings.ollama_model)
                + ',"stream":false,"think":false,"messages":[{"role":"system","content":'
                + json.dumps(node.system_prompt, ensure_ascii=True)
                + '},{"role":"user","content":String('
                + input_js(node.input_path)
                + ' ?? "")}],"options":{"temperature":0,'
                + '"num_ctx":'
                + str(settings.ollama_num_ctx)
                + ',"num_predict":1024}}'
            )
            options["timeout"] = int(settings.ollama_timeout_seconds * 1000)
            params = {
                "method": "POST",
                "url": settings.n8n_ollama_base_url + "/api/chat",
                "sendBody": True,
                "contentType": "json",
                "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify(" + body + ") }}",
                "options": options,
            }
        elif node.kind == "if_equals":
            params = {
                "conditions": {
                    "options": {
                        "caseSensitive": True,
                        "leftValue": "",
                        "typeValidation": "strict",
                        "version": 2,
                    },
                    "conditions": [
                        {
                            "id": str(uuid5(namespace, node.id + ".condition")),
                            "leftValue": "={{ String(" + input_js(node.input_path) + ' ?? "") }}',
                            "rightValue": node.equals,
                            "operator": {"type": "string", "operation": "equals"},
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            }
        elif node.kind == "respond":
            params = {
                "respondWith": "json",
                "responseBody": "={{ $json }}",
                "options": {"responseCode": node.status_code},
            }
        result = {
            "id": uid,
            "name": node.id,
            "type": node_type,
            "typeVersion": version,
            "position": [depth[node.id] * 280, lanes[depth[node.id]] * 220],
            "parameters": params,
        }
        lanes[depth[node.id]] += 1
        if node.kind == "webhook":
            result["webhookId"] = str(uuid5(namespace, node.id + ".webhook"))
        emitted.append(result)
    connections: dict[str, Any] = {}
    groups = defaultdict(list)
    for edge in sorted(ir.edges, key=lambda edge: (edge.source, edge.port, edge.target)):
        groups[edge.source].append(edge)
    by_id = {node.id: node for node in ir.nodes}
    for source, edges in sorted(groups.items()):
        ports: list[list[dict]] = [[], []] if by_id[source].kind == "if_equals" else [[]]
        for edge in edges:
            ports[1 if edge.port == "false" else 0].append(
                {"node": edge.target, "type": "main", "index": 0}
            )
        connections[source] = {"main": ports}
    return {
        "name": ir.name,
        "nodes": emitted,
        "connections": connections,
        "settings": {
            "executionOrder": "v1",
            "timezone": settings.workflow_timezone,
            "availableInMCP": False,
            "callerPolicy": "none",
        },
        "active": False,
    }


def create_payload(workflow: dict) -> dict:
    """n8n marks active read-only on create. Never send lifecycle fields."""
    return {key: workflow[key] for key in ("name", "nodes", "connections", "settings")}
