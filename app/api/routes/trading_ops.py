from __future__ import annotations

from app.modules.infrastructure.database import db_connect
from datetime import datetime, timezone
from hashlib import sha256

from fastapi import APIRouter

from app.schemas import (
    AiBriefing,
    AiInsight,
    AlertEvaluation,
    AlertRule,
    AlertRuleCreate,
    AlertRuleUpdate,
    AutomationRunRecord,
    AutomationRunResult,
    AutomationSettings,
    EventLog,
    FactorIdea,
    NewsEvent,
    NotificationChannel,
    NotificationChannelCreate,
    NotificationChannelUpdate,
    NotificationDelivery,
    PortfolioPosition,
    PortfolioPositionCreate,
    PortfolioPositionUpdate,
    PortfolioRiskReport,
    PortfolioSummary,
)
from app.modules.ai.service import agent_output_for_api, list_agents
from app.modules.news.service import list_news_events


from app.modules.platform.service import get_automation_settings, set_automation_settings
from app.modules.research.service import list_factor_definitions
from app.modules.trading.service import (
    create_notification_channel,
    create_portfolio_position,
    delete_alert_rule,
    delete_notification_channel,
    delete_portfolio_position,
    get_portfolio_risk_report,
    get_portfolio_summary,
    list_alert_rules,
    list_automation_runs,
    list_event_logs,
    list_notification_channels,
    list_notification_deliveries,
    run_automation_cycle,
    update_alert_rule,
    update_notification_channel,
    update_portfolio_position,
)
from app.modules.trading.service import (
    create_alert_rule,
    evaluate_alert_rules,
)


router = APIRouter(tags=["Trading operations"])


@router.get("/api/alerts", response_model=list[AlertRule])
def alerts(symbol: str | None = None) -> list[AlertRule]:
    return list_alert_rules(symbol)


@router.post("/api/alerts", response_model=AlertRule)
def create_alert(payload: AlertRuleCreate) -> AlertRule:
    return create_alert_rule(payload)


@router.patch("/api/alerts/{alert_id}", response_model=AlertRule)
def update_alert(alert_id: str, payload: AlertRuleUpdate) -> AlertRule:
    return update_alert_rule(alert_id, payload)


@router.delete("/api/alerts/{alert_id}", deprecated=True)
def remove_alert(alert_id: str) -> dict[str, bool]:
    return delete_alert_rule(alert_id)


@router.post("/api/alerts/evaluate", response_model=list[AlertEvaluation])
def evaluate_alerts(symbol: str | None = None) -> list[AlertEvaluation]:
    return evaluate_alert_rules(symbol)


@router.get("/api/events", response_model=list[EventLog])
def events(symbol: str | None = None, limit: int = 50) -> list[EventLog]:
    return list_event_logs(symbol=symbol, limit=limit)


@router.get("/api/automation/settings", response_model=AutomationSettings)
def automation_settings() -> AutomationSettings:
    return get_automation_settings()


@router.post("/api/automation/settings", response_model=AutomationSettings)
def update_automation_settings(settings: AutomationSettings) -> AutomationSettings:
    return set_automation_settings(settings)


@router.post("/api/automation/run", response_model=AutomationRunResult)
def automation_run() -> AutomationRunResult:
    return run_automation_cycle()


@router.get("/api/automation/runs", response_model=list[AutomationRunRecord])
def automation_runs(limit: int = 20) -> list[AutomationRunRecord]:
    return list_automation_runs(limit=limit)


@router.get("/api/notifications/channels", response_model=list[NotificationChannel])
def notification_channels() -> list[NotificationChannel]:
    return list_notification_channels()


@router.post("/api/notifications/channels", response_model=NotificationChannel)
def add_notification_channel(payload: NotificationChannelCreate) -> NotificationChannel:
    return create_notification_channel(payload)


@router.patch("/api/notifications/channels/{channel_id}", response_model=NotificationChannel)
def patch_notification_channel(channel_id: str, payload: NotificationChannelUpdate) -> NotificationChannel:
    return update_notification_channel(channel_id, payload)


@router.delete("/api/notifications/channels/{channel_id}", deprecated=True)
def remove_notification_channel(channel_id: str) -> dict[str, bool]:
    return delete_notification_channel(channel_id)


@router.get("/api/notifications/deliveries", response_model=list[NotificationDelivery])
def notification_deliveries(channel_id: str | None = None, limit: int = 50) -> list[NotificationDelivery]:
    return list_notification_deliveries(channel_id=channel_id, limit=limit)


@router.get("/api/portfolio", response_model=PortfolioSummary)
def portfolio() -> PortfolioSummary:
    return get_portfolio_summary()


@router.get("/api/portfolio/risk", response_model=PortfolioRiskReport)
def portfolio_risk() -> PortfolioRiskReport:
    return get_portfolio_risk_report()


@router.post("/api/portfolio/positions", response_model=PortfolioPosition)
def add_portfolio_position(payload: PortfolioPositionCreate) -> PortfolioPosition:
    return create_portfolio_position(payload)


