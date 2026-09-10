import { $ } from "../core/ui.js";

let drawSparkline = () => {};

export function setBacktestSparklineRenderer(renderer) {
  drawSparkline = renderer;
}

export function renderBacktests(items) {
  $("#backtestCount").textContent = `${items.length} 个`;
  $("#backtestList").innerHTML = items.map((item) => `
    <article class="backtest-card">
      <strong>${item.factor}</strong>
      <p>收益 ${item.total_return > 0 ? "+" : ""}${item.total_return.toFixed(2)}% · 最大回撤 ${item.max_drawdown.toFixed(2)}% · Sharpe ${item.sharpe.toFixed(2)}</p>
      <canvas class="equity-spark" data-equity="${item.equity_curve.join(",")}"></canvas>
      <div class="meta-row">
        <span class="mini-badge ${item.total_return >= 0 ? "up" : "down"}">${item.status}</span>
        <span>胜率 ${item.win_rate.toFixed(1)}%</span>
        <span>${item.trades} 笔</span>
        ${item.exposure !== undefined ? `<span>暴露 ${item.exposure.toFixed(1)}%</span>` : ""}
      </div>
    </article>
  `).join("");
  document.querySelectorAll(".equity-spark").forEach(drawSparkline);
}

export function renderBacktestBenchmark(report) {
  $("#backtestBenchmark").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>基准对照 · ${report.status}</strong>
      <span>${report.summary}</span>
      <small>策略 ${report.strategy_return > 0 ? "+" : ""}${report.strategy_return.toFixed(2)}% · 买入持有 ${report.benchmark_return > 0 ? "+" : ""}${report.benchmark_return.toFixed(2)}% · Alpha ${report.alpha > 0 ? "+" : ""}${report.alpha.toFixed(2)}pp</small>
      <small>回撤差 ${report.drawdown_delta > 0 ? "+" : ""}${report.drawdown_delta.toFixed(2)}pp · Sharpe 差 ${report.sharpe_delta > 0 ? "+" : ""}${report.sharpe_delta.toFixed(2)} · 暴露 ${report.exposure.toFixed(1)}%</small>
      ${report.checks.slice(0, 3).map((check) => `<small>${check}</small>`).join("")}
      ${report.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
  ` : `<article class="research-card"><span>暂无基准对照。</span></article>`;
}

export function renderBacktestForwardConsistency(report) {
  $("#backtestForwardConsistency").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>回测-前向一致性 · ${report.recommendation}</strong>
      <span>${report.summary}</span>
      <small>回测 ${report.backtest_return === null ? "--" : `${report.backtest_return > 0 ? "+" : ""}${report.backtest_return.toFixed(2)}%`} · 前向 ${report.forward_return === null ? "--" : `${report.forward_return > 0 ? "+" : ""}${report.forward_return.toFixed(2)}%`} · 衰减 ${report.degradation === null ? "--" : `${report.degradation.toFixed(2)}pp`}</small>
      <small>滚动通过率 ${report.walk_forward_pass_rate === null ? "--" : `${report.walk_forward_pass_rate.toFixed(1)}%`} · 压力最差 ${report.stress_worst_return === null ? "--" : `${report.stress_worst_return > 0 ? "+" : ""}${report.stress_worst_return.toFixed(2)}%`}</small>
      ${report.checks.map((check) => `<small>${check.name}：${check.status} · ${check.message}</small>`).join("")}
      ${report.actions.slice(0, 3).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
  ` : `<article class="research-card"><span>暂无回测-前向一致性报告。</span></article>`;
}

export function renderForwardTests(items) {
  $("#forwardTestList").innerHTML = items.length ? items.map((item) => `
    <article class="research-card ${item.pass_fail}">
      <strong>${item.factor_name} · ${item.pass_fail}</strong>
      <span>训练 ${item.train_return > 0 ? "+" : ""}${item.train_return.toFixed(2)}% · 前向 ${item.forward_return > 0 ? "+" : ""}${item.forward_return.toFixed(2)}% · 衰减 ${item.degradation.toFixed(2)}%</span>
      <canvas class="equity-spark" data-equity="${item.equity_curve.join(",")}"></canvas>
      ${item.checks.map((check) => `<small>${check}</small>`).join("")}
    </article>
  `).join("") : `<article class="research-card"><span>暂无前向测试。运行后会对比训练段和样本外表现。</span></article>`;
  document.querySelectorAll(".equity-spark").forEach(drawSparkline);
}

export function renderForwardLeaderboard(report) {
  $("#forwardLeaderboard").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>前向排行榜</strong>
      <span>${report.summary}</span>
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>#${item.rank} ${item.symbol} · ${item.factor_name}</strong>
        <span>${item.score}/100 · ${item.recommendation} · 前向 ${item.forward_return > 0 ? "+" : ""}${item.forward_return.toFixed(2)}%</span>
        <small>训练 ${item.train_return > 0 ? "+" : ""}${item.train_return.toFixed(2)}% · 衰减 ${item.degradation.toFixed(2)}pp · 回撤 ${item.max_drawdown.toFixed(2)}%</small>
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无前向排行榜。</span></article>`;
}

export function renderWalkForwardTests(items) {
  $("#walkForwardTestList").innerHTML = items.length ? items.map((item) => `
    <article class="research-card ${item.pass_fail}">
      <strong>${item.factor_name} · 滚动 ${item.pass_fail}</strong>
      <span>${item.window_count} 窗口 · 通过率 ${item.pass_rate.toFixed(1)}% · 样本外均值 ${item.average_forward_return > 0 ? "+" : ""}${item.average_forward_return.toFixed(2)}% · 稳定性 ${item.stability_score}/100</span>
      <div class="walk-window-grid">
        ${item.windows.map((windowItem) => `
          <small class="${windowItem.pass_fail}">W${windowItem.window} ${windowItem.forward_return > 0 ? "+" : ""}${windowItem.forward_return.toFixed(2)}% · 回撤 ${windowItem.max_drawdown.toFixed(2)}% · ${windowItem.pass_fail}</small>
        `).join("")}
      </div>
      ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
  `).join("") : `<article class="research-card"><span>暂无滚动样本外验证。运行后会按多个训练/验证窗口检查稳定性。</span></article>`;
}

export function renderWalkForwardLeaderboard(report) {
  $("#walkForwardLeaderboard").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>滚动样本外排行榜</strong>
      <span>${report.summary}</span>
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>#${item.rank} ${item.symbol} · ${item.factor_name}</strong>
        <span>${item.score}/100 · ${item.recommendation} · 通过率 ${item.pass_rate.toFixed(1)}% · 稳定性 ${item.stability_score}/100</span>
        <small>${item.window_count} 窗口 · 均值 ${item.average_forward_return > 0 ? "+" : ""}${item.average_forward_return.toFixed(2)}% · 最差 ${item.worst_forward_return > 0 ? "+" : ""}${item.worst_forward_return.toFixed(2)}% · 回撤 ${item.max_drawdown.toFixed(2)}%</small>
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无滚动样本外排行榜。</span></article>`;
}

export function renderStressTests(items) {
  $("#stressTestList").innerHTML = items.length ? items.map((item) => `
    <article class="research-card ${item.pass_fail}">
      <strong>${item.factor_name} · 压力 ${item.pass_fail}</strong>
      <span>基础 ${item.base_return > 0 ? "+" : ""}${item.base_return.toFixed(2)}% · 最差 ${item.worst_return > 0 ? "+" : ""}${item.worst_return.toFixed(2)}% · 最大压力回撤 ${item.worst_drawdown.toFixed(2)}%</span>
      ${item.scenarios.map((scenario) => `
        <small>${scenario.name}：${scenario.stressed_return > 0 ? "+" : ""}${scenario.stressed_return.toFixed(2)}% / 回撤 ${scenario.stressed_drawdown.toFixed(2)}% · ${scenario.pass_fail}</small>
      `).join("")}
      ${item.actions.map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
  `).join("") : `<article class="research-card"><span>暂无压力测试。运行后会模拟风险偏好、波动和流动性冲击。</span></article>`;
}

export function renderStressLeaderboard(report) {
  $("#stressLeaderboard").innerHTML = report ? `
    <article class="research-card ${report.status}">
      <strong>压力测试排行榜</strong>
      <span>${report.summary}</span>
    </article>
    ${report.items.map((item) => `
      <article class="research-card ${item.status}">
        <strong>#${item.rank} ${item.symbol} · ${item.factor_name}</strong>
        <span>${item.score}/100 · ${item.recommendation} · 最差 ${item.worst_return > 0 ? "+" : ""}${item.worst_return.toFixed(2)}% · 回撤 ${item.worst_drawdown.toFixed(2)}%</span>
        <small>基础 ${item.base_return > 0 ? "+" : ""}${item.base_return.toFixed(2)}% · 场景 ${item.pass_scenarios}/${item.scenario_count} 通过</small>
        ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
      </article>
    `).join("")}
  ` : `<article class="research-card"><span>暂无压力测试排行榜。</span></article>`;
}

export function renderSensitivityTest(item) {
  $("#sensitivityTestResult").innerHTML = item ? `
    <article class="research-card ${item.pass_fail}">
      <strong>${item.factor_name} · 敏感性 ${item.pass_fail}</strong>
      <span>收益离散 ${item.return_spread.toFixed(2)}% · 回撤离散 ${item.drawdown_spread.toFixed(2)}%</span>
      ${item.scenarios.map((scenario) => `
        <small>${scenario.name} ${scenario.interval} / ${scenario.fee_bps.toFixed(1)}bps：${scenario.total_return > 0 ? "+" : ""}${scenario.total_return.toFixed(2)}% · 回撤 ${scenario.max_drawdown.toFixed(2)}% · ${scenario.pass_fail}</small>
      `).join("")}
      ${item.actions.map((action) => `<small>动作：${action}</small>`).join("")}
    </article>
  ` : `<article class="research-card"><span>暂无参数敏感性测试。运行后会比较周期和手续费假设。</span></article>`;
}

export function renderRobustnessReport(report) {
  const statusText = { pass: "通过", watch: "观察", fail: "失败", missing: "缺失" };
  $("#robustnessMatrix").innerHTML = `
    <article class="robustness-summary ${report.status}">
      <div>
        <strong>稳健性矩阵 · ${statusText[report.status]}</strong>
        <span>${report.summary}</span>
      </div>
      <div class="score-pill ${report.status}">${report.regime.label} / ${report.regime.risk_level}</div>
    </article>
    <div class="robustness-grid">
      ${report.factors.map((item) => `
        <article class="robustness-card ${item.status}">
          <div class="robustness-card-head">
            <div>
              <strong>${item.factor_name}</strong>
              <span>${item.signal} · 分数 ${item.score}/100</span>
            </div>
            <span class="score-pill ${item.status}">${statusText[item.status]}</span>
          </div>
          <div class="check-grid">
            <span class="check-badge ${item.backtest_status}">回测 ${statusText[item.backtest_status]}</span>
            <span class="check-badge ${item.forward_status}">前向 ${statusText[item.forward_status]}</span>
            <span class="check-badge ${item.walk_forward_status}">滚动 ${statusText[item.walk_forward_status]}</span>
            <span class="check-badge ${item.stress_status}">压力 ${statusText[item.stress_status]}</span>
            <span class="check-badge ${item.regime_status}">状态 ${statusText[item.regime_status]}</span>
          </div>
          ${item.notes.slice(0, 2).map((note) => `<small>${note}</small>`).join("")}
          ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
        </article>
      `).join("")}
    </div>
  `;
}

export function renderStrategyDrift(report) {
  const statusText = { pass: "可控", watch: "观察", fail: "漂移" };
  $("#strategyDrift").innerHTML = `
    <article class="drift-summary ${report.status}">
      <div>
        <strong>策略漂移 ${report.score}/100 · ${statusText[report.status]}</strong>
        <span>${report.summary}</span>
        <small>市场状态 ${report.regime.label} / ${report.regime.risk_level}</small>
      </div>
    </article>
    <div class="drift-grid">
      ${report.factors.map((item) => `
        <article class="drift-card ${item.status}">
          <div class="robustness-card-head">
            <div>
              <strong>${item.factor_name}</strong>
              <span>漂移分 ${item.drift_score}/100 · 状态 ${statusText[item.status]}</span>
            </div>
            <span class="score-pill ${item.regime_alignment}">状态 ${item.regime_alignment}</span>
          </div>
          <div class="drift-metrics">
            <span>基准 ${item.baseline_return === null ? "--" : `${item.baseline_return > 0 ? "+" : ""}${item.baseline_return.toFixed(2)}%`}</span>
            <span>前向 ${item.forward_return === null ? "--" : `${item.forward_return > 0 ? "+" : ""}${item.forward_return.toFixed(2)}%`}</span>
            <span>衰减 ${item.return_degradation === null ? "--" : `${item.return_degradation > 0 ? "+" : ""}${item.return_degradation.toFixed(2)}pp`}</span>
            <span>滚动 ${item.walk_forward_pass_rate === null ? "--" : `${item.walk_forward_pass_rate.toFixed(1)}%`}</span>
          </div>
          ${item.evidence.slice(0, 3).map((evidence) => `<small>${evidence}</small>`).join("")}
          ${item.actions.slice(0, 2).map((action) => `<small>动作：${action}</small>`).join("")}
        </article>
      `).join("")}
    </div>
  `;
}

export function renderStrategyAllocation(report) {
  const statusText = { pass: "可执行", watch: "观察", fail: "暂停" };
  const actionText = { increase: "增加", hold: "持有", reduce: "降低", avoid: "规避" };
  $("#strategyAllocation").innerHTML = `
    <article class="allocation-summary ${report.status}">
      <div>
        <strong>策略配置 ${report.risk_budget.toFixed(1)}% 预算 · ${statusText[report.status]}</strong>
        <span>${report.summary}</span>
        <small>目标权重合计 ${report.total_target_weight.toFixed(1)}% · 市场 ${report.regime.label} / ${report.regime.risk_level}</small>
      </div>
    </article>
    <div class="allocation-grid">
      ${report.items.map((item) => `
        <article class="allocation-card ${item.status}">
          <div class="allocation-head">
            <div>
              <strong>${item.factor_name}</strong>
              <span>${actionText[item.status]} · 信心 ${item.confidence}/100</span>
            </div>
            <div class="allocation-weight">
              <strong>${item.target_weight.toFixed(1)}%</strong>
              <span>上限 ${item.max_weight.toFixed(1)}%</span>
            </div>
          </div>
          ${item.rationale.slice(0, 3).map((line) => `<small>${line}</small>`).join("")}
          ${item.controls.slice(0, 2).map((line) => `<small>控制：${line}</small>`).join("")}
        </article>
      `).join("")}
    </div>
    ${report.portfolio_notes.slice(0, 3).map((note) => `<small class="allocation-note">${note}</small>`).join("")}
    ${report.actions.slice(0, 2).map((action) => `<small class="allocation-note">动作：${action}</small>`).join("")}
  `;
}

export function renderTradeReadiness(report) {
  const decisionText = { go: "可小规模模拟", size_down: "降权模拟", hold: "继续观察", block: "阻断" };
  $("#tradeReadiness").innerHTML = report ? `
    <article class="trade-readiness-summary ${report.status}">
      <div>
        <strong>交易门禁 ${report.score}/100 · ${decisionText[report.decision]}</strong>
        <span>${report.summary}</span>
        <small>建议权重 ${report.recommended_weight.toFixed(1)}% · 上限 ${report.max_weight.toFixed(1)}% · 因子 ${report.factor_name}</small>
      </div>
      <div class="score-pill ${report.status}">${report.status}</div>
    </article>
    <div class="trade-readiness-grid">
      ${report.checks.map((item) => `
        <article class="trade-readiness-card ${item.status}">
          <strong>${item.name}</strong>
          <span>${item.detail}</span>
          <small>动作：${item.action}</small>
        </article>
      `).join("")}
    </div>
    ${report.blockers.slice(0, 3).map((blocker) => `<small class="trade-readiness-note">阻断：${blocker}</small>`).join("")}
    ${report.warnings.slice(0, 3).map((warning) => `<small class="trade-readiness-note">观察：${warning}</small>`).join("")}
    ${report.actions.slice(0, 3).map((action) => `<small class="trade-readiness-note">下一步：${action}</small>`).join("")}
  ` : `<article class="trade-readiness-summary"><span>暂无交易门禁结果。</span></article>`;
}

export function renderRegimeRiskGate(report) {
  const decisionText = { open: "开放", reduced: "降权", paused: "暂停", blocked: "阻断" };
  $("#regimeRiskGate").innerHTML = report ? `
    <article class="trade-readiness-summary ${report.status}">
      <div>
        <strong>状态风险闸门 ${report.score}/100 · ${decisionText[report.decision]}</strong>
        <span>${report.summary}</span>
        <small>允许权重 ${report.allowed_weight.toFixed(1)}% · 市场 ${report.regime.label} / ${report.regime.risk_level} · 迁移 ${report.transition.transition_score}/100</small>
      </div>
      <div class="score-pill ${report.status}">${report.status}</div>
    </article>
    <div class="trade-readiness-grid">
      ${report.checks.map((item) => `
        <article class="trade-readiness-card ${item.status}">
          <strong>${item.name}</strong>
          <span>${item.detail}</span>
          <small>动作：${item.action}</small>
        </article>
      `).join("")}
    </div>
    ${report.blockers.slice(0, 3).map((blocker) => `<small class="trade-readiness-note">阻断：${blocker}</small>`).join("")}
    ${report.actions.slice(0, 3).map((action) => `<small class="trade-readiness-note">下一步：${action}</small>`).join("")}
  ` : `<article class="trade-readiness-summary"><span>暂无状态风险闸门。</span></article>`;
}

export function renderFactorAdmission(report) {
  $("#factorAdmission").innerHTML = report ? `
    <article class="admission-summary ${report.status}">
      <strong>因子准入 ${report.score}/100 · ${report.status}</strong>
      <span>${report.summary}</span>
      ${report.blockers.slice(0, 2).map((blocker) => `<small>阻断：${blocker}</small>`).join("")}
    </article>
    <div class="admission-grid">
      ${report.checks.map((item) => `
        <article class="admission-card ${item.status}">
          <strong>${item.name}</strong>
          <span>${item.detail}</span>
          <small>动作：${item.action}</small>
        </article>
      `).join("")}
    </div>
    ${report.actions.slice(0, 3).map((action) => `<small class="admission-action">下一步：${action}</small>`).join("")}
  ` : `<article class="admission-summary"><span>暂无因子准入包。</span></article>`;
}

export function renderFactorLeaderboard(leaderboard) {
  $("#factorLeaderboard").innerHTML = `
    <article class="leaderboard-summary">
      <strong>因子排行榜</strong>
      <span>${leaderboard.summary}</span>
    </article>
    <div class="leaderboard-list">
      ${leaderboard.items.map((item) => `
        <article class="leaderboard-row ${item.status}">
          <div class="leader-rank">#${item.rank}</div>
          <div class="leader-main">
            <strong>${item.factor_name}</strong>
            <span>${item.recommendation} · 综合分 ${item.composite_score}/100 · 稳健性 ${item.robustness_score}/100</span>
            <small>${item.reasons.slice(0, 3).join(" · ")}</small>
          </div>
          <div class="leader-metrics">
            <span>回测 ${item.backtest_return === null ? "--" : `${item.backtest_return > 0 ? "+" : ""}${item.backtest_return.toFixed(2)}%`}</span>
            <span>前向 ${item.forward_return === null ? "--" : `${item.forward_return > 0 ? "+" : ""}${item.forward_return.toFixed(2)}%`}</span>
            <span>压力 ${item.stress_worst_return === null ? "--" : `${item.stress_worst_return > 0 ? "+" : ""}${item.stress_worst_return.toFixed(2)}%`}</span>
          </div>
        </article>
      `).join("")}
    </div>
  `;
}

export function renderFactorCorrelation(report) {
  const statusText = { pass: "分散", watch: "观察", fail: "拥挤" };
  $("#factorCorrelation").innerHTML = `
    <article class="correlation-summary ${report.status}">
      <strong>因子相关性 ${report.crowding_score}/100 · ${statusText[report.status]}</strong>
      <span>${report.summary}</span>
      <small>最高相关 ${report.max_correlation.toFixed(2)} · 市场 ${report.regime.label}</small>
    </article>
    <div class="correlation-grid">
      ${report.pairs.slice(0, 4).map((item) => `
        <article class="correlation-card ${item.status}">
          <strong>${item.factor_a_name} / ${item.factor_b_name}</strong>
          <span>相关 ${item.correlation.toFixed(2)} · 重叠 ${item.overlap_rate.toFixed(1)}%</span>
        </article>
      `).join("")}
    </div>
    <div class="exposure-strip">
      ${report.exposures.map((item) => `<span>${item.factor_name} ${item.exposure_rate.toFixed(1)}%</span>`).join("")}
    </div>
    ${report.actions.map((action) => `<small class="correlation-action">动作：${action}</small>`).join("")}
  `;
}

export function renderRegimeFactorFit(report) {
  const statusText = { pass: "优先", watch: "观察", fail: "暂停" };
  const fitText = { aligned: "状态匹配", neutral: "中性匹配", misaligned: "状态失配" };
  $("#regimeFactorFit").innerHTML = `
    <article class="leaderboard-summary ${report.status}">
      <strong>状态因子适配榜 · ${statusText[report.status]}</strong>
      <span>${report.summary}</span>
      <small>当前状态 ${report.regime.label} · 风险 ${report.regime.risk_level}</small>
    </article>
    <div class="leaderboard-list">
      ${report.items.map((item) => `
        <article class="leaderboard-row ${item.status}">
          <div class="leader-rank">#${item.rank}</div>
          <div class="leader-main">
            <strong>${item.factor_name}</strong>
            <span>${item.recommendation} · ${fitText[item.regime_fit]} · 适配分 ${item.score}/100</span>
            <small>${item.reasons.slice(0, 2).join(" · ")}</small>
            <small>动作：${item.actions.slice(0, 2).join(" · ")}</small>
          </div>
          <div class="leader-metrics">
            <span>榜单 ${item.leaderboard_score}/100</span>
            <span>稳健 ${item.robustness_score}/100</span>
            <span>最佳 ${item.best_regime || "--"}</span>
          </div>
        </article>
      `).join("")}
    </div>
  `;
}

export function renderRegimeAttribution(report) {
  const statusText = { pass: "适配", watch: "观察", fail: "失配" };
  $("#regimeAttribution").innerHTML = `
    <article class="attribution-summary ${report.status}">
      <strong>市场状态归因 · ${statusText[report.status]}</strong>
      <span>${report.summary}</span>
      <small>最佳 ${report.best_regime || "--"} · 最弱 ${report.worst_regime || "--"} · ${report.interval}</small>
    </article>
    <div class="attribution-grid">
      ${report.slices.map((item) => `
        <article class="attribution-card ${item.status}">
          <strong>${item.regime_label}</strong>
          <span>${item.total_return > 0 ? "+" : ""}${item.total_return.toFixed(2)}% · 回撤 ${item.max_drawdown.toFixed(2)}%</span>
          <small>样本 ${item.bars} · 暴露 ${item.exposure_rate.toFixed(1)}% · 胜率 ${item.win_rate.toFixed(1)}%</small>
        </article>
      `).join("")}
    </div>
    ${report.actions.map((action) => `<small class="attribution-action">动作：${action}</small>`).join("")}
  `;
}

export function renderParameterOptimization(report) {
  $("#parameterOptimizationResult").innerHTML = report ? `
    <article class="optimization-summary ${report.status}">
      <strong>参数优化 · ${report.status}</strong>
      <span>${report.summary}</span>
      ${report.best ? `<small>最佳 RSI >= ${report.best.min_rsi.toFixed(0)} · 趋势 >= ${report.best.min_trend} · 收益 ${report.best.total_return > 0 ? "+" : ""}${report.best.total_return.toFixed(2)}%</small>` : ""}
    </article>
    <div class="optimization-grid">
      ${report.results.map((item) => `
        <article class="optimization-card ${item.status}">
          <strong>#${item.rank} RSI ${item.min_rsi.toFixed(0)} / 趋势 ${item.min_trend}</strong>
          <span>分数 ${item.score}/100 · 收益 ${item.total_return > 0 ? "+" : ""}${item.total_return.toFixed(2)}% · 回撤 ${item.max_drawdown.toFixed(2)}%</span>
          <small>交易 ${item.trades} · 暴露 ${item.exposure_rate.toFixed(1)}%</small>
        </article>
      `).join("")}
    </div>
    ${report.actions.map((action) => `<small class="optimization-action">动作：${action}</small>`).join("")}
  ` : `<article class="optimization-summary"><span>暂无参数优化结果。</span></article>`;
}
