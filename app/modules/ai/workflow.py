"""Versioned, observable Agent workflow orchestration."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from time import perf_counter
from typing import Annotated, Callable, TypedDict

os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from fastapi import HTTPException
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.modules.infrastructure import database
from app.schemas import (
    InvestmentArtifact,
    ToolDefinition,
    WorkflowApproval,
    WorkflowApprovalDecision,
    WorkflowDefinition,
    WorkflowDefinitionCreate,
    WorkflowDefinitionUpdate,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunRequest,
    WorkflowSpan,
    WorkflowTrace,
    WorkflowValidation,
    WorkflowVersion,
)


def _json(value: str | None, fallback: object) -> object:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: object) -> object:
    from app.modules.ai.service import sanitize_agent_input

    return sanitize_agent_input(value)


def _model(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_model(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _model(item) for key, item in value.items()}
    return value


def _merge_results(left: dict[str, object], right: dict[str, object]) -> dict[str, object]:
    return {**left, **right}


class WorkflowState(TypedDict, total=False):
    run_id: str
    trace_id: str
    workflow_id: str
    workflow_version: int
    input: dict[str, object]
    results: Annotated[dict[str, object], _merge_results]
    span_ids: Annotated[dict[str, str], _merge_results]
    output: dict[str, object]


ToolHandler = Callable[[dict[str, object]], object]


def _market_indicators(args: dict[str, object]) -> object:
    from app.modules.market.service import get_market_indicators

    return get_market_indicators(str(args.get("symbol") or "BTCUSD"))


def _news_search(args: dict[str, object]) -> object:
    from app.modules.news.service import list_news_events

    return list_news_events(str(args.get("symbol") or "BTCUSD"), int(args.get("limit") or 10))


def _factor_catalog(args: dict[str, object]) -> object:
    from app.modules.research.service import list_factor_definitions

    return list_factor_definitions(enabled_only=bool(args.get("enabled_only", True)))


def _alert_catalog(args: dict[str, object]) -> object:
    from app.modules.trading.service import list_alert_rules

    return list_alert_rules(str(args["symbol"]) if args.get("symbol") else None)


TOOL_HANDLERS: dict[str, ToolHandler] = {
    "market.indicators": _market_indicators,
    "news.search": _news_search,
    "factor.catalog": _factor_catalog,
    "alert.catalog": _alert_catalog,
}


TOOL_CATALOG = [
    ToolDefinition(name="market.indicators", version="1", title="行情指标", description="读取标的行情和技术指标", category="market", permission_level="L0", side_effect=False, approval_required=False, input_schema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}, output_schema={"type": "array"}),
    ToolDefinition(name="news.search", version="1", title="关联新闻", description="读取标的相关新闻", category="news", permission_level="L0", side_effect=False, approval_required=False, input_schema={"type": "object", "properties": {"symbol": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["symbol"]}, output_schema={"type": "array"}),
    ToolDefinition(name="factor.catalog", version="1", title="因子目录", description="读取平台因子定义", category="research", permission_level="L0", side_effect=False, approval_required=False, input_schema={"type": "object"}, output_schema={"type": "array"}),
    ToolDefinition(name="alert.catalog", version="1", title="预警目录", description="读取标的预警规则", category="trading", permission_level="L0", side_effect=False, approval_required=False, input_schema={"type": "object", "properties": {"symbol": {"type": "string"}}}, output_schema={"type": "array"}),
    ToolDefinition(name="agent.invoke", version="1", title="能力 Agent", description="调用已配置的能力 Agent", category="agent", permission_level="L0", side_effect=False, approval_required=False, input_schema={"type": "object", "properties": {"agent_id": {"type": "string"}}, "required": ["agent_id"]}, output_schema={"type": "object"}),
    ToolDefinition(name="artifact.publish", version="1", title="投资信息发布", description="发布带有效期和证据链的投资信息", category="output", permission_level="L1", side_effect=True, approval_required=False, input_schema={"type": "object"}, output_schema={"type": "object"}),
    ToolDefinition(name="resource.propose", version="1", title="资源变更建议", description="创建因子、预警或影响关系审批", category="output", permission_level="L2", side_effect=True, approval_required=True, input_schema={"type": "object"}, output_schema={"type": "object"}),
]


def list_workflow_tools() -> list[ToolDefinition]:
    return TOOL_CATALOG


def ensure_legacy_agent_workflows() -> None:
    """Expose existing capability Agents as published one-node workflow chains."""

    database.init_db()
    with database.db_connect() as connection:
        agents = connection.execute("SELECT * FROM agent_configs ORDER BY created_at").fetchall()
        now = _now()
        for agent in agents:
            workflow_id = f"workflow-legacy-{agent['id']}"
            if connection.execute("SELECT 1 FROM workflow_definitions WHERE id=?", (workflow_id,)).fetchone():
                continue
            output_type = {
                "risk_sentinel": "risk_signal",
                "factor_research": "factor_candidate",
                "news_tagger": "news_impact",
                "news_analyst": "news_impact",
            }.get(agent["template_type"], "chart_marker")
            triggers = _json(agent["triggers"], ["manual"])
            trigger_type = str(triggers[0]) if isinstance(triggers, list) and triggers else "manual"
            graph = WorkflowGraph(
                nodes=[
                    WorkflowNode(id="trigger", kind="trigger", type=trigger_type, name="触发器", x=80, y=180),
                    WorkflowNode(id="agent", kind="agent", type="agent.invoke", name=agent["name"], x=360, y=180, config={"agent_id": agent["id"]}),
                    WorkflowNode(id="output", kind="output", type=output_type, name="发布分析结果", x=650, y=180, config={"severity": "watch", "valid_minutes": 240}),
                ],
                edges=[
                    WorkflowEdge(id="trigger-agent", source="trigger", target="agent"),
                    WorkflowEdge(id="agent-output", source="agent", target="output"),
                ],
            )
            graph_json = graph.model_dump_json()
            digest = sha256(graph_json.encode()).hexdigest()
            connection.execute("INSERT INTO workflow_definitions VALUES (?, ?, ?, 'published', ?, 1, 1, ?, 'migration', ?, ?)", (workflow_id, f"{agent['name']} · 兼容工作流", agent["description"], agent["enabled"], graph_json, now, now))
            connection.execute("INSERT INTO workflow_versions VALUES (?, ?, 1, ?, ?, 'migration', ?)", (f"{workflow_id}:v1", workflow_id, graph_json, digest, now))


def _template_graph(template: str) -> WorkflowGraph:
    nodes = [WorkflowNode(id="trigger", kind="trigger", type="manual", name="手动触发", x=60, y=180)]
    if template == "risk_watch":
        nodes.extend([
            WorkflowNode(id="market", kind="resource", type="market.indicators", name="读取行情指标", x=300, y=180, config={"arguments": {"symbol": "$input.symbol"}}),
            WorkflowNode(id="risk", kind="agent", type="agent.invoke", name="风险哨兵", x=560, y=180, config={"agent_id": "risk_sentinel"}),
            WorkflowNode(id="publish", kind="output", type="chart_marker", name="发布风险标记", x=820, y=180, config={"severity": "risk", "valid_minutes": 240}),
        ])
    elif template == "news_impact":
        nodes.extend([
            WorkflowNode(id="news", kind="resource", type="news.search", name="读取关联新闻", x=300, y=180, config={"arguments": {"symbol": "$input.symbol", "limit": 10}}),
            WorkflowNode(id="analyse", kind="agent", type="agent.invoke", name="新闻分析", x=560, y=180, config={"agent_id": "news_analyst"}),
            WorkflowNode(id="publish", kind="output", type="news_impact", name="发布新闻影响", x=820, y=180, config={"severity": "watch", "valid_minutes": 720}),
        ])
    else:
        nodes.append(WorkflowNode(id="publish", kind="output", type="chart_marker", name="发布分析结果", x=360, y=180, config={"severity": "info", "valid_minutes": 60}))
    return WorkflowGraph(nodes=nodes, edges=[WorkflowEdge(id=f"edge-{index}", source=nodes[index].id, target=nodes[index + 1].id) for index in range(len(nodes) - 1)])


def _row_workflow(row: sqlite3.Row) -> WorkflowDefinition:
    return WorkflowDefinition(id=row["id"], name=row["name"], objective=row["objective"], status=row["status"], enabled=bool(row["enabled"]), draft_version=row["draft_version"], published_version=row["published_version"], graph=WorkflowGraph.model_validate(_json(row["draft_graph"], {})), created_by=row["created_by"], created_at=row["created_at"], updated_at=row["updated_at"])


def create_workflow(payload: WorkflowDefinitionCreate, actor: str) -> WorkflowDefinition:
    database.init_db()
    now = _now()
    workflow_id = f"workflow-{sha256(f'{payload.name}:{now}'.encode()).hexdigest()[:14]}"
    graph = _template_graph(payload.template)
    with database.db_connect() as connection:
        connection.execute("INSERT INTO workflow_definitions VALUES (?, ?, ?, 'draft', 0, 1, NULL, ?, ?, ?, ?)", (workflow_id, payload.name, payload.objective, graph.model_dump_json(), actor, now, now))
        row = connection.execute("SELECT * FROM workflow_definitions WHERE id=?", (workflow_id,)).fetchone()
    return _row_workflow(row)


def list_workflows() -> list[WorkflowDefinition]:
    database.init_db()
    ensure_legacy_agent_workflows()
    with database.db_connect() as connection:
        rows = connection.execute("SELECT * FROM workflow_definitions ORDER BY updated_at DESC").fetchall()
    return [_row_workflow(row) for row in rows]


def get_workflow(workflow_id: str) -> WorkflowDefinition:
    database.init_db()
    with database.db_connect() as connection:
        row = connection.execute("SELECT * FROM workflow_definitions WHERE id=?", (workflow_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return _row_workflow(row)


def update_workflow(workflow_id: str, payload: WorkflowDefinitionUpdate) -> WorkflowDefinition:
    current = get_workflow(workflow_id)
    values = payload.model_dump(exclude_unset=True)
    values.pop("graph", None)
    graph = payload.graph
    now = _now()
    name = values.get("name", current.name)
    objective = values.get("objective", current.objective)
    enabled = int(values.get("enabled", current.enabled))
    draft_version = current.draft_version + (1 if graph is not None else 0)
    with database.db_connect() as connection:
        connection.execute("UPDATE workflow_definitions SET name=?, objective=?, enabled=?, status='draft', draft_version=?, draft_graph=?, updated_at=? WHERE id=?", (name, objective, enabled, draft_version, (graph or current.graph).model_dump_json(), now, workflow_id))
    return get_workflow(workflow_id)


def validate_workflow(workflow_id: str) -> WorkflowValidation:
    workflow = get_workflow(workflow_id)
    graph = workflow.graph
    errors: list[str] = []
    warnings: list[str] = []
    triggers = [node for node in graph.nodes if node.kind == "trigger"]
    outputs = [node for node in graph.nodes if node.kind == "output"]
    if len(triggers) != 1:
        errors.append("工作流必须且只能包含一个触发节点")
    if not outputs:
        errors.append("工作流至少需要一个输出节点")
    outgoing: dict[str, list[WorkflowEdge]] = {node.id: [] for node in graph.nodes}
    incoming: dict[str, int] = {node.id: 0 for node in graph.nodes}
    for edge in graph.edges:
        outgoing[edge.source].append(edge)
        incoming[edge.target] += 1
    for node in graph.nodes:
        if node.kind == "resource" and node.type not in TOOL_HANDLERS:
            errors.append(f"节点 {node.name} 使用了未注册工具 {node.type}")
        if node.kind not in {"condition", "output"} and len(outgoing[node.id]) > 1:
            errors.append(f"节点 {node.name} 只能有一个成功出口")
        if node.kind == "condition" and not {edge.source_port for edge in outgoing[node.id]} <= {"true", "false"}:
            errors.append(f"条件节点 {node.name} 只能使用 true/false 出口")
        if node.id != (triggers[0].id if triggers else "") and incoming[node.id] == 0:
            errors.append(f"节点 {node.name} 不可达")
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(node_id: str) -> None:
        if node_id in visiting:
            errors.append("首期工作流不允许循环")
            return
        if node_id in visited:
            return
        visiting.add(node_id)
        for edge in outgoing.get(node_id, []):
            visit(edge.target)
        visiting.remove(node_id)
        visited.add(node_id)
    if triggers:
        visit(triggers[0].id)
    if len(graph.nodes) > 30:
        warnings.append("工作流节点较多，建议拆分为能力 Agent")
    summary = f"{len(graph.nodes)} 个节点 · {len(graph.edges)} 条链路 · {len(outputs)} 个投资输出"
    return WorkflowValidation(valid=not errors, errors=list(dict.fromkeys(errors)), warnings=warnings, summary=summary)


def publish_workflow(workflow_id: str, actor: str) -> WorkflowDefinition:
    workflow = get_workflow(workflow_id)
    validation = validate_workflow(workflow_id)
    if not validation.valid:
        raise HTTPException(status_code=422, detail={"message": "Workflow validation failed", "errors": validation.errors})
    version = (workflow.published_version or 0) + 1
    graph_json = workflow.graph.model_dump_json()
    digest = sha256(graph_json.encode()).hexdigest()
    now = _now()
    version_id = f"{workflow_id}:v{version}"
    with database.db_connect() as connection:
        connection.execute("INSERT INTO workflow_versions VALUES (?, ?, ?, ?, ?, ?, ?)", (version_id, workflow_id, version, graph_json, digest, actor, now))
        connection.execute("UPDATE workflow_definitions SET status='published', enabled=1, published_version=?, updated_at=? WHERE id=?", (version, now, workflow_id))
    return get_workflow(workflow_id)


def list_workflow_versions(workflow_id: str) -> list[WorkflowVersion]:
    get_workflow(workflow_id)
    with database.db_connect() as connection:
        rows = connection.execute("SELECT * FROM workflow_versions WHERE workflow_id=? ORDER BY version DESC", (workflow_id,)).fetchall()
    return [WorkflowVersion(id=row["id"], workflow_id=row["workflow_id"], version=row["version"], graph=WorkflowGraph.model_validate(_json(row["graph"], {})), content_hash=row["content_hash"], published_by=row["published_by"], created_at=row["created_at"]) for row in rows]


def rollback_workflow(workflow_id: str, version: int, actor: str) -> WorkflowDefinition:
    with database.db_connect() as connection:
        row = connection.execute("SELECT graph FROM workflow_versions WHERE workflow_id=? AND version=?", (workflow_id, version)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Workflow version not found")
    update_workflow(workflow_id, WorkflowDefinitionUpdate(graph=WorkflowGraph.model_validate(_json(row["graph"], {}))))
    return publish_workflow(workflow_id, actor)


def _row_run(row: sqlite3.Row) -> WorkflowRun:
    return WorkflowRun(id=row["id"], workflow_id=row["workflow_id"], workflow_name=row["workflow_name"], workflow_version=row["workflow_version"], trace_id=row["trace_id"], trigger=row["trigger"], status=row["status"], symbol=row["symbol"], input=_json(row["input_payload"], {}), output=_json(row["output"], None), error=row["error"], test=bool(row["test"]), started_at=row["started_at"], finished_at=row["finished_at"], created_at=row["created_at"])


def enqueue_workflow_run(workflow_id: str, payload: WorkflowRunRequest, trigger: str = "manual") -> WorkflowRun:
    workflow = get_workflow(workflow_id)
    if not payload.test and (not workflow.enabled or not workflow.published_version):
        raise HTTPException(status_code=409, detail="Workflow is not published and enabled")
    version = workflow.published_version or workflow.draft_version
    now = _now()
    run_id = f"workflow-run-{secrets.token_hex(9)}"
    trace_id = f"trace-{secrets.token_hex(16)}"
    input_payload = {**payload.context, "symbol": payload.symbol.upper() if payload.symbol else payload.context.get("symbol")}
    with database.db_connect() as connection:
        connection.execute("INSERT INTO workflow_runs VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, NULL, NULL, ?, ?, ?, NULL, NULL)", (run_id, workflow.id, workflow.name, version, trace_id, trigger, input_payload.get("symbol"), json.dumps(input_payload, ensure_ascii=False), int(payload.test), run_id, now))
        row = connection.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
    return _row_run(row)


def enqueue_workflow_event(trigger: str, symbol: str | None = None, context: dict[str, object] | None = None) -> int:
    queued = 0
    for item in list_workflows():
        if item.id.startswith("workflow-legacy-") or not item.enabled or not item.published_version:
            continue
        trigger_node = next((node for node in item.graph.nodes if node.kind == "trigger"), None)
        if trigger_node and trigger_node.type == trigger:
            enqueue_workflow_run(item.id, WorkflowRunRequest(symbol=symbol, context=context or {}), trigger=trigger)
            queued += 1
    return queued


def get_workflow_run(run_id: str) -> WorkflowRun:
    with database.db_connect() as connection:
        row = connection.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return _row_run(row)


def list_workflow_runs(workflow_id: str | None = None, limit: int = 100) -> list[WorkflowRun]:
    database.init_db()
    with database.db_connect() as connection:
        if workflow_id:
            rows = connection.execute("SELECT * FROM workflow_runs WHERE workflow_id=? ORDER BY created_at DESC LIMIT ?", (workflow_id, min(limit, 200))).fetchall()
        else:
            rows = connection.execute("SELECT * FROM workflow_runs ORDER BY created_at DESC LIMIT ?", (min(limit, 200),)).fetchall()
    return [_row_run(row) for row in rows]


def _row_span(row: sqlite3.Row) -> WorkflowSpan:
    return WorkflowSpan(id=row["id"], trace_id=row["trace_id"], run_id=row["run_id"], parent_span_id=row["parent_span_id"], node_id=row["node_id"], node_name=row["node_name"], kind=row["kind"], operation=row["operation"], status=row["status"], input=_json(row["input_payload"], {}), output=_json(row["output"], None), decision_summary=row["decision_summary"], resource_refs=_json(row["resource_refs"], []), model=row["model"], prompt_tokens=row["prompt_tokens"], completion_tokens=row["completion_tokens"], retry_count=row["retry_count"], latency_ms=row["latency_ms"], error=row["error"], started_at=row["started_at"], finished_at=row["finished_at"])


def get_workflow_trace(run_id: str) -> WorkflowTrace:
    run = get_workflow_run(run_id)
    with database.db_connect() as connection:
        version = connection.execute("SELECT graph FROM workflow_versions WHERE workflow_id=? AND version=?", (run.workflow_id, run.workflow_version)).fetchone()
        rows = connection.execute("SELECT * FROM workflow_spans WHERE run_id=? ORDER BY started_at", (run_id,)).fetchall()
    graph = WorkflowGraph.model_validate(_json(version["graph"], {})) if version else get_workflow(run.workflow_id).graph
    return WorkflowTrace(run=run, spans=[_row_span(row) for row in rows], graph=graph)


def _path(value: str, state: WorkflowState) -> object:
    if not value.startswith("$"):
        return value
    parts = value[1:].split(".")
    current: object = state.get(parts.pop(0), {})
    for part in parts:
        current = current.get(part) if isinstance(current, dict) else None
    return current


def _resolve(value: object, state: WorkflowState) -> object:
    if isinstance(value, str):
        return _path(value, state)
    if isinstance(value, list):
        return [_resolve(item, state) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, state) for key, item in value.items()}
    return value


def _incoming_result(node: WorkflowNode, graph: WorkflowGraph, state: WorkflowState) -> object:
    sources = [edge.source for edge in graph.edges if edge.target == node.id]
    results = state.get("results", {})
    return results.get(sources[-1], state.get("input", {})) if sources else state.get("input", {})


def _start_span(state: WorkflowState, node: WorkflowNode, graph: WorkflowGraph, kind: str, operation: str, input_value: object) -> tuple[str, float]:
    span_id = f"span-{secrets.token_hex(10)}"
    sources = [edge.source for edge in graph.edges if edge.target == node.id]
    parent_span_id = next((state.get("span_ids", {}).get(source) for source in reversed(sources) if state.get("span_ids", {}).get(source)), None)
    with database.db_connect() as connection:
        connection.execute("INSERT INTO workflow_spans (id, trace_id, run_id, parent_span_id, node_id, node_name, kind, operation, status, input_payload, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)", (span_id, state["trace_id"], state["run_id"], parent_span_id, node.id, node.name, kind, operation, json.dumps(_safe(input_value), ensure_ascii=False), _now()))
    return span_id, perf_counter()


def _finish_span(span_id: str, started: float, status: str, output: object = None, summary: str = "", error: str | None = None, model: str | None = None, prompt_tokens: int = 0, completion_tokens: int = 0, refs: list[dict[str, str]] | None = None) -> None:
    with database.db_connect() as connection:
        connection.execute("UPDATE workflow_spans SET status=?, output=?, decision_summary=?, resource_refs=?, model=?, prompt_tokens=?, completion_tokens=?, latency_ms=?, error=?, finished_at=? WHERE id=?", (status, json.dumps(_safe(_model(output)), ensure_ascii=False) if output is not None else None, summary[:1000], json.dumps(refs or [], ensure_ascii=False), model, prompt_tokens, completion_tokens, int((perf_counter() - started) * 1000), error, _now(), span_id))


def _publish_artifact(state: WorkflowState, node: WorkflowNode, source: object, span_id: str) -> dict[str, object]:
    value = source if isinstance(source, dict) else {"data": _model(source)}
    config = _resolve(node.config, state)
    symbol = str(state.get("input", {}).get("symbol") or "").upper()
    artifact_type = node.type if node.type in {"chart_marker", "risk_signal", "news_impact", "factor_candidate", "alert_candidate"} else "chart_marker"
    confidence = max(0.0, min(1.0, float(value.get("confidence", config.get("confidence", 0.7)))))
    title = str(value.get("title") or node.name)[:200]
    summary = str(value.get("summary") or value.get("decision_summary") or f"{node.name} 已产生投资信息")[:2000]
    severity = str(value.get("risk_level") or value.get("severity") or config.get("severity", "info"))
    if severity not in {"info", "watch", "risk", "opportunity"}:
        severity = "watch"
    direction = str(value.get("direction") or "neutral")
    if direction not in {"positive", "negative", "neutral", "mixed"}:
        direction = "neutral"
    valid_from = datetime.now(timezone.utc)
    valid_until = valid_from + timedelta(minutes=max(1, int(config.get("valid_minutes", 60))))
    artifact_key = f"{state['run_id']}:{node.id}:{artifact_type}"
    artifact_id = f"artifact-{sha256(artifact_key.encode()).hexdigest()[:18]}"
    evidence = [str(item)[:500] for item in value.get("evidence", []) if str(item).strip()][:20] if isinstance(value.get("evidence"), list) else []
    preview = {"artifact_id": artifact_id, "artifact_type": artifact_type, "title": title, "summary": summary, "trace_id": state["trace_id"], "test": bool(state.get("input", {}).get("test"))}
    if preview["test"]:
        return preview
    with database.db_connect() as connection:
        connection.execute(
            """INSERT OR REPLACE INTO investment_artifacts (
                   id, artifact_type, title, summary, scope_type, scope_ids, direction, severity,
                   confidence, evidence, payload, workflow_id, workflow_version, run_id, trace_id,
                   span_id, status, valid_from, valid_until, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
            (artifact_id, artifact_type, title, summary, "instrument" if symbol else "global",
             json.dumps([symbol] if symbol else []), direction, severity, confidence,
             json.dumps(evidence, ensure_ascii=False), json.dumps(_safe(value), ensure_ascii=False),
             state["workflow_id"], state["workflow_version"], state["run_id"], state["trace_id"],
             span_id, valid_from.isoformat(), valid_until.isoformat(), valid_from.isoformat()),
        )
    return preview


