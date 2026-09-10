from __future__ import annotations

from fastapi import Query

from fastapi import APIRouter

from app.modules.ai.service import enqueue_agent_event

from app.schemas import (
    BacktestBenchmarkReport,
    BacktestForwardConsistencyReport,
    BacktestRunRequest,
    BacktestRunResult,
    BacktestSummary,
    DataCatalogItem,
    DataFreshnessReport,
    DataLineageReport,
    DataQualityReport,
    FactorAdmissionQueueReport,
    FactorAdmissionReport,
    FactorAdmissionRequest,
    FactorCorrelationReport,
    FactorCoverageBackfillRequest,
    FactorCoverageBackfillResult,
    FactorCoverageReport,
    FactorDefinition,
    FactorDefinitionCreate,
    FactorDefinitionUpdate,
    FactorLeaderboard,
    FactorLifecycleReport,
    FilterAdmissionReport,
    FilterContributionReport,
    FilterStressTestReport,
    FilterValidationRequest,
    FilterValidationResult,
    ForwardLeaderboard,
    ForwardTestRequest,
    ForwardTestResult,
    Literal,
    MarketRegime,
    MarketRegimeTransition,
    MarketStateControlReport,
    ParameterOptimizationReport,
    ParameterOptimizationRequest,
    RegimeAttributionReport,
    RegimeExposureReport,
    RegimeFactorFitReport,
    RegimeFilterTemplate,
    RegimePlaybook,
    RegimeRiskGate,
    RegimeScenarioRiskReport,
    ResearchActionPlan,
    ResearchCoverageBackfillRequest,
    ResearchCoverageBackfillResult,
    ResearchCoverageReport,
    ResearchDashboardSummary,
    ResearchHealthReport,
    ResearchPipelineRequest,
    ResearchPipelineResult,
    ResearchReadiness,
    ResearchRunAuditReport,
    RevalidationQueueReport,
    RobustnessReport,
    SensitivityTestRequest,
    SensitivityTestResult,
    StrategyAllocationReport,
    StrategyBacktestMonitorReport,
    StrategyCrowdingReport,
    StrategyDriftReport,
    StrategyEvidencePackReport,
    StrategyForwardMonitorReport,
    StrategyLeaderboard,
    StrategyLifecycleReport,
    StrategyOperatingPlanReport,
    StrategyPromotionGate,
    StrategyRegimeFitReport,
    StrategyRegimeMatrixReport,
    StrategyRevalidationPlanReport,
    StrategyRiskControlReport,
    StrategySensitivityReport,
    StrategyTemplate,
    StrategyTemplateCreate,
    StrategyTemplateUpdate,
    StrategyValidationCoverageReport,
    StrategyValidationDecayReport,
    StrategyValidationRequest,
    StrategyValidationResult,
    StressLeaderboard,
    StressTestRequest,
    StressTestResult,
    TradeReadinessReport,
    WalkForwardLeaderboard,
    WalkForwardRequest,
    WalkForwardResult,
)

from app.modules.market.service import create_strategy_template, delete_strategy_template, update_strategy_template

