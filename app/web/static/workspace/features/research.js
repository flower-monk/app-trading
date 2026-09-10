import { formatTime } from "../core/format.js";
import { escapeHtml } from "../core/http.js";
import { state } from "../core/state.js";
import { $ } from "../core/ui.js";

export function renderMarketRegime(regime, history = []) {
  const badgeClass = regime.risk_level === "risk" ? "down" : regime.risk_level === "watch" ? "watch" : regime.risk_level === "constructive" ? "up" : "neutral";
  $("#regimeBadge").textContent = regime.label;
  $("#regimeBadge").className = `badge ${badgeClass}`;
  $("#marketRegime").innerHTML = `
    <article class="research-card ${regime.risk_level}">
      <strong>${regime.summary}</strong>
      <span>广度 ${regime.breadth_score} · 波动 ${regime.volatility_score}% · 趋势 ${regime.trend_score}</span>
      ${regime.evidence.map((item) => `<small>${item}</small>`).join("")}
      ${regime.actions.map((item) => `<small>${item}</small>`).join("")}
    </article>
    <div class="regime-history">
      ${history.length ? history.map((item) => `
        <article class="regime-history-item ${item.risk_level}">
          <strong>${item.label}</strong>
          <span>${formatTime(item.generated_at)} · ${item.risk_level}</span>
          <small>广度 ${item.breadth_score} · 波动 ${item.volatility_score}%</small>
        </article>
      `).join("") : `<article class="regime-history-item"><span>暂无历史快照。</span></article>`}
    </div>
  `;
}

export function renderMarketRegimeTransition(report) {
  $("#marketRegimeTransition").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>迁移分 ${report.transition_score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      <small>广度 ${report.breadth_delta > 0 ? "+" : ""}${report.breadth_delta.toFixed(1)} · 波动 ${report.volatility_delta > 0 ? "+" : ""}${report.volatility_delta.toFixed(2)}% · 趋势 ${report.trend_delta > 0 ? "+" : ""}${report.trend_delta.toFixed(1)}</small>
      ${report.previous ? `<small>前一状态 ${report.previous.label} / ${report.previous.risk_level}</small>` : `<small>暂无可比较历史状态</small>`}
      ${report.evidence.slice(0, 3).map((item) => `<small>${item}</small>`).join("")}
      ${report.actions.slice(0, 2).map((item) => `<small>动作：${item}</small>`).join("")}
    </article>
  ` : `<article class="research-card"><span>暂无状态迁移监控。</span></article>`;
}

export function renderRegimeScenarioRisk(report) {
  $("#regimeScenarioRisk").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>状态情景风险 · ${report.status}</strong>
      <span>${report.summary}</span>
      ${report.actions.slice(0, 2).map((item) => `<small>动作：${item}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.regime_label} · 预算 ${item.risk_budget.toFixed(1)}%</strong>
        <span>情景分 ${item.score}/100 · 策略均分 ${item.average_strategy_score.toFixed(1)}</span>
        <small>open ${item.open_count} · 降权 ${item.reduced_count} · 暂停 ${item.paused_count} · 阻断 ${item.blocked_count}</small>
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无状态情景风险。</span></article>`;
}

export function renderRegimeExposure(report) {
  $("#regimeExposure").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>市场状态暴露 ${report.score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      <small>总目标 ${report.total_target_weight.toFixed(1)}% · 平均预算 ${report.average_risk_budget.toFixed(1)}% · 平均拥挤 ${report.average_crowding_score}/100</small>
      ${report.actions.slice(0, 3).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.symbol} · 目标 ${item.target_weight.toFixed(1)}%</strong>
        <span>预算 ${item.risk_budget.toFixed(1)}% · 拥挤 ${item.crowding_score}/100 · ${item.dominant_factor || "无主导因子"}</span>
        <small>配置 ${item.allocation_status} · 相关性 ${item.correlation_status}</small>
        ${item.notes.map((note) => `<small>${note}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无市场状态暴露报告。</span></article>`;
}

export function renderRegimePlaybook(playbook) {
  $("#regimePlaybook").innerHTML = `
    <article class="playbook-summary ${playbook.status}">
      <strong>${playbook.summary}</strong>
      <span>偏好因子 ${playbook.preferred_factors.join(" / ")}</span>
      <small>状态 ${playbook.regime.label} / ${playbook.regime.risk_level}</small>
    </article>
    <div class="playbook-grid">
      ${playbook.steps.map((step) => `
        <article class="playbook-card ${step.status}">
          <strong>${step.name}</strong>
          <span>${step.detail}</span>
          ${step.actions.slice(0, 3).map((action) => `<small>${action}</small>`).join("")}
        </article>
      `).join("")}
    </div>
    <div class="playbook-controls">
      ${playbook.risk_controls.map((control) => `<small>控制：${control}</small>`).join("")}
      ${playbook.next_actions.slice(0, 4).map((action) => `<small>下一步：${action}</small>`).join("")}
    </div>
  `;
}

export function renderFilterValidation(result) {
  const className = result.valid ? "pass" : "fail";
  $("#filterValidationStatus").textContent = result.valid ? `通过，命中 ${result.match_count} 个` : `需调整，命中 ${result.match_count} 个`;
  $("#filterValidationResult").innerHTML = `
    <article class="research-card ${className}">
      <strong>${result.valid ? "过滤器可用于验证" : "过滤器需要调整"}</strong>
      <span>${Object.entries(result.normalized_filters).map(([key, value]) => `${key}: ${value}`).join(" · ") || "未设置过滤器"}</span>
      ${result.warnings.map((item) => `<small>${item}</small>`).join("")}
      ${result.suggestions.map((item) => `<small>${item}</small>`).join("")}
    </article>
  `;
}

export function renderFilterContribution(report) {
  $("#filterContributionResult").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>过滤器贡献诊断 ${report.concentration_score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      <small>当前过滤器：${Object.entries(report.filters).map(([key, value]) => `${key}:${value}`).join(" · ") || "未设置"}</small>
      ${report.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.severity}">
        <strong>${item.label} · ${item.current_value}</strong>
        <span>移除后命中 ${item.relaxed_match_count}，新增 ${item.removed_by_filter} 个样本</span>
        <small>${item.insight}</small>
        <small>动作：${item.action}</small>
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无过滤器贡献诊断。</span></article>`;
}

