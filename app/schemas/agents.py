from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field, HttpUrl, SecretStr

class AiInsight(BaseModel):
    id: str
    agent: str
    symbol: str
    title: str
    summary: str
    severity: Literal["info", "watch", "risk", "opportunity"]
    confidence: int
    evidence: list[str]
    created_at: str


class AiBriefing(BaseModel):
    id: str
    title: str
    summary: str
    risk_level: Literal["info", "watch", "risk", "opportunity"]
    generated_at: str
    opportunities: list[str]
    risks: list[str]
    actions: list[str]
    evidence: list[str]


AgentTemplate = Literal["market_monitor", "news_tagger", "news_event_extractor", "news_impact_mapper", "news_impact_validator", "news_analyst", "economic_event_analyst", "risk_sentinel", "factor_research", "briefing"]
AgentRuntimeMode = Literal["rule", "llm", "hybrid"]


class AgentProviderProfile(BaseModel):
    id: str
    name: str
    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: int
    temperature: float
    max_tokens: int
    enabled: bool
    secret_configured: bool = False
    credential_id: str | None = None
    credential_origin: Literal["database", "environment", "none", "error"] = "none"
    secret_hint: str = ""
    credential_test_status: Literal["unknown", "pending", "pass", "partial", "fail"] = "unknown"
    created_at: str
    updated_at: str


class AgentProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=8, max_length=500)
    model: str = Field(min_length=1, max_length=120)
    api_key_env: str = Field(min_length=2, max_length=120)
    api_key: SecretStr | None = Field(default=None, max_length=10000)
    timeout_seconds: int = Field(default=30, ge=3, le=180)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=1200, ge=100, le=8000)
    enabled: bool = True


class AgentProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    api_key_env: str | None = Field(default=None, min_length=2, max_length=120)
    api_key: SecretStr | None = Field(default=None, max_length=10000)
    timeout_seconds: int | None = Field(default=None, ge=3, le=180)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=100, le=8000)
    enabled: bool | None = None


class AgentConfig(BaseModel):
    id: str
    template_type: AgentTemplate
    builtin: bool
    name: str
    description: str
    enabled: bool
    runtime_mode: AgentRuntimeMode
    provider_id: str | None = None
    model_route_id: str | None = None
    symbol_scope: Literal["watchlist", "all", "custom"]
    symbols: list[str]
    schedule_enabled: bool
    interval_seconds: int
    triggers: list[str]
    system_prompt: str
    task_prompt: str
    settings: dict[str, object]
    notification_channel_ids: list[str]
    fallback_to_rules: bool
    last_run_at: str | None = None
    next_run_at: str | None = None
    created_at: str
    updated_at: str


class AgentCreate(BaseModel):
    template_type: AgentTemplate
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    runtime_mode: AgentRuntimeMode = "rule"
    provider_id: str | None = None
    model_route_id: str | None = None
    symbol_scope: Literal["watchlist", "all", "custom"] = "watchlist"
    symbols: list[str] = Field(default_factory=list)
    schedule_enabled: bool = False
    interval_seconds: int = Field(default=300, ge=30, le=86400)
    triggers: list[str] = Field(default_factory=lambda: ["manual"])
    system_prompt: str = Field(default="", max_length=12000)
    task_prompt: str = Field(default="", max_length=12000)
    settings: dict[str, object] = Field(default_factory=dict)
    notification_channel_ids: list[str] = Field(default_factory=list)
    fallback_to_rules: bool = True
    enabled: bool = True


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    runtime_mode: AgentRuntimeMode | None = None
    provider_id: str | None = None
    model_route_id: str | None = None
    symbol_scope: Literal["watchlist", "all", "custom"] | None = None
    symbols: list[str] | None = None
    schedule_enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=30, le=86400)
    triggers: list[str] | None = None
    system_prompt: str | None = Field(default=None, max_length=12000)
    task_prompt: str | None = Field(default=None, max_length=12000)
    settings: dict[str, object] | None = None
    notification_channel_ids: list[str] | None = None
    fallback_to_rules: bool | None = None
    enabled: bool | None = None


class AgentRunRequest(BaseModel):
    symbol: str | None = None
    context: dict[str, object] = Field(default_factory=dict)
    test: bool = False


