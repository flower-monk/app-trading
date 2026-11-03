from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.ai import workflow
from app.modules.infrastructure import database
from app.schemas import (
    WorkflowApprovalDecision,
    WorkflowDefinitionCreate,
    WorkflowDefinitionUpdate,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRunRequest,
)


@pytest.fixture()
def workflow_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "workflow.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    database._INITIALIZED_DB_PATHS.discard(str(path))
    workflow._CHECKPOINTERS.clear()
    database.init_db()


def test_workflow_publish_is_versioned_and_produces_traceable_artifact(workflow_db: None) -> None:
    item = workflow.create_workflow(
        WorkflowDefinitionCreate(name="Risk workflow", objective="Publish a chart risk marker", template="blank"),
        "admin",
    )
    assert workflow.validate_workflow(item.id).valid
    published = workflow.publish_workflow(item.id, "admin")
    assert published.published_version == 1

    run = workflow.enqueue_workflow_run(
        item.id, WorkflowRunRequest(symbol="BTCUSD", context={"source": "test"})
    )
    completed = workflow.execute_workflow_run(run.id)
    trace = workflow.get_workflow_trace(run.id)
    artifacts = workflow.list_investment_artifacts(symbol="BTCUSD")

    assert completed.status == "succeeded"
    assert [span.node_id for span in trace.spans] == ["trigger", "publish"]
    assert all(span.status == "succeeded" for span in trace.spans)
    assert artifacts[0].trace_id == completed.trace_id
    assert artifacts[0].workflow_version == 1


def test_workflow_validation_rejects_cycles_and_unknown_tools(workflow_db: None) -> None:
    item = workflow.create_workflow(WorkflowDefinitionCreate(name="Invalid", template="blank"), "admin")
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(id="trigger", kind="trigger", type="manual", name="Trigger"),
            WorkflowNode(id="unknown", kind="resource", type="unsafe.http", name="Unknown"),
            WorkflowNode(id="output", kind="output", type="chart_marker", name="Output"),
        ],
        edges=[
            WorkflowEdge(id="one", source="trigger", target="unknown"),
            WorkflowEdge(id="two", source="unknown", target="output"),
            WorkflowEdge(id="cycle", source="output", target="unknown"),
        ],
    )
    workflow.update_workflow(item.id, WorkflowDefinitionUpdate(graph=graph))
    result = workflow.validate_workflow(item.id)
    assert not result.valid
    assert any("未注册工具" in error for error in result.errors)
    assert any("不允许循环" in error for error in result.errors)


def test_workflow_approval_pauses_and_resumes_from_checkpoint(workflow_db: None) -> None:
    item = workflow.create_workflow(WorkflowDefinitionCreate(name="Approval", template="blank"), "admin")
    graph = WorkflowGraph(
        nodes=[
            WorkflowNode(id="trigger", kind="trigger", type="manual", name="Trigger"),
            WorkflowNode(id="review", kind="approval", type="approval", name="Risk review"),
            WorkflowNode(id="publish", kind="output", type="risk_signal", name="Publish"),
        ],
        edges=[
            WorkflowEdge(id="one", source="trigger", target="review"),
            WorkflowEdge(id="two", source="review", target="publish"),
        ],
    )
    workflow.update_workflow(item.id, WorkflowDefinitionUpdate(graph=graph))
    workflow.publish_workflow(item.id, "admin")
    run = workflow.enqueue_workflow_run(item.id, WorkflowRunRequest(symbol="NVDA", test=True))
    paused = workflow.execute_workflow_run(run.id)
    assert paused.status == "waiting_approval"
    approval = workflow.list_workflow_approvals("pending")[0]

    completed = workflow.decide_workflow_approval(
        approval.id, True, WorkflowApprovalDecision(note="Reviewed"), "admin"
    )
    trace = workflow.get_workflow_trace(run.id)
    assert completed.status == "succeeded"
    assert any(span.status == "waiting_approval" for span in trace.spans)
    assert trace.spans[-1].node_id == "publish"


def test_tool_catalog_exposes_only_controlled_permissions(workflow_db: None) -> None:
    tools = workflow.list_workflow_tools()
    assert {tool.permission_level for tool in tools} <= {"L0", "L1", "L2"}
    assert not any(tool.name.endswith("sql") or tool.name.endswith("http") for tool in tools)


def test_workflow_test_run_does_not_publish_investment_artifacts(workflow_db: None) -> None:
    item = workflow.create_workflow(WorkflowDefinitionCreate(name="Isolated test", template="blank"), "admin")
    run = workflow.enqueue_workflow_run(item.id, WorkflowRunRequest(symbol="AAPL", test=True))
    completed = workflow.execute_workflow_run(run.id)
    assert completed.status == "succeeded"
    assert completed.output["test"] is True
    assert workflow.list_investment_artifacts(symbol="AAPL") == []


def test_agent_data_cleanup_removes_only_expired_terminal_runtime_data(workflow_db: None) -> None:
    item = workflow.create_workflow(WorkflowDefinitionCreate(name="Retention", template="blank"), "admin")
    workflow.publish_workflow(item.id, "admin")
    completed = workflow.execute_workflow_run(
        workflow.enqueue_workflow_run(item.id, WorkflowRunRequest(symbol="AAPL")).id
    )
    cutoff = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    expired = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with database.db_connect() as connection:
        connection.execute("UPDATE workflow_runs SET created_at=? WHERE id=?", (cutoff, completed.id))
        connection.execute(
            "UPDATE investment_artifacts SET created_at=?, valid_until=? WHERE run_id=?",
            (cutoff, expired, completed.id),
        )

    result = workflow.cleanup_agent_data(1, 1, 1, vacuum_after_cleanup=False)

    assert result["spans"] > 0
    assert result["artifacts"] > 0
    assert result["checkpoints"] > 0
    with database.db_connect() as connection:
        assert connection.execute("SELECT 1 FROM workflow_runs WHERE id=?", (completed.id,)).fetchone() is None