export function renderFilterAdmission(report) {
  $("#filterAdmissionResult").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>过滤器准入 · ${report.decision} · ${report.score}/100</strong>
      <span>${report.summary}</span>
      <small>${Object.entries(report.filters).map(([key, value]) => `${key}:${value}`).join(" · ") || "未设置过滤器"}</small>
      ${report.actions.slice(0, 3).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.checks.map((check) => `
      <article class="research-card ${check.status}">
        <strong>${check.name} · ${check.status}</strong>
        <span>${check.message}</span>
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无过滤器准入包。</span></article>`;
}

export function renderFilterStressTest(report) {
  $("#filterStressTestResult").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>过滤器压力测试 ${report.stability_score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      <small>命中范围 ${report.min_match_count}-${report.max_match_count} · 基准 ${report.baseline_match_count}</small>
      ${report.actions.slice(0, 3).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.scenarios.map((scenario) => `
      <article class="research-card ${scenario.status}">
        <strong>${scenario.name} · 命中 ${scenario.match_count}</strong>
        <span>${Object.entries(scenario.filters).filter(([, value]) => value !== null && value !== undefined && value !== "").map(([key, value]) => `${key}:${value}`).join(" · ") || "未设置过滤器"}</span>
        ${scenario.warnings.slice(0, 2).map((warning) => `<small>${warning}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无过滤器压力测试。</span></article>`;
}

export function renderDataCatalog(items) {
  $("#dataCatalogCount").textContent = `${items.length} 项`;
  $("#dataCatalogList").innerHTML = items.map((item) => `
    <article class="research-card ${item.status === "ready" ? "pass" : "watch"}">
      <strong>${item.name}</strong>
      <span>${item.type} · ${item.provider} · ${item.update_mode}</span>
      <small>${item.coverage.join(" / ") || "暂无覆盖"}</small>
      <small>${item.rows} 行 · ${item.fields.join(", ")}</small>
      <small>${item.notes}</small>
    </article>
  `).join("");
}

export function renderDataQuality(report) {
  $("#dataQuality").innerHTML = `
    <article class="research-card ${report.status}">
      <strong>数据质量 ${report.score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      ${report.blockers.map((item) => `<small>阻断：${item}</small>`).join("")}
      ${report.actions.map((item) => `<small>动作：${item}</small>`).join("")}
      ${report.checks.map((item) => `<small>${item.name}：${item.message}</small>`).join("")}
    </article>
  `;
}

export function renderDataFreshness(report) {
  $("#dataFreshness").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>数据新鲜度 ${report.score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      ${report.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.name} · ${item.lag_score}/100</strong>
        <span>${item.source} · ${item.rows} 行 · SLA ${item.sla_hours}h</span>
        <small>最新 ${item.latest_at ? formatTime(item.latest_at) : "--"} · 延迟 ${item.age_hours === null ? "--" : `${item.age_hours.toFixed(1)}h`}</small>
        <small>动作：${item.action}</small>
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无数据新鲜度报告。</span></article>`;
}

export function renderResearchHealth(report) {
  $("#researchHealth").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>研究健康总览 · ${report.decision} · ${report.score}/100</strong>
      <span>${report.summary}</span>
      ${report.blockers.slice(0, 3).map((blocker) => `<small>阻断：${blocker}</small>`).join("")}
      ${report.actions.slice(0, 3).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.checks.map((check) => `
      <article class="research-card ${check.status}">
        <strong>${check.name} · ${check.score}/100</strong>
        <span>${check.detail}</span>
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无研究健康总览。</span></article>`;
}

export function renderResearchDashboard(summary) {
  $("#researchDashboard").innerHTML = `
    <article class="research-card ${summary.status}">
      <strong>研究工作台 ${summary.score}/100 · ${summary.status}</strong>
      <span>${summary.summary}</span>
    </article>
    ${summary.metrics.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.name} · ${item.value}</strong>
        <small>${item.detail}</small>
      </article>
    `).join("")}
  `;
}

export function renderResearchReadiness(readiness) {
  const className = readiness.status === "pass" ? "pass" : readiness.status === "watch" ? "watch" : "fail";
  $("#researchReadiness").innerHTML = `
    <article class="research-card ${className}">
      <strong>研究就绪度 ${readiness.score}/100 · ${readiness.status}</strong>
      <span>${readiness.summary}</span>
      <small>市场：${readiness.regime.label} / ${readiness.regime.risk_level}</small>
      ${readiness.blockers.map((item) => `<small>阻断：${item}</small>`).join("")}
      ${readiness.actions.map((item) => `<small>动作：${item}</small>`).join("")}
      ${readiness.checks.map((item) => `<small>${item.name}：${item.message}</small>`).join("")}
    </article>
  `;
}

export function renderResearchCoverage(report) {
  $("#researchCoverage").innerHTML = `
    <article class="research-card ${report.status}">
      <strong>研究覆盖矩阵</strong>
      <span>${report.summary}</span>
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.symbol} · ${item.score}/100</strong>
        <span>${item.name} · 回测 ${item.backtest_count} · 前向 ${item.forward_count} · 滚动 ${item.walk_forward_count} · 压力 ${item.stress_count}</span>
        ${item.latest_run_at ? `<small>最近：${formatTime(item.latest_run_at)}</small>` : `<small>暂无研究运行记录</small>`}
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  `;
}

export function renderFactorCoverage(report) {
  $("#factorCoverage").innerHTML = `
    <article class="research-card ${report.status}">
      <strong>因子覆盖矩阵</strong>
      <span>${report.summary}</span>
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.factor_name} · ${item.score}/100</strong>
        <span>${item.category} · 标的 ${item.symbol_count} · 回测 ${item.backtest_symbols} · 前向 ${item.forward_symbols} · 滚动 ${item.walk_forward_symbols} · 压力 ${item.stress_symbols}</span>
        ${item.latest_run_at ? `<small>最近：${formatTime(item.latest_run_at)}</small>` : `<small>暂无研究运行记录</small>`}
        ${item.missing_symbols.length ? `<small>待补：${item.missing_symbols.join(", ")}</small>` : `<small>覆盖完整</small>`}
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  `;
}

export function renderFactorLifecycle(report) {
  $("#factorLifecycle").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>因子生命周期</strong>
      <span>${report.summary}</span>
      ${report.actions.slice(0, 3).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.factor_name} · ${item.stage}</strong>
        <span>${item.category} · 总分 ${item.score}/100 · ${item.enabled ? "启用" : "停用"}</span>
        <small>覆盖 ${item.coverage_score === null ? "--" : item.coverage_score} · 榜单 ${item.leaderboard_score === null ? "--" : item.leaderboard_score} · 准入 ${item.admission_score === null ? "--" : item.admission_score}</small>
        ${item.latest_run_at ? `<small>最近运行：${formatTime(item.latest_run_at)}</small>` : `<small>暂无研究运行记录</small>`}
        ${item.blockers.slice(0, 2).map((blocker) => `<small>阻断：${blocker}</small>`).join("")}
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无因子生命周期报告。</span></article>`;
}

export function renderDataLineage(report) {
  $("#dataLineage").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>研究数据血缘 ${report.score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      <small>节点 ${report.nodes.length} 个 · 策略 ${report.strategies.length} 个</small>
      ${report.actions.slice(0, 3).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.strategies.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.strategy_name} · ${item.score}/100</strong>
        <span>因子 ${item.factor_ids.join(", ") || "未绑定"} · 数据 ${item.datasets.join(" / ")}</span>
        <small>覆盖：回测 ${item.validation_coverage.backtest} · 前向 ${item.validation_coverage.forward} · 滚动 ${item.validation_coverage.walk_forward} · 压力 ${item.validation_coverage.stress}</small>
        ${item.missing.slice(0, 2).map((missing) => `<small>缺口：${missing}</small>`).join("")}
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无数据血缘报告。</span></article>`;
}

export function renderResearchActionPlan(plan) {
  $("#researchActionPlan").innerHTML = `
    <article class="research-card ${plan.status}">
      <strong>研究行动计划</strong>
      <span>${plan.summary}</span>
    </article>
    ${plan.items.slice(0, 8).map((item) => `
      <article class="research-card ${item.severity}">
        <strong>${item.title}</strong>
        <span>${item.source} · 优先级 ${item.priority}</span>
        <small>${item.detail}</small>
        <small>动作：${item.action}</small>
      </article>
    `).join("")}
  `;
}

export function renderStrategyCandidates(report) {
  const decisionText = { go: "小规模模拟", size_down: "降权模拟", hold: "观察", block: "阻断" };
  $("#strategyCandidates").innerHTML = `
    <article class="research-card ${report.status}">
      <strong>策略候选池</strong>
      <span>${report.summary}</span>
      <small>过滤器：${Object.entries(report.filters).map(([key, value]) => `${key}:${value}`).join(" · ") || "未设置"}</small>
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>#${item.rank} ${item.symbol} · ${item.factor_name}</strong>
        <span>${item.score}/100 · ${decisionText[item.decision]} · 建议 ${item.recommended_weight.toFixed(1)}%</span>
        <small>${item.match_reasons.slice(0, 2).join("；") || item.market}</small>
        ${item.blockers.slice(0, 2).map((blocker) => `<small>阻断：${blocker}</small>`).join("")}
        ${item.warnings.slice(0, 2).map((warning) => `<small>观察：${warning}</small>`).join("")}
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  `;
}

export function renderAdmissionQueue(report) {
  $("#admissionQueue").innerHTML = `
    <article class="research-card ${report.status}">
      <strong>因子准入队列</strong>
      <span>${report.summary}</span>
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>#${item.rank} ${item.symbol} · ${item.factor_name}</strong>
        <span>${item.score}/100 · ${item.recommendation}</span>
        ${item.blockers.slice(0, 2).map((blocker) => `<small>阻断：${blocker}</small>`).join("")}
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  `;
}

export function renderRevalidationQueue(report) {
  $("#revalidationQueue").innerHTML = `
    <article class="research-card ${report.status}">
      <strong>复验队列</strong>
      <span>${report.summary}</span>
      <small>过期阈值 ${report.stale_days} 天</small>
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>#${item.rank} ${item.symbol} · ${item.factor_name}</strong>
        <span>优先级 ${item.priority} · ${item.due_tasks.join(" / ")}</span>
        ${item.latest_run_at ? `<small>最近：${formatTime(item.latest_run_at)}</small>` : `<small>暂无完整研究记录</small>`}
        <small>${item.reason}</small>
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  `;
}

export function renderResearchRunAudit(report) {
  $("#researchRunAudit").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>研究运行审计 ${report.score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      <small>pass ${report.pass_count} · watch ${report.watch_count} · fail ${report.fail_count}</small>
      ${report.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.slice(0, 8).map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.symbol} · ${item.factor_name} · ${item.run_type}</strong>
        <span>${item.summary}</span>
        <small>${formatTime(item.generated_at)} · 指标 ${item.primary_metric > 0 ? "+" : ""}${item.primary_metric.toFixed(2)} · 回撤 ${item.drawdown.toFixed(2)}%</small>
        <small>动作：${item.action}</small>
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无研究运行审计。</span></article>`;
}

export function renderStrategyTemplates(items, gates = []) {
  const gateByStrategy = new Map(gates.map((item) => [item.strategy_id, item]));
  $("#strategyTemplateList").innerHTML = items.map((item) => `
    <article class="research-card ${item.enabled ? "pass" : "watch"}">
      <strong>${item.name}</strong>
      <span>${item.category} · ${item.factor_ids.join(", ") || "未绑定因子"}</span>
      <small>${item.description}</small>
      <small>过滤器：${Object.entries(item.default_filters).map(([key, value]) => `${key}:${value}`).join(" · ") || "无"}</small>
      <small>验证：${item.validation_rules.join("；") || "无"}</small>
      ${gateByStrategy.get(item.id) ? `
        <small>准入：${gateByStrategy.get(item.id).recommendation} · ${gateByStrategy.get(item.id).score}/100 · ${gateByStrategy.get(item.id).status}</small>
        ${gateByStrategy.get(item.id).blockers.slice(0, 1).map((blocker) => `<small>阻断：${blocker}</small>`).join("")}
      ` : ""}
      <div class="meta-row">
        <button class="text-button" type="button" data-strategy-validate="${item.id}">验证策略</button>
        <button class="text-button" type="button" data-strategy-toggle="${item.id}" data-strategy-enabled="${item.enabled ? "0" : "1"}">${item.enabled ? "停用" : "启用"}</button>
        <button class="text-button" type="button" data-strategy-delete="${item.id}">停用</button>
      </div>
    </article>
  `).join("");
}

export function renderStrategyValidations(items) {
  $("#strategyValidationList").innerHTML = items.length ? items.map((item) => `
    <article class="research-card ${item.pass_fail}">
      <strong>${item.strategy_name} · ${item.pass_fail}</strong>
      <span>${item.symbols.join(", ")} · 回测均值 ${item.average_backtest_return > 0 ? "+" : ""}${item.average_backtest_return.toFixed(2)}% · 前向均值 ${item.average_forward_return > 0 ? "+" : ""}${item.average_forward_return.toFixed(2)}%</span>
      <small>市场：${item.regime.label} / ${item.regime.risk_level}</small>
      ${item.checks.map((check) => `<small>${check}</small>`).join("")}
    </article>
  `).join("") : `<article class="research-card"><span>暂无策略验证记录。</span></article>`;
}

export function renderStrategyValidationDecay(report) {
  $("#strategyValidationDecay").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>策略验证衰减 ${report.score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      ${report.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.strategy_name} · ${item.score}/100</strong>
        <span>回测 ${item.backtest_return > 0 ? "+" : ""}${item.backtest_return.toFixed(2)}% · 前向 ${item.forward_return > 0 ? "+" : ""}${item.forward_return.toFixed(2)}% · 衰减 ${item.decay.toFixed(2)}pp</span>
        <small>比例 ${item.decay_ratio === null ? "--" : `${item.decay_ratio.toFixed(1)}%`} · 标的 ${item.symbol_count} · 回测 ${item.backtest_count} · 前向 ${item.forward_count}</small>
        <small>状态 ${item.regime_label}/${item.risk_level} · ${formatTime(item.generated_at)}</small>
        <small>动作：${item.action}</small>
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无策略验证衰减报告。</span></article>`;
}

export function renderStrategyLeaderboard(leaderboard) {
  $("#strategyLeaderboard").innerHTML = leaderboard.items.length ? `
    <article class="research-card">
      <strong>策略排行榜</strong>
      <span>${leaderboard.summary}</span>
    </article>
    ${leaderboard.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>#${item.rank} ${item.strategy_name} · ${item.recommendation}</strong>
        <span>${item.category} · 分数 ${item.score}/100 · 最近验证 ${item.latest_validation_status}</span>
        <small>因子 ${item.factor_count} 个 · 过滤器 ${item.filter_count} 项${item.average_backtest_return === null ? "" : ` · 回测 ${item.average_backtest_return > 0 ? "+" : ""}${item.average_backtest_return.toFixed(2)}%`}${item.average_forward_return === null ? "" : ` · 前向 ${item.average_forward_return > 0 ? "+" : ""}${item.average_forward_return.toFixed(2)}%`}</small>
        ${item.reasons.slice(0, 3).map((reason) => `<small>${reason}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无策略排行榜。</span></article>`;
}

export function renderStrategyRegimeFit(report) {
  const statusText = { pass: "优先", watch: "观察", fail: "暂停" };
  const fitText = { aligned: "匹配", neutral: "中性", misaligned: "失配" };
  $("#strategyRegimeFit").innerHTML = report.items.length ? `
    <article class="research-card ${report.status}">
      <strong>策略状态适配榜 · ${statusText[report.status]}</strong>
      <span>${report.summary}</span>
      <small>当前状态 ${report.regime.label} / ${report.regime.risk_level}</small>
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>#${item.rank} ${item.strategy_name} · ${item.recommendation}</strong>
        <span>${item.category} · 适配分 ${item.score}/100 · 过滤器 ${item.filter_status} · 最近验证 ${item.latest_validation_status}</span>
        <small>因子：${item.factor_fits.map((factor) => `${factor.factor_name} ${factor.score}/100 ${fitText[factor.regime_fit]}`).join("；") || "未绑定"}</small>
        ${item.reasons.slice(0, 2).map((reason) => `<small>${reason}</small>`).join("")}
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无策略状态适配结果。</span></article>`;
}

export function renderStrategyRegimeMatrix(report) {
  const decisionText = { open: "打开", reduced: "降权", paused: "暂停", blocked: "阻断" };
  $("#strategyRegimeMatrix").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>策略状态准入矩阵 · ${report.status}</strong>
      <span>${report.summary}</span>
      <small>当前状态 ${report.current_regime.label} / ${report.current_regime.risk_level}</small>
      ${report.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.overall_status}">
        <strong>${item.strategy_name} · ${item.latest_validation_status}</strong>
        <span>${item.category} · ${item.enabled ? "已启用" : "已停用"}</span>
        <div class="meta-row">
          ${item.cells.map((cell) => `<span class="${cell.status}">${cell.regime_label} ${decisionText[cell.decision]} ${cell.score}/100</span>`).join("")}
        </div>
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无策略状态准入矩阵。</span></article>`;
}

export function renderStrategyCrowding(report) {
  $("#strategyCrowding").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>策略拥挤度 ${report.score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      <small>当前状态 ${report.current_regime.label} / ${report.current_regime.risk_level}</small>
      ${report.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.pairs.slice(0, 6).map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.strategy_a_name} / ${item.strategy_b_name}</strong>
        <span>重叠分 ${item.overlap_score}/100 · 过滤器 ${item.filter_overlap.toFixed(1)}%</span>
        <small>共享因子：${item.shared_factors.join(" / ") || "无"} · 决策 ${item.current_decisions.join(" / ")}</small>
        <small>${item.detail}</small>
        <small>动作：${item.action}</small>
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无策略拥挤报告。</span></article>`;
}

export function renderStrategyLifecycle(report) {
  const stageText = {
    draft: "草稿",
    validation: "验证中",
    candidate: "候选",
    active: "活跃",
    paused: "暂停",
  };
  $("#strategyLifecycle").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>策略生命周期 · ${report.status}</strong>
      <span>${report.summary}</span>
      ${report.actions.slice(0, 3).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.strategy_name} · ${stageText[item.stage] || item.stage}</strong>
        <span>${item.category} · 总分 ${item.score}/100 · ${item.enabled ? "已启用" : "已停用"}</span>
        <small>榜单 ${item.leaderboard_score === null ? "--" : item.leaderboard_score} · 准入 ${item.promotion_score === null ? "--" : item.promotion_score} · 状态 ${item.regime_score === null ? "--" : item.regime_score}</small>
        <small>最近验证 ${item.latest_validation_status}</small>
        ${item.blockers.slice(0, 2).map((blocker) => `<small>阻断：${blocker}</small>`).join("")}
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无策略生命周期报告。</span></article>`;
}

export function renderStrategyRiskControls(report) {
  $("#strategyRiskControls").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>策略风险控制 ${report.score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      ${report.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.strategy_name} · ${item.score}/100</strong>
        <span>${item.category} · 风控 ${item.risk_control_count} · 验证规则 ${item.validation_rule_count} · ${item.enabled ? "启用" : "停用"}</span>
        <small>准入 ${item.gate_status} · 生命周期 ${item.lifecycle_stage} · 拥挤 ${item.crowding_status} · 衰减 ${item.decay_status}</small>
        ${item.blockers.slice(0, 2).map((blocker) => `<small>阻断：${blocker}</small>`).join("")}
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无策略风险控制审计。</span></article>`;
}

export function renderMarketStateControls(report) {
  const decisionText = { expand: "扩大模拟", observe: "小规模观察", reduce: "降权运行", halt: "暂停新增" };
  $("#marketStateControls").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>市场状态策略总览 ${report.score}/100 · ${decisionText[report.decision] || report.decision}</strong>
      <span>${report.summary}</span>
      <small>当前状态 ${report.current_regime.label} · 风险 ${report.current_regime.risk_level}</small>
      ${report.actions.slice(0, 3).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.name} · ${item.score}/100</strong>
        <span>${item.detail}</span>
        <small>状态 ${item.status} · ${item.action}</small>
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无市场状态策略总览。</span></article>`;
}

export function renderStrategyOperatingPlan(report) {
  const decisionText = { open: "打开模拟", observe: "观察", reduce: "降权", block: "阻断" };
  $("#strategyOperatingPlan").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>策略操作计划 ${report.score}/100 · ${report.market_decision}</strong>
      <span>${report.summary}</span>
      <small>当前状态 ${report.current_regime.label} · 风险 ${report.current_regime.risk_level}</small>
      ${report.actions.slice(0, 3).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.strategy_name} · ${decisionText[item.decision] || item.decision}</strong>
        <span>${item.category} · 建议权重 ${item.recommended_weight.toFixed(2)}% · 评分 ${item.score}/100</span>
        <small>准入 ${item.gate_status} · 生命周期 ${item.lifecycle_stage} · 状态矩阵 ${item.regime_decision}</small>
        <small>回测 ${item.backtest_status} · 前向 ${item.forward_status} · 风控 ${item.risk_control_status}</small>
        ${item.reasons.slice(0, 2).map((reason) => `<small>依据：${reason}</small>`).join("")}
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无策略操作计划。</span></article>`;
}

export function renderStrategyEvidencePacks(report) {
  $("#strategyEvidencePacks").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>策略验证证据包 ${report.score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      ${report.actions.slice(0, 3).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.strategy_name} · ${item.score}/100</strong>
        <span>${item.category} · 操作 ${item.operating_decision} · 过滤器 ${item.filter_valid ? "通过" : "失败"} · 命中 ${item.filter_match_count}</span>
        <small>验证 ${item.latest_validation_status} · 回测 ${item.backtest_return === null ? "--" : `${item.backtest_return > 0 ? "+" : ""}${item.backtest_return.toFixed(2)}%`} · 前向 ${item.forward_return === null ? "--" : `${item.forward_return > 0 ? "+" : ""}${item.forward_return.toFixed(2)}%`} · 衰减 ${item.decay === null ? "--" : `${item.decay.toFixed(2)}pp`}</small>
        <small>准入 ${item.gate_status} · 风控 ${item.risk_control_status}</small>
        ${item.evidence.slice(0, 2).map((evidence) => `<small>证据：${evidence}</small>`).join("")}
        ${item.gaps.slice(0, 2).map((gap) => `<small>缺口：${gap}</small>`).join("")}
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无策略验证证据包。</span></article>`;
}

export function renderStrategyBacktestMonitor(report) {
  $("#strategyBacktestMonitor").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>策略回测观察台 · ${report.status}</strong>
      <span>${report.summary}</span>
      ${report.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.strategy_name} · ${item.score}/100</strong>
        <span>${item.category} · 因子覆盖 ${item.tested_factor_count}/${item.factor_count} · 回测 ${item.run_count} 次</span>
        <small>正收益 ${item.positive_count} · 标的 ${item.symbol_count} · 新鲜度 ${item.freshness_days === null ? "--" : `${item.freshness_days} 天`}</small>
        <small>收益 ${item.average_return === null ? "--" : `${item.average_return > 0 ? "+" : ""}${item.average_return.toFixed(2)}%`} · Sharpe ${item.average_sharpe === null ? "--" : item.average_sharpe.toFixed(2)} · 胜率 ${item.average_win_rate === null ? "--" : `${item.average_win_rate.toFixed(1)}%`} · 回撤 ${item.max_drawdown === null ? "--" : `${item.max_drawdown.toFixed(2)}%`}</small>
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无策略回测观察。</span></article>`;
}

export function renderStrategyForwardMonitor(report) {
  $("#strategyForwardMonitor").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>策略前向观察台 · ${report.status}</strong>
      <span>${report.summary}</span>
      ${report.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.strategy_name} · ${item.score}/100</strong>
        <span>${item.category} · 因子覆盖 ${item.tested_factor_count}/${item.factor_count} · 标的 ${item.symbol_count}</span>
        <small>通过 ${item.pass_count} · 观察 ${item.watch_count} · 失败 ${item.fail_count} · 新鲜度 ${item.freshness_days === null ? "--" : `${item.freshness_days} 天`}</small>
        <small>前向 ${item.average_forward_return === null ? "--" : `${item.average_forward_return > 0 ? "+" : ""}${item.average_forward_return.toFixed(2)}%`} · 衰减 ${item.average_degradation === null ? "--" : `${item.average_degradation.toFixed(2)}pp`} · 最大回撤 ${item.max_drawdown === null ? "--" : `${item.max_drawdown.toFixed(2)}%`}</small>
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无策略前向观察。</span></article>`;
}

export function renderStrategySensitivity(report) {
  $("#strategySensitivity").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>策略敏感性 ${report.score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      ${report.actions.slice(0, 3).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.strategy_name} · ${item.score}/100</strong>
        <span>${item.category} · 因子覆盖 ${item.tested_factor_count}/${item.factor_count} · 标的 ${item.symbol_count} · 周期 ${item.interval_count}</span>
        <small>回测 ${item.backtest_count} · 前向 ${item.forward_count} · 窗口 ${item.window_variants}</small>
        <small>正收益率 ${item.positive_backtest_rate.toFixed(1)}% · 前向通过率 ${item.forward_pass_rate.toFixed(1)}% · 最差回撤 ${item.worst_drawdown === null ? "--" : `${item.worst_drawdown.toFixed(2)}%`}</small>
        <small>收益离散 ${item.return_dispersion === null ? "--" : `${item.return_dispersion.toFixed(2)}pp`} · 衰减离散 ${item.degradation_dispersion === null ? "--" : `${item.degradation_dispersion.toFixed(2)}pp`}</small>
        ${item.evidence.slice(0, 2).map((evidence) => `<small>证据：${evidence}</small>`).join("")}
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无策略敏感性报告。</span></article>`;
}

export function renderStrategyRevalidationPlan(report) {
  $("#strategyRevalidationPlan").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>策略状态复验计划 ${report.score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      <small>当前状态 ${report.current_regime.label} · 风险 ${report.current_regime.risk_level}</small>
      ${report.actions.slice(0, 3).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>#${item.rank} ${item.strategy_name} · 优先级 ${item.priority}</strong>
        <span>${item.category} · 状态 ${item.current_regime} · 矩阵 ${item.regime_decision}</span>
        <small>敏感性 ${item.sensitivity_status} · 证据 ${item.evidence_status} · 生命周期 ${item.lifecycle_stage}</small>
        <small>任务：${item.due_tasks.join(" / ") || "常规观察"}</small>
        ${item.reasons.slice(0, 2).map((reason) => `<small>原因：${reason}</small>`).join("")}
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无策略状态复验计划。</span></article>`;
}

export function renderStrategyValidationCoverage(report) {
  $("#strategyValidationCoverage").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>策略验证覆盖矩阵 ${report.score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      ${report.actions.slice(0, 3).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.strategy_name} · ${item.score}/100</strong>
        <span>${item.category} · 完整因子 ${item.fully_validated_factor_count}/${item.factor_count} · 标的 ${item.symbol_count}</span>
        <small>回测 ${item.backtest_factor_count}/${item.factor_count} (${item.backtest_run_count}) · 前向 ${item.forward_factor_count}/${item.factor_count} (${item.forward_run_count})</small>
        <small>滚动 ${item.walk_forward_factor_count}/${item.factor_count} (${item.walk_forward_run_count}) · 压力 ${item.stress_factor_count}/${item.factor_count} (${item.stress_run_count})</small>
        ${item.missing_tasks.length ? `<small>待补：${item.missing_tasks.slice(0, 6).join(" / ")}</small>` : `<small>四阶段验证证据已覆盖全部绑定因子。</small>`}
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无策略验证覆盖矩阵。</span></article>`;
}

export function renderScreenerPresets(items, validations = []) {
  state.screenerPresets = items;
  state.screenerPresetValidations = validations;
  const validationById = new Map(validations.map((item) => [item.id, item]));
  $("#screenerPresetList").innerHTML = items.length ? items.map((item) => `
    <article class="screener-preset-item ${validationById.get(item.id)?.status || ""}">
      <div>
        <strong>${item.name}</strong>
        <span>${Object.entries(item.filters).map(([key, value]) => `${key}:${value}`).join(" · ") || "无条件"}</span>
        ${validationById.get(item.id) ? `<span>验证 ${validationById.get(item.id).status} · 命中 ${validationById.get(item.id).match_count} 个</span>` : ""}
        ${validationById.get(item.id)?.warnings?.[0] ? `<span>${validationById.get(item.id).warnings[0]}</span>` : ""}
      </div>
      <button class="text-button" type="button" data-screener-load="${item.id}">加载</button>
      <button class="text-button" type="button" data-screener-delete="${item.id}">停用</button>
    </article>
  `).join("") : `<article class="screener-preset-item"><span>暂无筛选模板。</span></article>`;
}

export function renderFilterLibraryScore(report) {
  state.filterLibraryScore = report;
  $("#filterLibraryScore").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>过滤器库评分 · ${report.status}</strong>
      <span>${report.summary}</span>
      ${report.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.name} · ${item.decision}</strong>
        <span>得分 ${item.score}/100 · 命中 ${item.match_count} · 条件 ${item.filter_count}</span>
        <small>集中度 ${item.concentration_score}/100 · 稳定 ${item.stability_score}/100 · ${item.regime_label}/${item.risk_level}</small>
        <small>${Object.entries(item.filters).map(([key, value]) => `${key}:${value}`).join(" · ") || "无条件"}</small>
        ${item.warnings.slice(0, 1).map((warning) => `<small>提示：${warning}</small>`).join("")}
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无过滤器库评分。</span></article>`;
}

export function renderFilterStrategyCoverage(report) {
  state.filterStrategyCoverage = report;
  const typeText = {
    preset_reused: "已复用",
    orphan_preset: "孤立模板",
    unregistered_strategy_filter: "策略未登记",
  };
  $("#filterStrategyCoverage").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>过滤器策略覆盖 ${report.score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      ${report.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>${item.preset_name || item.strategy_name} · ${typeText[item.match_type] || item.match_type}</strong>
        <span>得分 ${item.score}/100 · 条件 ${item.filter_count} · 关联策略 ${item.linked_strategy_count}</span>
        <small>${Object.entries(item.filters).map(([key, value]) => `${key}:${value}`).join(" · ") || "无条件"}</small>
        <small>${item.detail}</small>
        <small>动作：${item.action}</small>
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无过滤器策略覆盖报告。</span></article>`;
}

export function renderRegimeFilterTemplates(items) {
  state.regimeFilterTemplates = items;
  $("#regimeFilterTemplates").innerHTML = items.length ? items.map((item) => `
    <article class="regime-filter-item ${item.valid ? "pass" : "watch"}">
      <div>
        <strong>${item.name}</strong>
        <span>${item.regime_label} / ${item.risk_level} · 命中 ${item.match_count} 个</span>
        <small>${Object.entries(item.filters).filter(([, value]) => value !== null && value !== undefined).map(([key, value]) => `${key}:${value}`).join(" · ")}</small>
        ${item.rationale.map((text) => `<small>${text}</small>`).join("")}
        ${item.warnings.slice(0, 1).map((text) => `<small>提示：${text}</small>`).join("")}
      </div>
      <button class="text-button" type="button" data-regime-filter="${item.id}">应用</button>
    </article>
  `).join("") : `<article class="regime-filter-item"><span>暂无状态模板。</span></article>`;
}