class AgentRun(BaseModel):
    id: str
    agent_id: str
    agent_name: str
    trigger: str
    symbol: str | None = None
    status: Literal["queued", "running", "succeeded", "failed", "canceled"]
    runtime_mode: AgentRuntimeMode
    output: dict[str, object] | None = None
    error: str | None = None
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    test: bool = False
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class AgentRunSummary(BaseModel):
    id: str
    agent_id: str
    agent_name: str
    trigger: str
    symbol: str | None = None
    status: Literal["queued", "running", "succeeded", "failed", "canceled"]
    runtime_mode: AgentRuntimeMode
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    test: bool = False
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class AgentRunDetail(AgentRun):
    trigger_input: dict[str, object] = Field(default_factory=dict)
    input_snapshot: dict[str, object] | None = None


class AgentProposal(BaseModel):
    id: str
    run_id: str
    agent_id: str
    resource_type: str
    action: str
    title: str
    payload: dict[str, object]
    status: Literal["pending", "approved", "rejected", "executed", "failed"]
    reviewed_by: str | None = None
    review_note: str | None = None
    created_at: str
    reviewed_at: str | None = None


class AgentProposalReview(BaseModel):
    note: str = Field(default="", max_length=500)
    payload_override: dict[str, object] | None = None


class NewsSymbolTag(BaseModel):
    id: str
    article_id: str
    instrument_id: str
    symbol: str
    name: str
    relation: Literal["primary", "secondary"]
    confidence: float
    direction: Literal["positive", "negative", "neutral", "mixed"] = "neutral"
    origin: Literal["upstream", "rule", "ai", "manual", "legacy"]
    status: Literal["accepted", "pending", "rejected", "superseded"]
    evidence: str = ""
    agent_run_id: str | None = None
    model: str | None = None
    tagging_version: int = 1
    created_at: str
    updated_at: str


NewsImpactTargetType = Literal["instrument", "sector", "industry", "index", "etf", "asset_class", "commodity", "currency", "country", "market"]
NewsImpactDirection = Literal["positive", "negative", "neutral", "mixed", "uncertain"]
NewsImpactSourceType = Literal["topic", "macro_factor", "country", "region", "event"]


class NewsEventAnnotation(BaseModel):
    article_id: str
    taxonomy: Literal["geopolitical", "monetary_policy", "macro_data", "regulatory", "corporate_action", "earnings", "product", "supply_chain", "commodity", "flow", "other"]
    scope: Literal["company", "sector", "market", "macro", "global"]
    summary: str
    topics: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    actors: list[str] = Field(default_factory=list)
    macro_factors: list[str] = Field(default_factory=list)
    horizon: Literal["intraday", "short", "medium", "long"] = "short"
    significance: int = Field(default=50, ge=0, le=100)
    novelty: int = Field(default=100, ge=0, le=100)
    content_completeness: float = Field(default=0.5, ge=0, le=1)
    event_cluster_id: str
    origin: Literal["rule", "ai", "manual", "legacy"] = "rule"
    model: str | None = None
    pipeline_version: int = 2
    created_at: str
    updated_at: str


class NewsImpactLink(BaseModel):
    id: str
    article_id: str
    target_type: NewsImpactTargetType
    target_id: str
    target_label: str
    symbol: str = ""
    link_type: Literal["direct", "derived", "legacy_unspecified"]
    channel: str
    direction: NewsImpactDirection = "uncertain"
    relevance: float = Field(ge=0, le=1)
    impact_score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    horizon: Literal["intraday", "short", "medium", "long"] = "short"
    path: list[str] = Field(default_factory=list)
    evidence: str = ""
    origin: Literal["rule", "ai", "manual", "legacy"]
    status: Literal["accepted", "pending", "rejected", "superseded"]
    graph_edge_ids: list[str] = Field(default_factory=list)
    agent_run_id: str | None = None
    model: str | None = None
    pipeline_version: int = 2
    created_at: str
    updated_at: str


