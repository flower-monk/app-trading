from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field, HttpUrl

class FactorIdea(BaseModel):
    id: str
    name: str
    category: str
    description: str
    signal: Literal["long", "short", "neutral"]
    score: int
    horizon: str


class FactorDefinition(BaseModel):
    id: str
    name: str
    category: str
    description: str
    signal: Literal["long", "short", "neutral"]
    horizon: str
    enabled: bool
    created_at: str


class FactorDefinitionCreate(BaseModel):
    name: str
    category: str
    description: str
    signal: Literal["long", "short", "neutral"] = "long"
    horizon: str = "3-10 days"
    enabled: bool = True


class FactorDefinitionUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    signal: Literal["long", "short", "neutral"] | None = None
    horizon: str | None = None
    enabled: bool | None = None


class BacktestSummary(BaseModel):
    id: str
    name: str
    symbol: str
    factor: str
    total_return: float
    max_drawdown: float
    sharpe: float
    win_rate: float
    trades: int
    status: Literal["completed", "running"]
    equity_curve: list[float]


class BacktestRunRequest(BaseModel):
    symbol: str
    factor_id: str = "momentum-news"
    interval: Literal["15m", "1h", "4h", "1d"] = "1h"
    limit: int = 180
    initial_capital: float = 10000
    fee_bps: float = 2.0


class BacktestRunResult(BaseModel):
    id: str
    symbol: str
    factor_id: str
    factor_name: str
    interval: str
    initial_capital: float
    final_equity: float
    total_return: float
    max_drawdown: float
    sharpe: float
    win_rate: float
    trades: int
    exposure: float
    created_at: str
    equity_curve: list[float]
    signal_points: list[dict[str, str | float]]


class BacktestBenchmarkReport(BaseModel):
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    interval: str
    strategy_return: float
    benchmark_return: float
    alpha: float
    strategy_drawdown: float
    benchmark_drawdown: float
    drawdown_delta: float
    strategy_sharpe: float
    benchmark_sharpe: float
    sharpe_delta: float
    trades: int
    exposure: float
    summary: str
    checks: list[str]
    actions: list[str]


class StrategyTemplate(BaseModel):
    id: str
    name: str
    category: str
    description: str
    factor_ids: list[str]
    default_filters: dict[str, str | float]
    risk_controls: list[str]
    validation_rules: list[str]
    enabled: bool
    created_at: str


class StrategyTemplateCreate(BaseModel):
    name: str
    category: str
    description: str
    factor_ids: list[str] = []
    default_filters: dict[str, str | float] = {}
    risk_controls: list[str] = []
    validation_rules: list[str] = []
    enabled: bool = True


class StrategyTemplateUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    factor_ids: list[str] | None = None
    default_filters: dict[str, str | float] | None = None
    risk_controls: list[str] | None = None
    validation_rules: list[str] | None = None
    enabled: bool | None = None


class DataCatalogItem(BaseModel):
    id: str
    name: str
    type: Literal["market", "news", "factor", "strategy", "portfolio"]
    provider: str
    coverage: list[str]
    update_mode: str
    status: Literal["ready", "degraded", "offline"]
    rows: int
    fields: list[str]
    notes: str


class MarketRegime(BaseModel):
    generated_at: str
    label: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    risk_level: Literal["constructive", "neutral", "watch", "risk"]
    breadth_score: float
    volatility_score: float
    trend_score: float
    liquidity_score: float
    summary: str
    evidence: list[str]
    actions: list[str]