from app.modules.research.service import (
    backfill_factor_coverage,
    backfill_research_coverage,
    backtest_benchmark_report,
    backtest_forward_consistency_report,
    create_factor_definition,
    data_catalog_items,
    data_freshness_report,
    data_lineage_report,
    data_quality_report,
    factor_admission_queue_report,
    factor_admission_report,
    factor_correlation_report,
    factor_coverage_report,
    factor_leaderboard_report,
    factor_lifecycle_report,
    filter_admission_report,
    filter_contribution_report,
    filter_stress_test_report,
    forward_leaderboard_report,
    list_factor_definitions,
    list_forward_tests,
    list_market_regime_history,
    list_strategy_templates,
    list_strategy_validations,
    list_stress_tests,
    list_walk_forward_tests,
    market_regime_report,
    market_regime_transition_report,
    market_state_control_report,
    parameter_optimization_report,
    record_market_regime_snapshot,
    regime_attribution_report,
    regime_exposure_report,
    regime_factor_fit_report,
    regime_filter_templates,
    regime_playbook_report,
    regime_risk_gate_report,
    regime_scenario_risk_report,
    research_action_plan,
    research_coverage_report,
    research_dashboard_summary,
    research_health_report,
    research_readiness_report,
    research_run_audit_report,
    revalidation_queue_report,
    robustness_report,
    run_backtest,
    run_forward_test,
    run_research_pipeline,
    run_sensitivity_test,
    run_strategy_validation,
    run_stress_test,
    run_walk_forward_test,
    strategy_allocation_report,
    strategy_backtest_monitor_report,
    strategy_crowding_report,
    strategy_drift_report,
    strategy_evidence_pack_report,
    strategy_forward_monitor_report,
    strategy_leaderboard_report,
    strategy_lifecycle_report,
    strategy_operating_plan_report,
    strategy_promotion_gate_report,
    strategy_promotion_gates,
    strategy_regime_fit_report,
    strategy_regime_matrix_report,
    strategy_revalidation_plan_report,
    strategy_risk_control_report,
    strategy_sensitivity_report,
    strategy_validation_coverage_report,
    strategy_validation_decay_report,
    stress_leaderboard_report,
    trade_readiness_report,
    update_factor_definition,
    validate_filters,
    walk_forward_leaderboard_report,
)
from app.modules.research.backtesting import (
    backtest_summaries as backtest_summaries_service,
    list_backtest_runs as list_backtest_runs_service,
)


router = APIRouter(tags=["Quant research"])


@router.get("/api/factor-definitions", response_model=list[FactorDefinition])
def factor_definitions(enabled_only: bool = False) -> list[FactorDefinition]:
    return list_factor_definitions(enabled_only=enabled_only)


@router.get("/api/data-catalog", response_model=list[DataCatalogItem])
def data_catalog() -> list[DataCatalogItem]:
    return data_catalog_items()


@router.get("/api/data-quality", response_model=DataQualityReport)
def data_quality() -> DataQualityReport:
    return data_quality_report()


@router.get("/api/data-freshness", response_model=DataFreshnessReport)
def data_freshness() -> DataFreshnessReport:
    return data_freshness_report()


@router.get("/api/market-regime", response_model=MarketRegime)
def market_regime() -> MarketRegime:
    return record_market_regime_snapshot(market_regime_report())


@router.get("/api/market-regime/history", response_model=list[MarketRegime])
def market_regime_history(limit: int = 12) -> list[MarketRegime]:
    return list_market_regime_history(limit=limit)


@router.get("/api/market-regime/transition", response_model=MarketRegimeTransition)
def market_regime_transition() -> MarketRegimeTransition:
    return market_regime_transition_report()


@router.get("/api/regime-scenario-risk", response_model=RegimeScenarioRiskReport)
def regime_scenario_risk() -> RegimeScenarioRiskReport:
    return regime_scenario_risk_report()


@router.get("/api/research-readiness", response_model=ResearchReadiness)
def research_readiness() -> ResearchReadiness:
    return research_readiness_report()


@router.get("/api/research-coverage", response_model=ResearchCoverageReport)
def research_coverage() -> ResearchCoverageReport:
    return research_coverage_report()


@router.get("/api/factor-coverage", response_model=FactorCoverageReport)
def factor_coverage() -> FactorCoverageReport:
    return factor_coverage_report()


@router.get("/api/factor-lifecycle", response_model=FactorLifecycleReport)
def factor_lifecycle(symbol: str | None = None) -> FactorLifecycleReport:
    return factor_lifecycle_report(symbol=symbol)


@router.get("/api/data-lineage", response_model=DataLineageReport)
def data_lineage() -> DataLineageReport:
    return data_lineage_report()


@router.get("/api/research-run-audit", response_model=ResearchRunAuditReport)
def research_run_audit(
    limit: int = 12,
    symbol: str | None = None,
    factor_id: str | None = None,
) -> ResearchRunAuditReport:
    return research_run_audit_report(limit=limit, symbol=symbol, factor_id=factor_id)


