"""Extensible n8n node catalog and a native workflow compiler beyond the small IR."""

import json
from typing import Any
from uuid import uuid5

from jsonschema import Draft202012Validator
from pydantic import Field

from master_agent.compiler import NAMESPACE, digest
from master_agent.config import Settings
from master_agent.ir import ClosedModel
from master_agent.local.store import LocalStore
from master_agent.repository import StateConflict


class CatalogEntry(ClosedModel):
    type: str = Field(min_length=3, max_length=180, pattern=r"^[A-Za-z0-9@_./-]+$")
    label: str = Field(min_length=1, max_length=160)
    versions: list[float] = Field(min_length=1, max_length=40)
    trigger: bool = False
    outputs: int = Field(default=1, ge=0, le=30)
    description: str = Field(default="", max_length=2000)
    parameter_schema: dict = Field(default_factory=lambda: {"type": "object"})
    n8n_properties: list[dict] = Field(default_factory=list)


class NativeNode(ClosedModel):
    name: str = Field(min_length=1, max_length=80)
    type: str
    typeVersion: float
    parameters: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(
        default_factory=dict,
        description="Credential type to existing n8n credential ID; never a token",
    )


class NativeLink(ClosedModel):
    node: str
    type: str = "main"
    index: int = Field(default=0, ge=0, le=30)


class NativeSpec(ClosedModel):
    name: str = Field(min_length=1, max_length=100)
    nodes: list[NativeNode] = Field(min_length=1, max_length=100)
    connections: dict[str, dict[str, list[list[NativeLink]]]] = Field(default_factory=dict)