class NewsImpactGraphEdge(BaseModel):
    id: str
    source_type: str
    source_id: str
    source_label: str
    target_type: NewsImpactTargetType
    target_id: str
    target_label: str
    relation: str
    direction: NewsImpactDirection = "uncertain"
    weight: float = Field(default=0.8, ge=0, le=1)
    evidence: str
    provenance: str
    status: Literal["pending", "approved", "rejected", "disabled"]
    created_by: str
    reviewed_by: str | None = None
    created_at: str
    updated_at: str


class NewsImpactGraphEdgeCreate(BaseModel):
    source_type: NewsImpactSourceType
    source_id: str = Field(min_length=1, max_length=120)
    source_label: str = Field(min_length=1, max_length=160)
    target_type: NewsImpactTargetType
    target_id: str = Field(min_length=1, max_length=160)
    target_label: str = Field(min_length=1, max_length=160)
    relation: str = Field(min_length=1, max_length=80)
    direction: NewsImpactDirection = "uncertain"
    weight: float = Field(default=0.8, ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=1000)
    provenance: str = Field(min_length=1, max_length=500)


class NewsImpactGraphNode(BaseModel):
    id: str
    type: str
    entity_id: str
    label: str
    edge_count: int = 0
    in_degree: int = 0
    out_degree: int = 0


class NewsImpactGraphViewEdge(BaseModel):
    id: str
    source: str
    target: str
    relation: str
    direction: NewsImpactDirection
    weight: float = Field(ge=0, le=1)
    status: Literal["pending", "approved", "rejected", "disabled", "failed"]
    evidence: str = ""
    provenance: str = ""
    proposal_id: str | None = None
    article_id: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    path: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    normalization_warnings: list[str] = Field(default_factory=list)
    created_by: str | None = None
    reviewed_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class NewsImpactGraphView(BaseModel):
    nodes: list[NewsImpactGraphNode] = Field(default_factory=list)
    edges: list[NewsImpactGraphViewEdge] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    total_nodes: int = 0
    total_edges: int = 0
    truncated: bool = False


class NewsImpactDiagnosticFacet(BaseModel):
    key: str
    label: str
    count: int


class NewsImpactDiagnosticItem(BaseModel):
    id: str
    run_id: str
    title: str
    payload: dict[str, object] = Field(default_factory=dict)
    validation_errors: list[str] = Field(default_factory=list)
    reason_keys: list[str] = Field(default_factory=list)
    review_note: str | None = None
    created_at: str


class NewsImpactDiagnosticPage(BaseModel):
    items: list[NewsImpactDiagnosticItem] = Field(default_factory=list)
    facets: list[NewsImpactDiagnosticFacet] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class NewsImpactGraphEdgeReview(BaseModel):
    status: Literal["approved", "rejected", "disabled"]


class NewsImpactOverrideUpdate(BaseModel):
    impacts: list[dict[str, object]] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=500)


class NewsTaggingStatus(BaseModel):
    provider_ready: bool
    agent_mode: AgentRuntimeMode
    total_articles: int
    queued: int
    running: int
    waiting_provider: int
    needs_review: int
    succeeded: int
    failed: int
    accepted_tags: int
    pending_tags: int
    auto_accept_rate: float | None = None
    annotated_articles: int = 0
    accepted_impacts: int = 0
    pending_impacts: int = 0
    accepted_derived_impacts: int = 0
    pipeline_agents: dict[str, str] = Field(default_factory=dict)


class NewsTaggingBackfillRequest(BaseModel):
    days: int = Field(default=30, ge=1, le=90)
    batch_size: int = Field(default=8, ge=1, le=20)
    scope: Literal["recent", "unlinked", "impact_priority"] = "recent"
    engine: Literal["agent", "rule"] = "agent"


class NewsTaggingBackfillResult(BaseModel):
    days: int
    matched: int
    queued: int
    skipped: int


class NewsSymbolOverrideUpdate(BaseModel):
    primary_instrument_ids: list[str] = Field(default_factory=list, max_length=5)
    secondary_instrument_ids: list[str] = Field(default_factory=list, max_length=8)
    note: str = Field(default="", max_length=500)


class NewsTaggingReviewOverride(BaseModel):
    primary_symbols: list[str] = Field(default_factory=list, max_length=5)
    secondary_symbols: list[str] = Field(default_factory=list, max_length=8)