@router.post("/api/factor-coverage/backfill", response_model=FactorCoverageBackfillResult)
def factor_coverage_backfill(payload: FactorCoverageBackfillRequest) -> FactorCoverageBackfillResult:
    return backfill_factor_coverage(payload)


@router.post("/api/research-coverage/backfill", response_model=ResearchCoverageBackfillResult)
def research_coverage_backfill(payload: ResearchCoverageBackfillRequest) -> ResearchCoverageBackfillResult:
    return backfill_research_coverage(payload)


@router.get("/api/research-action-plan", response_model=ResearchActionPlan)
def research_actions() -> ResearchActionPlan:
    return research_action_plan()


@router.get("/api/research-dashboard", response_model=ResearchDashboardSummary)
def research_dashboard() -> ResearchDashboardSummary:
    return research_dashboard_summary()


@router.get("/api/research-health", response_model=ResearchHealthReport)
def research_health() -> ResearchHealthReport:
    return research_health_report()


@router.post("/api/filters/validate", response_model=FilterValidationResult)
def filter_validation(payload: FilterValidationRequest) -> FilterValidationResult:
    return validate_filters(payload)


@router.post("/api/filters/contribution", response_model=FilterContributionReport)
def filter_contribution(payload: FilterValidationRequest) -> FilterContributionReport:
    return filter_contribution_report(payload)


@router.post("/api/filters/admission", response_model=FilterAdmissionReport)
def filter_admission(payload: FilterValidationRequest) -> FilterAdmissionReport:
    return filter_admission_report(payload)


@router.post("/api/filters/stress-test", response_model=FilterStressTestReport)
def filter_stress_test(payload: FilterValidationRequest) -> FilterStressTestReport:
    return filter_stress_test_report(payload)


@router.get("/api/filters/regime-templates", response_model=list[RegimeFilterTemplate])
def filter_regime_templates() -> list[RegimeFilterTemplate]:
    return regime_filter_templates()


@router.get("/api/regime-playbook", response_model=RegimePlaybook)
def regime_playbook(symbol: str | None = None) -> RegimePlaybook:
    return regime_playbook_report(symbol=symbol)


@router.get("/api/strategy-templates", response_model=list[StrategyTemplate])
def strategy_templates(enabled_only: bool = False) -> list[StrategyTemplate]:
    return list_strategy_templates(enabled_only=enabled_only)


@router.post("/api/strategy-templates", response_model=StrategyTemplate)
def add_strategy_template(payload: StrategyTemplateCreate) -> StrategyTemplate:
    return create_strategy_template(payload)


@router.patch("/api/strategy-templates/{strategy_id}", response_model=StrategyTemplate)
def patch_strategy_template(strategy_id: str, payload: StrategyTemplateUpdate) -> StrategyTemplate:
    return update_strategy_template(strategy_id, payload)


@router.delete("/api/strategy-templates/{strategy_id}", deprecated=True)
def remove_strategy_template(strategy_id: str) -> dict[str, bool]:
    return delete_strategy_template(strategy_id)


@router.post("/api/strategy-templates/{strategy_id}/validate", response_model=StrategyValidationResult)
def validate_strategy_template(strategy_id: str, payload: StrategyValidationRequest) -> StrategyValidationResult:
    return run_strategy_validation(strategy_id, payload)


@router.get("/api/strategy-promotion-gates", response_model=list[StrategyPromotionGate])
def strategy_promotion_gate_list() -> list[StrategyPromotionGate]:
    return strategy_promotion_gates()


@router.get("/api/strategy-promotion-gates/{strategy_id}", response_model=StrategyPromotionGate)
def strategy_promotion_gate(strategy_id: str) -> StrategyPromotionGate:
    return strategy_promotion_gate_report(strategy_id)