class NativeCatalog:
    def __init__(self, store: LocalStore):
        self.store = store

    def initialize(self):
        types = [
            ("manualTrigger", "شروع دستی", [1], True, 1, {}),
            (
                "webhook",
                "Webhook",
                [2],
                True,
                1,
                {
                    "path": {"type": "string"},
                    "httpMethod": {"enum": ["GET", "POST"]},
                    "responseMode": {"enum": ["onReceived", "lastNode", "responseNode"]},
                },
            ),
            ("scheduleTrigger", "زمان‌بندی", [1.2], True, 1, {"rule": {"type": "object"}}),
            (
                "httpRequest",
                "درخواست HTTP",
                [4.2],
                False,
                1,
                {
                    "url": {"type": "string"},
                    "method": {
                        "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
                    },
                },
            ),
            ("set", "تنظیم فیلد", [3.4], False, 1, {}),
            ("if", "شرط", [2.2], False, 2, {}),
            ("respondToWebhook", "پاسخ Webhook", [1.4], False, 0, {}),
            (
                "code",
                "کد JavaScript/Python",
                [2],
                False,
                1,
                {"language": {"enum": ["javaScript", "python"]}, "jsCode": {"type": "string"}},
            ),
            ("merge", "ادغام مسیرها", [3.2], False, 1, {}),
            ("splitInBatches", "پردازش دسته‌ای", [3], False, 2, {}),
            (
                "telegram",
                "Telegram Bot",
                [1.2],
                False,
                1,
                {
                    "resource": {"type": "string"},
                    "operation": {"type": "string"},
                    "chatId": {"type": ["string", "integer"]},
                    "text": {"type": "string"},
                },
            ),
            (
                "telegramTrigger",
                "Telegram Bot Trigger",
                [1.2],
                True,
                1,
                {"updates": {"type": "array"}},
            ),
        ]
        for name, label, versions, trigger, outputs, properties in types:
            key = "n8n-nodes-base." + name
            if any(row["type"] == key for row in self.store.list("catalog")):
                continue
            required = {"webhook": ["path"], "httpRequest": ["url"]}.get(name, [])
            self.install(
                CatalogEntry(
                    type=key,
                    label=label,
                    versions=versions,
                    trigger=trigger,
                    outputs=outputs,
                    parameter_schema={
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                ),
                builtin=True,
            )

    def install(self, entry: CatalogEntry, builtin=False):
        try:
            Draft202012Validator.check_schema(entry.parameter_schema)
        except Exception:
            raise StateConflict("Invalid parameter JSON Schema") from None

        # Catalog schemas are data, never executable imports or remote schema fetches.
        def check(value):
            if isinstance(value, dict):
                for ref in ("$ref", "$dynamicRef"):
                    if ref in value and not value[ref].startswith("#"):
                        raise StateConflict("Remote JSON Schema references are not allowed")
                for item in value.values():
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)

        check(entry.parameter_schema)
        record = {**entry.model_dump(), "source": "builtin" if builtin else "owner"}
        self.store.put("catalog", entry.type, record)
        return record

    def search(self, query=""):
        words = query.lower().split()
        return [
            {k: row[k] for k in ("type", "label", "versions", "trigger", "description")}
            for row in self.store.list("catalog")
            if not words
            or any(word in json.dumps(row, ensure_ascii=False).lower() for word in words)
        ][:40]

    def import_n8n(self, descriptions: list[dict]):
        entries = []
        for raw in descriptions:
            properties = raw.get("properties", [])
            schema_properties = {}
            required = []
            for prop in properties:
                if (
                    not isinstance(prop, dict)
                    or not prop.get("name")
                    or prop.get("type") == "notice"
                ):
                    continue
                kind = prop.get("type")
                base = {
                    "type": {
                        "string": "string",
                        "number": "number",
                        "boolean": "boolean",
                        "multiOptions": "array",
                        "collection": "object",
                        "fixedCollection": "object",
                        "filter": "object",
                        "assignmentCollection": "object",
                    }.get(kind, "string")
                }
                if kind in {"options", "resourceLocator", "json"}:
                    base = {}
                schema_properties[prop["name"]] = {
                    "anyOf": [base, {"type": "string", "pattern": "^="}]
                }
                if prop.get("required") and not prop.get("displayOptions"):
                    required.append(prop["name"])
            versions = raw.get("version", 1)
            if not isinstance(versions, list):
                versions = [versions]
            outputs = raw.get("outputs", ["main"])
            entries.append(
                CatalogEntry(
                    type=raw["name"],
                    label=raw.get("displayName", raw["name"]),
                    description=str(raw.get("description", ""))[:2000],
                    versions=versions,
                    trigger="trigger" in raw.get("group", []),
                    outputs=len(outputs) if isinstance(outputs, list) else 2,
                    parameter_schema={
                        "type": "object",
                        "properties": schema_properties,
                        "required": required,
                        "additionalProperties": False,
                    },
                    n8n_properties=properties,
                )
            )
        # Validate the complete import before mutating the catalog.
        for entry in entries:
            Draft202012Validator.check_schema(entry.parameter_schema)
        for entry in entries:
            self.install(entry)
        return {"imported": len(entries)}

    def describe(self, node_type: str):
        return self.store.get("catalog", node_type)

    def compile(self, spec: NativeSpec, settings: Settings):
        names = [node.name for node in spec.nodes]
        if len(set(names)) != len(names):
            raise StateConflict("Native node names must be unique")
        allowed = set(names)
        triggers = []
        for node in spec.nodes:
            entry = self.describe(node.type)
            if node.typeVersion not in entry["versions"]:
                raise StateConflict(f"Unsupported installed version for {node.type}")
            errors = list(
                Draft202012Validator(entry["parameter_schema"]).iter_errors(node.parameters)
            )
            if errors:
                raise StateConflict(
                    f"{node.name}: invalid parameters at " + ".".join(map(str, errors[0].path))
                )
            if entry["trigger"]:
                triggers.append(node.name)
            if any(not value or len(value) > 160 for value in node.credentials.values()):
                raise StateConflict("Use existing credential IDs only")
        if not triggers:
            raise StateConflict("A native workflow requires at least one registered trigger")
        edges = []
        for source, channels in spec.connections.items():
            if source not in allowed:
                raise StateConflict("Unknown connection source")
            source_node = next(n for n in spec.nodes if n.name == source)
            for channel, ports in channels.items():
                if channel == "main" and len(ports) > self.describe(source_node.type)["outputs"]:
                    raise StateConflict("Output port exceeds node output count")
                for port, links in enumerate(ports):
                    for link in links:
                        if link.node not in allowed or link.type != channel:
                            raise StateConflict("Unknown node or mismatched connection channel")
                        edges.append(
                            {
                                "source": source,
                                "target": link.node,
                                "port": str(port),
                                "channel": channel,
                            }
                        )
        reached = set(triggers)
        while True:
            updated = reached | {edge["target"] for edge in edges if edge["source"] in reached}
            # n8n AI subnodes (model, memory, tools) point INTO their consuming agent.
            # They are attached to a reachable consumer, not executed by a main input.
            updated |= {
                edge["source"]
                for edge in edges
                if edge["channel"] != "main" and edge["target"] in reached
            }
            if updated == reached:
                break
            reached = updated
        if reached != allowed:
            raise StateConflict("Every node must be reachable from a trigger")
        namespace = uuid5(NAMESPACE, digest(spec.model_dump()))
        nodes = []
        for index, node in enumerate(spec.nodes):
            data = node.model_dump(exclude={"credentials"})
            data.update(id=str(uuid5(namespace, node.name)), position=[index * 260, 0])
            if node.credentials:
                data["credentials"] = {
                    kind: {"id": credential_id} for kind, credential_id in node.credentials.items()
                }
            if node.type == "n8n-nodes-base.webhook":
                data["webhookId"] = str(uuid5(namespace, node.name + ".webhook"))
            nodes.append(data)
        workflow = {
            "name": spec.name,
            "nodes": nodes,
            "connections": {
                s: {
                    c: [[link.model_dump() for link in links] for links in ports]
                    for c, ports in channels.items()
                }
                for s, channels in spec.connections.items()
            },
            "settings": {
                "executionOrder": "v1",
                "timezone": settings.workflow_timezone,
                "availableInMCP": False,
                "callerPolicy": "none",
                "executionTimeout": 1200,
            },
            "active": False,
        }
        ir = {
            "native_version": "1.0",
            "name": spec.name,
            "nodes": [
                {"id": n.name, "kind": "native", "node_type": n.type, "parameters": n.parameters}
                for n in spec.nodes
            ],
            "edges": edges,
            "native_spec": spec.model_dump(),
        }
        warnings = [
            {
                "code": "native_workflow",
                "message": "Native workflow: expressions and code require owner review. Parameter "
                "schemas do not prove runtime/semantic correctness.",
            }
        ]
        return ir, workflow, warnings