def _execute_node(node: WorkflowNode, graph: WorkflowGraph, state: WorkflowState) -> dict[str, object]:
    source = _incoming_result(node, graph, state)
    input_value = {"source": source, "config": _resolve(node.config, state)}
    kind = {"resource": "tool", "agent": "agent", "condition": "condition", "approval": "approval", "output": "output"}.get(node.kind, "workflow")
    span_id, started = _start_span(state, node, graph, kind, node.type, input_value)
    try:
        if node.kind == "trigger":
            output = state.get("input", {})
            summary = f"由 {node.type} 触发"
        elif node.kind == "resource":
            args = _resolve(node.config.get("arguments", {}), state)
            output = _model(TOOL_HANDLERS[node.type](args if isinstance(args, dict) else {}))
            summary = f"调用只读工具 {node.type}"
        elif node.kind == "agent":
            from app.modules.ai.service import enqueue_agent_run, execute_agent_run
            agent_id = str(node.config.get("agent_id") or "")
            child = enqueue_agent_run(agent_id, trigger="pipeline", symbol=str(state.get("input", {}).get("symbol") or "") or None, context={"workflow_context": source}, test=bool(state.get("input", {}).get("test")), trace_id=state["trace_id"], workflow_run_id=state["run_id"], parent_span_id=span_id)
            child = execute_agent_run(child.id)
            if child.status != "succeeded":
                raise RuntimeError(child.error or "Child Agent failed")
            output = child.output or {}
            summary = f"能力 Agent {child.agent_name} 完成"
            _finish_span(span_id, started, "succeeded", output, summary, model=child.model, prompt_tokens=child.prompt_tokens, completion_tokens=child.completion_tokens, refs=[{"type": "agent_run", "id": child.id}])
            return {"results": {node.id: output}, "span_ids": {node.id: span_id}}
        elif node.kind == "condition":
            field = str(node.config.get("field") or "")
            actual = _path(field, state) if field.startswith("$") else (source.get(field) if isinstance(source, dict) else None)
            expected = node.config.get("value")
            operator = str(node.config.get("operator") or "eq")
            matched = {"eq": actual == expected, "ne": actual != expected, "gt": float(actual or 0) > float(expected or 0), "gte": float(actual or 0) >= float(expected or 0), "lt": float(actual or 0) < float(expected or 0), "contains": str(expected) in str(actual)}.get(operator, False)
            output = {"matched": matched, "actual": actual, "operator": operator, "expected": expected}
            summary = f"{field or '上游结果'} {operator} {expected!r} => {matched}"
        elif node.kind == "transform":
            mapping = node.config.get("mapping", {})
            output = _resolve(mapping, state) if mapping else source
            summary = "完成字段映射"
        elif node.kind == "approval":
            approval_key = f"{state['run_id']}:{node.id}"
            approval_id = f"approval-{sha256(approval_key.encode()).hexdigest()[:18]}"
            with database.db_connect() as connection:
                connection.execute("INSERT OR IGNORE INTO workflow_approvals VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, NULL)", (approval_id, state["run_id"], span_id, node.id, node.name, json.dumps(_safe(source), ensure_ascii=False), _now()))
            decision = interrupt({"approval_id": approval_id, "title": node.name, "payload": _safe(source)})
            if not isinstance(decision, dict) or not decision.get("approved"):
                raise RuntimeError("Workflow approval rejected")
            output = decision.get("payload_override") or source
            summary = f"由 {decision.get('reviewed_by', '管理员')} 批准"
        elif node.kind == "output":
            output = _publish_artifact(state, node, source, span_id)
            summary = f"发布 {output['artifact_type']}"
        else:
            output, summary = source, "节点完成"
        _finish_span(span_id, started, "succeeded", output, summary)
        result: dict[str, object] = {"results": {node.id: _model(output)}, "span_ids": {node.id: span_id}}
        if node.kind == "output":
            result["output"] = _model(output)
        return result
    except GraphInterrupt:
        _finish_span(span_id, started, "waiting_approval", summary="等待管理员审批")
        raise
    except Exception as exc:
        _finish_span(span_id, started, "failed", error=str(exc)[:1000])
        raise