@router.get("/api/strategy-validations", response_model=list[StrategyValidationResult])
def strategy_validations(strategy_id: str | None = None, limit: int = 10) -> list[StrategyValidationResult]:
    return list_strategy_validations(strategy_id=strategy_id, limit=limit)


@router.get("/api/strategy-validation-decay", response_model=StrategyValidationDecayReport)
def strategy_validation_decay(limit: int = 12) -> StrategyValidationDecayReport:
    return strategy_validation_decay_report(limit=limit)


@router.get("/api/strategy-risk-controls", response_model=StrategyRiskControlReport)
def strategy_risk_controls() -> StrategyRiskControlReport:
    return strategy_risk_control_report()


@router.get("/api/market-state-controls", response_model=MarketStateControlReport)
def market_state_controls() -> MarketStateControlReport:
    return market_state_control_report()


@router.get("/api/strategy-operating-plan", response_model=StrategyOperatingPlanReport)
def strategy_operating_plan() -> StrategyOperatingPlanReport:
    return strategy_operating_plan_report()


@router.get("/api/strategy-evidence-packs", response_model=StrategyEvidencePackReport)
def strategy_evidence_packs() -> StrategyEvidencePackReport:
    return strategy_evidence_pack_report()


@router.post("/api/factor-definitions", response_model=FactorDefinition)
def add_factor_definition(payload: FactorDefinitionCreate) -> FactorDefinition:
    return create_factor_definition(payload)


@router.patch("/api/factor-definitions/{factor_id}", response_model=FactorDefinition)
def patch_factor_definition(factor_id: str, payload: FactorDefinitionUpdate) -> FactorDefinition:
    return update_factor_definition(factor_id, payload)


@router.post("/api/backtest-runs", response_model=BacktestRunResult)
def create_backtest_run(payload: BacktestRunRequest) -> BacktestRunResult:
    result = run_backtest(payload)
    enqueue_agent_event("backtest_completed", symbol=payload.symbol, context={"backtest_id": result.id, "factor_id": payload.factor_id})
    return result


@router.get("/api/backtest-benchmark/{symbol}", response_model=BacktestBenchmarkReport)
def backtest_benchmark(
    symbol: str,
    factor_id: str = "momentum-news",
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    limit: int = 180,
) -> BacktestBenchmarkReport:
    return backtest_benchmark_report(symbol=symbol, factor_id=factor_id, interval=interval, limit=limit)


@router.get("/api/backtest-forward-consistency/{symbol}", response_model=BacktestForwardConsistencyReport)
def backtest_forward_consistency(symbol: str, factor_id: str = "momentum-news") -> BacktestForwardConsistencyReport:
    return backtest_forward_consistency_report(symbol=symbol, factor_id=factor_id)


@router.post("/api/forward-tests", response_model=ForwardTestResult)
def create_forward_test(payload: ForwardTestRequest) -> ForwardTestResult:
    return run_forward_test(payload)


@router.get("/api/forward-tests/{symbol}", response_model=list[ForwardTestResult])
def forward_tests(symbol: str, limit: int = 10) -> list[ForwardTestResult]:
    return list_forward_tests(symbol, limit=limit)


@router.get("/api/forward-leaderboard", response_model=ForwardLeaderboard)
def forward_leaderboard(limit: int = 10, symbol: str | None = None, factor_id: str | None = None) -> ForwardLeaderboard:
    return forward_leaderboard_report(limit=limit, symbol=symbol, factor_id=factor_id)


@router.get("/api/strategy-backtest-monitor", response_model=StrategyBacktestMonitorReport)
def strategy_backtest_monitor(limit: int = 12) -> StrategyBacktestMonitorReport:
    return strategy_backtest_monitor_report(limit=limit)


@router.get("/api/strategy-forward-monitor", response_model=StrategyForwardMonitorReport)
def strategy_forward_monitor(limit: int = 12) -> StrategyForwardMonitorReport:
    return strategy_forward_monitor_report(limit=limit)


@router.get("/api/strategy-sensitivity", response_model=StrategySensitivityReport)
def strategy_sensitivity(limit: int = 12) -> StrategySensitivityReport:
    return strategy_sensitivity_report(limit=limit)


