const state = {
  currentUser: null,
  csrfToken: "",
  availableSymbols: [],
  symbols: [],
  filtered: [],
  activeSymbol: "AAPL",
  interval: "15m",
  candles: [],
  news: [],
  chartEvents: [],
  eventMarkers: [],
  aiInsights: [],
  aiBriefing: null,
  factors: [],
  factorDefinitions: [],
  backtests: [],
  newsSources: [],
  marketProvider: null,
  providerOptions: [],
  alerts: [],
  alertEvaluations: [],
  events: [],
  automationRuns: [],
  automationSettings: null,
  appSettings: null,
  notificationChannels: [],
  notificationDeliveries: [],
  portfolio: null,
  portfolioRisk: null,
  health: null,
  marketBreadth: null,
  screener: null,
  screenerPresets: [],
  screenerPresetValidations: [],
  dataCatalog: [],
  researchHealth: null,
  researchDashboard: null,
  dataQuality: null,
  dataFreshness: null,
  strategyTemplates: [],
  strategyValidations: [],
  strategyValidationDecay: null,
  strategyLeaderboard: null,
  strategyRegimeFit: null,
  strategyRegimeMatrix: null,
  strategyCrowding: null,
  strategyLifecycle: null,
  strategyRiskControls: null,
  marketStateControls: null,
  strategyOperatingPlan: null,
  strategyEvidencePacks: null,
  strategyBacktestMonitor: null,
  strategyForwardMonitor: null,
  strategySensitivity: null,
  strategyRevalidationPlan: null,
  strategyValidationCoverage: null,
  strategyPromotionGates: [],
  researchReadiness: null,
  researchCoverage: null,
  factorCoverage: null,
  factorLifecycle: null,
  dataLineage: null,
  researchRunAudit: null,
  researchActionPlan: null,
  strategyCandidates: null,
  admissionQueue: null,
  revalidationQueue: null,
  marketRegime: null,
  marketRegimeHistory: [],
  marketRegimeTransition: null,
  regimeScenarioRisk: null,
  regimeExposure: null,
  regimePlaybook: null,
  filterValidation: null,
  filterContribution: null,
  filterAdmission: null,
  filterStressTest: null,
  filterLibraryScore: null,
  filterStrategyCoverage: null,
  regimeFilterTemplates: [],
  forwardTests: [],
  forwardLeaderboard: null,
  walkForwardTests: [],
  walkForwardLeaderboard: null,
  stressTests: [],
  stressLeaderboard: null,
  sensitivityTest: null,
  backtestBenchmark: null,
  backtestForwardConsistency: null,
  robustnessReport: null,
  strategyDrift: null,
  strategyAllocation: null,
  tradeReadiness: null,
  regimeRiskGate: null,
  factorLeaderboard: null,
  factorCorrelation: null,
  regimeFactorFit: null,
  regimeAttribution: null,
  parameterOptimization: null,
  factorAdmission: null,
  marketStream: null,
  streamReconnectTimer: null,
};

const signalText = {
  strong_buy: "强买入",
  buy: "买入",
  neutral: "中性",
  sell: "卖出",
  strong_sell: "强卖出",
};

const signalClass = {
  strong_buy: "up",
  buy: "up",
  neutral: "neutral",
  sell: "down",
  strong_sell: "down",
};

const $ = (selector) => document.querySelector(selector);

const sentimentClass = {
  bullish: "up",
  bearish: "down",
  neutral: "flat",
  mixed: "watch",
};

const severityClass = {
  info: "neutral",
  watch: "watch",
  risk: "down",
  opportunity: "up",
};

const riskClass = {
  risk: "down",
  watch: "watch",
  neutral: "neutral",
  constructive: "up",
};

const riskText = {
  risk: "Risk",
  watch: "Watch",
  neutral: "Neutral",
  constructive: "Constructive",
};

const viewConfig = {
  market: { title: "行情", modules: ["market"], focus: "marketPanel", wide: false },
  news: { title: "新闻", modules: ["news"], focus: "newsPanel", wide: true },
  ai: { title: "AI盯盘", modules: ["ai"], focus: "aiPanel", wide: true },
  factors: { title: "因子", modules: ["factors"], focus: "factorSignalPanel", wide: true },
  backtest: { title: "回测", modules: ["backtest"], focus: "backtestPanel", wide: true },
  alerts: { title: "预警", modules: ["alerts"], focus: "alertPanel", wide: true },
};

