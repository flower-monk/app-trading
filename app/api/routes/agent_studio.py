from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.modules.ai.workflow import (
    create_workflow,
    decide_workflow_approval,
    enqueue_workflow_run,
    execute_workflow_run,
    get_workflow,
    get_workflow_run,
    get_workflow_trace,
    list_investment_artifacts,
    list_workflow_approvals,
    list_workflow_runs,
    list_workflow_tools,
    list_workflow_versions,
    list_workflows,
    publish_workflow,
    rollback_workflow,
    update_workflow,
    validate_workflow,
)
from app.modules.platform.service import add_admin_audit, current_admin_user, current_request_user
from app.schemas import (
    InvestmentArtifact,
    ToolDefinition,
    WorkflowApproval,
    WorkflowApprovalDecision,
    WorkflowDefinition,
    WorkflowDefinitionCreate,
    WorkflowDefinitionUpdate,
    WorkflowRun,
    WorkflowRunRequest,
    WorkflowTrace,
    WorkflowValidation,
    WorkflowVersion,
)


router = APIRouter(tags=["Agent Studio"])


@router.get("/api/agent-studio/tools", response_model=list[ToolDefinition])
def workflow_tools(request: Request) -> list[ToolDefinition]:
    current_admin_user(request)
    return list_workflow_tools()


@router.get("/api/agent-studio/workflows", response_model=list[WorkflowDefinition])
def workflows(request: Request) -> list[WorkflowDefinition]:
    current_admin_user(request)
    return list_workflows()


@router.post("/api/agent-studio/workflows", response_model=WorkflowDefinition)
def add_workflow(payload: WorkflowDefinitionCreate, request: Request) -> WorkflowDefinition:
    actor = current_admin_user(request)
    workflow = create_workflow(payload, actor.username)
    add_admin_audit(actor, "create", "agent-workflows", workflow.id, workflow.name, after_state=workflow.model_dump(mode="json"))
    return workflow


@router.get("/api/agent-studio/workflows/{workflow_id}", response_model=WorkflowDefinition)
def workflow_detail(workflow_id: str, request: Request) -> WorkflowDefinition:
    current_admin_user(request)
    return get_workflow(workflow_id)


@router.patch("/api/agent-studio/workflows/{workflow_id}", response_model=WorkflowDefinition)
def patch_workflow(workflow_id: str, payload: WorkflowDefinitionUpdate, request: Request) -> WorkflowDefinition:
    actor = current_admin_user(request)
    before = get_workflow(workflow_id)
    workflow = update_workflow(workflow_id, payload)
    add_admin_audit(actor, "edit", "agent-workflows", workflow_id, workflow.name, before_state=before.model_dump(mode="json"), after_state=workflow.model_dump(mode="json"))
    return workflow


@router.post("/api/agent-studio/workflows/{workflow_id}/validate", response_model=WorkflowValidation)
def validate_workflow_api(workflow_id: str, request: Request) -> WorkflowValidation:
    current_admin_user(request)
    return validate_workflow(workflow_id)


@router.post("/api/agent-studio/workflows/{workflow_id}/publish", response_model=WorkflowDefinition)
def publish_workflow_api(workflow_id: str, request: Request) -> WorkflowDefinition:
    actor = current_admin_user(request)
    workflow = publish_workflow(workflow_id, actor.username)
    add_admin_audit(actor, "publish", "agent-workflows", workflow_id, f"Published {workflow.name} v{workflow.published_version}")
    return workflow


@router.get("/api/agent-studio/workflows/{workflow_id}/versions", response_model=list[WorkflowVersion])
def workflow_versions(workflow_id: str, request: Request) -> list[WorkflowVersion]:
    current_admin_user(request)
    return list_workflow_versions(workflow_id)


@router.post("/api/agent-studio/workflows/{workflow_id}/rollback", response_model=WorkflowDefinition)
def rollback_workflow_api(workflow_id: str, request: Request, version: int = Query(ge=1)) -> WorkflowDefinition:
    actor = current_admin_user(request)
    workflow = rollback_workflow(workflow_id, version, actor.username)
    add_admin_audit(actor, "rollback", "agent-workflows", workflow_id, f"Rolled back from v{version} as v{workflow.published_version}")
    return workflow