@router.get("/api/strategy-revalidation-plan", response_model=StrategyRevalidationPlanReport)
def strategy_revalidation_plan(limit: int = 12) -> StrategyRevalidationPlanReport:
    return strategy_revalidation_plan_report(limit=limit)


@router.get("/api/strategy-validation-coverage", response_model=StrategyValidationCoverageReport)
def strategy_validation_coverage(limit: int = 12) -> StrategyValidationCoverageReport:
    return strategy_validation_coverage_report(limit=limit)


@router.post("/api/walk-forward-tests", response_model=WalkForwardResult)
def create_walk_forward_test(payload: WalkForwardRequest) -> WalkForwardResult:
    return run_walk_forward_test(payload)


@router.get("/api/walk-forward-tests/{symbol}", response_model=list[WalkForwardResult])
def walk_forward_tests(symbol: str, limit: int = 10) -> list[WalkForwardResult]:
    return list_walk_forward_tests(symbol, limit=limit)


@router.get("/api/walk-forward-leaderboard", response_model=WalkForwardLeaderboard)
def walk_forward_leaderboard(limit: int = 10, symbol: str | None = None, factor_id: str | None = None) -> WalkForwardLeaderboard:
    return walk_forward_leaderboard_report(limit=limit, symbol=symbol, factor_id=factor_id)


@router.post("/api/stress-tests", response_model=StressTestResult)
def create_stress_test(payload: StressTestRequest) -> StressTestResult:
    return run_stress_test(payload)


@router.get("/api/stress-tests/{symbol}", response_model=list[StressTestResult])
def stress_tests(symbol: str, limit: int = 10) -> list[StressTestResult]:
    return list_stress_tests(symbol, limit=limit)


@router.get("/api/stress-leaderboard", response_model=StressLeaderboard)
def stress_leaderboard(limit: int = 10, symbol: str | None = None, factor_id: str | None = None) -> StressLeaderboard:
    return stress_leaderboard_report(limit=limit, symbol=symbol, factor_id=factor_id)


@router.get("/api/robustness/{symbol}", response_model=RobustnessReport)
def robustness(symbol: str) -> RobustnessReport:
    return robustness_report(symbol)


@router.get("/api/drift/{symbol}", response_model=StrategyDriftReport)
def strategy_drift(symbol: str) -> StrategyDriftReport:
    return strategy_drift_report(symbol)


@router.get("/api/allocation/{symbol}", response_model=StrategyAllocationReport)
def strategy_allocation(symbol: str) -> StrategyAllocationReport:
    return strategy_allocation_report(symbol)


@router.get("/api/trade-readiness/{symbol}", response_model=TradeReadinessReport)
def trade_readiness(
    symbol: str,
    factor_id: str = "momentum-news",
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    min_rsi: float | None = Query(default=None, ge=0, le=100),
    min_trend: int | None = Query(default=None, ge=0, le=100),
    signal: str | None = None,
) -> TradeReadinessReport:
    return trade_readiness_report(
        symbol=symbol,
        factor_id=factor_id,
        interval=interval,
        min_rsi=min_rsi,
        min_trend=min_trend,
        signal=signal,
    )


@router.get("/api/regime-risk-gate/{symbol}", response_model=RegimeRiskGate)
def regime_risk_gate(
    symbol: str,
    factor_id: str = "momentum-news",
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
) -> RegimeRiskGate:
    return regime_risk_gate_report(symbol=symbol, factor_id=factor_id, interval=interval)


@router.post("/api/research-pipelines", response_model=ResearchPipelineResult)
def create_research_pipeline(payload: ResearchPipelineRequest) -> ResearchPipelineResult:
    return run_research_pipeline(payload)


@router.post("/api/factor-admission", response_model=FactorAdmissionReport)
def create_factor_admission(payload: FactorAdmissionRequest) -> FactorAdmissionReport:
    return factor_admission_report(payload)