async function fetchJson(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && state.csrfToken) headers.set("X-CSRF-Token", state.csrfToken);
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Authentication required");
  }
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Request failed: ${url} ${detail}`);
  }
  return response.json();
}

async function loadCurrentUser() {
  const session = await fetchJson("/api/auth/me");
  state.currentUser = session.user;
  state.csrfToken = session.csrf_token;
  const label = document.querySelector("#currentUserLabel");
  if (label) label.textContent = `${session.user.display_name} · ${session.user.role === "admin" ? "管理员" : "只读"}`;
  document.body.classList.toggle("viewer-mode", session.user.role !== "admin");
}

async function logout() {
  await fetchJson("/api/auth/logout", { method: "POST" });
  window.location.href = "/login";
}

function formatPrice(value) {
  return value > 1000 ? value.toLocaleString("en-US", { maximumFractionDigits: 0 }) : value.toFixed(2);
}

function applyFilters() {
  const query = $("#searchInput").value.trim().toUpperCase();
  const signal = $("#signalFilter").value;
  const rsiMin = Number($("#rsiFilter").value);
  const positiveOnly = $("#positiveOnly").checked;
  state.filtered = state.symbols.filter((item) => {
    const matchesQuery = !query || item.symbol.includes(query) || item.name.toUpperCase().includes(query);
    const matchesSignal = signal === "all" || item.signal === signal;
    const matchesRsi = item.rsi >= rsiMin;
    const matchesPositive = !positiveOnly || item.change_percent > 0;
    return matchesQuery && matchesSignal && matchesRsi && matchesPositive;
  });
  renderWatchlist();
  renderMarketStrip();
}

function renderMarketStrip() {
  const source = state.filtered.length ? state.filtered : state.symbols;
  $("#marketStrip").innerHTML = source.slice(0, 4).map((item) => {
    const direction = item.change_percent >= 0 ? "up" : "down";
    return `
      <button class="ticker-tile" type="button" data-symbol="${item.symbol}">
        <strong>${item.symbol}</strong>
        <span>${item.name}</span>
        <b>${formatPrice(item.price)}</b>
        <em class="${direction}">${item.change_percent > 0 ? "+" : ""}${item.change_percent.toFixed(2)}%</em>
      </button>
    `;
  }).join("");
}

function renderWatchlist() {
  $("#matchCount").textContent = `${state.filtered.length} 个标的`;
  $("#symbolOptions").innerHTML = state.availableSymbols.map((item) => `
    <option value="${item.symbol}">${item.name} · ${item.market}</option>
  `).join("");
  $("#watchlistBody").innerHTML = state.filtered.map((item) => {
    const direction = item.change_percent >= 0 ? "up" : "down";
    const active = item.symbol === state.activeSymbol ? "active" : "";
    return `
      <tr class="${active}" data-symbol="${item.symbol}">
        <td><strong>${item.symbol}</strong><br><span class="flat">${item.name}</span></td>
        <td>${item.market}</td>
        <td>${formatPrice(item.price)}</td>
        <td class="${direction}">${item.change_percent > 0 ? "+" : ""}${item.change_percent.toFixed(2)}%</td>
        <td>${item.rsi.toFixed(1)}</td>
        <td class="${item.macd >= 0 ? "up" : "down"}">${item.macd.toFixed(2)}</td>
        <td><span class="badge ${signalClass[item.signal]}">${signalText[item.signal]}</span></td>
        <td><button class="icon-action" type="button" data-watchlist-remove="${item.symbol}" title="停用自选">×</button></td>
      </tr>
    `;
  }).join("");
}

function renderMarketBreadth(breadth) {
  $("#breadthBadge").textContent = riskText[breadth.risk_level] || "--";
  $("#breadthBadge").className = `badge ${riskClass[breadth.risk_level] || "neutral"}`;
  const changeClass = breadth.average_change >= 0 ? "up" : "down";
  $("#breadthPanel").innerHTML = `
    <div class="breadth-metric">
      <span>A/D</span>
      <strong>${breadth.advancers}/${breadth.decliners}</strong>
      <small>Ratio ${breadth.advance_decline_ratio.toFixed(2)}</small>
    </div>
    <div class="breadth-metric">
      <span>均涨跌</span>
      <strong class="${changeClass}">${breadth.average_change > 0 ? "+" : ""}${breadth.average_change.toFixed(2)}%</strong>
      <small>${breadth.total} 个自选</small>
    </div>
    <div class="breadth-metric">
      <span>均 RSI</span>
      <strong>${breadth.average_rsi.toFixed(1)}</strong>
      <small>趋势 ${breadth.average_trend_score.toFixed(1)}</small>
    </div>
    <div class="breadth-metric">
      <span>信号</span>
      <strong>${breadth.strong_buy_count + breadth.buy_count}/${breadth.sell_count + breadth.strong_sell_count}</strong>
      <small>多/空</small>
    </div>
  `;
  $("#breadthHeatmap").innerHTML = breadth.groups.length ? breadth.groups.map((group) => {
    const className = group.average_change >= 0 ? "up" : "down";
    const width = Math.max(8, Math.min(100, Math.abs(group.average_change) * 24 + 10));
    return `
      <article class="breadth-group ${group.risk_level}">
        <div>
          <strong>${group.name}</strong>
          <span>${group.advancers}/${group.decliners} · RSI ${group.average_rsi.toFixed(1)}</span>
        </div>
        <b class="${className}">${group.average_change > 0 ? "+" : ""}${group.average_change.toFixed(2)}%</b>
        <i class="${className}" style="width: ${width}%"></i>
      </article>
    `;
  }).join("") : `<article class="breadth-group"><span>暂无自选数据</span></article>`;
}

function renderScreener(result) {
  $("#screenerCount").textContent = `${result.total} 个`;
  $("#screenerList").innerHTML = result.items.length ? result.items.map((item) => {
    const snapshot = item.snapshot;
    const direction = snapshot.change_percent >= 0 ? "up" : "down";
    return `
      <article class="screener-item" data-symbol="${snapshot.symbol}">
        <div>
          <strong>${snapshot.symbol}</strong>
          <span>${snapshot.name} · ${snapshot.market}</span>
        </div>
        <b class="${direction}">${snapshot.change_percent > 0 ? "+" : ""}${snapshot.change_percent.toFixed(2)}%</b>
        <small>${item.reasons.join(" · ")}</small>
      </article>
    `;
  }).join("") : `<article class="screener-item"><span>暂无命中标的。</span></article>`;
}

function renderMarketRegime(regime, history = []) {
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

function renderMarketRegimeTransition(report) {
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

function renderRegimeScenarioRisk(report) {
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

function renderRegimeExposure(report) {
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

function renderRegimePlaybook(playbook) {
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

function renderFilterValidation(result) {
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

function renderFilterContribution(report) {
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

function renderFilterAdmission(report) {
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

function renderFilterStressTest(report) {
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

function renderDataCatalog(items) {
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

function renderDataQuality(report) {
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

function renderDataFreshness(report) {
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

function renderResearchHealth(report) {
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

function renderResearchDashboard(summary) {
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

function renderResearchReadiness(readiness) {
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

function renderResearchCoverage(report) {
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

function renderFactorCoverage(report) {
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

function renderFactorLifecycle(report) {
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

function renderDataLineage(report) {
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

function renderResearchActionPlan(plan) {
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

function renderStrategyCandidates(report) {
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

function renderAdmissionQueue(report) {
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

function renderRevalidationQueue(report) {
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

function renderResearchRunAudit(report) {
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

function renderStrategyTemplates(items, gates = []) {
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

function renderStrategyValidations(items) {
  $("#strategyValidationList").innerHTML = items.length ? items.map((item) => `
    <article class="research-card ${item.pass_fail}">
      <strong>${item.strategy_name} · ${item.pass_fail}</strong>
      <span>${item.symbols.join(", ")} · 回测均值 ${item.average_backtest_return > 0 ? "+" : ""}${item.average_backtest_return.toFixed(2)}% · 前向均值 ${item.average_forward_return > 0 ? "+" : ""}${item.average_forward_return.toFixed(2)}%</span>
      <small>市场：${item.regime.label} / ${item.regime.risk_level}</small>
      ${item.checks.map((check) => `<small>${check}</small>`).join("")}
    </article>
  `).join("") : `<article class="research-card"><span>暂无策略验证记录。</span></article>`;
}

function renderStrategyValidationDecay(report) {
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

function renderStrategyLeaderboard(leaderboard) {
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

function renderStrategyRegimeFit(report) {
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

function renderStrategyRegimeMatrix(report) {
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

function renderStrategyCrowding(report) {
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

function renderStrategyLifecycle(report) {
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

function renderStrategyRiskControls(report) {
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

function renderMarketStateControls(report) {
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

function renderStrategyOperatingPlan(report) {
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

function renderStrategyEvidencePacks(report) {
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

function renderStrategyBacktestMonitor(report) {
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

function renderStrategyForwardMonitor(report) {
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

function renderStrategySensitivity(report) {
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

function renderStrategyRevalidationPlan(report) {
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

function renderStrategyValidationCoverage(report) {
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

function renderScreenerPresets(items, validations = []) {
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

function renderFilterLibraryScore(report) {
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

function renderFilterStrategyCoverage(report) {
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

function renderRegimeFilterTemplates(items) {
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

function renderIndicators(items) {
  $("#indicatorGrid").innerHTML = items.map((item) => `
    <article class="indicator-card">
      <span>${item.label}</span>
      <b class="${item.state}">${item.value}</b>
      <small>${item.detail}</small>
    </article>
  `).join("");
}

function renderAiInsights(items) {
  $("#aiFeed").innerHTML = items.map((item) => `
    <article class="insight-card">
      <strong>${item.title}</strong>
      <p>${item.summary}</p>
      <div class="meta-row">
        <span class="mini-badge ${severityClass[item.severity]}">${item.agent}</span>
        <span>置信度 ${item.confidence}%</span>
      </div>
    </article>
  `).join("");
}

function renderAiBriefing(briefing) {
  const badgeClass = severityClass[briefing.risk_level] || "neutral";
  $("#aiBriefing").innerHTML = `
    <article>
      <div class="meta-row">
        <span class="mini-badge ${badgeClass}">${riskText[briefing.risk_level] || briefing.risk_level}</span>
        <span>${formatTime(briefing.generated_at)}</span>
      </div>
      <strong>${briefing.title}</strong>
      <p>${briefing.summary}</p>
      <div class="briefing-columns">
        <div>
          <span>机会</span>
          ${briefing.opportunities.map((item) => `<small>${item}</small>`).join("")}
        </div>
        <div>
          <span>风险</span>
          ${briefing.risks.map((item) => `<small>${item}</small>`).join("")}
        </div>
      </div>
      <div class="briefing-actions">
        ${briefing.actions.map((item) => `<small>${item}</small>`).join("")}
      </div>
    </article>
  `;
}

function renderFactors(items) {
  $("#factorCount").textContent = `${items.length}`;
  $("#factorList").innerHTML = items.map((item) => `
    <article class="factor-card">
      <strong>${item.name}</strong>
      <p>${item.description}</p>
      <div class="meta-row">
        <span class="mini-badge ${item.signal === "long" ? "up" : item.signal === "short" ? "down" : ""}">${item.category}</span>
        <span>${item.horizon}</span>
        <span>评分 ${item.score}</span>
      </div>
    </article>
  `).join("");
}

function renderFactorDefinitions(items) {
  const enabled = items.filter((item) => item.enabled);
  $("#backtestFactor").innerHTML = enabled.map((item) => `
    <option value="${item.id}">${item.name}</option>
  `).join("");
  $("#forwardFactor").innerHTML = enabled.map((item) => `
    <option value="${item.id}">${item.name}</option>
  `).join("");
  $("#walkForwardFactor").innerHTML = enabled.map((item) => `
    <option value="${item.id}">${item.name}</option>
  `).join("");
  $("#stressFactor").innerHTML = enabled.map((item) => `
    <option value="${item.id}">${item.name}</option>
  `).join("");
  $("#strategyFactor").innerHTML = enabled.map((item) => `
    <option value="${item.id}">${item.name}</option>
  `).join("");
  $("#factorDefinitionList").innerHTML = items.map((item) => `
    <article class="factor-definition-item ${item.enabled ? "" : "disabled"}">
      <div>
        <strong>${item.name}</strong>
        <span>${item.category} · ${item.signal} · ${item.horizon}</span>
      </div>
      <button class="text-button" type="button" data-factor-toggle="${item.id}" data-factor-enabled="${item.enabled ? "0" : "1"}">
        ${item.enabled ? "停用" : "启用"}
      </button>
    </article>
  `).join("");
}

function renderNews(items) {
  $("#newsCount").textContent = `${items.length} 条`;
  $("#newsTimeline").innerHTML = [...items].reverse().map((item) => `
    <article class="news-item ${item.sentiment}" data-news-id="${item.id}">
      <strong>${item.title}</strong>
      <p>${item.summary}</p>
      <div class="meta-row">
        <span class="mini-badge ${sentimentClass[item.sentiment]}">${item.sentiment}</span>
        <span>${item.source}</span>
        <span>影响 ${item.impact_score}</span>
        <span>${formatTime(item.published_at)}</span>
      </div>
    </article>
  `).join("");
}

function renderNewsDetail(detail) {
  $("#newsDrawerTitle").textContent = detail.title;
  $("#newsDrawerBody").innerHTML = `
    <div class="meta-row">
      <span class="mini-badge ${sentimentClass[detail.sentiment]}">${detail.sentiment}</span>
      <span>${detail.source}</span>
      <span>影响 ${detail.impact_score}</span>
      <span>${formatTime(detail.published_at)}</span>
    </div>
    <p>${detail.ai_summary}</p>
    <section class="drawer-section">
      <strong>关键点</strong>
      ${detail.key_points.map((item) => `<span>${item}</span>`).join("")}
    </section>
    <section class="drawer-section">
      <strong>关联标的</strong>
      <span>${detail.related_symbols.join(" / ")}</span>
    </section>
    <section class="drawer-section">
      <strong>原始摘要</strong>
      <span>${detail.detail}</span>
    </section>
    <a class="drawer-link" href="${detail.url}" target="_blank" rel="noopener noreferrer">打开原文</a>
  `;
}

async function openNewsDetail(newsId) {
  $("#newsDrawer").hidden = false;
  $("#newsDrawerTitle").textContent = "新闻详情";
  $("#newsDrawerBody").innerHTML = `<p>正在加载新闻详情...</p>`;
  const detail = await fetchJson(`/api/news-events/${newsId}`);
  renderNewsDetail(detail);
}

function closeNewsDetail() {
  $("#newsDrawer").hidden = true;
}

function exportDataset(dataset) {
  const params = new URLSearchParams();
  if (dataset === "backtests") params.set("symbol", state.activeSymbol);
  window.location.href = `/api/export/${dataset}${params.toString() ? `?${params.toString()}` : ""}`;
}

function activateNavItem(button) {
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item === button));
}

function setActiveView(viewName, options = {}) {
  const config = viewConfig[viewName] || viewConfig.market;
  const modules = new Set(config.modules);
  $("#viewTitle").textContent = config.title;
  $("#terminalGrid").classList.toggle("view-wide", config.wide);
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === viewName);
  });
  document.querySelectorAll("[data-module]").forEach((section) => {
    const sectionModules = section.dataset.module.split(/\s+/);
    const visible = sectionModules.some((item) => modules.has(item));
    section.classList.toggle("module-hidden", !visible);
  });
  if (options.updateHash !== false) history.replaceState(null, "", `#${viewName}`);
  if (options.focus !== false) {
    const target = document.getElementById(config.focus);
    if (target) {
      window.scrollTo({ top: 0, behavior: "smooth" });
      target.classList.remove("section-focus");
      void target.offsetWidth;
      target.classList.add("section-focus");
      window.setTimeout(() => target.classList.remove("section-focus"), 1400);
    }
  }
  window.setTimeout(drawChart, 80);
}