@router.post("/api/agent-studio/workflows/{workflow_id}/run", response_model=WorkflowRun)
def run_workflow(workflow_id: str, payload: WorkflowRunRequest, request: Request) -> WorkflowRun:
    actor = current_admin_user(request)
    run = enqueue_workflow_run(workflow_id, payload)
    add_admin_audit(actor, "run", "agent-workflows", workflow_id, f"Queued workflow run {run.id}")
    return run


@router.post("/api/agent-studio/workflows/{workflow_id}/test", response_model=WorkflowRun)
def test_workflow(workflow_id: str, payload: WorkflowRunRequest, request: Request) -> WorkflowRun:
    actor = current_admin_user(request)
    run = enqueue_workflow_run(workflow_id, payload.model_copy(update={"test": True}))
    result = execute_workflow_run(run.id)
    add_admin_audit(actor, "test", "agent-workflows", workflow_id, f"Test workflow run {result.id}: {result.status}")
    return result


@router.get("/api/agent-studio/runs", response_model=list[WorkflowRun])
def workflow_runs(request: Request, workflow_id: str | None = None, limit: int = Query(default=100, ge=1, le=200)) -> list[WorkflowRun]:
    current_admin_user(request)
    return list_workflow_runs(workflow_id, limit)


@router.get("/api/agent-studio/runs/{run_id}", response_model=WorkflowRun)
def workflow_run(run_id: str, request: Request) -> WorkflowRun:
    current_admin_user(request)
    return get_workflow_run(run_id)


@router.get("/api/agent-studio/runs/{run_id}/trace", response_model=WorkflowTrace)
def workflow_trace(run_id: str, request: Request) -> WorkflowTrace:
    current_admin_user(request)
    return get_workflow_trace(run_id)


@router.get("/api/agent-studio/runs/{run_id}/stream")
async def workflow_run_stream(run_id: str, request: Request) -> StreamingResponse:
    current_admin_user(request)
    async def events():
        last = ""
        for _ in range(300):
            run = get_workflow_run(run_id)
            payload = run.model_dump(mode="json")
            encoded = json.dumps(payload, ensure_ascii=False)
            if encoded != last:
                yield f"event: run\ndata: {encoded}\n\n"
                last = encoded
            if run.status in {"succeeded", "failed", "canceled", "waiting_approval"}:
                return
            await asyncio.sleep(1)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/api/agent-studio/approvals", response_model=list[WorkflowApproval])
def workflow_approvals(request: Request, status: str | None = None) -> list[WorkflowApproval]:
    current_admin_user(request)
    return list_workflow_approvals(status)


@router.post("/api/agent-studio/approvals/{approval_id}/approve", response_model=WorkflowRun)
def approve_workflow(approval_id: str, payload: WorkflowApprovalDecision, request: Request) -> WorkflowRun:
    actor = current_admin_user(request)
    run = decide_workflow_approval(approval_id, True, payload, actor.username)
    add_admin_audit(actor, "approve", "workflow-approvals", approval_id, f"Resumed workflow run {run.id}")
    return run


@router.post("/api/agent-studio/approvals/{approval_id}/reject", response_model=WorkflowRun)
def reject_workflow(approval_id: str, payload: WorkflowApprovalDecision, request: Request) -> WorkflowRun:
    actor = current_admin_user(request)
    run = decide_workflow_approval(approval_id, False, payload, actor.username)
    add_admin_audit(actor, "reject", "workflow-approvals", approval_id, f"Rejected workflow run {run.id}")
    return run


@router.get("/api/investment-artifacts", response_model=list[InvestmentArtifact])
def investment_artifacts(request: Request, artifact_type: str | None = None, symbol: str | None = None, active_only: bool = True, limit: int = Query(default=100, ge=1, le=200)) -> list[InvestmentArtifact]:
    current_request_user(request)
    return list_investment_artifacts(artifact_type, symbol, active_only, limit)