@router.get("/api/factor-admission-queue", response_model=FactorAdmissionQueueReport)
def factor_admission_queue(
    limit: int = 6,
    factor_id: str | None = None,
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
) -> FactorAdmissionQueueReport:
    return factor_admission_queue_report(limit=limit, factor_id=factor_id, interval=interval)


@router.get("/api/revalidation-queue", response_model=RevalidationQueueReport)
def revalidation_queue(limit: int = 8, stale_days: int = 7, factor_id: str | None = None) -> RevalidationQueueReport:
    return revalidation_queue_report(limit=limit, stale_days=stale_days, factor_id=factor_id)


@router.post("/api/sensitivity-tests", response_model=SensitivityTestResult)
def create_sensitivity_test(payload: SensitivityTestRequest) -> SensitivityTestResult:
    return run_sensitivity_test(payload)


@router.post("/api/parameter-optimization", response_model=ParameterOptimizationReport)
def create_parameter_optimization(payload: ParameterOptimizationRequest) -> ParameterOptimizationReport:
    return parameter_optimization_report(payload)


@router.get("/api/factor-leaderboard/{symbol}", response_model=FactorLeaderboard)
def factor_leaderboard(symbol: str) -> FactorLeaderboard:
    return factor_leaderboard_report(symbol)


@router.get("/api/factor-correlation/{symbol}", response_model=FactorCorrelationReport)
def factor_correlation(symbol: str, interval: Literal["15m", "1h", "4h", "1d"] = "1h", limit: int = 180) -> FactorCorrelationReport:
    return factor_correlation_report(symbol, interval=interval, limit=limit)


@router.get("/api/regime-exposure", response_model=RegimeExposureReport)
def regime_exposure(interval: Literal["15m", "1h", "4h", "1d"] = "1h") -> RegimeExposureReport:
    return regime_exposure_report(interval=interval)


@router.get("/api/regime-attribution/{symbol}", response_model=RegimeAttributionReport)
def regime_attribution(
    symbol: str,
    factor_id: str = "momentum-news",
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    limit: int = 220,
) -> RegimeAttributionReport:
    return regime_attribution_report(symbol, factor_id=factor_id, interval=interval, limit=limit)


@router.get("/api/regime-factor-fit/{symbol}", response_model=RegimeFactorFitReport)
def regime_factor_fit(
    symbol: str,
    interval: Literal["15m", "1h", "4h", "1d"] = "1h",
    limit: int = 6,
) -> RegimeFactorFitReport:
    return regime_factor_fit_report(symbol, interval=interval, limit=limit)


@router.get("/api/strategy-leaderboard", response_model=StrategyLeaderboard)
def strategy_leaderboard() -> StrategyLeaderboard:
    return strategy_leaderboard_report()


@router.get("/api/strategy-regime-fit", response_model=StrategyRegimeFitReport)
def strategy_regime_fit(limit: int = 8) -> StrategyRegimeFitReport:
    return strategy_regime_fit_report(limit=limit)


@router.get("/api/strategy-regime-matrix", response_model=StrategyRegimeMatrixReport)
def strategy_regime_matrix() -> StrategyRegimeMatrixReport:
    return strategy_regime_matrix_report()


@router.get("/api/strategy-crowding", response_model=StrategyCrowdingReport)
def strategy_crowding() -> StrategyCrowdingReport:
    return strategy_crowding_report()


@router.get("/api/strategy-lifecycle", response_model=StrategyLifecycleReport)
def strategy_lifecycle() -> StrategyLifecycleReport:
    return strategy_lifecycle_report()


@router.get("/api/backtest-runs/{symbol}", response_model=list[BacktestRunResult])
def list_backtest_runs(symbol: str, limit: int = 10) -> list[BacktestRunResult]:
    return list_backtest_runs_service(symbol, limit=limit)


@router.get("/api/backtests/{symbol}", response_model=list[BacktestSummary])
def backtest_summaries(symbol: str) -> list[BacktestSummary]:
    return backtest_summaries_service(symbol)