function renderBacktests(items) {
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

function renderBacktestBenchmark(report) {
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

function renderBacktestForwardConsistency(report) {
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

function renderForwardTests(items) {
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

function renderForwardLeaderboard(report) {
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

function renderWalkForwardTests(items) {
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

function renderWalkForwardLeaderboard(report) {
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

function renderStressTests(items) {
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

function renderStressLeaderboard(report) {
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

function renderSensitivityTest(item) {
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

function renderRobustnessReport(report) {
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

function renderStrategyDrift(report) {
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

function renderStrategyAllocation(report) {
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

function renderTradeReadiness(report) {
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

function renderRegimeRiskGate(report) {
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

function renderFactorAdmission(report) {
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

function renderFactorLeaderboard(leaderboard) {
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

function renderFactorCorrelation(report) {
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

function renderRegimeFactorFit(report) {
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

function renderRegimeAttribution(report) {
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

function renderParameterOptimization(report) {
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

function renderSources(items) {
  $("#sourceList").innerHTML = items.length ? items.map((item) => `
    <article class="source-item">
      <strong>${item.name}</strong>
      <span>${item.url}</span>
      <div class="meta-row">
        <span class="mini-badge ${item.enabled ? "up" : "down"}">${item.enabled ? "启用" : "停用"}</span>
        <span>${item.symbols.length ? item.symbols.join(", ") : "自动识别标的"}</span>
        <span>${item.last_error ? "抓取异常" : item.last_fetched_at ? `已刷新 ${formatTime(item.last_fetched_at)}` : "未刷新"}</span>
      </div>
    </article>
  `).join("") : `<div class="source-item"><span>暂无 RSS 源，可添加财经媒体或公司 IR RSS。</span></div>`;
}

function renderAlerts(items, evaluations = []) {
  const evalById = new Map(evaluations.map((item) => [item.rule.id, item]));
  $("#alertList").innerHTML = items.length ? items.map((item) => {
    const evaluation = evalById.get(item.id);
    const triggered = evaluation?.triggered;
    const metricText = { price: "价格", rsi: "RSI", news_impact: "新闻影响分" }[item.metric];
    return `
      <article class="alert-item ${triggered ? "triggered" : ""}">
        <strong>${item.name}</strong>
        <span>${item.symbol} · ${metricText} ${item.operator} ${Number(item.threshold).toFixed(2)}</span>
        <div class="meta-row">
          <span class="mini-badge ${triggered ? "watch" : item.enabled ? "up" : "down"}">${triggered ? "已触发" : item.enabled ? "启用" : "停用"}</span>
          ${evaluation ? `<span>当前 ${evaluation.current_value.toFixed(2)}</span>` : ""}
          ${item.last_triggered_at ? `<span>上次 ${formatTime(item.last_triggered_at)}</span>` : ""}
          <button class="text-button" type="button" data-alert-toggle="${item.id}" data-alert-enabled="${item.enabled ? "0" : "1"}">${item.enabled ? "停用" : "启用"}</button>
          <button class="text-button" type="button" data-alert-delete="${item.id}">停用</button>
        </div>
      </article>
    `;
  }).join("") : `<div class="alert-item"><span>暂无预警，可为当前标的添加价格、RSI 或新闻影响分规则。</span></div>`;
}

function renderEvents(items) {
  $("#eventLog").innerHTML = items.length ? items.map((item) => `
    <article class="event-item ${item.severity}">
      <strong>${item.title}</strong>
      <span>${item.message}</span>
      <div class="meta-row">
        <span class="mini-badge ${item.severity === "risk" || item.severity === "error" ? "down" : item.severity === "watch" ? "watch" : "up"}">${item.event_type}</span>
        ${item.symbol ? `<span>${item.symbol}</span>` : ""}
        <span>${formatTime(item.created_at)}</span>
      </div>
    </article>
  `).join("") : `<div class="event-item"><span>暂无事件。运行一键盯盘或触发预警后会记录在这里。</span></div>`;
}

function renderAutomationRuns(items) {
  $("#automationRuns").innerHTML = items.length ? items.slice(0, 5).map((item) => `
    <article class="automation-run-item ${item.status}">
      <strong>${item.status === "failed" ? "运行异常" : item.status === "warning" ? "触发预警" : "运行完成"}</strong>
      <span>${item.summary}</span>
      <div class="meta-row">
        <span>${formatTime(item.finished_at)}</span>
        <span>新闻 ${item.inserted_news}</span>
        <span>预警 ${item.triggered_alerts}</span>
      </div>
    </article>
  `).join("") : `<article class="automation-run-item"><span>暂无自动化运行记录。</span></article>`;
}

function renderNotificationChannels(channels, deliveries) {
  $("#deliveryCount").textContent = `${deliveries.length}`;
  const latestByChannel = new Map();
  deliveries.forEach((item) => {
    if (!latestByChannel.has(item.channel_id)) latestByChannel.set(item.channel_id, item);
  });
  $("#channelList").innerHTML = channels.length ? channels.map((item) => {
    const latest = latestByChannel.get(item.id);
    return `
      <article class="channel-item">
        <strong>${item.name}</strong>
        <span>${item.target}</span>
        <div class="meta-row">
          <span class="mini-badge ${item.enabled ? "up" : "down"}">${item.enabled ? "启用" : "停用"}</span>
          ${latest ? `<span>${latest.success ? "最近成功" : "最近失败"}</span>` : "<span>暂无投递</span>"}
          ${item.last_delivery_at ? `<span>${formatTime(item.last_delivery_at)}</span>` : ""}
          <button class="text-button" type="button" data-channel-toggle="${item.id}" data-channel-enabled="${item.enabled ? "0" : "1"}">${item.enabled ? "停用" : "启用"}</button>
          <button class="text-button" type="button" data-channel-delete="${item.id}">停用</button>
        </div>
      </article>
    `;
  }).join("") : `<div class="channel-item"><span>暂无通知渠道。添加 Webhook 后，预警触发会自动投递 JSON。</span></div>`;
}

function renderPortfolio(summary) {
  state.portfolio = summary;
  const pnlClass = summary.total_unrealized_pnl >= 0 ? "up" : "down";
  $("#portfolioPnl").innerHTML = `<span class="${pnlClass}">${summary.total_unrealized_pnl >= 0 ? "+" : ""}${summary.total_unrealized_pnl.toFixed(2)} (${summary.total_unrealized_pnl_percent.toFixed(2)}%)</span>`;
  $("#portfolioList").innerHTML = summary.positions.length ? summary.positions.map((item) => {
    const pnlClassName = item.unrealized_pnl >= 0 ? "up" : "down";
    return `
      <article class="position-item">
        <strong>${item.symbol} · ${item.side.toUpperCase()}</strong>
        <span>${item.quantity} @ ${item.average_price.toFixed(2)} · 现价 ${item.current_price === null ? "N/A" : item.current_price.toFixed(2)}</span>
        <div class="meta-row">
          <span class="mini-badge ${pnlClassName}">${item.unrealized_pnl >= 0 ? "+" : ""}${item.unrealized_pnl.toFixed(2)}</span>
          <span>市值 ${item.market_value.toFixed(2)}</span>
          ${item.stale_price ? "<span>价格过期</span>" : ""}
          <button class="text-button" type="button" data-position-id="${item.id}">停用</button>
        </div>
      </article>
    `;
  }).join("") : `<div class="position-item"><span>暂无模拟持仓。添加持仓后会按当前行情源估值。</span></div>`;
}

function renderPortfolioRisk(report) {
  const badgeClass = report.risk_level === "risk" ? "down" : report.risk_level === "watch" ? "watch" : "up";
  $("#portfolioRisk").innerHTML = `
    <article class="risk-summary">
      <div class="meta-row">
        <span class="mini-badge ${badgeClass}">${report.risk_level}</span>
        <span>净敞口 ${report.net_exposure_percent.toFixed(1)}%</span>
        <span>集中度 ${report.concentration_score.toFixed(1)}</span>
      </div>
      <strong>${report.largest_position_symbol ? `最大持仓 ${report.largest_position_symbol} · ${report.largest_position_weight.toFixed(1)}%` : "暂无持仓风险"}</strong>
      ${report.warnings.map((item) => `<span>${item}</span>`).join("")}
      <div class="risk-actions">
        ${report.actions.map((item) => `<small>${item}</small>`).join("")}
      </div>
    </article>
    ${report.contributions.map((item) => `
      <article class="risk-contribution ${item.risk_flag}">
        <strong>${item.symbol} · ${item.side.toUpperCase()}</strong>
        <span>权重 ${item.weight_percent.toFixed(1)}% · 市值 ${item.market_value.toFixed(2)} · PnL ${item.unrealized_pnl >= 0 ? "+" : ""}${item.unrealized_pnl.toFixed(2)}</span>
      </article>
    `).join("")}
  `;
}

function renderAutomationSettings(settings) {
  state.automationSettings = settings;
  $("#automationEnabled").checked = settings.enabled;
  $("#automationInterval").value = settings.interval_seconds;
  $("#automationStatus").textContent = settings.enabled
    ? `定时运行已开启，每 ${settings.interval_seconds} 秒执行一次`
    : "定时运行已关闭";
}

function formatTime(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function renderSnapshot() {
  const item = state.symbols.find((row) => row.symbol === state.activeSymbol);
  if (!item) return;
  $("#activeSymbol").textContent = `${item.symbol} · ${item.name}`;
  $("#lastPrice").textContent = formatPrice(item.price);
  $("#lastChange").textContent = `${item.change_percent > 0 ? "+" : ""}${item.change_percent.toFixed(2)}%`;
  $("#lastChange").className = item.change_percent >= 0 ? "up" : "down";
  $("#trendScore").textContent = item.trend_score;
  $("#signalBadge").textContent = signalText[item.signal];
  $("#signalBadge").className = `badge ${signalClass[item.signal]}`;
  renderWatchlist();
}

function renderProviderStatus(provider) {
  const realtime = provider.is_realtime ? "实时" : provider.is_delayed ? "延迟/模拟" : "未知";
  $("#providerStatus").textContent = `${provider.display_name} · ${realtime} · ${provider.status}`;
}

function renderStreamStatus(status, message) {
  const className = status === "online" ? "stream-online" : status === "connecting" ? "stream-connecting" : "stream-offline";
  $("#streamStatus").className = `provider-status ${className}`;
  $("#streamStatus").textContent = message;
}

function renderProviderOptions(items) {
  $("#providerSelect").innerHTML = items.map((item) => `
    <option value="${item.name}" ${item.is_active ? "selected" : ""}>${item.name}</option>
  `).join("");
}

function renderHealth(health) {
  const badgeText = {
    ok: "OK",
    warn: "Watch",
    error: "Risk",
  };
  const badgeClass = {
    ok: "up",
    warn: "watch",
    error: "down",
  };
  $("#healthBadge").textContent = badgeText[health.status] || "--";
  $("#healthBadge").className = `badge ${badgeClass[health.status] || "neutral"}`;
  $("#healthList").innerHTML = health.components.map((item) => `
    <article class="health-item ${item.status}">
      <span class="health-dot"></span>
      <div>
        <strong>${item.name}</strong>
        <span>${item.message}</span>
      </div>
    </article>
  `).join("");
}

function renderAppSettings(settings) {
  state.appSettings = settings;
  $("#settingsSummary").textContent = `${settings.active_market_provider} · ${settings.watchlist_symbols} 自选`;
  $("#settingsSignal").value = settings.filters.signal_filter;
  $("#settingsRsi").value = settings.filters.rsi_min;
  $("#settingsPositiveOnly").checked = settings.filters.positive_only;
  $("#settingsAutomationEnabled").checked = settings.automation.enabled;
  $("#settingsAutomationInterval").value = settings.automation.interval_seconds;
  $("#signalFilter").value = settings.filters.signal_filter;
  $("#rsiFilter").value = String(settings.filters.rsi_min);
  $("#rsiValue").textContent = String(settings.filters.rsi_min);
  $("#positiveOnly").checked = settings.filters.positive_only;
  applyFilters();
}

function drawChart() {
  const canvas = $("#priceChart");
  const context = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.floor(rect.width * ratio);
  canvas.height = Math.floor(rect.height * ratio);
  context.scale(ratio, ratio);

  const width = rect.width;
  const height = rect.height;
  const pad = { top: 16, right: 58, bottom: 34, left: 12 };
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#131820";
  context.fillRect(0, 0, width, height);

  const candles = state.candles;
  if (!candles.length) return;

  const values = candles.flatMap((item) => [item.high, item.low, item.ma20, item.ma60]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const xStep = plotWidth / candles.length;
  const y = (value) => pad.top + (max - value) / range * plotHeight;
  const x = (index) => pad.left + index * xStep + xStep / 2;

  context.strokeStyle = "#252b35";
  context.lineWidth = 1;
  context.font = "12px Inter, sans-serif";
  context.fillStyle = "#8b95a7";
  for (let i = 0; i <= 5; i += 1) {
    const yy = pad.top + plotHeight / 5 * i;
    context.beginPath();
    context.moveTo(pad.left, yy);
    context.lineTo(width - pad.right, yy);
    context.stroke();
    const label = max - range / 5 * i;
    context.fillText(label.toFixed(2), width - pad.right + 8, yy + 4);
  }

  drawLine(context, candles.map((item, index) => [x(index), y(item.ma20)]), "#f3ba2f", 1.4);
  drawLine(context, candles.map((item, index) => [x(index), y(item.ma60)]), "#00bcd4", 1.4);

  const candleWidth = Math.max(3, Math.min(10, xStep * 0.62));
  candles.forEach((item, index) => {
    const rising = item.close >= item.open;
    const color = rising ? "#22ab94" : "#f23645";
    const cx = x(index);
    context.strokeStyle = color;
    context.fillStyle = color;
    context.beginPath();
    context.moveTo(cx, y(item.high));
    context.lineTo(cx, y(item.low));
    context.stroke();
    const top = y(Math.max(item.open, item.close));
    const bottom = y(Math.min(item.open, item.close));
    context.fillRect(cx - candleWidth / 2, top, candleWidth, Math.max(1, bottom - top));
  });

  context.fillStyle = "#8b95a7";
  context.fillText("MA20", pad.left + 10, 18);
  context.fillStyle = "#f3ba2f";
  context.fillRect(pad.left + 48, 10, 20, 3);
  context.fillStyle = "#8b95a7";
  context.fillText("MA60", pad.left + 80, 18);
  context.fillStyle = "#00bcd4";
  context.fillRect(pad.left + 118, 10, 20, 3);

  drawEventMarkers(context, candles, { width, height, pad, min, max, range, plotWidth, plotHeight, xStep });
}

function drawLine(context, points, color, width) {
  context.strokeStyle = color;
  context.lineWidth = width;
  context.beginPath();
  points.forEach(([x, y], index) => {
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.stroke();
}

function drawEventMarkers(context, candles, chart) {
  state.eventMarkers = [];
  if (!state.chartEvents.length || !candles.length) return;

  const eventColors = {
    bullish: "#22ab94",
    bearish: "#f23645",
    neutral: "#8b95a7",
    mixed: "#f3ba2f",
  };
  const startTime = new Date(candles[0].time).getTime();
  const endTime = new Date(candles[candles.length - 1].time).getTime();
  const totalTime = Math.max(1, endTime - startTime);
  const baseline = chart.height - chart.pad.bottom + 14;

  state.chartEvents.forEach((event, order) => {
    const eventTime = new Date(event.published_at).getTime();
    const bounded = Math.max(startTime, Math.min(endTime, eventTime));
    const ratio = (bounded - startTime) / totalTime;
    const x = chart.pad.left + ratio * chart.plotWidth;
    const y = baseline - (order % 4) * 18;
    const color = eventColors[event.sentiment] || "#8b95a7";

    context.fillStyle = color;
    context.strokeStyle = "#11161d";
    context.lineWidth = 2;
    context.beginPath();
    context.arc(x, y, event.impact_score >= 75 ? 6 : 4.5, 0, Math.PI * 2);
    context.fill();
    context.stroke();

    state.eventMarkers.push({ x, y, radius: 10, event });
  });
}

function drawSparkline(canvas) {
  const values = canvas.dataset.equity.split(",").map(Number);
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.floor(rect.width * ratio);
  canvas.height = Math.floor(rect.height * ratio);
  const context = canvas.getContext("2d");
  context.scale(ratio, ratio);
  const width = rect.width;
  const height = rect.height;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  context.clearRect(0, 0, width, height);
  context.strokeStyle = values[values.length - 1] >= values[0] ? "#22ab94" : "#f23645";
  context.lineWidth = 1.8;
  context.beginPath();
  values.forEach((value, index) => {
    const x = index / (values.length - 1) * width;
    const y = height - (value - min) / range * (height - 4) - 2;
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.stroke();
}

function showEventTooltip(marker, clientX, clientY) {
  const tooltip = $("#eventTooltip");
  const chartRect = $("#priceChart").getBoundingClientRect();
  const maxLeft = Math.max(10, chartRect.width - 330);
  tooltip.hidden = false;
  tooltip.innerHTML = `
    <strong>${marker.event.title}</strong>
    <p>${marker.event.summary}</p>
    <div class="meta-row">
      <span>${marker.event.source}</span>
      <span>影响 ${marker.event.impact_score}</span>
      <span>${marker.event.price_reaction}</span>
    </div>
  `;
  const localX = clientX - chartRect.left;
  const localY = clientY - chartRect.top;
  tooltip.style.left = `${Math.min(Math.max(localX + 14, 10), maxLeft)}px`;
  tooltip.style.top = `${Math.max(localY - 96, 10)}px`;
}

function hideEventTooltip() {
  $("#eventTooltip").hidden = true;
}

async function selectSymbol(symbol) {
  state.activeSymbol = symbol;
  const activeFactor = $("#backtestFactor")?.value || "momentum-news";
  const [candles, indicators, news, chartEvents, aiInsights, factors, backtests, alerts, forwardTests, forwardLeaderboard, walkForwardTests, walkForwardLeaderboard, stressTests, stressLeaderboard, backtestBenchmark, backtestForwardConsistency, robustnessReport, strategyDrift, strategyAllocation, tradeReadiness, regimeRiskGate, factorLeaderboard, factorCorrelation, regimeFactorFit, regimeAttribution] = await Promise.all([
    fetchJson(`/api/candles/${symbol}?interval=${state.interval}`),
    fetchJson(`/api/indicators/${symbol}`),
    fetchJson(`/api/news/${symbol}`),
    fetchJson(`/api/chart-events/${symbol}`),
    fetchJson(`/api/ai/insights/${symbol}`),
    fetchJson(`/api/factors/${symbol}`),
    fetchJson(`/api/backtests/${symbol}`),
    fetchJson(`/api/alerts?symbol=${symbol}`),
    fetchJson(`/api/forward-tests/${symbol}`),
    fetchJson(`/api/forward-leaderboard?symbol=${symbol}&limit=6`),
    fetchJson(`/api/walk-forward-tests/${symbol}`),
    fetchJson(`/api/walk-forward-leaderboard?symbol=${symbol}&limit=6`),
    fetchJson(`/api/stress-tests/${symbol}`),
    fetchJson(`/api/stress-leaderboard?symbol=${symbol}&limit=6`),
    fetchJson(`/api/backtest-benchmark/${symbol}?factor_id=${activeFactor}&interval=${state.interval}`),
    fetchJson(`/api/backtest-forward-consistency/${symbol}?factor_id=${activeFactor}`),
    fetchJson(`/api/robustness/${symbol}`),
    fetchJson(`/api/drift/${symbol}`),
    fetchJson(`/api/allocation/${symbol}`),
    fetchJson(`/api/trade-readiness/${symbol}?factor_id=${activeFactor}&interval=${state.interval}`),
    fetchJson(`/api/regime-risk-gate/${symbol}?factor_id=${activeFactor}&interval=${state.interval}`),
    fetchJson(`/api/factor-leaderboard/${symbol}`),
    fetchJson(`/api/factor-correlation/${symbol}`),
    fetchJson(`/api/regime-factor-fit/${symbol}?interval=${state.interval}&limit=6`),
    fetchJson(`/api/regime-attribution/${symbol}?factor_id=${activeFactor}`),
  ]);
  state.candles = candles;
  state.news = news;
  state.chartEvents = chartEvents;
  state.aiInsights = aiInsights;
  state.factors = factors;
  state.backtests = backtests;
  state.alerts = alerts;
  state.forwardTests = forwardTests;
  state.forwardLeaderboard = forwardLeaderboard;
  state.walkForwardTests = walkForwardTests;
  state.walkForwardLeaderboard = walkForwardLeaderboard;
  state.stressTests = stressTests;
  state.stressLeaderboard = stressLeaderboard;
  state.backtestBenchmark = backtestBenchmark;
  state.backtestForwardConsistency = backtestForwardConsistency;
  state.robustnessReport = robustnessReport;
  state.strategyDrift = strategyDrift;
  state.strategyAllocation = strategyAllocation;
  state.tradeReadiness = tradeReadiness;
  state.regimeRiskGate = regimeRiskGate;
  state.factorLeaderboard = factorLeaderboard;
  state.factorCorrelation = factorCorrelation;
  state.regimeFactorFit = regimeFactorFit;
  state.regimeAttribution = regimeAttribution;
  state.alertEvaluations = [];
  renderSnapshot();
  renderIndicators(indicators);
  renderNews(news);
  renderAiInsights(aiInsights);
  renderFactors(factors);
  renderBacktests(backtests);
  renderAlerts(alerts);
  renderForwardTests(forwardTests);
  renderForwardLeaderboard(forwardLeaderboard);
  renderWalkForwardTests(walkForwardTests);
  renderWalkForwardLeaderboard(walkForwardLeaderboard);
  renderStressTests(stressTests);
  renderStressLeaderboard(stressLeaderboard);
  renderBacktestBenchmark(backtestBenchmark);
  renderBacktestForwardConsistency(backtestForwardConsistency);
  renderRobustnessReport(robustnessReport);
  renderStrategyDrift(strategyDrift);
  renderStrategyAllocation(strategyAllocation);
  renderTradeReadiness(tradeReadiness);
  renderRegimeRiskGate(regimeRiskGate);
  renderFactorLeaderboard(factorLeaderboard);
  renderFactorCorrelation(factorCorrelation);
  renderRegimeFactorFit(regimeFactorFit);
  renderRegimeAttribution(regimeAttribution);
  drawChart();
}

async function loadWatchlist() {
  const [availableSymbols, watchlist] = await Promise.all([
    fetchJson("/api/symbols"),
    fetchJson("/api/watchlist"),
  ]);
  state.availableSymbols = availableSymbols;
  state.symbols = watchlist;
  if (!state.symbols.some((item) => item.symbol === state.activeSymbol) && state.symbols.length) {
    state.activeSymbol = state.symbols[0].symbol;
  }
  applyFilters();
  if (state.symbols.length) await selectSymbol(state.activeSymbol);
}

async function loadMarketBreadth() {
  const breadth = await fetchJson("/api/market-breadth");
  state.marketBreadth = breadth;
  renderMarketBreadth(breadth);
}

async function loadScreener() {
  const params = new URLSearchParams();
  const minChange = $("#screenerMinChange").value;
  const minRsi = $("#screenerMinRsi").value;
  const minTrend = $("#screenerMinTrend").value;
  const signal = $("#screenerSignal").value;
  if (minChange) params.set("min_change", minChange);
  if (minRsi) params.set("min_rsi", minRsi);
  if (minTrend) params.set("min_trend", minTrend);
  if (signal) params.set("signal", signal);
  const result = await fetchJson(`/api/screener?${params.toString()}`);
  state.screener = result;
  renderScreener(result);
}

function currentScreenerFilters() {
  const filters = {};
  if ($("#screenerMinChange").value) filters.min_change = Number($("#screenerMinChange").value);
  if ($("#screenerMinRsi").value) filters.min_rsi = Number($("#screenerMinRsi").value);
  if ($("#screenerMinTrend").value) filters.min_trend = Number($("#screenerMinTrend").value);
  if ($("#screenerSignal").value) filters.signal = $("#screenerSignal").value;
  return filters;
}

async function loadScreenerPresets() {
  const [presets, validations, libraryScore, strategyCoverage] = await Promise.all([
    fetchJson("/api/screener/presets"),
    fetchJson("/api/screener/preset-validations"),
    fetchJson("/api/filters/library-score"),
    fetchJson("/api/filters/strategy-coverage"),
  ]);
  renderScreenerPresets(presets, validations);
  renderFilterLibraryScore(libraryScore);
  renderFilterStrategyCoverage(strategyCoverage);
}

async function loadRegimeFilterTemplates() {
  const templates = await fetchJson("/api/filters/regime-templates");
  renderRegimeFilterTemplates(templates);
}

async function loadResearchLibraries() {
  const [catalog, health, dashboard, quality, freshness, coverage, factorCoverage, factorLifecycle, dataLineage, runAudit, actionPlan, strategyCandidates, admissionQueue, revalidationQueue, strategies, validations, validationDecay, leaderboard, strategyRegimeFit, strategyRegimeMatrix, strategyCrowding, strategyLifecycle, strategyRiskControls, marketStateControls, strategyOperatingPlan, strategyEvidencePacks, strategyBacktestMonitor, strategyForwardMonitor, strategySensitivity, strategyRevalidationPlan, strategyValidationCoverage, gates, readiness] = await Promise.all([
    fetchJson("/api/data-catalog"),
    fetchJson("/api/research-health"),
    fetchJson("/api/research-dashboard"),
    fetchJson("/api/data-quality"),
    fetchJson("/api/data-freshness"),
    fetchJson("/api/research-coverage"),
    fetchJson("/api/factor-coverage"),
    fetchJson(`/api/factor-lifecycle?symbol=${state.activeSymbol}`),
    fetchJson("/api/data-lineage"),
    fetchJson("/api/research-run-audit?limit=12"),
    fetchJson("/api/research-action-plan"),
    fetchJson("/api/strategy-candidates?min_rsi=30&min_trend=40&signal=buy&limit=5"),
    fetchJson("/api/factor-admission-queue?limit=4"),
    fetchJson("/api/revalidation-queue?limit=5"),
    fetchJson("/api/strategy-templates"),
    fetchJson("/api/strategy-validations?limit=10"),
    fetchJson("/api/strategy-validation-decay?limit=12"),
    fetchJson("/api/strategy-leaderboard"),
    fetchJson("/api/strategy-regime-fit?limit=8"),
    fetchJson("/api/strategy-regime-matrix"),
    fetchJson("/api/strategy-crowding"),
    fetchJson("/api/strategy-lifecycle"),
    fetchJson("/api/strategy-risk-controls"),
    fetchJson("/api/market-state-controls"),
    fetchJson("/api/strategy-operating-plan"),
    fetchJson("/api/strategy-evidence-packs"),
    fetchJson("/api/strategy-backtest-monitor?limit=12"),
    fetchJson("/api/strategy-forward-monitor?limit=12"),
    fetchJson("/api/strategy-sensitivity?limit=12"),
    fetchJson("/api/strategy-revalidation-plan?limit=12"),
    fetchJson("/api/strategy-validation-coverage?limit=12"),
    fetchJson("/api/strategy-promotion-gates"),
    fetchJson("/api/research-readiness"),
  ]);
  state.dataCatalog = catalog;
  state.researchHealth = health;
  state.researchDashboard = dashboard;
  state.dataQuality = quality;
  state.dataFreshness = freshness;
  state.researchCoverage = coverage;
  state.factorCoverage = factorCoverage;
  state.factorLifecycle = factorLifecycle;
  state.dataLineage = dataLineage;
  state.researchRunAudit = runAudit;
  state.researchActionPlan = actionPlan;
  state.strategyCandidates = strategyCandidates;
  state.admissionQueue = admissionQueue;
  state.revalidationQueue = revalidationQueue;
  state.strategyTemplates = strategies;
  state.strategyValidations = validations;
  state.strategyValidationDecay = validationDecay;
  state.strategyLeaderboard = leaderboard;
  state.strategyRegimeFit = strategyRegimeFit;
  state.strategyRegimeMatrix = strategyRegimeMatrix;
  state.strategyCrowding = strategyCrowding;
  state.strategyLifecycle = strategyLifecycle;
  state.strategyRiskControls = strategyRiskControls;
  state.marketStateControls = marketStateControls;
  state.strategyOperatingPlan = strategyOperatingPlan;
  state.strategyEvidencePacks = strategyEvidencePacks;
  state.strategyBacktestMonitor = strategyBacktestMonitor;
  state.strategyForwardMonitor = strategyForwardMonitor;
  state.strategySensitivity = strategySensitivity;
  state.strategyRevalidationPlan = strategyRevalidationPlan;
  state.strategyValidationCoverage = strategyValidationCoverage;
  state.strategyPromotionGates = gates;
  state.researchReadiness = readiness;
  renderResearchHealth(health);
  renderResearchDashboard(dashboard);
  renderResearchReadiness(readiness);
  renderMarketStateControls(marketStateControls);
  renderStrategyOperatingPlan(strategyOperatingPlan);
  renderStrategyEvidencePacks(strategyEvidencePacks);
  renderResearchActionPlan(actionPlan);
  renderStrategyCandidates(strategyCandidates);
  renderAdmissionQueue(admissionQueue);
  renderRevalidationQueue(revalidationQueue);
  renderResearchRunAudit(runAudit);
  renderDataQuality(quality);
  renderDataFreshness(freshness);
  renderResearchCoverage(coverage);
  renderFactorCoverage(factorCoverage);
  renderFactorLifecycle(factorLifecycle);
  renderDataLineage(dataLineage);
  renderDataCatalog(catalog);
  renderStrategyLeaderboard(leaderboard);
  renderStrategyRegimeFit(strategyRegimeFit);
  renderStrategyRegimeMatrix(strategyRegimeMatrix);
  renderStrategyCrowding(strategyCrowding);
  renderStrategyLifecycle(strategyLifecycle);
  renderStrategyRiskControls(strategyRiskControls);
  renderStrategyBacktestMonitor(strategyBacktestMonitor);
  renderStrategyForwardMonitor(strategyForwardMonitor);
  renderStrategySensitivity(strategySensitivity);
  renderStrategyRevalidationPlan(strategyRevalidationPlan);
  renderStrategyValidationCoverage(strategyValidationCoverage);
  renderStrategyTemplates(strategies, gates);
  renderStrategyValidationDecay(validationDecay);
  renderStrategyValidations(validations);
}

async function backfillResearchCoverage() {
  $("#backfillResearchStatus").textContent = "正在补齐研究覆盖...";
  const result = await fetchJson("/api/research-coverage/backfill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      factor_id: $("#backtestFactor")?.value || "momentum-news",
      missing_only: true,
      max_tasks: 4,
    }),
  });
  $("#backfillResearchStatus").textContent = `完成：执行 ${result.executed_tasks.length} 个，跳过 ${result.skipped.length} 个`;
  await Promise.all([loadResearchLibraries(), loadEvents(), loadHealth(), loadRobustnessReport()]);
}

async function backfillFactorCoverage() {
  $("#backfillResearchStatus").textContent = "正在补齐因子覆盖...";
  const result = await fetchJson("/api/factor-coverage/backfill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      factor_id: $("#backtestFactor")?.value || "momentum-news",
      missing_only: true,
      max_tasks: 6,
    }),
  });
  $("#backfillResearchStatus").textContent = `因子补齐完成：执行 ${result.executed_tasks.length} 个，跳过 ${result.skipped.length} 个`;
  await Promise.all([loadResearchLibraries(), loadEvents(), loadHealth(), loadRobustnessReport()]);
}

async function loadMarketRegime() {
  const [regime, history, transition, scenarioRisk, exposure, playbook] = await Promise.all([
    fetchJson("/api/market-regime"),
    fetchJson("/api/market-regime/history?limit=8"),
    fetchJson("/api/market-regime/transition"),
    fetchJson("/api/regime-scenario-risk"),
    fetchJson(`/api/regime-exposure?interval=${state.interval}`),
    fetchJson(`/api/regime-playbook?symbol=${state.activeSymbol}`),
  ]);
  state.marketRegime = regime;
  state.marketRegimeHistory = history;
  state.marketRegimeTransition = transition;
  state.regimeScenarioRisk = scenarioRisk;
  state.regimeExposure = exposure;
  state.regimePlaybook = playbook;
  renderMarketRegime(regime, history);
  renderMarketRegimeTransition(transition);
  renderRegimeScenarioRisk(scenarioRisk);
  renderRegimeExposure(exposure);
  renderRegimePlaybook(playbook);
  await loadRegimeFilterTemplates();
}

async function validateCurrentFilters() {
  $("#filterValidationStatus").textContent = "正在验证...";
  const filters = currentScreenerFilters();
  const request = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(filters),
  };
  const [result, admission, contribution, stressTest] = await Promise.all([
    fetchJson("/api/filters/validate", request),
    fetchJson("/api/filters/admission", request),
    fetchJson("/api/filters/contribution", request),
    fetchJson("/api/filters/stress-test", request),
  ]);
  state.filterValidation = result;
  state.filterAdmission = admission;
  state.filterContribution = contribution;
  state.filterStressTest = stressTest;
  renderFilterValidation(result);
  renderFilterAdmission(admission);
  renderFilterContribution(contribution);
  renderFilterStressTest(stressTest);
}

async function validateStrategy(strategyId) {
  const target = document.querySelector(`[data-strategy-validate="${strategyId}"]`);
  if (target) target.textContent = "验证中";
  try {
    const result = await fetchJson(`/api/strategy-templates/${strategyId}/validate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol_limit: 3,
        interval: $("#forwardInterval")?.value || "1h",
        initial_capital: 10000,
      }),
    });
    state.strategyValidations = [result, ...state.strategyValidations.filter((item) => item.id !== result.id)].slice(0, 10);
    renderStrategyValidations(state.strategyValidations);
    await Promise.all([loadResearchLibraries(), loadEvents(), loadHealth()]);
  } finally {
    if (target) target.textContent = "验证策略";
  }
}

async function createStrategyTemplateFromCurrentFilters() {
  $("#strategyStatus").textContent = "正在保存策略...";
  const factorId = $("#strategyFactor").value;
  const strategy = await fetchJson("/api/strategy-templates", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: $("#strategyName").value.trim(),
      category: $("#strategyCategory").value.trim(),
      description: $("#strategyDescription").value.trim(),
      factor_ids: factorId ? [factorId] : [],
      default_filters: currentScreenerFilters(),
      risk_controls: ["前向测试未通过时暂停自动化", "市场状态为 risk 时降低仓位"],
      validation_rules: ["过滤器需命中样本", "前向测试状态不能为 fail"],
      enabled: true,
    }),
  });
  $("#strategyStatus").textContent = `已保存 ${strategy.name}`;
  $("#strategyForm").reset();
  await Promise.all([loadResearchLibraries(), loadEvents(), loadHealth()]);
}

async function updateStrategyEnabled(strategyId, enabled) {
  await fetchJson(`/api/strategy-templates/${strategyId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  await Promise.all([loadResearchLibraries(), loadEvents(), loadHealth()]);
}

async function deleteStrategyTemplate(strategyId) {
  await fetchJson(`/api/strategy-templates/${strategyId}`, { method: "DELETE" });
  await Promise.all([loadResearchLibraries(), loadEvents(), loadHealth()]);
}

async function saveScreenerPreset() {
  $("#screenerPresetStatus").textContent = "正在保存模板...";
  const preset = await fetchJson("/api/screener/presets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: $("#screenerPresetName").value.trim(),
      filters: currentScreenerFilters(),
    }),
  });
  $("#screenerPresetStatus").textContent = `已保存 ${preset.name}`;
  $("#screenerPresetForm").reset();
  await Promise.all([loadScreenerPresets(), loadEvents()]);
}

function applyScreenerFilters(filters) {
  $("#screenerMinChange").value = filters.min_change ?? "";
  $("#screenerMinRsi").value = filters.min_rsi ?? "";
  $("#screenerMinTrend").value = filters.min_trend ?? "";
  $("#screenerSignal").value = filters.signal ?? "";
  Promise.all([loadScreener(), validateCurrentFilters()]).catch((error) => console.error(error));
}

function applyScreenerPreset(preset) {
  applyScreenerFilters(preset.filters);
}

async function loadAiBriefing() {
  const briefing = await fetchJson("/api/ai/briefing");
  state.aiBriefing = briefing;
  renderAiBriefing(briefing);
}

async function loadFactorDefinitions() {
  const definitions = await fetchJson("/api/factor-definitions");
  state.factorDefinitions = definitions;
  renderFactorDefinitions(definitions);
}

function applyMarketStreamPayload(payload) {
  if (payload.type !== "market_snapshot") return;
  state.marketProvider = payload.provider;
  state.symbols = payload.watchlist;
  state.marketBreadth = payload.breadth;
  renderProviderStatus(state.marketProvider);
  if (!state.symbols.some((item) => item.symbol === state.activeSymbol) && state.symbols.length) {
    state.activeSymbol = state.symbols[0].symbol;
  }
  applyFilters();
  renderMarketBreadth(state.marketBreadth);
  loadScreener().catch((error) => console.error(error));
  renderSnapshot();
  loadAiBriefing().catch((error) => console.error(error));
}

function connectMarketStream() {
  if (state.marketStream) state.marketStream.close();
  if (state.streamReconnectTimer) clearTimeout(state.streamReconnectTimer);
  renderStreamStatus("connecting", "实时流连接中");
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/api/stream/market?interval=5`);
  state.marketStream = socket;
  socket.addEventListener("open", () => renderStreamStatus("online", "实时流在线"));
  socket.addEventListener("message", (event) => {
    try {
      applyMarketStreamPayload(JSON.parse(event.data));
    } catch (error) {
      console.error(error);
      renderStreamStatus("offline", "实时流解析失败");
    }
  });
  socket.addEventListener("close", () => {
    if (state.marketStream !== socket) return;
    renderStreamStatus("offline", "实时流重连中");
    state.streamReconnectTimer = window.setTimeout(connectMarketStream, 3000);
  });
  socket.addEventListener("error", () => {
    renderStreamStatus("offline", "实时流异常");
    socket.close();
  });
}

async function addWatchlistSymbol(symbol) {
  $("#watchlistStatus").textContent = "正在添加自选...";
  const snapshot = await fetchJson("/api/watchlist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol }),
  });
  $("#watchlistStatus").textContent = `已添加 ${snapshot.symbol}`;
  await loadWatchlist();
  await loadMarketBreadth();
  await loadEvents();
  await loadHealth();
}

async function removeWatchlistSymbol(symbol) {
  $("#watchlistStatus").textContent = `正在移除 ${symbol}...`;
  await fetchJson(`/api/watchlist/${symbol}`, { method: "DELETE" });
  $("#watchlistStatus").textContent = `已移除 ${symbol}`;
  await loadWatchlist();
  await loadMarketBreadth();
  await loadEvents();
  await loadHealth();
}

async function loadProviderStatus() {
  const [provider, options] = await Promise.all([
    fetchJson("/api/market-provider"),
    fetchJson("/api/market-provider/options"),
  ]);
  state.marketProvider = provider;
  state.providerOptions = options;
  renderProviderStatus(state.marketProvider);
  renderProviderOptions(options);
}

async function switchProvider(provider) {
  $("#providerStatus").textContent = "正在切换行情源...";
  await fetchJson("/api/market-provider", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider }),
  });
  await loadProviderStatus();
  await loadWatchlist();
  await loadMarketBreadth();
  await loadPortfolio();
  await loadHealth();
  await loadAppSettings();
}

async function loadSources() {
  state.newsSources = await fetchJson("/api/news-sources");
  renderSources(state.newsSources);
}

async function loadEvents() {
  state.events = await fetchJson("/api/events?limit=30");
  renderEvents(state.events);
}

async function loadAutomationRuns() {
  state.automationRuns = await fetchJson("/api/automation/runs?limit=10");
  renderAutomationRuns(state.automationRuns);
}

async function loadNotifications() {
  const [channels, deliveries] = await Promise.all([
    fetchJson("/api/notifications/channels"),
    fetchJson("/api/notifications/deliveries?limit=20"),
  ]);
  state.notificationChannels = channels;
  state.notificationDeliveries = deliveries;
  renderNotificationChannels(channels, deliveries);
}

async function loadPortfolio() {
  const [summary, risk] = await Promise.all([
    fetchJson("/api/portfolio"),
    fetchJson("/api/portfolio/risk"),
  ]);
  renderPortfolio(summary);
  state.portfolioRisk = risk;
  renderPortfolioRisk(risk);
}

async function loadHealth() {
  const health = await fetchJson("/api/health");
  state.health = health;
  renderHealth(health);
}

async function loadAutomationSettings() {
  const settings = await fetchJson("/api/automation/settings");
  renderAutomationSettings(settings);
}

async function loadAppSettings() {
  const settings = await fetchJson("/api/settings");
  renderAppSettings(settings);
}

async function saveAppSettings() {
  $("#settingsStatus").textContent = "正在保存设置...";
  const settings = await fetchJson("/api/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      signal_filter: $("#settingsSignal").value,
      rsi_min: Number($("#settingsRsi").value || 0),
      positive_only: $("#settingsPositiveOnly").checked,
      automation_enabled: $("#settingsAutomationEnabled").checked,
      automation_interval_seconds: Number($("#settingsAutomationInterval").value || 300),
    }),
  });
  renderAppSettings(settings);
  renderAutomationSettings(settings.automation);
  $("#settingsStatus").textContent = "设置已保存";
  await Promise.all([loadEvents(), loadHealth()]);
}

async function refreshSources() {
  $("#sourceStatus").textContent = "正在刷新 RSS 源...";
  const results = await fetchJson("/api/news-sources/refresh", { method: "POST" });
  const inserted = results.reduce((sum, item) => sum + item.inserted, 0);
  const errors = results.filter((item) => item.error).length;
  $("#sourceStatus").textContent = `刷新完成，新增 ${inserted} 条${errors ? `，${errors} 个源异常` : ""}`;
  await loadSources();
  await loadHealth();
  await selectSymbol(state.activeSymbol);
}

async function loadRobustnessReport() {
  const activeFactor = $("#backtestFactor")?.value || "momentum-news";
  const [backtestBenchmark, backtestForwardConsistency, robustnessReport, strategyDrift, strategyAllocation, tradeReadiness, regimeRiskGate, factorLeaderboard, factorCorrelation, regimeFactorFit, regimeAttribution] = await Promise.all([
    fetchJson(`/api/backtest-benchmark/${state.activeSymbol}?factor_id=${activeFactor}&interval=${state.interval}`),
    fetchJson(`/api/backtest-forward-consistency/${state.activeSymbol}?factor_id=${activeFactor}`),
    fetchJson(`/api/robustness/${state.activeSymbol}`),
    fetchJson(`/api/drift/${state.activeSymbol}`),
    fetchJson(`/api/allocation/${state.activeSymbol}`),
    fetchJson(`/api/trade-readiness/${state.activeSymbol}?factor_id=${activeFactor}&interval=${state.interval}`),
    fetchJson(`/api/regime-risk-gate/${state.activeSymbol}?factor_id=${activeFactor}&interval=${state.interval}`),
    fetchJson(`/api/factor-leaderboard/${state.activeSymbol}`),
    fetchJson(`/api/factor-correlation/${state.activeSymbol}`),
    fetchJson(`/api/regime-factor-fit/${state.activeSymbol}?interval=${state.interval}&limit=6`),
    fetchJson(`/api/regime-attribution/${state.activeSymbol}?factor_id=${activeFactor}`),
  ]);
  state.backtestBenchmark = backtestBenchmark;
  state.backtestForwardConsistency = backtestForwardConsistency;
  state.robustnessReport = robustnessReport;
  state.strategyDrift = strategyDrift;
  state.strategyAllocation = strategyAllocation;
  state.tradeReadiness = tradeReadiness;
  state.regimeRiskGate = regimeRiskGate;
  state.factorLeaderboard = factorLeaderboard;
  state.factorCorrelation = factorCorrelation;
  state.regimeFactorFit = regimeFactorFit;
  state.regimeAttribution = regimeAttribution;
  renderBacktestBenchmark(backtestBenchmark);
  renderBacktestForwardConsistency(backtestForwardConsistency);
  renderRobustnessReport(robustnessReport);
  renderStrategyDrift(strategyDrift);
  renderStrategyAllocation(strategyAllocation);
  renderTradeReadiness(tradeReadiness);
  renderRegimeRiskGate(regimeRiskGate);
  renderFactorLeaderboard(factorLeaderboard);
  renderFactorCorrelation(factorCorrelation);
  renderRegimeFactorFit(regimeFactorFit);
  renderRegimeAttribution(regimeAttribution);
}

async function runBacktest() {
  $("#backtestStatus").textContent = "正在运行回测...";
  const result = await fetchJson("/api/backtest-runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbol: state.activeSymbol,
      factor_id: $("#backtestFactor").value,
      interval: $("#backtestInterval").value,
      limit: 180,
      initial_capital: 10000,
      fee_bps: 2,
    }),
  });
  $("#backtestStatus").textContent = `完成：${result.factor_name} ${result.total_return > 0 ? "+" : ""}${result.total_return.toFixed(2)}%`;
  const backtests = await fetchJson(`/api/backtests/${state.activeSymbol}`);
  state.backtests = backtests;
  renderBacktests(backtests);
  await loadRobustnessReport();
}

async function runResearchPipeline() {
  $("#pipelineStatus").textContent = "正在一键验证...";
  const result = await fetchJson("/api/research-pipelines", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbol: state.activeSymbol,
      factor_id: $("#backtestFactor").value,
      interval: $("#backtestInterval").value,
      initial_capital: 10000,
      filters: currentScreenerFilters(),
    }),
  });
  $("#pipelineStatus").textContent = `完成：${result.factor_name} ${result.pass_fail}`;
  state.filterValidation = result.filter_validation;
  renderFilterValidation(result.filter_validation);
  const [backtests, forwardTests, stressTests] = await Promise.all([
    fetchJson(`/api/backtests/${state.activeSymbol}`),
    fetchJson(`/api/forward-tests/${state.activeSymbol}`),
    fetchJson(`/api/stress-tests/${state.activeSymbol}`),
  ]);
  state.backtests = backtests;
  state.forwardTests = forwardTests;
  state.stressTests = stressTests;
  renderBacktests(backtests);
  renderForwardTests(forwardTests);
  renderStressTests(stressTests);
  await Promise.all([loadRobustnessReport(), loadEvents(), loadHealth(), loadResearchLibraries()]);
}

async function runFactorAdmission() {
  $("#admissionStatus").textContent = "正在生成准入包...";
  const result = await fetchJson("/api/factor-admission", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbol: state.activeSymbol,
      factor_id: $("#backtestFactor").value,
      interval: $("#backtestInterval").value,
      filters: currentScreenerFilters(),
    }),
  });
  state.factorAdmission = result;
  $("#admissionStatus").textContent = `完成：${result.status} ${result.score}/100`;
  renderFactorAdmission(result);
}

async function runForwardTest() {
  $("#forwardTestStatus").textContent = "正在运行前向测试...";
  const result = await fetchJson("/api/forward-tests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbol: state.activeSymbol,
      factor_id: $("#forwardFactor").value,
      interval: $("#forwardInterval").value,
      train_bars: 140,
      forward_bars: 40,
      initial_capital: 10000,
      fee_bps: 2,
    }),
  });
  $("#forwardTestStatus").textContent = `完成：${result.factor_name} ${result.pass_fail}，前向 ${result.forward_return > 0 ? "+" : ""}${result.forward_return.toFixed(2)}%`;
  const [forwardTests, forwardLeaderboard] = await Promise.all([
    fetchJson(`/api/forward-tests/${state.activeSymbol}`),
    fetchJson(`/api/forward-leaderboard?symbol=${state.activeSymbol}&limit=6`),
  ]);
  state.forwardTests = forwardTests;
  state.forwardLeaderboard = forwardLeaderboard;
  renderForwardTests(forwardTests);
  renderForwardLeaderboard(forwardLeaderboard);
  await Promise.all([loadEvents(), loadHealth(), loadResearchLibraries(), loadRobustnessReport()]);
}

async function runWalkForwardTest() {
  $("#walkForwardTestStatus").textContent = "正在运行滚动样本外验证...";
  const result = await fetchJson("/api/walk-forward-tests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbol: state.activeSymbol,
      factor_id: $("#walkForwardFactor").value,
      interval: $("#walkForwardInterval").value,
      train_bars: 90,
      forward_bars: 30,
      windows: 4,
      initial_capital: 10000,
      fee_bps: 2,
    }),
  });
  $("#walkForwardTestStatus").textContent = `完成：${result.factor_name} ${result.pass_fail}，通过率 ${result.pass_rate.toFixed(1)}%`;
  const [walkForwardTests, walkForwardLeaderboard] = await Promise.all([
    fetchJson(`/api/walk-forward-tests/${state.activeSymbol}`),
    fetchJson(`/api/walk-forward-leaderboard?symbol=${state.activeSymbol}&limit=6`),
  ]);
  state.walkForwardTests = walkForwardTests;
  state.walkForwardLeaderboard = walkForwardLeaderboard;
  renderWalkForwardTests(walkForwardTests);
  renderWalkForwardLeaderboard(walkForwardLeaderboard);
  await Promise.all([loadEvents(), loadHealth(), loadResearchLibraries(), loadRobustnessReport()]);
}

async function runStressTest() {
  $("#stressTestStatus").textContent = "正在运行压力测试...";
  const result = await fetchJson("/api/stress-tests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbol: state.activeSymbol,
      factor_id: $("#stressFactor").value,
      interval: $("#stressInterval").value,
      initial_capital: 10000,
    }),
  });
  $("#stressTestStatus").textContent = `完成：${result.factor_name} ${result.pass_fail}，最差 ${result.worst_return > 0 ? "+" : ""}${result.worst_return.toFixed(2)}%`;
  const [stressTests, stressLeaderboard] = await Promise.all([
    fetchJson(`/api/stress-tests/${state.activeSymbol}`),
    fetchJson(`/api/stress-leaderboard?symbol=${state.activeSymbol}&limit=6`),
  ]);
  state.stressTests = stressTests;
  state.stressLeaderboard = stressLeaderboard;
  renderStressTests(stressTests);
  renderStressLeaderboard(stressLeaderboard);
  await Promise.all([loadEvents(), loadHealth(), loadResearchLibraries(), loadRobustnessReport()]);
}

async function runSensitivityTest() {
  $("#sensitivityStatus").textContent = "正在运行敏感性测试...";
  const result = await fetchJson("/api/sensitivity-tests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbol: state.activeSymbol,
      factor_id: $("#backtestFactor").value,
      initial_capital: 10000,
    }),
  });
  state.sensitivityTest = result;
  $("#sensitivityStatus").textContent = `完成：${result.factor_name} ${result.pass_fail}`;
  renderSensitivityTest(result);
  const backtests = await fetchJson(`/api/backtests/${state.activeSymbol}`);
  state.backtests = backtests;
  renderBacktests(backtests);
  await Promise.all([loadEvents(), loadHealth(), loadResearchLibraries(), loadRobustnessReport()]);
}

async function runParameterOptimization() {
  $("#optimizationStatus").textContent = "正在优化参数...";
  const result = await fetchJson("/api/parameter-optimization", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbol: state.activeSymbol,
      factor_id: $("#backtestFactor").value,
      interval: $("#backtestInterval").value,
      limit: 220,
      initial_capital: 10000,
      fee_bps: 2,
    }),
  });
  state.parameterOptimization = result;
  $("#optimizationStatus").textContent = `完成：${result.factor_name} ${result.status}`;
  renderParameterOptimization(result);
}

async function createFactorDefinition() {
  $("#factorStatus").textContent = "正在保存因子...";
  const factor = await fetchJson("/api/factor-definitions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: $("#factorName").value.trim(),
      category: $("#factorCategory").value.trim(),
      signal: $("#factorSignal").value,
      horizon: $("#factorHorizon").value.trim() || "3-10 days",
      description: $("#factorDescription").value.trim(),
    }),
  });
  $("#factorStatus").textContent = `已加入 ${factor.name}`;
  $("#factorForm").reset();
  $("#factorHorizon").value = "3-10 days";
  await loadFactorDefinitions();
  await selectSymbol(state.activeSymbol);
  await loadEvents();
  await loadHealth();
}

async function toggleFactorDefinition(factorId, enabled) {
  await fetchJson(`/api/factor-definitions/${factorId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  await loadFactorDefinitions();
  await selectSymbol(state.activeSymbol);
  await loadHealth();
}

async function loadAlerts() {
  state.alerts = await fetchJson(`/api/alerts?symbol=${state.activeSymbol}`);
  renderAlerts(state.alerts, state.alertEvaluations);
}

async function evaluateAlerts() {
  $("#alertStatus").textContent = "正在评估预警...";
  state.alertEvaluations = await fetchJson(`/api/alerts/evaluate?symbol=${state.activeSymbol}`, { method: "POST" });
  const triggered = state.alertEvaluations.filter((item) => item.triggered).length;
  $("#alertStatus").textContent = `评估完成，触发 ${triggered} 条`;
  await loadAlerts();
  renderAlerts(state.alerts, state.alertEvaluations);
  await loadEvents();
  await loadNotifications();
  await loadHealth();
}

async function runAutomation() {
  $("#automationStatus").textContent = "正在刷新新闻源并评估预警...";
  const result = await fetchJson("/api/automation/run", { method: "POST" });
  $("#automationStatus").textContent = `完成：新增新闻 ${result.inserted_news} 条，触发预警 ${result.triggered_alerts} 条`;
  await Promise.all([loadEvents(), loadAutomationRuns(), loadSources(), loadAlerts(), loadNotifications(), loadHealth(), loadMarketBreadth(), loadAiBriefing(), selectSymbol(state.activeSymbol)]);
}

async function saveAutomationSettings() {
  $("#automationStatus").textContent = "正在保存自动化设置...";
  const settings = await fetchJson("/api/automation/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      enabled: $("#automationEnabled").checked,
      interval_seconds: Number($("#automationInterval").value || 300),
    }),
  });
  renderAutomationSettings(settings);
  await loadEvents();
  await loadHealth();
  await loadAppSettings();
}

document.addEventListener("click", (event) => {
  const viewTarget = event.target.closest("[data-view]");
  if (viewTarget) {
    setActiveView(viewTarget.dataset.view);
    return;
  }

  const strategyValidateTarget = event.target.closest("[data-strategy-validate]");
  if (strategyValidateTarget) {
    validateStrategy(strategyValidateTarget.dataset.strategyValidate).catch((error) => {
      console.error(error);
      renderStrategyValidations([
        {
          id: "strategy-validation-error",
          strategy_name: "策略验证失败",
          pass_fail: "fail",
          symbols: [],
          average_backtest_return: 0,
          average_forward_return: 0,
          regime: { label: "range", risk_level: "watch" },
          checks: ["请检查策略因子、过滤器或行情源状态。"],
        },
      ]);
    });
    return;
  }

  const strategyToggleTarget = event.target.closest("[data-strategy-toggle]");
  if (strategyToggleTarget) {
    updateStrategyEnabled(strategyToggleTarget.dataset.strategyToggle, strategyToggleTarget.dataset.strategyEnabled === "1").catch((error) => {
      console.error(error);
      $("#strategyStatus").textContent = "更新策略状态失败。";
    });
    return;
  }

  const strategyDeleteTarget = event.target.closest("[data-strategy-delete]");
  if (strategyDeleteTarget) {
    deleteStrategyTemplate(strategyDeleteTarget.dataset.strategyDelete).catch((error) => {
      console.error(error);
      $("#strategyStatus").textContent = "停用策略失败。";
    });
    return;
  }

  const factorToggleTarget = event.target.closest("[data-factor-toggle]");
  if (factorToggleTarget) {
    toggleFactorDefinition(factorToggleTarget.dataset.factorToggle, factorToggleTarget.dataset.factorEnabled === "1").catch((error) => {
      console.error(error);
      $("#factorStatus").textContent = "更新因子状态失败。";
    });
    return;
  }

  const exportTarget = event.target.closest("[data-export]");
  if (exportTarget) {
    exportDataset(exportTarget.dataset.export);
    return;
  }

  const screenerLoadTarget = event.target.closest("[data-screener-load]");
  if (screenerLoadTarget) {
    const preset = state.screenerPresets.find((item) => item.id === screenerLoadTarget.dataset.screenerLoad);
    if (preset) applyScreenerPreset(preset);
    return;
  }

  const regimeFilterTarget = event.target.closest("[data-regime-filter]");
  if (regimeFilterTarget) {
    const template = state.regimeFilterTemplates.find((item) => item.id === regimeFilterTarget.dataset.regimeFilter);
    if (template) applyScreenerFilters(template.filters);
    return;
  }

  const screenerDeleteTarget = event.target.closest("[data-screener-delete]");
  if (screenerDeleteTarget) {
    fetchJson(`/api/screener/presets/${screenerDeleteTarget.dataset.screenerDelete}`, { method: "DELETE" })
      .then(() => Promise.all([loadScreenerPresets(), loadEvents()]))
      .catch((error) => {
        console.error(error);
        $("#screenerPresetStatus").textContent = "停用模板失败。";
      });
    return;
  }

  const alertToggleTarget = event.target.closest("[data-alert-toggle]");
  if (alertToggleTarget) {
    fetchJson(`/api/alerts/${alertToggleTarget.dataset.alertToggle}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: alertToggleTarget.dataset.alertEnabled === "1" }),
    })
      .then(() => Promise.all([loadAlerts(), loadEvents(), loadHealth()]))
      .catch((error) => {
        console.error(error);
        $("#alertStatus").textContent = "更新预警失败。";
      });
    return;
  }

  const alertDeleteTarget = event.target.closest("[data-alert-delete]");
  if (alertDeleteTarget) {
    fetchJson(`/api/alerts/${alertDeleteTarget.dataset.alertDelete}`, { method: "DELETE" })
      .then(() => Promise.all([loadAlerts(), loadEvents(), loadHealth()]))
      .catch((error) => {
        console.error(error);
        $("#alertStatus").textContent = "停用预警失败。";
      });
    return;
  }

  const channelToggleTarget = event.target.closest("[data-channel-toggle]");
  if (channelToggleTarget) {
    fetchJson(`/api/notifications/channels/${channelToggleTarget.dataset.channelToggle}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: channelToggleTarget.dataset.channelEnabled === "1" }),
    })
      .then(() => Promise.all([loadNotifications(), loadEvents(), loadHealth(), loadAppSettings()]))
      .catch((error) => {
        console.error(error);
        $("#channelStatus").textContent = "更新通知通道失败。";
      });
    return;
  }

  const channelDeleteTarget = event.target.closest("[data-channel-delete]");
  if (channelDeleteTarget) {
    fetchJson(`/api/notifications/channels/${channelDeleteTarget.dataset.channelDelete}`, { method: "DELETE" })
      .then(() => Promise.all([loadNotifications(), loadEvents(), loadHealth(), loadAppSettings()]))
      .catch((error) => {
        console.error(error);
        $("#channelStatus").textContent = "停用通知通道失败。";
      });
    return;
  }

  const watchlistRemoveTarget = event.target.closest("[data-watchlist-remove]");
  if (watchlistRemoveTarget) {
    removeWatchlistSymbol(watchlistRemoveTarget.dataset.watchlistRemove).catch((error) => {
      console.error(error);
      $("#watchlistStatus").textContent = "停用失败，请稍后再试。";
    });
    return;
  }

  const symbolTarget = event.target.closest("[data-symbol]");
  if (symbolTarget) selectSymbol(symbolTarget.dataset.symbol);

  const positionTarget = event.target.closest("[data-position-id]");
  if (positionTarget) {
    fetchJson(`/api/portfolio/positions/${positionTarget.dataset.positionId}`, { method: "DELETE" })
      .then(() => Promise.all([loadPortfolio(), loadHealth()]))
      .catch((error) => {
        console.error(error);
        $("#positionStatus").textContent = "停用失败。";
      });
  }

  const newsTarget = event.target.closest("[data-news-id]");
  if (newsTarget) {
    openNewsDetail(newsTarget.dataset.newsId).catch((error) => {
      console.error(error);
      $("#newsDrawerBody").innerHTML = `<p>新闻详情加载失败。</p>`;
    });
    return;
  }

  const intervalTarget = event.target.closest("[data-interval]");
  if (intervalTarget) {
    state.interval = intervalTarget.dataset.interval;
    document.querySelectorAll("[data-interval]").forEach((button) => button.classList.toggle("active", button === intervalTarget));
    selectSymbol(state.activeSymbol);
  }
});

$("#priceChart").addEventListener("mousemove", (event) => {
  const rect = $("#priceChart").getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const marker = state.eventMarkers.find((item) => Math.hypot(item.x - x, item.y - y) <= item.radius);
  if (marker) showEventTooltip(marker, event.clientX, event.clientY);
  else hideEventTooltip();
});

$("#priceChart").addEventListener("click", (event) => {
  const rect = $("#priceChart").getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const marker = state.eventMarkers.find((item) => Math.hypot(item.x - x, item.y - y) <= item.radius);
  if (marker) {
    openNewsDetail(marker.event.id).catch((error) => {
      console.error(error);
      $("#newsDrawerBody").innerHTML = `<p>新闻详情加载失败。</p>`;
    });
  }
});

$("#priceChart").addEventListener("mouseleave", hideEventTooltip);

$("#closeNewsDrawer").addEventListener("click", closeNewsDetail);
$("#newsDrawer").addEventListener("click", (event) => {
  if (event.target.id === "newsDrawer") closeNewsDetail();
});

["#searchInput", "#signalFilter", "#rsiFilter", "#positiveOnly"].forEach((selector) => {
  $(selector).addEventListener("input", () => {
    $("#rsiValue").textContent = $("#rsiFilter").value;
    applyFilters();
  });
});

$("#clearFilters").addEventListener("click", () => {
  $("#searchInput").value = "";
  $("#signalFilter").value = "all";
  $("#rsiFilter").value = "0";
  $("#rsiValue").textContent = "0";
  $("#positiveOnly").checked = false;
  applyFilters();
});

$("#sourceForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#sourceStatus").textContent = "正在添加新闻源...";
  const symbols = $("#sourceSymbols").value.split(/[,，\s]+/).map((item) => item.trim()).filter(Boolean);
  try {
    const source = await fetchJson("/api/news-sources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: $("#sourceName").value.trim(),
        url: $("#sourceUrl").value.trim(),
        symbols,
      }),
    });
    $("#sourceStatus").textContent = `已添加 ${source.name}`;
    $("#sourceForm").reset();
    await loadSources();
    await refreshSources();
  } catch (error) {
    console.error(error);
    $("#sourceStatus").textContent = "添加失败，请检查 URL 或是否已存在。";
  }
});

$("#watchlistForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const symbol = $("#watchlistSymbol").value.trim().toUpperCase();
  if (!symbol) return;
  try {
    await addWatchlistSymbol(symbol);
    $("#watchlistForm").reset();
  } catch (error) {
    console.error(error);
    $("#watchlistStatus").textContent = "添加失败，请检查当前行情源是否支持该代码。";
  }
});

$("#screenerForm").addEventListener("submit", (event) => {
  event.preventDefault();
  loadScreener().catch((error) => {
    console.error(error);
    $("#screenerList").innerHTML = `<article class="screener-item"><span>筛选失败。</span></article>`;
  });
});

$("#screenerPresetForm").addEventListener("submit", (event) => {
  event.preventDefault();
  saveScreenerPreset().catch((error) => {
    console.error(error);
    $("#screenerPresetStatus").textContent = "保存模板失败。";
  });
});

$("#validateFilters").addEventListener("click", () => {
  validateCurrentFilters().catch((error) => {
    console.error(error);
    $("#filterValidationStatus").textContent = "验证失败。";
  });
});

$("#backfillResearchCoverage").addEventListener("click", () => {
  backfillResearchCoverage().catch((error) => {
    console.error(error);
    $("#backfillResearchStatus").textContent = "补齐失败，请检查行情源或因子设置。";
  });
});

$("#backfillFactorCoverage").addEventListener("click", () => {
  backfillFactorCoverage().catch((error) => {
    console.error(error);
    $("#backfillResearchStatus").textContent = "因子补齐失败，请检查行情源或因子设置。";
  });
});

$("#resetScreener").addEventListener("click", () => {
  $("#screenerForm").reset();
  loadScreener().catch((error) => console.error(error));
});

$("#factorForm").addEventListener("submit", (event) => {
  event.preventDefault();
  createFactorDefinition().catch((error) => {
    console.error(error);
    $("#factorStatus").textContent = "保存失败，请检查因子名称和描述。";
  });
});

$("#strategyForm").addEventListener("submit", (event) => {
  event.preventDefault();
  createStrategyTemplateFromCurrentFilters().catch((error) => {
    console.error(error);
    $("#strategyStatus").textContent = "保存策略失败，请检查名称、因子和过滤器。";
  });
});

$("#channelForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#channelStatus").textContent = "正在添加通知渠道...";
  try {
    const channel = await fetchJson("/api/notifications/channels", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: $("#channelName").value.trim(),
        target: $("#channelTarget").value.trim(),
      }),
    });
    $("#channelStatus").textContent = `已添加 ${channel.name}`;
    $("#channelForm").reset();
    await loadNotifications();
    await loadHealth();
  } catch (error) {
    console.error(error);
    $("#channelStatus").textContent = "添加失败，请检查 Webhook URL。";
  }
});

$("#positionForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#positionStatus").textContent = "正在添加持仓...";
  try {
    const position = await fetchJson("/api/portfolio/positions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: $("#positionSymbol").value.trim().toUpperCase(),
        side: $("#positionSide").value,
        quantity: Number($("#positionQuantity").value),
        average_price: Number($("#positionAveragePrice").value),
      }),
    });
    $("#positionStatus").textContent = `已添加 ${position.symbol}`;
    $("#positionForm").reset();
    await loadPortfolio();
    await loadHealth();
  } catch (error) {
    console.error(error);
    $("#positionStatus").textContent = "添加失败，请检查代码、数量和成本价。";
  }
});

