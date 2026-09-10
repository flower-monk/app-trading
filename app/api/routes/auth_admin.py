from __future__ import annotations

from app.modules.infrastructure.database import PASSWORD_ITERATIONS, SESSION_COOKIE, db_connect
from app.modules.market.service import MARKET_PROVIDERS
from datetime import datetime, timezone
from fastapi import HTTPException, Query, Request
from fastapi.responses import Response
from io import StringIO
import csv, json, secrets

from fastapi import APIRouter

from app.schemas import (
    AdminOverview,
    AdminResourceDescriptor,
    AdminResourcePage,
    AgentConfig,
    AgentCreate,
    AgentProposal,
    AgentProposalReview,
    AgentProviderCreate,
    AgentProviderProfile,
    AgentProviderUpdate,
    AgentRun,
    AgentRunDetail,
    AgentRunRequest,
    AgentRunSummary,
    AgentUpdate,
    AlertRuleCreate,
    AlertRuleUpdate,
    AuthSessionInfo,
    AuthUser,
    BacktestRunRequest,
    BaseModel,
    BulkResourceStatusUpdate,
    CredentialCreate,
    CredentialProfile,
    CredentialSecretUpdate,
    CredentialUpdate,
    CredentialVaultStatus,
    FactorDefinitionCreate,
    FactorDefinitionUpdate,
    ForwardTestRequest,
    Literal,
    LoginRequest,
    NewsSourceCreate,
    NewsSourceUpdate,
    NotificationChannelCreate,
    NotificationChannelUpdate,
    PasswordResetRequest,
    PortfolioPositionCreate,
    PortfolioPositionUpdate,
    ResourceSecretUpdate,
    ResourceStatusUpdate,
    ScheduledTask,
    ScheduledTaskBulkResult,
    ScheduledTaskBulkUpdate,
    ScheduledTaskRunResult,
    ScheduledTaskUpdate,
    ScreenerPresetCreate,
    ScreenerPresetUpdate,
    SetupRequest,
    SetupStatus,
    StrategyTemplateCreate,
    StrategyTemplateUpdate,
    StrategyValidationRequest,
    StressTestRequest,
    UserCreate,
    UserPreferences,
    UserPreferencesUpdate,
    UserUpdate,
    WalkForwardRequest,
    WatchlistItemCreate,
)
from app.modules.ai.service import (
    add_watchlist_symbol,
    agent_http_post,
    clone_agent,
    create_agent,
    create_agent_provider,
    create_alert,
    create_factor_definition,
    execute_agent_run,
    get_agent_provider,
    get_agent_run_detail,
    list_agent_proposals,
    list_agent_providers,
    review_agent_proposal,
    update_agent_provider,
)

from app.modules.operations.service import (
    bulk_update_scheduled_tasks,
    enqueue_agent_run,
    get_admin_overview,
    get_agent,
    get_scheduled_task,
    list_agent_runs,
    list_agents,
    list_scheduled_tasks,
    refresh_source,
    run_automation_cycle,
    run_scheduled_task_now,
    update_agent,
    update_scheduled_task,
)
from app.modules.news.service import create_news_source, update_news_source
from app.modules.market.service import (
    create_screener_preset,
    create_strategy_template,
    update_screener_preset,
    update_strategy_template,
)
from app.modules.research.service import (
    run_backtest,
    run_forward_test,
    run_strategy_validation,
    run_stress_test,
    run_walk_forward_test,
    update_factor_definition,
)
from app.modules.trading.service import (
    create_notification_channel,
    create_portfolio_position,
    evaluate_alert_rule,
    list_alert_rules,
    test_notification_channel,
    update_alert_rule,
    update_notification_channel,
    update_portfolio_position,
)
from app.modules.platform.service import (
    add_admin_audit,
    admin_resource_config,
    auth_setup_required,
    authenticate_user,
    create_auth_session,
    create_credential,
    create_initial_admin,
    credential_vault_status,
    current_admin_user,
    current_request_user,
    get_admin_resource_item,
    get_credential,
    get_user_preferences,
    insert_user,
    list_admin_resources,
    list_credentials,
    password_digest,
    query_admin_resource,
    remove_credential_secret,
    require_governance_managed_resource,
    resolve_api_key,
    revoke_auth_session,
    row_to_auth_user,
    schedule_credential_test,
    set_config_value,
    set_credential_secret,
    set_resource_secret,
    set_session_cookie,
    set_user_preferences,
    update_credential,
)