def _graph_for_run(run: WorkflowRun) -> WorkflowGraph:
    with database.db_connect() as connection:
        row = connection.execute("SELECT graph FROM workflow_versions WHERE workflow_id=? AND version=?", (run.workflow_id, run.workflow_version)).fetchone()
    return WorkflowGraph.model_validate(_json(row["graph"], {})) if row else get_workflow(run.workflow_id).graph


_CHECKPOINTERS: dict[str, tuple[sqlite3.Connection, SqliteSaver]] = {}


def _checkpointer() -> SqliteSaver:
    path = str(database.DB_PATH)
    if path not in _CHECKPOINTERS:
        connection = sqlite3.connect(path, check_same_thread=False)
        _CHECKPOINTERS[path] = (connection, SqliteSaver(connection))
    return _CHECKPOINTERS[path][1]


def _compile(run: WorkflowRun):
    graph = _graph_for_run(run)
    builder = StateGraph(WorkflowState)
    for node in graph.nodes:
        builder.add_node(node.id, lambda state, current=node: _execute_node(current, graph, state))
    trigger = next(node for node in graph.nodes if node.kind == "trigger")
    builder.add_edge(START, trigger.id)
    outgoing = {node.id: [edge for edge in graph.edges if edge.source == node.id] for node in graph.nodes}
    for node in graph.nodes:
        edges = outgoing[node.id]
        if node.kind == "condition" and edges:
            routes = {edge.source_port: edge.target for edge in edges}
            def route(state, current=node, destinations=routes):
                matched = bool(state.get("results", {}).get(current.id, {}).get("matched"))
                return destinations.get("true" if matched else "false", END)
            builder.add_conditional_edges(node.id, route)
        elif edges:
            builder.add_edge(node.id, edges[0].target)
        else:
            builder.add_edge(node.id, END)
    return builder.compile(checkpointer=_checkpointer())