$("#alertForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#alertStatus").textContent = "正在添加预警...";
  try {
    const alert = await fetchJson("/api/alerts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: state.activeSymbol,
        name: $("#alertName").value.trim(),
        metric: $("#alertMetric").value,
        operator: $("#alertOperator").value,
        threshold: Number($("#alertThreshold").value),
      }),
    });
    $("#alertStatus").textContent = `已添加 ${alert.name}`;
    $("#alertForm").reset();
    await loadAlerts();
    await loadHealth();
  } catch (error) {
    console.error(error);
    $("#alertStatus").textContent = "添加失败，请检查当前行情源是否支持该标的。";
  }
});

$("#evaluateAlerts").addEventListener("click", () => {
  evaluateAlerts().catch((error) => {
    console.error(error);
    $("#alertStatus").textContent = "评估失败，请检查行情源。";
  });
});

$("#refreshBriefing").addEventListener("click", () => {
  loadAiBriefing().catch((error) => {
    console.error(error);
    $("#aiBriefing").innerHTML = `<article><span>简报生成失败。</span></article>`;
  });
});

$("#runAutomation").addEventListener("click", () => {
  runAutomation().catch((error) => {
    console.error(error);
    $("#automationStatus").textContent = "一键盯盘失败，请检查新闻源或行情源。";
  });
});