class MarketRegimeTransition(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    transition_score: int
    current: MarketRegime
    previous: MarketRegime | None = None
    label_changed: bool
    risk_level_changed: bool
    breadth_delta: float
    volatility_delta: float
    trend_delta: float
    liquidity_delta: float
    summary: str
    evidence: list[str]
    actions: list[str]


class FilterValidationRequest(BaseModel):
    min_change: float | None = None
    max_change: float | None = None
    min_rsi: float | None = None
    max_rsi: float | None = None
    min_trend: int | None = None
    signal: str | None = None
    market: str | None = None


class FilterValidationResult(BaseModel):
    valid: bool
    normalized_filters: dict[str, str | float]
    match_count: int
    warnings: list[str]
    suggestions: list[str]
    regime: MarketRegime


class FilterContributionItem(BaseModel):
    filter_name: str
    label: str
    current_value: str | float
    baseline_match_count: int
    relaxed_match_count: int
    removed_by_filter: int
    severity: Literal["pass", "watch", "fail"]
    insight: str
    action: str


class FilterContributionReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    baseline_match_count: int
    total_universe: int
    concentration_score: int
    summary: str
    filters: dict[str, str | float]
    items: list[FilterContributionItem]
    actions: list[str]


class FilterStressScenario(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    match_count: int
    filters: FilterValidationRequest
    warnings: list[str]
    suggestions: list[str]


class FilterStressTestReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    stability_score: int
    baseline_match_count: int
    min_match_count: int
    max_match_count: int
    summary: str
    scenarios: list[FilterStressScenario]
    actions: list[str]


class FilterAdmissionCheck(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    message: str


class FilterAdmissionReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    decision: Literal["accept", "revise", "reject"]
    score: int
    summary: str
    filters: dict[str, str | float]
    validation: FilterValidationResult
    contribution: FilterContributionReport
    stress_test: FilterStressTestReport
    checks: list[FilterAdmissionCheck]
    actions: list[str]


class RegimeFilterTemplate(BaseModel):
    id: str
    name: str
    regime_label: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    risk_level: Literal["constructive", "neutral", "watch", "risk"]
    filters: FilterValidationRequest
    match_count: int
    valid: bool
    rationale: list[str]
    warnings: list[str]
    suggestions: list[str]


class ForwardTestRequest(BaseModel):
    symbol: str
    factor_id: str = "momentum-news"
    interval: Literal["15m", "1h", "4h", "1d"] = "1h"
    train_bars: int = 140
    forward_bars: int = 40
    initial_capital: float = 10000
    fee_bps: float = 2.0


class ForwardTestResult(BaseModel):
    id: str
    symbol: str
    factor_id: str
    factor_name: str
    interval: str
    train_bars: int
    forward_bars: int
    train_return: float
    forward_return: float
    degradation: float
    max_drawdown: float
    pass_fail: Literal["pass", "watch", "fail"]
    generated_at: str
    equity_curve: list[float]
    checks: list[str]


class ForwardLeaderboardItem(BaseModel):
    rank: int
    symbol: str
    factor_id: str
    factor_name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    train_return: float
    forward_return: float
    degradation: float
    max_drawdown: float
    generated_at: str
    recommendation: str
    actions: list[str]


class ForwardLeaderboard(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[ForwardLeaderboardItem]


class StrategyBacktestMonitorItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    score: int
    factor_count: int
    tested_factor_count: int
    symbol_count: int
    run_count: int
    positive_count: int
    average_return: float | None = None
    average_sharpe: float | None = None
    average_win_rate: float | None = None
    max_drawdown: float | None = None
    latest_run_at: str | None = None
    freshness_days: int | None = None
    actions: list[str]


class StrategyBacktestMonitorReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[StrategyBacktestMonitorItem]
    actions: list[str]


class StrategyForwardMonitorItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    score: int
    factor_count: int
    tested_factor_count: int
    symbol_count: int
    pass_count: int
    watch_count: int
    fail_count: int
    average_forward_return: float | None = None
    average_degradation: float | None = None
    max_drawdown: float | None = None
    latest_run_at: str | None = None
    freshness_days: int | None = None
    actions: list[str]


class StrategyForwardMonitorReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[StrategyForwardMonitorItem]
    actions: list[str]


class StrategySensitivityItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    score: int
    factor_count: int
    tested_factor_count: int
    symbol_count: int
    interval_count: int
    backtest_count: int
    forward_count: int
    positive_backtest_rate: float
    forward_pass_rate: float
    return_dispersion: float | None = None
    degradation_dispersion: float | None = None
    worst_drawdown: float | None = None
    window_variants: int
    evidence: list[str]
    actions: list[str]


class StrategySensitivityReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    items: list[StrategySensitivityItem]
    actions: list[str]


class StrategyRevalidationPlanItem(BaseModel):
    rank: int
    strategy_id: str
    strategy_name: str
    category: str
    status: Literal["pass", "watch", "fail"]
    priority: int
    current_regime: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    regime_decision: Literal["open", "reduced", "paused", "blocked", "missing"]
    sensitivity_status: Literal["pass", "watch", "fail", "missing"]
    evidence_status: Literal["pass", "watch", "fail", "missing"]
    lifecycle_stage: Literal["draft", "validation", "candidate", "active", "paused", "missing"]
    due_tasks: list[str]
    reasons: list[str]
    actions: list[str]


class StrategyRevalidationPlanReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    current_regime: MarketRegime
    summary: str
    items: list[StrategyRevalidationPlanItem]
    actions: list[str]


class StrategyValidationCoverageItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    score: int
    factor_count: int
    symbol_count: int
    backtest_factor_count: int
    forward_factor_count: int
    walk_forward_factor_count: int
    stress_factor_count: int
    fully_validated_factor_count: int
    backtest_run_count: int
    forward_run_count: int
    walk_forward_run_count: int
    stress_run_count: int
    missing_tasks: list[str]
    evidence: list[str]
    actions: list[str]


class StrategyValidationCoverageReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    items: list[StrategyValidationCoverageItem]
    actions: list[str]


class WalkForwardRequest(BaseModel):
    symbol: str
    factor_id: str = "momentum-news"
    interval: Literal["15m", "1h", "4h", "1d"] = "1h"
    train_bars: int = 90
    forward_bars: int = 30
    windows: int = 4
    initial_capital: float = 10000
    fee_bps: float = 2.0


class WalkForwardWindowResult(BaseModel):
    window: int
    train_start: str
    train_end: str
    forward_start: str
    forward_end: str
    train_return: float
    forward_return: float
    degradation: float
    max_drawdown: float
    trades: int
    pass_fail: Literal["pass", "watch", "fail"]


class WalkForwardResult(BaseModel):
    id: str
    symbol: str
    factor_id: str
    factor_name: str
    interval: str
    train_bars: int
    forward_bars: int
    window_count: int
    pass_rate: float
    average_forward_return: float
    worst_forward_return: float
    max_drawdown: float
    stability_score: int
    pass_fail: Literal["pass", "watch", "fail"]
    generated_at: str
    windows: list[WalkForwardWindowResult]
    checks: list[str]
    actions: list[str]


class WalkForwardLeaderboardItem(BaseModel):
    rank: int
    symbol: str
    factor_id: str
    factor_name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    pass_rate: float
    average_forward_return: float
    worst_forward_return: float
    max_drawdown: float
    stability_score: int
    window_count: int
    generated_at: str
    recommendation: str
    actions: list[str]


class WalkForwardLeaderboard(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[WalkForwardLeaderboardItem]


class StressTestRequest(BaseModel):
    symbol: str
    factor_id: str = "momentum-news"
    interval: Literal["15m", "1h", "4h", "1d"] = "1h"
    initial_capital: float = 10000


class StressScenarioResult(BaseModel):
    name: str
    shock: str
    stressed_return: float
    stressed_drawdown: float
    pass_fail: Literal["pass", "watch", "fail"]
    notes: list[str]


class StressTestResult(BaseModel):
    id: str
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    base_return: float
    base_drawdown: float
    worst_return: float
    worst_drawdown: float
    pass_fail: Literal["pass", "watch", "fail"]
    scenarios: list[StressScenarioResult]
    actions: list[str]


class StressLeaderboardItem(BaseModel):
    rank: int
    symbol: str
    factor_id: str
    factor_name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    base_return: float
    worst_return: float
    worst_drawdown: float
    scenario_count: int
    pass_scenarios: int
    generated_at: str
    recommendation: str
    actions: list[str]


class StressLeaderboard(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[StressLeaderboardItem]


class ConsistencyCheck(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail", "missing"]
    message: str


class BacktestForwardConsistencyReport(BaseModel):
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    recommendation: str
    backtest_return: float | None = None
    forward_return: float | None = None
    walk_forward_pass_rate: float | None = None
    stress_worst_return: float | None = None
    degradation: float | None = None
    summary: str
    checks: list[ConsistencyCheck]
    actions: list[str]


class RobustnessFactorReport(BaseModel):
    factor_id: str
    factor_name: str
    signal: Literal["long", "short", "neutral"]
    status: Literal["pass", "watch", "fail"]
    score: int
    backtest_status: Literal["pass", "watch", "fail", "missing"]
    forward_status: Literal["pass", "watch", "fail", "missing"]
    walk_forward_status: Literal["pass", "watch", "fail", "missing"]
    stress_status: Literal["pass", "watch", "fail", "missing"]
    regime_status: Literal["pass", "watch", "fail"]
    latest_backtest_return: float | None = None
    latest_backtest_drawdown: float | None = None
    latest_forward_return: float | None = None
    latest_walk_forward_pass_rate: float | None = None
    latest_stress_worst_return: float | None = None
    notes: list[str]
    actions: list[str]


class RobustnessReport(BaseModel):
    symbol: str
    generated_at: str
    regime: MarketRegime
    status: Literal["pass", "watch", "fail"]
    summary: str
    factors: list[RobustnessFactorReport]


class StrategyDriftFactor(BaseModel):
    factor_id: str
    factor_name: str
    status: Literal["pass", "watch", "fail"]
    drift_score: int
    baseline_return: float | None = None
    forward_return: float | None = None
    return_degradation: float | None = None
    walk_forward_pass_rate: float | None = None
    stress_worst_return: float | None = None
    regime_alignment: Literal["pass", "watch", "fail"]
    evidence: list[str]
    actions: list[str]


class StrategyDriftReport(BaseModel):
    symbol: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    regime: MarketRegime
    factors: list[StrategyDriftFactor]


class StrategyAllocationItem(BaseModel):
    factor_id: str
    factor_name: str
    status: Literal["increase", "hold", "reduce", "avoid"]
    target_weight: float
    max_weight: float
    confidence: int
    rationale: list[str]
    controls: list[str]


class StrategyAllocationReport(BaseModel):
    symbol: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    total_target_weight: float
    risk_budget: float
    summary: str
    regime: MarketRegime
    items: list[StrategyAllocationItem]
    portfolio_notes: list[str]
    actions: list[str]


class TradeReadinessCheck(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    detail: str
    action: str


class TradeReadinessReport(BaseModel):
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    decision: Literal["go", "size_down", "hold", "block"]
    score: int
    recommended_weight: float
    max_weight: float
    summary: str
    checks: list[TradeReadinessCheck]
    blockers: list[str]
    warnings: list[str]
    actions: list[str]


class RegimeRiskGateCheck(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    detail: str
    action: str


class RegimeRiskGate(BaseModel):
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    decision: Literal["open", "reduced", "paused", "blocked"]
    score: int
    allowed_weight: float
    regime: MarketRegime
    transition: MarketRegimeTransition
    summary: str
    checks: list[RegimeRiskGateCheck]
    blockers: list[str]
    actions: list[str]


class RegimePlaybookStep(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    detail: str
    actions: list[str]


class RegimePlaybook(BaseModel):
    generated_at: str
    regime: MarketRegime
    status: Literal["pass", "watch", "fail"]
    summary: str
    preferred_factors: list[str]
    filter_templates: list[RegimeFilterTemplate]
    steps: list[RegimePlaybookStep]
    risk_controls: list[str]
    next_actions: list[str]


class ResearchPipelineRequest(BaseModel):
    symbol: str
    factor_id: str = "momentum-news"
    interval: Literal["15m", "1h", "4h", "1d"] = "1h"
    initial_capital: float = 10000
    filters: FilterValidationRequest = Field(default_factory=FilterValidationRequest)


class ResearchPipelineResult(BaseModel):
    id: str
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    pass_fail: Literal["pass", "watch", "fail"]
    summary: str
    filter_validation: FilterValidationResult
    backtest: BacktestRunResult
    forward_test: ForwardTestResult
    stress_test: StressTestResult
    robustness: RobustnessFactorReport
    actions: list[str]


class FactorAdmissionRequest(BaseModel):
    symbol: str
    factor_id: str = "momentum-news"
    interval: Literal["15m", "1h", "4h", "1d"] = "1h"
    filters: FilterValidationRequest = Field(default_factory=FilterValidationRequest)


class FactorAdmissionCheck(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    detail: str
    action: str


class FactorAdmissionReport(BaseModel):
    id: str
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    checks: list[FactorAdmissionCheck]
    blockers: list[str]
    actions: list[str]


class FactorAdmissionQueueItem(BaseModel):
    rank: int
    symbol: str
    name: str
    factor_id: str
    factor_name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    target_weight: float
    recommendation: str
    blockers: list[str]
    actions: list[str]


class FactorAdmissionQueueReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[FactorAdmissionQueueItem]


class SensitivityScenarioResult(BaseModel):
    name: str
    interval: Literal["15m", "1h", "4h", "1d"]
    fee_bps: float
    total_return: float
    max_drawdown: float
    sharpe: float
    trades: int
    pass_fail: Literal["pass", "watch", "fail"]
    notes: list[str]


class SensitivityTestRequest(BaseModel):
    symbol: str
    factor_id: str = "momentum-news"
    initial_capital: float = 10000


class SensitivityTestResult(BaseModel):
    id: str
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    pass_fail: Literal["pass", "watch", "fail"]
    return_spread: float
    drawdown_spread: float
    scenarios: list[SensitivityScenarioResult]
    actions: list[str]


class FactorLeaderboardItem(BaseModel):
    rank: int
    factor_id: str
    factor_name: str
    status: Literal["pass", "watch", "fail"]
    composite_score: int
    robustness_score: int
    backtest_return: float | None = None
    backtest_drawdown: float | None = None
    forward_return: float | None = None
    stress_worst_return: float | None = None
    recommendation: str
    reasons: list[str]


class FactorLeaderboard(BaseModel):
    symbol: str
    generated_at: str
    summary: str
    items: list[FactorLeaderboardItem]


class FactorExposureItem(BaseModel):
    factor_id: str
    factor_name: str
    exposure_rate: float
    active_signals: int
    signal_changes: int


class FactorCorrelationPair(BaseModel):
    factor_a_id: str
    factor_a_name: str
    factor_b_id: str
    factor_b_name: str
    correlation: float
    overlap_rate: float
    status: Literal["pass", "watch", "fail"]


class FactorCorrelationReport(BaseModel):
    symbol: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    crowding_score: int
    max_correlation: float
    summary: str
    regime: MarketRegime
    exposures: list[FactorExposureItem]
    pairs: list[FactorCorrelationPair]
    actions: list[str]


class RegimeExposureItem(BaseModel):
    symbol: str
    allocation_status: Literal["pass", "watch", "fail"]
    correlation_status: Literal["pass", "watch", "fail"]
    risk_budget: float
    target_weight: float
    crowding_score: int
    dominant_factor: str | None = None
    status: Literal["pass", "watch", "fail"]
    notes: list[str]


class RegimeExposureReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    regime: MarketRegime
    total_target_weight: float
    average_risk_budget: float
    average_crowding_score: int
    summary: str
    items: list[RegimeExposureItem]
    actions: list[str]


class RegimeAttributionSlice(BaseModel):
    regime_label: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    bars: int
    total_return: float
    max_drawdown: float
    trades: int
    win_rate: float
    exposure_rate: float
    status: Literal["pass", "watch", "fail"]
    notes: list[str]


class RegimeAttributionReport(BaseModel):
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    interval: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    best_regime: str | None = None
    worst_regime: str | None = None
    slices: list[RegimeAttributionSlice]
    actions: list[str]


class RegimeFactorFitItem(BaseModel):
    rank: int
    factor_id: str
    factor_name: str
    current_regime: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    status: Literal["pass", "watch", "fail"]
    score: int
    regime_fit: Literal["aligned", "neutral", "misaligned"]
    leaderboard_score: int
    robustness_score: int
    best_regime: str | None = None
    worst_regime: str | None = None
    recommendation: str
    reasons: list[str]
    actions: list[str]


class RegimeFactorFitReport(BaseModel):
    symbol: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    regime: MarketRegime
    summary: str
    items: list[RegimeFactorFitItem]


class ParameterOptimizationRequest(BaseModel):
    symbol: str
    factor_id: str = "momentum-news"
    interval: Literal["15m", "1h", "4h", "1d"] = "1h"
    limit: int = 220
    initial_capital: float = 10000
    fee_bps: float = 2.0


class ParameterOptimizationResult(BaseModel):
    rank: int
    min_rsi: float
    min_trend: int
    total_return: float
    max_drawdown: float
    trades: int
    exposure_rate: float
    score: int
    status: Literal["pass", "watch", "fail"]


class ParameterOptimizationReport(BaseModel):
    id: str
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    interval: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    best: ParameterOptimizationResult | None = None
    results: list[ParameterOptimizationResult]
    actions: list[str]


class StrategyLeaderboardItem(BaseModel):
    rank: int
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    score: int
    latest_validation_status: Literal["pass", "watch", "fail", "missing"]
    average_backtest_return: float | None = None
    average_forward_return: float | None = None
    factor_count: int
    filter_count: int
    recommendation: str
    reasons: list[str]


class StrategyLeaderboard(BaseModel):
    generated_at: str
    summary: str
    items: list[StrategyLeaderboardItem]


class StrategyRegimeFitFactor(BaseModel):
    factor_id: str
    factor_name: str
    score: int
    status: Literal["pass", "watch", "fail"]
    regime_fit: Literal["aligned", "neutral", "misaligned"]


class StrategyRegimeFitItem(BaseModel):
    rank: int
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    score: int
    current_regime: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    filter_status: Literal["pass", "watch", "fail"]
    latest_validation_status: Literal["pass", "watch", "fail", "missing"]
    recommendation: str
    factor_fits: list[StrategyRegimeFitFactor]
    reasons: list[str]
    actions: list[str]


class StrategyRegimeFitReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    regime: MarketRegime
    summary: str
    items: list[StrategyRegimeFitItem]


class StrategyRegimeMatrixCell(BaseModel):
    regime_label: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    status: Literal["pass", "watch", "fail"]
    decision: Literal["open", "reduced", "paused", "blocked"]
    score: int
    rationale: list[str]


class StrategyRegimeMatrixItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    latest_validation_status: Literal["pass", "watch", "fail", "missing"]
    overall_status: Literal["pass", "watch", "fail"]
    cells: list[StrategyRegimeMatrixCell]
    actions: list[str]


class StrategyRegimeMatrixReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    current_regime: MarketRegime
    summary: str
    items: list[StrategyRegimeMatrixItem]
    actions: list[str]


class StrategyCrowdingPair(BaseModel):
    strategy_a_id: str
    strategy_a_name: str
    strategy_b_id: str
    strategy_b_name: str
    status: Literal["pass", "watch", "fail"]
    overlap_score: int
    shared_factors: list[str]
    filter_overlap: float
    current_decisions: list[Literal["open", "reduced", "paused", "blocked"]]
    detail: str
    action: str


class StrategyCrowdingReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    current_regime: MarketRegime
    summary: str
    pairs: list[StrategyCrowdingPair]
    actions: list[str]


class RegimeScenarioRiskItem(BaseModel):
    regime_label: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    status: Literal["pass", "watch", "fail"]
    score: int
    open_count: int
    reduced_count: int
    paused_count: int
    blocked_count: int
    average_strategy_score: float
    risk_budget: float
    summary: str
    actions: list[str]


class RegimeScenarioRiskReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    current_regime: MarketRegime
    summary: str
    items: list[RegimeScenarioRiskItem]
    actions: list[str]


class StrategyLifecycleItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    stage: Literal["draft", "validation", "candidate", "active", "paused"]
    status: Literal["pass", "watch", "fail"]
    score: int
    leaderboard_score: int | None = None
    promotion_score: int | None = None
    regime_score: int | None = None
    latest_validation_status: Literal["pass", "watch", "fail", "missing"]
    blockers: list[str]
    actions: list[str]


class StrategyLifecycleReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[StrategyLifecycleItem]
    actions: list[str]


class StrategyPromotionGate(BaseModel):
    strategy_id: str
    strategy_name: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    recommendation: str
    checks: list[ReadinessCheck]
    blockers: list[str]
    actions: list[str]


class StrategyValidationRequest(BaseModel):
    symbol_limit: int = 3
    interval: Literal["15m", "1h", "4h", "1d"] = "1h"
    initial_capital: float = 10000


class StrategyValidationResult(BaseModel):
    id: str
    strategy_id: str
    strategy_name: str
    generated_at: str
    regime: MarketRegime
    filter_validation: FilterValidationResult
    symbols: list[str]
    backtest_ids: list[str]
    forward_test_ids: list[str]
    average_backtest_return: float
    average_forward_return: float
    pass_fail: Literal["pass", "watch", "fail"]
    checks: list[str]


class StrategyValidationDecayItem(BaseModel):
    strategy_id: str
    strategy_name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    validation_id: str
    generated_at: str
    backtest_return: float
    forward_return: float
    decay: float
    decay_ratio: float | None = None
    symbol_count: int
    backtest_count: int
    forward_count: int
    regime_label: Literal["risk_on", "risk_off", "trend", "range", "volatile"]
    risk_level: Literal["constructive", "neutral", "watch", "risk"]
    action: str


class StrategyValidationDecayReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    items: list[StrategyValidationDecayItem]
    actions: list[str]


class StrategyRiskControlItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    score: int
    risk_control_count: int
    validation_rule_count: int
    gate_status: Literal["pass", "watch", "fail", "missing"]
    lifecycle_stage: Literal["draft", "validation", "candidate", "active", "paused", "missing"]
    crowding_status: Literal["pass", "watch", "fail", "missing"]
    decay_status: Literal["pass", "watch", "fail", "missing"]
    blockers: list[str]
    actions: list[str]


class StrategyRiskControlReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    items: list[StrategyRiskControlItem]
    actions: list[str]


class MarketStateControlItem(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    detail: str
    action: str


class MarketStateControlReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    decision: Literal["expand", "observe", "reduce", "halt"]
    current_regime: MarketRegime
    summary: str
    items: list[MarketStateControlItem]
    actions: list[str]


class StrategyOperatingPlanItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    decision: Literal["open", "observe", "reduce", "block"]
    recommended_weight: float
    score: int
    gate_status: Literal["pass", "watch", "fail", "missing"]
    lifecycle_stage: Literal["draft", "validation", "candidate", "active", "paused", "missing"]
    regime_decision: Literal["open", "reduced", "paused", "blocked", "missing"]
    backtest_status: Literal["pass", "watch", "fail", "missing"]
    forward_status: Literal["pass", "watch", "fail", "missing"]
    risk_control_status: Literal["pass", "watch", "fail", "missing"]
    reasons: list[str]
    actions: list[str]


class StrategyOperatingPlanReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    market_decision: Literal["expand", "observe", "reduce", "halt"]
    summary: str
    current_regime: MarketRegime
    items: list[StrategyOperatingPlanItem]
    actions: list[str]


class StrategyEvidencePackItem(BaseModel):
    strategy_id: str
    strategy_name: str
    category: str
    enabled: bool
    status: Literal["pass", "watch", "fail"]
    score: int
    operating_decision: Literal["open", "observe", "reduce", "block", "missing"]
    filter_valid: bool
    filter_match_count: int
    latest_validation_status: Literal["pass", "watch", "fail", "missing"]
    validation_id: str | None = None
    backtest_return: float | None = None
    forward_return: float | None = None
    decay: float | None = None
    gate_status: Literal["pass", "watch", "fail", "missing"]
    risk_control_status: Literal["pass", "watch", "fail", "missing"]
    evidence: list[str]
    gaps: list[str]
    actions: list[str]


class StrategyEvidencePackReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    items: list[StrategyEvidencePackItem]
    actions: list[str]


class ReadinessCheck(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    message: str


class ResearchReadiness(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    regime: MarketRegime
    checks: list[ReadinessCheck]
    blockers: list[str]
    actions: list[str]
    latest_validations: list[StrategyValidationResult]


class DataQualityReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    checks: list[ReadinessCheck]
    blockers: list[str]
    actions: list[str]


class DataFreshnessItem(BaseModel):
    dataset_id: str
    name: str
    status: Literal["pass", "watch", "fail"]
    rows: int
    latest_at: str | None = None
    age_hours: float | None = None
    sla_hours: int
    lag_score: int
    source: str
    action: str


class DataFreshnessReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    items: list[DataFreshnessItem]
    actions: list[str]


class ResearchCoverageItem(BaseModel):
    symbol: str
    name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    backtest_count: int
    forward_count: int
    walk_forward_count: int
    stress_count: int
    latest_run_at: str | None = None
    notes: list[str]
    actions: list[str]


class ResearchCoverageReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[ResearchCoverageItem]


class FactorCoverageItem(BaseModel):
    factor_id: str
    factor_name: str
    category: str
    status: Literal["pass", "watch", "fail"]
    score: int
    symbol_count: int
    backtest_symbols: int
    forward_symbols: int
    walk_forward_symbols: int
    stress_symbols: int
    latest_run_at: str | None = None
    missing_symbols: list[str]
    actions: list[str]


class FactorCoverageReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[FactorCoverageItem]


class FactorLifecycleItem(BaseModel):
    factor_id: str
    factor_name: str
    category: str
    enabled: bool
    stage: Literal["idea", "research", "validation", "candidate", "active", "paused"]
    status: Literal["pass", "watch", "fail"]
    score: int
    coverage_score: int | None = None
    leaderboard_score: int | None = None
    admission_score: int | None = None
    latest_run_at: str | None = None
    blockers: list[str]
    actions: list[str]


class FactorLifecycleReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[FactorLifecycleItem]
    actions: list[str]


class DataLineageNode(BaseModel):
    id: str
    name: str
    type: Literal["dataset", "factor", "strategy", "validation"]
    status: Literal["pass", "watch", "fail"]
    detail: str


class StrategyLineageItem(BaseModel):
    strategy_id: str
    strategy_name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    factor_ids: list[str]
    datasets: list[str]
    validation_coverage: dict[str, int]
    missing: list[str]
    actions: list[str]


class DataLineageReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    nodes: list[DataLineageNode]
    strategies: list[StrategyLineageItem]
    actions: list[str]


class ResearchRunAuditItem(BaseModel):
    id: str
    run_type: Literal["backtest", "forward", "walk_forward", "stress"]
    symbol: str
    factor_id: str
    factor_name: str
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    primary_metric: float
    drawdown: float
    summary: str
    action: str


class ResearchRunAuditReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    total_runs: int
    pass_count: int
    watch_count: int
    fail_count: int
    items: list[ResearchRunAuditItem]
    actions: list[str]


class FactorCoverageBackfillRequest(BaseModel):
    factor_id: str = "momentum-news"
    symbol: str | None = None
    missing_only: bool = True
    max_tasks: int = 6


class FactorCoverageBackfillResult(BaseModel):
    id: str
    generated_at: str
    factor_id: str
    factor_name: str
    requested_symbols: list[str]
    executed_tasks: list[str]
    skipped: list[str]
    coverage: FactorCoverageReport


class RevalidationQueueItem(BaseModel):
    rank: int
    symbol: str
    name: str
    factor_id: str
    factor_name: str
    status: Literal["pass", "watch", "fail"]
    priority: int
    due_tasks: list[str]
    latest_run_at: str | None = None
    reason: str
    actions: list[str]


class RevalidationQueueReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    stale_days: int
    items: list[RevalidationQueueItem]


class ResearchCoverageBackfillRequest(BaseModel):
    symbol: str | None = None
    factor_id: str = "momentum-news"
    missing_only: bool = True
    max_tasks: int = 4


class ResearchCoverageBackfillResult(BaseModel):
    id: str
    generated_at: str
    factor_id: str
    factor_name: str
    requested_symbols: list[str]
    executed_tasks: list[str]
    skipped: list[str]
    coverage: ResearchCoverageReport


class ResearchActionItem(BaseModel):
    priority: int
    source: str
    severity: Literal["pass", "watch", "fail"]
    title: str
    detail: str
    action: str


class ResearchActionPlan(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    summary: str
    items: list[ResearchActionItem]


class ResearchDashboardMetric(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    value: str
    detail: str


class ResearchDashboardSummary(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    score: int
    summary: str
    metrics: list[ResearchDashboardMetric]


class ResearchHealthCheck(BaseModel):
    name: str
    status: Literal["pass", "watch", "fail"]
    score: int
    detail: str


class ResearchHealthReport(BaseModel):
    generated_at: str
    status: Literal["pass", "watch", "fail"]
    decision: Literal["go", "caution", "stop"]
    score: int
    summary: str
    checks: list[ResearchHealthCheck]
    blockers: list[str]
    actions: list[str]


