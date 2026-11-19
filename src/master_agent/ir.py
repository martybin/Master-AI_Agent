"""Versioned, closed intermediate representation. No arbitrary n8n parameters or code."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
InputPath = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*){0,7}$")]
Scalar = StrictStr | StrictBool | StrictInt | StrictFloat


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class LiteralValue(ClosedModel):
    kind: Literal["literal"] = "literal"
    value: Scalar


class InputValue(ClosedModel):
    kind: Literal["input"] = "input"
    path: InputPath


Value = Annotated[LiteralValue | InputValue, Field(discriminator="kind")]


class NodeBase(ClosedModel):
    id: Identifier


class ManualNode(NodeBase):
    kind: Literal["manual"]


class WebhookNode(NodeBase):
    kind: Literal["webhook"]
    path: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9/-]{0,79}$")]
    method: Literal["GET", "POST"] = "POST"


class ScheduleNode(NodeBase):
    kind: Literal["schedule"]
    every_minutes: int = Field(ge=1, le=59)


class Assignment(ClosedModel):
    name: Identifier
    value: Value
    value_type: Literal["string", "number", "boolean"] = "string"


class SetNode(NodeBase):
    kind: Literal["set"]
    assignments: list[Assignment] = Field(min_length=1, max_length=20)
    include_input: bool = True


class HttpNode(NodeBase):
    kind: Literal["http_request"]
    method: Literal["GET", "POST"] = "GET"
    url: str = Field(max_length=2048)
    body: dict[Identifier, Value] = Field(default_factory=dict, max_length=20)


class OllamaNode(NodeBase):
    kind: Literal["ollama_chat"]
    system_prompt: str = Field(min_length=1, max_length=6000)
    input_path: InputPath


class IfNode(NodeBase):
    kind: Literal["if_equals"]
    input_path: InputPath
    equals: str = Field(max_length=1000)


class RespondNode(NodeBase):
    kind: Literal["respond"]
    status_code: Literal[200, 201, 202, 400] = 200


Node = Annotated[
    ManualNode
    | WebhookNode
    | ScheduleNode
    | SetNode
    | HttpNode
    | OllamaNode
    | IfNode
    | RespondNode,
    Field(discriminator="kind"),
]


class Edge(ClosedModel):
    source: Identifier
    target: Identifier
    port: Literal["main", "true", "false"] = "main"


class WorkflowIR(ClosedModel):
    ir_version: Literal["1.0"] = "1.0"
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    nodes: list[Node] = Field(min_length=2, max_length=20)
    edges: list[Edge] = Field(min_length=1, max_length=25)