$("#automationForm").addEventListener("submit", (event) => {
  event.preventDefault();
  saveAutomationSettings().catch((error) => {
    console.error(error);
    $("#automationStatus").textContent = "保存自动化设置失败。";
  });
});

$("#settingsForm").addEventListener("submit", (event) => {
  event.preventDefault();
  saveAppSettings().catch((error) => {
    console.error(error);
    $("#settingsStatus").textContent = "保存设置失败。";
  });
});

$("#refreshSources").addEventListener("click", () => {
  refreshSources().catch((error) => {
    console.error(error);
    $("#sourceStatus").textContent = "刷新失败，可能是网络或 RSS 格式问题。";
  });
});

$("#providerSelect").addEventListener("change", (event) => {
  switchProvider(event.target.value).catch((error) => {
    console.error(error);
    $("#providerStatus").textContent = "行情源切换失败";
  });
});

$("#backtestForm").addEventListener("submit", (event) => {
  event.preventDefault();
  runBacktest().catch((error) => {
    console.error(error);
    $("#backtestStatus").textContent = "回测失败，请检查参数。";
  });
});

$("#runResearchPipeline").addEventListener("click", () => {
  runResearchPipeline().catch((error) => {
    console.error(error);
    $("#pipelineStatus").textContent = "一键验证失败，请检查过滤器或行情源。";
  });
});