def execute_workflow_run(run_id: str, resume: dict[str, object] | None = None) -> WorkflowRun:
    run = get_workflow_run(run_id)
    if run.status not in {"queued", "waiting_approval"}:
        return run
    now = _now()
    with database.db_connect() as connection:
        connection.execute("UPDATE workflow_runs SET status='running', started_at=COALESCE(started_at, ?) WHERE id=?", (now, run_id))
    try:
        compiled = _compile(run)
        config = {"configurable": {"thread_id": run.id}}
        if resume is None:
            result = compiled.invoke({"run_id": run.id, "trace_id": run.trace_id, "workflow_id": run.workflow_id, "workflow_version": run.workflow_version, "input": {**run.input, "test": run.test}, "results": {}, "span_ids": {}}, config)
        else:
            result = compiled.invoke(Command(resume=resume), config)
        snapshot = compiled.get_state(config)
        if snapshot.next:
            status, finished = "waiting_approval", None
        else:
            status, finished = "succeeded", _now()
        with database.db_connect() as connection:
            connection.execute("UPDATE workflow_runs SET status=?, output=?, error=NULL, finished_at=? WHERE id=?", (status, json.dumps(_safe(result.get("output", result.get("results", {}))), ensure_ascii=False), finished, run_id))
    except Exception as exc:
        with database.db_connect() as connection:
            connection.execute("UPDATE workflow_runs SET status='failed', error=?, finished_at=? WHERE id=?", (str(exc)[:1000], _now(), run_id))
    return get_workflow_run(run_id)