@router.patch("/api/portfolio/positions/{position_id}", response_model=PortfolioPosition)
def patch_portfolio_position(position_id: str, payload: PortfolioPositionUpdate) -> PortfolioPosition:
    return update_portfolio_position(position_id, payload)


@router.delete("/api/portfolio/positions/{position_id}", deprecated=True)
def remove_portfolio_position(position_id: str) -> dict[str, bool]:
    return delete_portfolio_position(position_id)


@router.get("/api/chart-events/{symbol}", response_model=list[NewsEvent])
def chart_events(symbol: str) -> list[NewsEvent]:
    return [event for event in list_news_events(symbol, limit=10) if event.impact_score >= 50]


@router.get("/api/ai/briefing", response_model=AiBriefing)
def ai_briefing() -> AiBriefing:
    agent = next((item for item in list_agents(enabled_only=True) if item.template_type == "briefing" and item.runtime_mode in {"llm", "hybrid"}), None)
    if agent is None:
        now = datetime.now(timezone.utc).isoformat()
        return AiBriefing(id="briefing-disabled", title="AI 全局盯盘简报已停用", summary="请在 Agent 管理中心启用 Briefing Agent。",
                          risk_level="watch", generated_at=now, opportunities=[], risks=["简报 Agent 当前停用。"], actions=["检查 Agent 配置。"], evidence=[])
    output = agent_output_for_api(agent)
    if output is None:
        now = datetime.now(timezone.utc).isoformat()
        return AiBriefing(id="briefing-unavailable", title="AI 全局盯盘简报暂无输出", summary="请先成功运行已配置模型服务的 Briefing Agent。",
                          risk_level="watch", generated_at=now, opportunities=[], risks=["当前没有真实模型输出。"], actions=["检查模型服务与 Agent 运行记录。"], evidence=[])
    generated_at = str(output.get("created_at") or datetime.now(timezone.utc).isoformat())
    return AiBriefing(
        id=sha256(f"{agent.id}:{generated_at}".encode()).hexdigest()[:20], title="AI 全局盯盘简报",
        summary=str(output.get("summary", "暂无简报输出。")), risk_level=output.get("risk_level", "watch"), generated_at=generated_at,
        opportunities=[str(item) for item in output.get("opportunities", [])][:4],
        risks=[str(item) for item in output.get("risks", [])][:4], actions=[str(item) for item in output.get("actions", [])][:5],
        evidence=[str(item) for item in output.get("evidence", [])][:8],
    )


@router.get("/api/ai/insights/{symbol}", response_model=list[AiInsight])
def ai_insights(symbol: str) -> list[AiInsight]:
    base_symbol = symbol.upper()
    titles = {"market_monitor": "实时盯盘结论", "news_analyst": "新闻催化解读", "risk_sentinel": "风险提示"}
    insights: list[AiInsight] = []
    for agent in list_agents(enabled_only=True):
        if agent.template_type not in titles:
            continue
        if agent.runtime_mode not in {"llm", "hybrid"}:
            continue
        if agent.symbol_scope == "custom" and base_symbol not in agent.symbols:
            continue
        output = agent_output_for_api(agent, base_symbol)
        if output is None:
            continue
        insights.append(AiInsight(
            id=f"{base_symbol.lower()}-{agent.id}", agent=agent.name, symbol=base_symbol, title=titles[agent.template_type],
            summary=str(output.get("summary", "暂无分析输出。")), severity=output.get("risk_level", "watch"),
            confidence=max(0, min(100, int(output.get("confidence", 50)))),
            evidence=[str(item) for item in output.get("evidence", [])][:8],
            created_at=str(output.get("created_at") or datetime.now(timezone.utc).isoformat()),
        ))
    return insights


@router.get("/api/factors/{symbol}", response_model=list[FactorIdea])
def factor_ideas(symbol: str) -> list[FactorIdea]:
    base_symbol = symbol.upper()
    factors = {item.id: item for item in list_factor_definitions(enabled_only=True)}
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT runs.*
            FROM backtest_runs AS runs
            JOIN (
                SELECT factor_id, MAX(created_at) AS created_at
                FROM backtest_runs WHERE symbol = ? GROUP BY factor_id
            ) AS latest ON latest.factor_id = runs.factor_id AND latest.created_at = runs.created_at
            WHERE runs.symbol = ?
            """,
            (base_symbol, base_symbol),
        ).fetchall()
    ideas: list[FactorIdea] = []
    for row in rows:
        factor = factors.get(row["factor_id"])
        if factor is None:
            continue
        score = round(max(0, min(100, 50 + row["total_return"] + row["sharpe"] * 10 - row["max_drawdown"])))
        ideas.append(FactorIdea(
            id=f"{base_symbol.lower()}-{factor.id}", name=factor.name, category=factor.category,
            description=factor.description, signal=factor.signal, score=score, horizon=factor.horizon,
        ))
    return sorted(ideas, key=lambda item: item.score, reverse=True)