$("#runFactorAdmission").addEventListener("click", () => {
  runFactorAdmission().catch((error) => {
    console.error(error);
    $("#admissionStatus").textContent = "准入包生成失败，请检查研究记录。";
  });
});

$("#runSensitivityTest").addEventListener("click", () => {
  runSensitivityTest().catch((error) => {
    console.error(error);
    $("#sensitivityStatus").textContent = "敏感性测试失败，请检查行情源。";
  });
});

$("#runParameterOptimization").addEventListener("click", () => {
  runParameterOptimization().catch((error) => {
    console.error(error);
    $("#optimizationStatus").textContent = "参数优化失败，请检查行情源。";
  });
});

$("#forwardTestForm").addEventListener("submit", (event) => {
  event.preventDefault();
  runForwardTest().catch((error) => {
    console.error(error);
    $("#forwardTestStatus").textContent = "前向测试失败，请检查参数。";
  });
});

$("#walkForwardTestForm").addEventListener("submit", (event) => {
  event.preventDefault();
  runWalkForwardTest().catch((error) => {
    console.error(error);
    $("#walkForwardTestStatus").textContent = "滚动验证失败，请检查参数。";
  });
});

$("#stressTestForm").addEventListener("submit", (event) => {
  event.preventDefault();
  runStressTest().catch((error) => {
    console.error(error);
    $("#stressTestStatus").textContent = "压力测试失败，请检查参数。";
  });
});