router = APIRouter(tags=["Authentication, administration and agents"])


@router.get("/api/auth/setup-status", response_model=SetupStatus)
def auth_setup_status() -> SetupStatus:
    return SetupStatus(required=auth_setup_required())


@router.post("/api/auth/setup", response_model=AuthSessionInfo)
def auth_setup(payload: SetupRequest, response: Response) -> AuthSessionInfo:
    user = create_initial_admin(payload)
    token, session = create_auth_session(user)
    set_session_cookie(response, token)
    add_admin_audit(user, "setup", "users", user.id, "Initial administrator created")
    return session


@router.post("/api/auth/login", response_model=AuthSessionInfo)
def auth_login(payload: LoginRequest, response: Response) -> AuthSessionInfo:
    user = authenticate_user(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token, session = create_auth_session(user)
    set_session_cookie(response, token)
    add_admin_audit(user, "login", "auth_sessions", None, "User signed in")
    return session


@router.get("/api/auth/me", response_model=AuthSessionInfo)
def auth_me(request: Request) -> AuthSessionInfo:
    user = current_request_user(request)
    return AuthSessionInfo(
        user=user,
        csrf_token=request.state.csrf_token,
        expires_at=request.state.session_expires_at,
        preferences=get_user_preferences(user.id),
    )


@router.get("/api/auth/preferences", response_model=UserPreferences)
def auth_preferences(request: Request) -> UserPreferences:
    return get_user_preferences(current_request_user(request).id)


@router.patch("/api/auth/preferences", response_model=UserPreferences)
def patch_auth_preferences(payload: UserPreferencesUpdate, request: Request) -> UserPreferences:
    return set_user_preferences(current_request_user(request).id, payload)


@router.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict[str, bool]:
    user = current_request_user(request)
    revoke_auth_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    add_admin_audit(user, "logout", "auth_sessions", None, "User signed out")
    return {"logged_out": True}


@router.get("/api/schedules", response_model=list[ScheduledTask])
def schedules(request: Request) -> list[ScheduledTask]:
    current_request_user(request)
    return list_scheduled_tasks()


@router.get("/api/schedules/{task_id}", response_model=ScheduledTask)
def schedule_detail(task_id: str, request: Request) -> ScheduledTask:
    current_request_user(request)
    return get_scheduled_task(task_id)


@router.patch("/api/schedules/{task_id}", response_model=ScheduledTask)
def patch_schedule(task_id: str, payload: ScheduledTaskUpdate, request: Request) -> ScheduledTask:
    actor = current_request_user(request)
    before = get_scheduled_task(task_id)
    updated = update_scheduled_task(task_id, payload)
    add_admin_audit(
        actor,
        "edit",
        "schedules",
        task_id,
        "Scheduled task configuration updated",
        before_state=before.model_dump(mode="json"),
        after_state=updated.model_dump(mode="json"),
    )
    return updated


@router.post("/api/schedules/bulk-status", response_model=ScheduledTaskBulkResult)
def schedule_bulk_status(payload: ScheduledTaskBulkUpdate, request: Request) -> ScheduledTaskBulkResult:
    actor = current_request_user(request)
    result = bulk_update_scheduled_tasks(payload)
    add_admin_audit(actor, "bulk_enable" if payload.enabled else "bulk_disable", "schedules", None, f"Updated {result.updated} scheduled tasks")
    return result


@router.post("/api/schedules/{task_id}/run", response_model=ScheduledTaskRunResult)
def run_schedule(task_id: str, request: Request) -> ScheduledTaskRunResult:
    actor = current_request_user(request)
    result = run_scheduled_task_now(task_id)
    add_admin_audit(actor, "run", "schedules", task_id, result.message)
    return result


@router.get("/api/agent-providers", response_model=list[AgentProviderProfile])
def agent_providers() -> list[AgentProviderProfile]:
    return list_agent_providers()


@router.post("/api/agent-providers", response_model=AgentProviderProfile)
def add_agent_provider(payload: AgentProviderCreate, request: Request) -> AgentProviderProfile:
    actor = current_request_user(request)
    provider = create_agent_provider(payload)
    add_admin_audit(actor, "create", "agent-providers", provider.id, "Agent model provider created", after_state=provider.model_dump(mode="json"))
    return provider


@router.patch("/api/agent-providers/{provider_id}", response_model=AgentProviderProfile)
def patch_agent_provider(provider_id: str, payload: AgentProviderUpdate, request: Request) -> AgentProviderProfile:
    actor = current_request_user(request)
    before = get_agent_provider(provider_id).model_dump(mode="json")
    provider = update_agent_provider(provider_id, payload)
    add_admin_audit(actor, "edit", "agent-providers", provider_id, "Agent model provider updated", before_state=before, after_state=provider.model_dump(mode="json"))
    return provider


@router.post("/api/agent-providers/{provider_id}/test")
def test_agent_provider_connection(provider_id: str, request: Request) -> dict[str, object]:
    actor = current_request_user(request)
    provider = get_agent_provider(provider_id, require_enabled=True)
    api_key = resolve_api_key("agent_provider", provider.id, provider.api_key_env)
    if not api_key:
        raise HTTPException(status_code=409, detail=f"Environment variable {provider.api_key_env} is not configured")
    started = datetime.now(timezone.utc)
    try:
        agent_http_post(
            f"{provider.base_url.rstrip('/')}/chat/completions",
            {"model": provider.model, "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 10, "temperature": 0},
            api_key,
            provider.timeout_seconds,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Provider connection failed: {str(exc)[:300]}") from exc
    latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    add_admin_audit(actor, "test", "agent-providers", provider_id, f"Provider connection succeeded in {latency}ms")
    return {"ok": True, "latency_ms": latency, "model": provider.model}


@router.get("/api/credentials/status", response_model=CredentialVaultStatus)
def credential_status(request: Request) -> CredentialVaultStatus:
    current_admin_user(request)
    return credential_vault_status()


@router.get("/api/credentials", response_model=list[CredentialProfile])
def credentials(request: Request) -> list[CredentialProfile]:
    current_admin_user(request)
    return list_credentials()


@router.get("/api/credentials/{credential_id}", response_model=CredentialProfile)
def credential_detail(credential_id: str, request: Request) -> CredentialProfile:
    current_admin_user(request)
    return get_credential(credential_id)


@router.post("/api/credentials", response_model=CredentialProfile)
def add_credential(payload: CredentialCreate, request: Request) -> CredentialProfile:
    actor = current_request_user(request)
    credential = create_credential(payload)
    add_admin_audit(
        actor, "create", "credentials", credential.id, "Credential metadata created",
        after_state={"id": credential.id, "name": credential.name, "env_alias": credential.env_alias},
    )
    return credential


@router.patch("/api/credentials/{credential_id}", response_model=CredentialProfile)
def patch_credential(credential_id: str, payload: CredentialUpdate, request: Request) -> CredentialProfile:
    actor = current_request_user(request)
    before = get_credential(credential_id)
    credential = update_credential(credential_id, payload)
    add_admin_audit(
        actor, "edit", "credentials", credential_id, "Credential metadata updated",
        before_state={"name": before.name, "env_alias": before.env_alias},
        after_state={"name": credential.name, "env_alias": credential.env_alias},
    )
    return credential


@router.put("/api/credentials/{credential_id}/secret", response_model=CredentialProfile)
def replace_credential_secret(credential_id: str, payload: CredentialSecretUpdate, request: Request) -> CredentialProfile:
    actor = current_request_user(request)
    credential = set_credential_secret(credential_id, payload.secret.get_secret_value())
    add_admin_audit(
        actor, "rotate", "credentials", credential_id,
        f"Credential rotated; version {credential.version}; affected resources {credential.binding_count}",
    )
    return credential


@router.delete("/api/credentials/{credential_id}/secret", response_model=CredentialProfile)
def clear_credential_secret(credential_id: str, request: Request) -> CredentialProfile:
    actor = current_request_user(request)
    credential = remove_credential_secret(credential_id)
    add_admin_audit(actor, "clear_override", "credentials", credential_id, "Database secret removed; environment fallback restored")
    return credential


@router.post("/api/credentials/{credential_id}/test", response_model=CredentialProfile)
def test_credential_connection(credential_id: str, request: Request) -> CredentialProfile:
    actor = current_request_user(request)
    credential = schedule_credential_test(credential_id)
    add_admin_audit(actor, "test", "credentials", credential_id, "Credential connection test queued")
    return credential


@router.put("/api/market-providers/{provider_id}/credential", response_model=CredentialProfile)
def replace_market_provider_credential(
    provider_id: str, payload: ResourceSecretUpdate, request: Request
) -> CredentialProfile:
    actor = current_request_user(request)
    if provider_id != "finnhub":
        raise HTTPException(status_code=409, detail="This market provider does not require an API key")
    credential = set_resource_secret(
        "market_provider", provider_id, "Finnhub 行情", "FINNHUB_API_KEY",
        payload.api_key.get_secret_value(),
    )
    add_admin_audit(actor, "rotate", "market-providers", provider_id, f"Market provider credential rotated; version {credential.version}")
    return credential


@router.get("/api/agents", response_model=list[AgentConfig])
def agents(enabled_only: bool = False) -> list[AgentConfig]:
    return list_agents(enabled_only=enabled_only)


@router.post("/api/agents", response_model=AgentConfig)
def add_agent(payload: AgentCreate, request: Request) -> AgentConfig:
    actor = current_request_user(request)
    agent = create_agent(payload)
    add_admin_audit(actor, "create", "agents", agent.id, "Agent created", after_state=agent.model_dump(mode="json"))
    return agent


@router.get("/api/agents/runs", response_model=list[AgentRunSummary])
def all_agent_runs(limit: int = Query(default=50, ge=1, le=200)) -> list[AgentRunSummary]:
    return [AgentRunSummary.model_validate(item.model_dump()) for item in list_agent_runs(limit=limit)]


@router.get("/api/agents/runs/{run_id}", response_model=AgentRunDetail)
def agent_run_detail(run_id: str, request: Request) -> AgentRunDetail:
    current_admin_user(request)
    return get_agent_run_detail(run_id)


@router.get("/api/agents/{agent_id}", response_model=AgentConfig)
def agent_detail(agent_id: str) -> AgentConfig:
    return get_agent(agent_id)


@router.patch("/api/agents/{agent_id}", response_model=AgentConfig)
def patch_agent(agent_id: str, payload: AgentUpdate, request: Request) -> AgentConfig:
    actor = current_request_user(request)
    before = get_agent(agent_id).model_dump(mode="json")
    agent = update_agent(agent_id, payload)
    add_admin_audit(actor, "edit", "agents", agent_id, "Agent configuration updated", before_state=before, after_state=agent.model_dump(mode="json"))
    return agent


@router.post("/api/agents/{agent_id}/clone", response_model=AgentConfig)
def clone_agent_api(agent_id: str, request: Request) -> AgentConfig:
    actor = current_request_user(request)
    agent = clone_agent(agent_id)
    add_admin_audit(actor, "clone", "agents", agent.id, f"Cloned from {agent_id}", after_state=agent.model_dump(mode="json"))
    return agent


@router.post("/api/agents/{agent_id}/run", response_model=AgentRun)
def run_agent_api(agent_id: str, payload: AgentRunRequest, request: Request) -> AgentRun:
    actor = current_admin_user(request)
    run = enqueue_agent_run(agent_id, trigger="manual", symbol=payload.symbol, context=payload.context, test=False)
    add_admin_audit(actor, "run", "agents", agent_id, f"Queued Agent run {run.id}")
    return run


@router.post("/api/agents/{agent_id}/test", response_model=AgentRun)
def test_agent_api(agent_id: str, payload: AgentRunRequest, request: Request) -> AgentRun:
    actor = current_admin_user(request)
    run = enqueue_agent_run(agent_id, trigger="manual", symbol=payload.symbol, context=payload.context, test=True)
    result = execute_agent_run(run.id)
    add_admin_audit(actor, "test", "agents", agent_id, f"Test Agent run {result.id}: {result.status}")
    return result


@router.get("/api/agents/{agent_id}/runs", response_model=list[AgentRunSummary])
def agent_run_history(agent_id: str, limit: int = Query(default=50, ge=1, le=200)) -> list[AgentRunSummary]:
    get_agent(agent_id)
    return [AgentRunSummary.model_validate(item.model_dump()) for item in list_agent_runs(agent_id=agent_id, limit=limit)]


@router.get("/api/agent-proposals", response_model=list[AgentProposal])
def agent_proposals(
    status: str | None = None,
    resource_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[AgentProposal]:
    return list_agent_proposals(status=status, resource_type=resource_type, limit=limit)


@router.post("/api/agent-proposals/{proposal_id}/approve", response_model=AgentProposal)
def approve_agent_proposal(proposal_id: str, payload: AgentProposalReview, request: Request) -> AgentProposal:
    actor = current_request_user(request)
    proposal = review_agent_proposal(proposal_id, True, actor, payload.note, payload.payload_override)
    add_admin_audit(actor, "approve", "agent-proposals", proposal_id, proposal.title, after_state=proposal.model_dump(mode="json"))
    return proposal


@router.post("/api/agent-proposals/{proposal_id}/reject", response_model=AgentProposal)
def reject_agent_proposal(proposal_id: str, payload: AgentProposalReview, request: Request) -> AgentProposal:
    actor = current_request_user(request)
    proposal = review_agent_proposal(proposal_id, False, actor, payload.note, payload.payload_override)
    add_admin_audit(actor, "reject", "agent-proposals", proposal_id, proposal.title, after_state=proposal.model_dump(mode="json"))
    return proposal


@router.get("/api/admin/users", response_model=list[AuthUser])
def admin_users(request: Request) -> list[AuthUser]:
    current_request_user(request)
    with db_connect() as connection:
        rows = connection.execute("SELECT * FROM users ORDER BY role, username").fetchall()
    return [row_to_auth_user(row) for row in rows]


@router.post("/api/admin/users", response_model=AuthUser)
def admin_create_user(payload: UserCreate, request: Request) -> AuthUser:
    actor = current_request_user(request)
    with db_connect() as connection:
        user = insert_user(connection, payload)
    add_admin_audit(actor, "create", "users", user.id, f"Created {user.username} as {user.role}", after_state=user.model_dump(mode="json"))
    return user


@router.patch("/api/admin/users/{user_id}", response_model=AuthUser)
def admin_update_user(user_id: str, payload: UserUpdate, request: Request) -> AuthUser:
    actor = current_request_user(request)
    with db_connect() as connection:
        current = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="User not found")
        before_user = row_to_auth_user(current).model_dump(mode="json")
        next_enabled = bool(current["enabled"]) if payload.enabled is None else payload.enabled
        next_role = current["role"] if payload.role is None else payload.role
        if user_id == actor.id and (not next_enabled or next_role != "admin"):
            raise HTTPException(status_code=409, detail="You cannot disable or demote your own administrator account")
        if current["role"] == "admin" and (not next_enabled or next_role != "admin"):
            other_admins = connection.execute(
                "SELECT COUNT(*) AS total FROM users WHERE role = 'admin' AND enabled = 1 AND id != ?",
                (user_id,),
            ).fetchone()["total"]
            if not other_admins:
                raise HTTPException(status_code=409, detail="At least one active administrator is required")
        connection.execute(
            "UPDATE users SET display_name = ?, role = ?, enabled = ?, updated_at = ? WHERE id = ?",
            (
                current["display_name"] if payload.display_name is None else payload.display_name.strip(),
                next_role,
                int(next_enabled),
                datetime.now(timezone.utc).isoformat(),
                user_id,
            ),
        )
        if not next_enabled:
            connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    user = row_to_auth_user(row)
    add_admin_audit(
        actor,
        "update",
        "users",
        user.id,
        f"Updated {user.username}: role={user.role}, enabled={user.enabled}",
        before_state=before_user,
        after_state=user.model_dump(mode="json"),
    )
    return user


@router.post("/api/admin/users/{user_id}/reset-password")
def admin_reset_user_password(user_id: str, payload: PasswordResetRequest, request: Request) -> dict[str, bool]:
    actor = current_request_user(request)
    salt = secrets.token_hex(16)
    with db_connect() as connection:
        cursor = connection.execute(
            "UPDATE users SET password_salt = ?, password_hash = ?, password_iterations = ?, updated_at = ? WHERE id = ?",
            (salt, password_digest(payload.password, salt), PASSWORD_ITERATIONS, datetime.now(timezone.utc).isoformat(), user_id),
        )
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
    add_admin_audit(actor, "reset_password", "users", user_id, "Password reset and sessions revoked")
    return {"reset": True}


@router.post("/api/admin/users/{user_id}/revoke-sessions")
def admin_revoke_user_sessions(user_id: str, request: Request) -> dict[str, bool]:
    actor = current_request_user(request)
    with db_connect() as connection:
        if connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
    add_admin_audit(actor, "revoke_sessions", "users", user_id, "All user sessions revoked")
    return {"revoked": True}


@router.get("/api/admin/resources", response_model=list[AdminResourceDescriptor])
def admin_resources(request: Request) -> list[AdminResourceDescriptor]:
    current_request_user(request)
    return list_admin_resources()


@router.get("/api/admin/overview", response_model=AdminOverview)
def admin_overview(request: Request) -> AdminOverview:
    current_admin_user(request)
    return get_admin_overview()


@router.post("/api/admin/resources/{resource_type}")
def admin_resource_create(resource_type: str, payload: dict[str, object], request: Request) -> dict[str, object]:
    actor = current_request_user(request)
    require_governance_managed_resource(resource_type)
    if resource_type == "users":
        with db_connect() as connection:
            result: BaseModel = insert_user(connection, UserCreate.model_validate(payload))
    elif resource_type == "watchlist":
        result = add_watchlist_symbol(WatchlistItemCreate.model_validate(payload))
    elif resource_type == "screener-presets":
        result = create_screener_preset(ScreenerPresetCreate.model_validate(payload))
    elif resource_type == "news-sources":
        result = create_news_source(NewsSourceCreate.model_validate(payload))
    elif resource_type == "factors":
        result = create_factor_definition(FactorDefinitionCreate.model_validate(payload))
    elif resource_type == "strategies":
        result = create_strategy_template(StrategyTemplateCreate.model_validate(payload))
    elif resource_type == "alerts":
        result = create_alert(AlertRuleCreate.model_validate(payload))
    elif resource_type == "notification-channels":
        result = create_notification_channel(NotificationChannelCreate.model_validate(payload))
    elif resource_type == "portfolio-positions":
        result = create_portfolio_position(PortfolioPositionCreate.model_validate(payload))
    else:
        raise HTTPException(status_code=409, detail="This resource cannot be created manually")
    result_data = result.model_dump(mode="json")
    resource_id = str(result_data.get("id", result_data.get("symbol", "")))
    add_admin_audit(actor, "create", resource_type, resource_id, "Resource created from management center", after_state=result_data)
    return result_data


@router.get("/api/admin/resources/{resource_type}", response_model=AdminResourcePage)
def admin_resource_list(
    resource_type: str,
    request: Request,
    page: int = 1,
    page_size: int = 25,
    query: str = "",
    enabled: bool | None = None,
    source_type: str = "",
    sort: str = "",
    direction: Literal["asc", "desc"] = "desc",
) -> AdminResourcePage:
    current_request_user(request)
    return query_admin_resource(
        resource_type, page=page, page_size=page_size, query=query, enabled=enabled,
        source_type=source_type, sort=sort, direction=direction,
    )


@router.get("/api/admin/resources/{resource_type}/export", response_class=Response)
def admin_resource_export(
    resource_type: str,
    request: Request,
    query: str = "",
    enabled: bool | None = None,
    source_type: str = "",
    sort: str = "",
    direction: Literal["asc", "desc"] = "desc",
) -> Response:
    current_request_user(request)
    page = query_admin_resource(
        resource_type, page=1, page_size=100, query=query, enabled=enabled,
        source_type=source_type, sort=sort, direction=direction,
    )
    items = list(page.items)
    page_number = 2
    while len(items) < page.total:
        next_page = query_admin_resource(
            resource_type, page=page_number, page_size=100, query=query, enabled=enabled,
            source_type=source_type, sort=sort, direction=direction,
        )
        if not next_page.items:
            break
        items.extend(next_page.items)
        page_number += 1
    output = StringIO()
    if items:
        fields = list(items[0].keys())
        writer = csv.writer(output)
        writer.writerow(fields)
        for item in items:
            writer.writerow([json.dumps(item.get(field), ensure_ascii=False) if isinstance(item.get(field), (dict, list)) else item.get(field, "") for field in fields])
    else:
        output.write("resource_id\n")
    generated = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(
        output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{resource_type}-{generated}.csv"'},
    )


@router.get("/api/admin/resources/{resource_type}/{resource_id}")
def admin_resource_detail(resource_type: str, resource_id: str, request: Request) -> dict[str, object]:
    current_request_user(request)
    return get_admin_resource_item(resource_type, resource_id)


@router.patch("/api/admin/resources/{resource_type}/{resource_id}")
def admin_resource_edit(resource_type: str, resource_id: str, payload: dict[str, object], request: Request) -> dict[str, object]:
    actor = current_request_user(request)
    require_governance_managed_resource(resource_type)
    before = get_admin_resource_item(resource_type, resource_id)
    if resource_type == "screener-presets":
        result: BaseModel = update_screener_preset(resource_id, ScreenerPresetUpdate.model_validate(payload))
    elif resource_type == "news-sources":
        result = update_news_source(int(resource_id), NewsSourceUpdate.model_validate(payload))
    elif resource_type == "factors":
        result = update_factor_definition(resource_id, FactorDefinitionUpdate.model_validate(payload))
    elif resource_type == "strategies":
        result = update_strategy_template(resource_id, StrategyTemplateUpdate.model_validate(payload))
    elif resource_type == "alerts":
        result = update_alert_rule(resource_id, AlertRuleUpdate.model_validate(payload))
    elif resource_type == "notification-channels":
        result = update_notification_channel(resource_id, NotificationChannelUpdate.model_validate(payload))
    elif resource_type == "portfolio-positions":
        result = update_portfolio_position(resource_id, PortfolioPositionUpdate.model_validate(payload))
    elif resource_type == "settings":
        allowed_settings = {
            "active_market_provider",
            "automation_enabled",
            "automation_interval_seconds",
            "default_signal_filter",
            "default_rsi_min",
            "default_positive_only",
        }
        if resource_id not in allowed_settings or "value" not in payload:
            raise HTTPException(status_code=422, detail="Unsupported setting or missing value")
        value = str(payload["value"]).strip()
        if resource_id == "active_market_provider" and value not in MARKET_PROVIDERS:
            raise HTTPException(status_code=422, detail="Unsupported market provider")
        if resource_id in {"automation_enabled", "default_positive_only"} and value.lower() not in {"true", "false"}:
            raise HTTPException(status_code=422, detail="Boolean setting must be true or false")
        if resource_id in {"automation_interval_seconds", "default_rsi_min"}:
            try:
                int(value)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Numeric setting requires an integer") from exc
        set_config_value(resource_id, value)
        result_data = {"key": resource_id, "value": value}
        add_admin_audit(actor, "edit", resource_type, resource_id, "System setting updated from management center", before_state=before, after_state=result_data)
        return result_data
    else:
        raise HTTPException(status_code=409, detail="This resource does not support field editing")
    result_data = result.model_dump(mode="json")
    add_admin_audit(actor, "edit", resource_type, resource_id, "Resource fields updated from management center", before_state=before, after_state=result_data)
    return result_data


@router.patch("/api/admin/resources/{resource_type}/{resource_id}/status")
def admin_resource_status(resource_type: str, resource_id: str, payload: ResourceStatusUpdate, request: Request) -> dict[str, object]:
    actor = current_request_user(request)
    require_governance_managed_resource(resource_type)
    config = admin_resource_config(resource_type)
    status_field = config.get("status")
    if not status_field or resource_type == "users":
        raise HTTPException(status_code=409, detail="This resource does not support generic status changes")
    before = get_admin_resource_item(resource_type, resource_id)
    with db_connect() as connection:
        cursor = connection.execute(
            f"UPDATE {config['table']} SET {status_field} = ? WHERE {config['id']} = ?",
            (int(payload.enabled), resource_id),
        )
    if not cursor.rowcount:
        raise HTTPException(status_code=404, detail="Resource not found")
    after = get_admin_resource_item(resource_type, resource_id)
    add_admin_audit(
        actor,
        "enable" if payload.enabled else "disable",
        resource_type,
        resource_id,
        f"enabled={payload.enabled}",
        before_state=before,
        after_state=after,
    )
    return after


@router.post("/api/admin/resources/{resource_type}/bulk-status")
def admin_resource_bulk_status(resource_type: str, payload: BulkResourceStatusUpdate, request: Request) -> dict[str, int | bool]:
    actor = current_request_user(request)
    require_governance_managed_resource(resource_type)
    config = admin_resource_config(resource_type)
    status_field = config.get("status")
    if not status_field or resource_type == "users":
        raise HTTPException(status_code=409, detail="This resource does not support bulk status changes")
    updated = 0
    with db_connect() as connection:
        for resource_id in dict.fromkeys(payload.ids):
            updated += connection.execute(
                f"UPDATE {config['table']} SET {status_field} = ? WHERE {config['id']} = ?",
                (int(payload.enabled), resource_id),
            ).rowcount
    add_admin_audit(actor, "bulk_enable" if payload.enabled else "bulk_disable", resource_type, None, f"Updated {updated} resources")
    return {"updated": updated, "enabled": payload.enabled}


@router.post("/api/admin/resources/{resource_type}/{resource_id}/rerun")
def admin_resource_rerun(resource_type: str, resource_id: str, request: Request) -> dict[str, object]:
    actor = current_request_user(request)
    item = get_admin_resource_item(resource_type, resource_id)
    payload = item.get("request_payload") if isinstance(item.get("request_payload"), dict) else {}
    if resource_type == "backtests":
        result = run_backtest(BacktestRunRequest(**(payload or {"symbol": item["symbol"], "factor_id": item["factor_id"], "interval": item["interval"], "initial_capital": item["initial_capital"]})))
    elif resource_type == "forward-tests":
        result = run_forward_test(ForwardTestRequest(**(payload or {"symbol": item["symbol"], "factor_id": item["factor_id"], "interval": item["interval"], "train_bars": item["train_bars"], "forward_bars": item["forward_bars"]})))
    elif resource_type == "walk-forward-tests":
        result = run_walk_forward_test(WalkForwardRequest(**(payload or {"symbol": item["symbol"], "factor_id": item["factor_id"], "interval": item["interval"], "train_bars": item["train_bars"], "forward_bars": item["forward_bars"], "windows": item["window_count"]})))
    elif resource_type == "stress-tests":
        result = run_stress_test(StressTestRequest(**(payload or {"symbol": item["symbol"], "factor_id": item["factor_id"], "interval": "1h"})))
    elif resource_type == "strategy-validations":
        result = run_strategy_validation(
            str(item["strategy_id"]),
            StrategyValidationRequest(**(payload or {"symbol_limit": max(1, len(item.get("symbols", []))), "interval": "1h"})),
        )
    elif resource_type == "automation-runs":
        result = run_automation_cycle()
    else:
        raise HTTPException(status_code=409, detail="This resource cannot be rerun")
    result_data = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
    result_id = str(result_data.get("id", "")) if isinstance(result_data, dict) else ""
    add_admin_audit(actor, "rerun", resource_type, resource_id, f"Created run {result_id}")
    return {"source_id": resource_id, "new_id": result_id, "result": result_data}


@router.post("/api/admin/resources/{resource_type}/{resource_id}/action/{action}")
def admin_resource_action(resource_type: str, resource_id: str, action: str, request: Request) -> dict[str, object]:
    actor = current_request_user(request)
    require_governance_managed_resource(resource_type)
    if resource_type == "news-sources" and action == "refresh":
        result: BaseModel = refresh_source(int(resource_id))
    elif resource_type == "strategies" and action == "validate":
        result = run_strategy_validation(resource_id, StrategyValidationRequest())
    elif resource_type == "alerts" and action == "evaluate":
        rule = next((item for item in list_alert_rules() if item.id == resource_id), None)
        if rule is None:
            raise HTTPException(status_code=404, detail="Alert not found")
        result = evaluate_alert_rule(rule)
    elif resource_type == "notification-channels" and action == "test":
        result = test_notification_channel(resource_id)
    elif resource_type == "auth-sessions" and action == "revoke":
        with db_connect() as connection:
            cursor = connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (resource_id,))
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="Session not found")
        result = {"revoked": True}
    else:
        raise HTTPException(status_code=409, detail="Unsupported resource action")
    result_data = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
    add_admin_audit(actor, action, resource_type, resource_id, "Resource action executed")
    return result_data
