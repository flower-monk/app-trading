from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


WorkflowStatus = Literal["draft", "published", "disabled"]
WorkflowRunStatus = Literal["queued", "running", "waiting_approval", "succeeded", "failed", "canceled"]
NodeKind = Literal["trigger", "resource", "agent", "condition", "transform", "approval", "output"]
SpanKind = Literal["workflow", "agent", "model", "tool", "condition", "guardrail", "approval", "output"]


class WorkflowNode(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    kind: NodeKind
    type: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    x: float = 0
    y: float = 0
    config: dict[str, object] = Field(default_factory=dict)


class WorkflowEdge(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    source: str
    target: str
    source_port: str = "success"
    condition: str | None = None


class WorkflowGraph(BaseModel):
    nodes: list[WorkflowNode] = Field(default_factory=list, max_length=50)
    edges: list[WorkflowEdge] = Field(default_factory=list, max_length=120)

    @model_validator(mode="after")
    def references_existing_nodes(self):
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Workflow node IDs must be unique")
        edge_ids = [edge.id for edge in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("Workflow edge IDs must be unique")
        missing = {
            value
            for edge in self.edges
            for value in (edge.source, edge.target)
            if value not in set(node_ids)
        }
        if missing:
            raise ValueError(f"Workflow edges reference missing nodes: {', '.join(sorted(missing))}")
        return self


class WorkflowDefinitionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    objective: str = Field(default="", max_length=1000)
    template: str = Field(default="blank", max_length=80)


class WorkflowDefinitionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    objective: str | None = Field(default=None, max_length=1000)
    graph: WorkflowGraph | None = None
    enabled: bool | None = None


class WorkflowDefinition(BaseModel):
    id: str
    name: str
    objective: str
    status: WorkflowStatus
    enabled: bool
    draft_version: int
    published_version: int | None = None
    graph: WorkflowGraph
    created_by: str
    created_at: str
    updated_at: str


class WorkflowVersion(BaseModel):
    id: str
    workflow_id: str
    version: int
    graph: WorkflowGraph
    content_hash: str
    published_by: str
    created_at: str


class WorkflowValidation(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: str


class WorkflowRunRequest(BaseModel):
    symbol: str | None = Field(default=None, max_length=30)
    context: dict[str, object] = Field(default_factory=dict)
    test: bool = False


class WorkflowRun(BaseModel):
    id: str
    workflow_id: str
    workflow_name: str
    workflow_version: int
    trace_id: str
    trigger: str
    status: WorkflowRunStatus
    symbol: str | None = None
    input: dict[str, object] = Field(default_factory=dict)
    output: dict[str, object] | None = None
    error: str | None = None
    test: bool = False
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str


class WorkflowSpan(BaseModel):
    id: str
    trace_id: str
    run_id: str
    parent_span_id: str | None = None
    node_id: str
    node_name: str
    kind: SpanKind
    operation: str
    status: str
    input: dict[str, object] = Field(default_factory=dict)
    output: dict[str, object] | None = None
    decision_summary: str = ""
    resource_refs: list[dict[str, str]] = Field(default_factory=list)
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    retry_count: int = 0
    latency_ms: int = 0
    error: str | None = None
    started_at: str
    finished_at: str | None = None


class WorkflowTrace(BaseModel):
    run: WorkflowRun
    spans: list[WorkflowSpan]
    graph: WorkflowGraph


class WorkflowApprovalDecision(BaseModel):
    note: str = Field(default="", max_length=500)
    payload_override: dict[str, object] | None = None


class WorkflowApproval(BaseModel):
    id: str
    run_id: str
    span_id: str
    node_id: str
    title: str
    payload: dict[str, object]
    status: Literal["pending", "approved", "rejected"]
    reviewed_by: str | None = None
    review_note: str | None = None
    created_at: str
    reviewed_at: str | None = None


class ToolDefinition(BaseModel):
    name: str
    version: str
    title: str
    description: str
    category: str
    permission_level: Literal["L0", "L1", "L2", "L3"]
    side_effect: bool
    approval_required: bool
    input_schema: dict[str, object]
    output_schema: dict[str, object]


class InvestmentArtifact(BaseModel):
    id: str
    artifact_type: Literal["chart_marker", "risk_signal", "news_impact", "factor_candidate", "alert_candidate"]
    title: str
    summary: str
    scope_type: Literal["instrument", "market", "portfolio", "global"]
    scope_ids: list[str]
    direction: Literal["positive", "negative", "neutral", "mixed"]
    severity: Literal["info", "watch", "risk", "opportunity"]
    confidence: float
    evidence: list[str]
    payload: dict[str, object]
    workflow_id: str
    workflow_version: int
    run_id: str
    trace_id: str
    span_id: str
    status: Literal["active", "expired", "superseded", "rejected"]
    valid_from: str
    valid_until: str
    created_at: str