$("#refreshButton").addEventListener("click", () => Promise.all([loadProviderStatus(), loadAppSettings(), loadWatchlist(), loadMarketBreadth(), loadMarketRegime(), loadResearchLibraries(), loadScreener(), loadAiBriefing(), loadPortfolio(), loadHealth()]));
$("#logoutButton").addEventListener("click", () => logout().catch((error) => console.error(error)));
window.addEventListener("resize", drawChart);

const initialView = viewConfig[window.location.hash.slice(1)] ? window.location.hash.slice(1) : "market";
setActiveView(initialView, { updateHash: false, focus: false });

async function bootstrap() {
  await loadCurrentUser();
  await Promise.all([loadProviderStatus(), loadAppSettings(), loadFactorDefinitions(), loadWatchlist(), loadMarketBreadth(), loadMarketRegime(), loadResearchLibraries(), loadScreener(), loadScreenerPresets(), loadAiBriefing(), loadSources(), loadEvents(), loadAutomationRuns(), loadAutomationSettings(), loadNotifications(), loadPortfolio(), loadHealth()]);
  connectMarketStream();
}

bootstrap().catch((error) => {
    console.error(error);
    $("#watchlistBody").innerHTML = `<tr><td colspan="8">数据加载失败</td></tr>`;
  });
