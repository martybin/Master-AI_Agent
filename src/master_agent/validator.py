"""Pure semantic validation; never resolves DNS or executes generated content."""

import ipaddress
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from master_agent.ir import HttpNode, LiteralValue, SetNode, WorkflowIR

FORBIDDEN_KEYS = {"__proto__", "prototype", "constructor"}
TRIGGERS = {"manual", "webhook", "schedule"}


class Issue(BaseModel):
    code: str
    message: str


class ValidationReport(BaseModel):
    errors: list[Issue] = Field(default_factory=list)
    warnings: list[Issue] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class Policy:
    http_allowed_hosts: frozenset[str] = frozenset()


class InvalidIR(ValueError):
    def __init__(self, report: ValidationReport):
        self.report = report
        super().__init__("; ".join(issue.message for issue in report.errors))


def validate_ir(ir: WorkflowIR, policy: Policy = Policy()) -> ValidationReport:
    report = ValidationReport()

    def error(code: str, message: str) -> None:
        report.errors.append(Issue(code=code, message=message))

    def inspect(value: object, path: str = "ir") -> None:
        if isinstance(value, str):
            if "{{" in value or "}}" in value or value.lstrip().startswith("="):
                error("expression", f"{path}: raw n8n expressions are forbidden; use input paths")
            if len(value) > 8000:
                error("size", f"{path}: string exceeds 8000 characters")
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in FORBIDDEN_KEYS:
                    error("unsafe_key", f"{path}: forbidden object key")
                inspect(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                inspect(item, f"{path}[{index}]")

    inspect(ir.model_dump())
    ids = [node.id for node in ir.nodes]
    if len(ids) != len(set(ids)):
        error("duplicate_id", "Node ids must be unique")
        return report
    nodes = {node.id: node for node in ir.nodes}
    triggers = [node for node in ir.nodes if node.kind in TRIGGERS]
    if len(triggers) != 1:
        error("trigger_count", "Exactly one manual, webhook or schedule trigger is required")
    outgoing = defaultdict(list)
    incoming: Counter = Counter()
    seen_edges = set()
    for edge in ir.edges:
        if edge.source not in nodes or edge.target not in nodes:
            error("missing_node", f"Unknown node in edge {edge.source} -> {edge.target}")
            continue
        key = (edge.source, edge.target, edge.port)
        if key in seen_edges:
            error("duplicate_edge", "Duplicate edges are not allowed")
        seen_edges.add(key)
        outgoing[edge.source].append(edge)
        incoming[edge.target] += 1
        allowed = {"true", "false"} if nodes[edge.source].kind == "if_equals" else {"main"}
        if edge.port not in allowed:
            error("port", f"{edge.source}: invalid output port {edge.port}")
    for node in ir.nodes:
        edges = outgoing[node.id]
        if node.kind in TRIGGERS and incoming[node.id]:
            error("trigger_input", "Triggers cannot have incoming edges")
        if incoming[node.id] > 1:
            error("join", "MVP supports trees, without joins or fan-in")
        ports = Counter(edge.port for edge in edges)
        if any(count > 1 for count in ports.values()):
            error("fanout", f"{node.id}: only one edge per output is supported")
        if node.kind == "if_equals" and set(ports) != {"true", "false"}:
            error("branch", f"{node.id}: both true and false branches are required")
        if node.kind == "respond" and edges:
            error("response_terminal", "Respond nodes must be terminal")
        if node.kind in TRIGGERS and not edges:
            error("empty_trigger", "The trigger must connect to a processing node")
        paths = []
        if hasattr(node, "input_path"):
            paths.append(node.input_path)
        if isinstance(node, SetNode):
            names = [assignment.name for assignment in node.assignments]
            if len(names) != len(set(names)):
                error("assignment", f"{node.id}: assignment names must be unique")
            for assignment in node.assignments:
                if assignment.name in FORBIDDEN_KEYS:
                    error("unsafe_key", "Unsafe assignment name")
                if assignment.value.kind == "input":
                    paths.append(assignment.value.path)
                elif isinstance(assignment.value, LiteralValue):
                    value = assignment.value.value
                    expected = {"string": str, "number": (int, float), "boolean": bool}
                    if (
                        not isinstance(value, expected[assignment.value_type])
                        or assignment.value_type == "number"
                        and isinstance(value, bool)
                    ):
                        error("value_type", f"{node.id}: literal type does not match value_type")
        if isinstance(node, HttpNode):
            try:
                url = urlsplit(node.url)
                host = (url.hostname or "").lower()
                if (
                    url.scheme != "https"
                    or not host
                    or url.port not in {None, 443}
                    or url.username
                    or url.password
                    or url.fragment
                ):
                    error(
                        "http_url", f"{node.id}: use an HTTPS URL on port 443 without credentials"
                    )
                if host not in policy.http_allowed_hosts:
                    error("http_host", f"{node.id}: host is not explicitly allowed: {host}")
                try:
                    ip = ipaddress.ip_address(host)
                    if not ip.is_global:
                        error("http_private", "Private/reserved IP destinations are forbidden")
                except ValueError:
                    if "." not in host or host.endswith((".localhost", ".local", ".internal")):
                        error("http_private", "Local and single-label hostnames are forbidden")
                if node.method == "GET" and node.body:
                    error("http_body", "GET requests cannot have a body")
            except ValueError:
                error("http_url", f"{node.id}: malformed URL")
            paths.extend(value.path for value in node.body.values() if value.kind == "input")
        for path in paths:
            if any(part in FORBIDDEN_KEYS for part in path.split(".")):
                error("unsafe_path", f"{node.id}: unsafe input path")

    degrees = {node_id: incoming[node_id] for node_id in nodes}
    queue = deque(sorted(node_id for node_id, degree in degrees.items() if degree == 0))
    visited = []
    while queue:
        current = queue.popleft()
        visited.append(current)
        for edge in outgoing[current]:
            degrees[edge.target] -= 1
            if degrees[edge.target] == 0:
                queue.append(edge.target)
    if len(visited) != len(nodes):
        error("cycle", "Cycles are not supported")
    if len(triggers) == 1:
        reached = set()
        stack = [triggers[0].id]
        while stack:
            current = stack.pop()
            if current in reached:
                continue
            reached.add(current)
            stack.extend(edge.target for edge in outgoing[current])
        if reached != set(nodes):
            error("unreachable", "Every node must be reachable from the trigger")
        terminals = [nodes[node_id] for node_id in reached if not outgoing[node_id]]
        if triggers[0].kind == "webhook":
            if any(node.kind != "respond" for node in terminals):
                error("webhook_response", "Every webhook branch must end in a respond node")
            report.warnings.append(
                Issue(
                    code="webhook_public",
                    message="Webhook has no authentication in this MVP; keep local",
                )
            )
        elif any(node.kind == "respond" for node in ir.nodes):
            error("response_without_webhook", "Respond nodes require a webhook trigger")
    if any(node.kind == "http_request" for node in ir.nodes):
        report.warnings.append(
            Issue(
                code="external_http",
                message=(
                    "Activation may send data to allowed HTTP hosts. Host allowlists trust DNS; "
                    "use network egress controls for untrusted deployments."
                ),
            )
        )
    if any(node.kind == "ollama_chat" for node in ir.nodes):
        report.warnings.append(
            Issue(
                code="ollama_result",
                message=(
                    "ollama_chat replaces input with the Ollama response; "
                    "read message.content downstream."
                ),
            )
        )
    return report


def require_valid(ir: WorkflowIR, policy: Policy = Policy()) -> ValidationReport:
    report = validate_ir(ir, policy)
    if not report.valid:
        raise InvalidIR(report)
    return report