def _row_approval(row: sqlite3.Row) -> WorkflowApproval:
    return WorkflowApproval(id=row["id"], run_id=row["run_id"], span_id=row["span_id"], node_id=row["node_id"], title=row["title"], payload=_json(row["payload"], {}), status=row["status"], reviewed_by=row["reviewed_by"], review_note=row["review_note"], created_at=row["created_at"], reviewed_at=row["reviewed_at"])


def list_workflow_approvals(status: str | None = None) -> list[WorkflowApproval]:
    database.init_db()
    with database.db_connect() as connection:
        rows = connection.execute("SELECT * FROM workflow_approvals" + (" WHERE status=?" if status else "") + " ORDER BY created_at DESC LIMIT 200", (status,) if status else ()).fetchall()
    return [_row_approval(row) for row in rows]


def decide_workflow_approval(approval_id: str, approved: bool, payload: WorkflowApprovalDecision, actor: str) -> WorkflowRun:
    with database.db_connect() as connection:
        row = connection.execute("SELECT * FROM workflow_approvals WHERE id=?", (approval_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Workflow approval not found")
        if row["status"] != "pending":
            raise HTTPException(status_code=409, detail="Workflow approval has already been reviewed")
        connection.execute("UPDATE workflow_approvals SET status=?, reviewed_by=?, review_note=?, reviewed_at=? WHERE id=?", ("approved" if approved else "rejected", actor, payload.note, _now(), approval_id))
    return execute_workflow_run(row["run_id"], {"approved": approved, "reviewed_by": actor, "payload_override": payload.payload_override})


def _row_artifact(row: sqlite3.Row) -> InvestmentArtifact:
    return InvestmentArtifact(id=row["id"], artifact_type=row["artifact_type"], title=row["title"], summary=row["summary"], scope_type=row["scope_type"], scope_ids=_json(row["scope_ids"], []), direction=row["direction"], severity=row["severity"], confidence=row["confidence"], evidence=_json(row["evidence"], []), payload=_json(row["payload"], {}), workflow_id=row["workflow_id"], workflow_version=row["workflow_version"], run_id=row["run_id"], trace_id=row["trace_id"], span_id=row["span_id"], status=row["status"], valid_from=row["valid_from"], valid_until=row["valid_until"], created_at=row["created_at"])


def list_investment_artifacts(artifact_type: str | None = None, symbol: str | None = None, active_only: bool = True, limit: int = 100) -> list[InvestmentArtifact]:
    database.init_db()
    clauses: list[str] = []
    params: list[object] = []
    if artifact_type:
        clauses.append("artifact_type=?"); params.append(artifact_type)
    if active_only:
        clauses.append("status='active' AND valid_until>=?"); params.append(_now())
    if symbol:
        clauses.append("scope_ids LIKE ?"); params.append(f'%"{symbol.upper()}"%')
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with database.db_connect() as connection:
        rows = connection.execute(f"SELECT * FROM investment_artifacts{where} ORDER BY created_at DESC LIMIT ?", (*params, min(limit, 200))).fetchall()
    return [_row_artifact(row) for row in rows]


def cleanup_agent_data(
    checkpoint_retention_days: int,
    trace_retention_days: int,
    artifact_retention_days: int,
    vacuum_after_cleanup: bool = True,
) -> dict[str, int | bool]:
    """Remove terminal workflow runtime data while preserving active and reviewable runs."""
    database.init_db()
    now = datetime.now(timezone.utc)
    checkpoint_cutoff = (now - timedelta(days=checkpoint_retention_days)).isoformat()
    trace_cutoff = (now - timedelta(days=trace_retention_days)).isoformat()
    artifact_cutoff = (now - timedelta(days=artifact_retention_days)).isoformat()
    database_path = database.DB_PATH
    size_before = database_path.stat().st_size if database_path.exists() else 0
    deleted: dict[str, int | bool] = {
        "checkpoints": 0,
        "checkpoint_writes": 0,
        "spans": 0,
        "approvals": 0,
        "runs": 0,
        "artifacts": 0,
        "vacuumed": False,
    }
    terminal = "('succeeded', 'failed')"
    with database.db_connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "writes" in tables:
            cursor = connection.execute(
                f"""DELETE FROM writes WHERE thread_id IN (
                    SELECT checkpoint_thread_id FROM workflow_runs
                    WHERE status IN {terminal} AND created_at < ?
                )""",
                (checkpoint_cutoff,),
            )
            deleted["checkpoint_writes"] = max(cursor.rowcount, 0)
        if "checkpoints" in tables:
            cursor = connection.execute(
                f"""DELETE FROM checkpoints WHERE thread_id IN (
                    SELECT checkpoint_thread_id FROM workflow_runs
                    WHERE status IN {terminal} AND created_at < ?
                )""",
                (checkpoint_cutoff,),
            )
            deleted["checkpoints"] = max(cursor.rowcount, 0)

        cursor = connection.execute(
            """DELETE FROM investment_artifacts
               WHERE created_at < ? AND (status != 'active' OR valid_until < ?)""",
            (artifact_cutoff, now.isoformat()),
        )
        deleted["artifacts"] = max(cursor.rowcount, 0)
        cursor = connection.execute(
            f"""DELETE FROM workflow_approvals WHERE status != 'pending' AND run_id IN (
                SELECT id FROM workflow_runs WHERE status IN {terminal} AND created_at < ?
            )""",
            (trace_cutoff,),
        )
        deleted["approvals"] = max(cursor.rowcount, 0)
        cursor = connection.execute(
            f"""DELETE FROM workflow_spans WHERE run_id IN (
                SELECT id FROM workflow_runs WHERE status IN {terminal} AND created_at < ?
            )""",
            (trace_cutoff,),
        )
        deleted["spans"] = max(cursor.rowcount, 0)
        cursor = connection.execute(
            f"""DELETE FROM workflow_runs
               WHERE status IN {terminal} AND created_at < ?
                 AND NOT EXISTS (SELECT 1 FROM workflow_spans WHERE workflow_spans.run_id=workflow_runs.id)
                 AND NOT EXISTS (SELECT 1 FROM workflow_approvals WHERE workflow_approvals.run_id=workflow_runs.id)
                 AND NOT EXISTS (SELECT 1 FROM investment_artifacts WHERE investment_artifacts.run_id=workflow_runs.id)""",
            (trace_cutoff,),
        )
        deleted["runs"] = max(cursor.rowcount, 0)
        connection.commit()
        if vacuum_after_cleanup:
            try:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.execute("VACUUM")
                deleted["vacuumed"] = True
            except sqlite3.OperationalError:
                # Deleted pages remain reusable even when a concurrent reader prevents compaction.
                deleted["vacuumed"] = False
        connection.execute("PRAGMA optimize")

    size_after = database_path.stat().st_size if database_path.exists() else 0
    deleted["database_bytes_before"] = size_before
    deleted["database_bytes_after"] = size_after
    deleted["reclaimed_bytes"] = max(size_before - size_after, 0)
    return deleted


async def workflow_worker_loop() -> None:
    while True:
        try:
            database.init_db()
            with database.db_connect() as connection:
                row = connection.execute("SELECT id FROM workflow_runs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row:
                await asyncio.to_thread(execute_workflow_run, row["id"])
            else:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(2)
