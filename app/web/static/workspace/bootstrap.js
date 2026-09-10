import { ensureMarketToolsAssets, ensureProfessionalChartAssets } from "./core/assets.js";
import { formatPrice, formatTime } from "./core/format.js";
import { escapeHtml, fetchJson } from "./core/http.js";
import { state } from "./core/state.js";
import { $, riskClass, riskText, sentimentClass, severityClass, signalClass, signalText, translateText, viewConfig } from "./core/ui.js";

import {
  calendarDateKey,
  calendarWeekStart,
  loadFinancialCalendar,
  loadMarketOverview,
  loadMarketTools,
  openFinancialCalendarEvent,
  renderFinancialCalendar,
  renderFinancialCalendarDays,
  renderMarketHeatmap,
  renderMarketOverviewChart,
  renderMarketRanking,
} from "./features/market-tools.js";
setBacktestSparklineRenderer(drawSparkline);

async function loadCurrentUser() {
  const session = await fetchJson("/api/auth/me");
  state.currentUser = session.user;
  state.csrfToken = session.csrf_token;
  window.AppTheme?.apply(session.preferences?.theme || "system");
  const label = document.querySelector("#currentUserLabel");
  if (label) label.textContent = `${session.user.display_name} · ${session.user.role === "admin" ? "管理员" : "用户"}`;
  document.body.classList.toggle("viewer-mode", session.user.role !== "admin");
}

async function logout() {
  await fetchJson("/api/auth/logout", { method: "POST" });
  window.location.href = "/login";
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
  const viewport = $("#marketStrip");
  const previousScrollLeft = viewport.scrollLeft;
  const activeChanged = state.marketStripActiveSymbol !== state.activeSymbol;
  const current = state.symbols.find((item) => item.symbol === state.activeSymbol);
  const source = [current, ...state.symbols]
    .filter(Boolean)
    .filter((item, index, rows) => rows.findIndex((row) => row.symbol === item.symbol) === index);
  $("#marketStripCount").textContent = `${source.length} 个标的`;
  viewport.innerHTML = source.length ? source.map((item) => {
    const direction = item.change_percent >= 0 ? "up" : "down";
    const active = item.symbol === state.activeSymbol ? "active" : "";
    return `
      <button class="ticker-tile ${active}" type="button" data-symbol="${escapeHtml(item.symbol)}" aria-pressed="${active ? "true" : "false"}" title="${escapeHtml(item.name)} · ${escapeHtml(item.market)}">
        <span class="ticker-card-heading"><strong>${escapeHtml(item.symbol)}</strong><small>${escapeHtml(item.market)}</small></span>
        <span class="ticker-card-name">${escapeHtml(item.name)}</span>
        <span class="ticker-card-quote"><b>${formatPrice(item.price)}</b><em class="${direction}">${item.change_percent > 0 ? "+" : ""}${item.change_percent.toFixed(2)}%</em></span>
        <span class="ticker-card-metrics"><span class="mini-badge ${signalClass[item.signal]}">${signalText[item.signal]}</span><small>RSI ${item.rsi.toFixed(1)}</small><small>趋势 ${item.trend_score}</small></span>
      </button>
    `;
  }).join("") : `<div class="market-strip-empty"><span>暂无自选标的</span><a href="/watchlist" data-view="watchlist">管理自选</a></div>`;
  state.marketStripActiveSymbol = state.activeSymbol;
  requestAnimationFrame(() => {
    viewport.scrollLeft = activeChanged ? 0 : previousScrollLeft;
    updateMarketStripControls();
  });
  window.lucide?.createIcons();
}

function updateMarketStripControls() {
  const viewport = $("#marketStrip");
  const maximum = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
  $("#marketStripPrevious").disabled = viewport.scrollLeft <= 2 || maximum === 0;
  $("#marketStripNext").disabled = viewport.scrollLeft >= maximum - 2 || maximum === 0;
}

function scrollMarketStrip(direction) {
  const viewport = $("#marketStrip");
  const card = viewport.querySelector(".ticker-tile");
  const gap = Number.parseFloat(getComputedStyle(viewport).gap) || 10;
  viewport.scrollBy({ left: direction * ((card?.getBoundingClientRect().width || 232) + gap), behavior: "smooth" });
}

function renderWatchlist() {
  $("#matchCount").textContent = `${state.filtered.length} 个标的`;
  renderSymbolOptions(state.availableSymbols);
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

function renderSymbolOptions(items) {
  $("#symbolOptions").innerHTML = items.map((item) => `
    <option value="${item.symbol}">${item.name} · ${item.market}</option>
  `).join("");
}

function renderWatchlistInstrumentResults(items, query = "") {
  const target = $("#watchlistInstrumentResults");
  if (!target || state.watchlistEditing) {
    if (target) target.innerHTML = "";
    return;
  }
  const needle = query.trim().toLowerCase();
  const assetLabels = { stock: "股票", etf: "ETF", crypto: "现货", crypto_perpetual: "永续", index: "指数" };
  const rows = items
    .filter((item) => !needle || `${item.symbol} ${item.name} ${item.market}`.toLowerCase().includes(needle))
    .sort((left, right) => (left.asset_class === "index" ? -1 : 0) - (right.asset_class === "index" ? -1 : 0))
    .slice(0, 30);
  target.innerHTML = rows.map((item) => `
    <button class="instrument-search-result" type="button" role="option" data-watchlist-instrument="${escapeHtml(item.symbol)}">
      <b>${escapeHtml(item.symbol)}</b><span>${escapeHtml(item.name)} · ${escapeHtml(item.market)}</span><small>${escapeHtml(assetLabels[item.asset_class] || item.asset_class)}</small>
    </button>
  `).join("") || `<div class="empty-cell">未找到匹配标的</div>`;
}

async function searchAvailableSymbols(query) {
  const needle = query.trim();
  if (needle.length < 2) {
    renderSymbolOptions(state.availableSymbols);
    renderWatchlistInstrumentResults(state.availableSymbols, needle);
    return;
  }
  const items = await fetchJson(`/api/symbols?query=${encodeURIComponent(needle)}`);
  renderSymbolOptions(items);
  renderWatchlistInstrumentResults(items, needle);
}

function scheduleAvailableSymbolSearch(event) {
  clearTimeout(state.symbolSearchTimer);
  state.symbolSearchTimer = setTimeout(() => {
    searchAvailableSymbols(event.target.value).catch(console.error);
  }, 220);
}

function emptyTableRow(columns, message) {
  return `<tr><td colspan="${columns}" class="empty-cell">${escapeHtml(message)}</td></tr>`;
}

import { loadDashboard } from "./features/dashboard.js";

function groupColorValue(color) {
  return { blue: "var(--app-blue)", green: "var(--app-green)", yellow: "var(--app-yellow)", red: "var(--app-red)", gray: "var(--app-text-muted)", purple: "var(--app-purple)" }[color] || "var(--app-blue)";
}

function renderWatchlistGroupOptions() {
  const options = state.watchlistGroups.map((group) => `<option value="${escapeHtml(group.id)}">${escapeHtml(group.name)} (${group.item_count})</option>`).join("");
  const filterValue = $("#watchlistGroupFilter").value;
  $("#watchlistGroupFilter").innerHTML = `<option value="">全部分组</option>${options}`;
  $("#watchlistGroupFilter").value = filterValue;
  $("#watchlistBulkGroup").innerHTML = `<option value="">移动到...</option>${options}`;
  $("#watchlistResourceGroup").innerHTML = options;
  $("#watchlistGroupList").innerHTML = state.watchlistGroups.map((group) => `
    <article class="source-item"><div><strong><span class="group-chip" style="--group-color:${groupColorValue(group.color)}">${escapeHtml(group.name)}</span></strong><small>${group.item_count} 个标的${group.is_default ? " · 默认" : ""}</small></div><div class="row-actions">${group.is_default ? "" : `<button class="icon-button admin-write" type="button" data-watchlist-group-rename="${group.id}" title="重命名"><i data-lucide="pencil"></i></button><button class="icon-button admin-write" type="button" data-watchlist-group-delete="${group.id}" title="删除分组"><i data-lucide="trash-2"></i></button>`}</div></article>
  `).join("");
  window.lucide?.createIcons();
}

function renderWatchlistResourceTable(page) {
  state.watchlistResources = page.items;
  state.watchlistResourceTotal = page.total;
  const pages = Math.max(1, Math.ceil(page.total / page.page_size));
  $("#watchlistResourceSummary").textContent = `${page.total} 个${state.watchlistResourceStatus === "active" ? "活动" : "已停用"}标的 · ${state.watchlistGroups.length} 个分组`;
  $("#watchlistPageLabel").textContent = `${page.page} / ${pages}`;
  $("#watchlistPreviousPage").disabled = page.page <= 1;
  $("#watchlistNextPage").disabled = page.page >= pages;
  $("#watchlistResourceBody").innerHTML = page.items.length ? page.items.map((item) => {
    const snapshot = item.snapshot;
    const selected = state.watchlistSelected.has(item.symbol);
    return `<tr class="${selected ? "selected" : ""}" data-resource-symbol="${escapeHtml(item.symbol)}">
      <td><input type="checkbox" data-watchlist-select="${escapeHtml(item.symbol)}" ${selected ? "checked" : ""} aria-label="选择 ${escapeHtml(item.symbol)}" /></td>
      <td><div class="symbol-cell"><strong>${escapeHtml(item.symbol)}</strong><small>${escapeHtml(item.name)}</small></div></td>
      <td>${escapeHtml(item.market)}</td>
      <td><div class="tag-list"><span class="group-chip" style="--group-color:${groupColorValue(item.group_color)}">${escapeHtml(item.group_name)}</span>${item.tags.map((tag) => `<span class="tag-chip">${escapeHtml(tag)}</span>`).join("")}</div></td>
      <td>${snapshot ? formatPrice(snapshot.price) : "--"}</td>
      <td class="${snapshot ? (snapshot.change_percent >= 0 ? "up" : "down") : "flat"}">${snapshot ? `${snapshot.change_percent > 0 ? "+" : ""}${snapshot.change_percent.toFixed(2)}%` : "--"}</td>
      <td>${snapshot ? snapshot.rsi.toFixed(1) : "--"}</td>
      <td class="${snapshot ? (snapshot.macd >= 0 ? "up" : "down") : "flat"}">${snapshot ? snapshot.macd.toFixed(2) : "--"}</td>
      <td>${snapshot ? `<span class="badge ${signalClass[snapshot.signal]}">${signalText[snapshot.signal]}</span>` : "--"}</td>
      <td><div class="row-actions"><button class="icon-button" type="button" data-watchlist-open-chart="${escapeHtml(item.symbol)}" title="打开 K 线"><i data-lucide="chart-candlestick"></i></button><button class="icon-button admin-write" type="button" data-watchlist-edit="${escapeHtml(item.symbol)}" title="编辑"><i data-lucide="pencil"></i></button><button class="icon-button admin-write" type="button" data-watchlist-toggle="${escapeHtml(item.symbol)}" data-watchlist-enable="${item.enabled ? "false" : "true"}" title="${item.enabled ? "停用" : "恢复"}"><i data-lucide="${item.enabled ? "archive" : "archive-restore"}"></i></button></div></td>
    </tr>`;
  }).join("") : emptyTableRow(10, "当前条件下没有标的");
  renderWatchlistSelection();
  window.lucide?.createIcons();
}

function renderWatchlistSelection() {
  const count = state.watchlistSelected.size;
  $("#watchlistBulkBar").hidden = count === 0;
  $("#watchlistSelectedCount").textContent = `已选择 ${count} 项`;
  $("#watchlistBulkStatus").textContent = state.watchlistResourceStatus === "active" ? "批量停用" : "批量恢复";
  $("#watchlistSelectAll").checked = state.watchlistResources.length > 0 && state.watchlistResources.every((item) => state.watchlistSelected.has(item.symbol));
}

async function loadWatchlistResources(resetPage = false) {
  if (resetPage) state.watchlistResourcePage = 1;
  const params = new URLSearchParams({
    status: state.watchlistResourceStatus,
    sort_by: state.watchlistResourceSort,
    direction: state.watchlistResourceDirection,
    page: String(state.watchlistResourcePage),
    page_size: String(state.watchlistResourcePageSize),
  });
  const filters = {
    query: $("#watchlistResourceSearch").value.trim(),
    group_id: $("#watchlistGroupFilter").value,
    market: $("#watchlistMarketFilter").value,
    signal: $("#watchlistSignalFilter").value,
  };
  Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, value); });
  const [groups, page] = await Promise.all([
    fetchJson("/api/watchlist/groups"),
    fetchJson(`/api/watchlist/items?${params.toString()}`),
  ]);
  state.watchlistGroups = groups;
  renderWatchlistGroupOptions();
  renderWatchlistResourceTable(page);
}

function openWatchlistDrawer(item = null) {
  state.watchlistEditing = item;
  $("#watchlistDrawer").hidden = false;
  $("#watchlistDrawerTitle").textContent = item ? `编辑 ${item.symbol}` : "添加 Watchlist 标的";
  $("#watchlistResourceSymbol").disabled = Boolean(item);
  $("#watchlistResourceSymbol").value = item?.symbol || "";
  $("#watchlistResourceGroup").value = item?.group_id || state.watchlistGroups[0]?.id || "";
  $("#watchlistResourceTags").value = item?.tags.join(",") || "";
  $("#watchlistResourceNote").value = item?.note || "";
  const snapshot = item?.snapshot;
  $("#watchlistDrawerSummary").innerHTML = item ? `<div><span>最新价</span><strong>${snapshot ? formatPrice(snapshot.price) : "--"}</strong><small>${escapeHtml(item.market)}</small></div><div><span>涨跌幅</span><strong class="${snapshot?.change_percent >= 0 ? "up" : "down"}">${snapshot ? `${snapshot.change_percent > 0 ? "+" : ""}${snapshot.change_percent.toFixed(2)}%` : "--"}</strong><small>RSI ${snapshot ? snapshot.rsi.toFixed(1) : "--"}</small></div><div><span>信号</span><strong>${snapshot ? signalText[snapshot.signal] : "--"}</strong><small>${escapeHtml(item.provider)}</small></div>` : "";
  $("#watchlistResourceStatus").textContent = "";
  renderWatchlistInstrumentResults(state.availableSymbols, "");
}

function closeWatchlistDrawer() {
  $("#watchlistDrawer").hidden = true;
  state.watchlistEditing = null;
}

async function saveWatchlistResource(event) {
  event.preventDefault();
  const tags = $("#watchlistResourceTags").value.split(/[,，]/).map((item) => item.trim()).filter(Boolean);
  const body = { group_id: $("#watchlistResourceGroup").value, tags, note: $("#watchlistResourceNote").value.trim() };
  $("#watchlistResourceStatus").textContent = "正在保存...";
  if (state.watchlistEditing) {
    await fetchJson(`/api/watchlist/items/${state.watchlistEditing.symbol}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } else {
    body.symbol = $("#watchlistResourceSymbol").value.trim().toUpperCase();
    await fetchJson("/api/watchlist/items", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  }
  closeWatchlistDrawer();
  await Promise.all([loadWatchlistResources(true), loadWatchlist(), loadDashboard()]);
}

async function runWatchlistBulk(action, extra = {}) {
  if (!state.watchlistSelected.size) return;
  await fetchJson("/api/watchlist/items/bulk-actions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbols: [...state.watchlistSelected], action, ...extra }) });
  state.watchlistSelected.clear();
  await Promise.all([loadWatchlistResources(true), loadWatchlist(), loadDashboard()]);
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

import {
  renderMarketRegime,
  renderMarketRegimeTransition,
  renderRegimeScenarioRisk,
  renderRegimeExposure,
  renderRegimePlaybook,
  renderFilterValidation,
  renderFilterContribution,
  renderFilterAdmission,
  renderFilterStressTest,
  renderDataCatalog,
  renderDataQuality,
  renderDataFreshness,
  renderResearchHealth,
  renderResearchDashboard,
  renderResearchReadiness,
  renderResearchCoverage,
  renderFactorCoverage,
  renderFactorLifecycle,
  renderDataLineage,
  renderResearchActionPlan,
  renderStrategyCandidates,
  renderAdmissionQueue,
  renderRevalidationQueue,
  renderResearchRunAudit,
  renderStrategyTemplates,
  renderStrategyValidations,
  renderStrategyValidationDecay,
  renderStrategyLeaderboard,
  renderStrategyRegimeFit,
  renderStrategyRegimeMatrix,
  renderStrategyCrowding,
  renderStrategyLifecycle,
  renderStrategyRiskControls,
  renderMarketStateControls,
  renderStrategyOperatingPlan,
  renderStrategyEvidencePacks,
  renderStrategyBacktestMonitor,
  renderStrategyForwardMonitor,
  renderStrategySensitivity,
  renderStrategyRevalidationPlan,
  renderStrategyValidationCoverage,
  renderScreenerPresets,
  renderFilterLibraryScore,
  renderFilterStrategyCoverage,
  renderRegimeFilterTemplates,
} from "./features/research.js";

function renderIndicators(items) {
  const grid = $("#indicatorGrid");
  if (!items?.length) {
    grid.innerHTML = `<div class="indicator-empty"><strong>暂无外部指标</strong><span>请在数据源管理中配置并启用指标资源 API Key。</span><a href="/data-sources">管理数据源</a></div>`;
    $("#signalBadge").textContent = "0 项";
    return;
  }
  const categoryLabels = { fundamental: "基本面", valuation: "估值", macro: "宏观", sentiment: "情绪" };
  const groups = Object.groupBy ? Object.groupBy(items, (item) => item.category) : items.reduce((result, item) => {
    (result[item.category] ||= []).push(item);
    return result;
  }, {});
  grid.innerHTML = Object.entries(groups).map(([category, entries]) => `
    <h3 class="indicator-group-title">${categoryLabels[category] || category}<span>${entries.length}</span></h3>
    ${entries.map((item) => {
      const numeric = typeof item.value === "number";
      const value = numeric ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 3, notation: Math.abs(item.value) >= 1e9 ? "compact" : "standard" }).format(item.value) : item.value;
      return `<article class="indicator-card ${item.stale ? "stale" : ""}">
        <span>${escapeHtml(item.label)}</span>
        <b>${escapeHtml(String(value ?? "--"))}<small>${escapeHtml(item.unit || "")}</small></b>
        <small title="${escapeHtml(item.source_id)}">${escapeHtml(item.source_name)}${item.stale ? " · 缓存" : ""}</small>
      </article>`;
    }).join("")}
  `).join("");
  $("#signalBadge").textContent = `${items.length} 项`;
}

function renderIndicatorSnapshotBundle(bundle) {
  renderIndicators(bundle?.items || []);
  if (!bundle?.items?.length && bundle?.warnings?.length) {
    $("#indicatorGrid").querySelector("span").textContent = bundle.warnings[0];
  }
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

function newsAssociationStatusLabel(status) {
  return ({
    queued: "等待标注", running: "正在标注", waiting_provider: "等待模型服务", review: "等待人工审核",
    succeeded: "标注完成", failed: "标注失败", manual: "人工结果", unprocessed: "尚未标注",
  })[status] || status || "尚未标注";
}

function renderNewsAssociations(item, editable) {
  const associationTags = (item.association_tags || []).filter((tag) => ["accepted", "pending"].includes(tag.status));
  const fallbackTags = associationTags.length ? [] : (item.symbols || []).map((symbol) => ({ symbol, relation: "primary", confidence: 1, status: "accepted" }));
  const tags = [...associationTags, ...fallbackTags];
  const visible = tags.slice(0, 3);
  const status = newsAssociationStatusLabel(item.association_status);
  const translatedStatus = escapeHtml(translateText(status));
  return `<span class="news-association-inline" role="group" aria-label="${escapeHtml(translateText("关联标的"))}">
    <i data-lucide="link-2" aria-hidden="true"></i>
    ${visible.length ? visible.map((tag) => {
      const relation = translateText(tag.relation === "secondary" ? "次要关联" : "主要关联");
      const review = tag.status === "pending" ? ` · ${translateText("等待人工审核")}` : "";
      const tooltip = `${tag.name || tag.symbol} · ${relation} · ${(Number(tag.confidence || 0) * 100).toFixed(0)}%${review}`;
      return `<span class="news-association-symbol ${tag.relation} ${tag.status === "pending" ? "pending" : ""}" title="${escapeHtml(tooltip)}">${escapeHtml(tag.symbol)}</span>`;
    }).join("") : `<span class="news-association-empty" title="${escapeHtml(translateText(item.manual_association ? "人工确认无可靠关联标的" : "暂无可靠关联标的"))}">${escapeHtml(translateText("未关联"))}</span>`}
    ${tags.length > visible.length ? `<span class="news-association-more" title="${escapeHtml(translateText("关联标的"))}">+${tags.length - visible.length}</span>` : ""}
    <span class="news-association-status-dot ${item.manual_association ? "manual" : ""} ${item.association_status === "failed" ? "failed" : ""}" title="${translatedStatus}" aria-label="${translatedStatus}"></span>
  </span>
  ${editable ? `<button class="news-association-edit admin-write" type="button" data-edit-news-association="${escapeHtml(item.id)}" title="${escapeHtml(translateText("编辑关联标的"))}" aria-label="${escapeHtml(translateText("编辑关联标的"))}"><i data-lucide="pencil" aria-hidden="true"></i></button>` : ""}`;
}

function renderNews(items) {
  const feedMode = state.activeView === "news";
  const ordered = feedMode ? items : [...items].reverse();
  $("#newsCount").textContent = feedMode ? `${items.length} / ${state.newsFeedTotal} 条` : `${items.length} 条`;
  $("#newsTimeline").innerHTML = ordered.length ? ordered.map((item) => `
    <article class="news-item ${item.sentiment}" data-news-id="${escapeHtml(item.id)}">
      <div class="news-item-heading"><strong>${escapeHtml(item.title)}</strong>${feedMode ? `<span>${formatTime(item.published_at)}</span>` : ""}</div>
      <p>${escapeHtml(item.summary)}</p>
      <div class="meta-row">
        ${feedMode ? renderNewsAssociations(item, state.currentUser?.role === "admin") : ""}
        <span class="mini-badge ${sentimentClass[item.sentiment]}">${escapeHtml(item.sentiment)}</span>
        <button class="news-source-badge" type="button" data-news-source="${item.source_id || ""}">${escapeHtml(item.source)}</button>
        <span>影响 ${item.impact_score}</span>
        ${feedMode ? `<button class="news-detail-command" type="button" data-news-id="${escapeHtml(item.id)}">${escapeHtml(translateText("查看详情"))}</button>` : `<span>${formatTime(item.published_at)}</span>`}
      </div>
    </article>
  `).join("") : `<div class="news-feed-empty"><i data-lucide="newspaper"></i><strong>${escapeHtml(translateText("当前条件下没有新闻"))}</strong><span>${escapeHtml(translateText("请切换新闻范围或清除来源筛选。"))}</span></div>`;
  $("#newsFeedProgress").textContent = feedMode ? `已显示 ${items.length} / ${state.newsFeedTotal} 条` : "";
  $("#loadMoreNews").hidden = !feedMode || items.length >= state.newsFeedTotal;
  window.lucide?.createIcons();
}

function loadStoredNewsSourceFilters() {
  try {
    const stored = JSON.parse(localStorage.getItem("app-trading.news-source-filter.v2") || "null");
    state.newsSourceFilterMode = stored?.mode === "selected" ? "selected" : "all";
    state.newsSourceFilterIds = new Set(Array.isArray(stored?.ids) ? stored.ids.map(Number).filter(Number.isInteger) : []);
  } catch {
    state.newsSourceFilterMode = "all";
    state.newsSourceFilterIds = new Set();
  }
}

function persistNewsSourceFilters() {
  localStorage.setItem("app-trading.news-source-filter.v2", JSON.stringify({ mode: state.newsSourceFilterMode, ids: [...state.newsSourceFilterIds] }));
}

function normalizeSourceSearch(value) {
  return String(value || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

function editDistance(left, right, limit) {
  if (Math.abs(left.length - right.length) > limit) return limit + 1;
  let previous = Array.from({ length: right.length + 1 }, (_item, index) => index);
  for (let leftIndex = 1; leftIndex <= left.length; leftIndex += 1) {
    const current = [leftIndex];
    let rowMinimum = current[0];
    for (let rightIndex = 1; rightIndex <= right.length; rightIndex += 1) {
      const cost = left[leftIndex - 1] === right[rightIndex - 1] ? 0 : 1;
      current[rightIndex] = Math.min(current[rightIndex - 1] + 1, previous[rightIndex] + 1, previous[rightIndex - 1] + cost);
      rowMinimum = Math.min(rowMinimum, current[rightIndex]);
    }
    if (rowMinimum > limit) return limit + 1;
    previous = current;
  }
  return previous[right.length];
}

function sourceFieldSearchScore(field, token) {
  if (!field) return Number.POSITIVE_INFINITY;
  if (field === token) return 0;
  if (field.startsWith(token)) return 2;
  const containedAt = field.indexOf(token);
  if (containedAt >= 0) return 5 + Math.min(containedAt, 10);
  const tolerance = token.length >= 8 ? 2 : token.length >= 4 ? 1 : 0;
  if (!tolerance) return Number.POSITIVE_INFINITY;
  const candidates = [field, ...field.split(" ")];
  const distance = Math.min(...candidates.map((candidate) => editDistance(candidate, token, tolerance)));
  return distance <= tolerance ? 20 + distance : Number.POSITIVE_INFINITY;
}

function sourceFacetSearchScore(item, tokens) {
  const fields = [item.name, item.provider, item.adapter].map(normalizeSourceSearch);
  let score = 0;
  for (const token of tokens) {
    const tokenScore = Math.min(...fields.map((field) => sourceFieldSearchScore(field, token)));
    if (!Number.isFinite(tokenScore)) return Number.POSITIVE_INFINITY;
    score += tokenScore;
  }
  return score;
}

function visibleNewsSourceFacets() {
  const query = normalizeSourceSearch($("#newsSourceFilterSearch")?.value || "");
  const facets = [...state.newsSourceFacets];
  if (state.newsSourceFilterMode === "selected") {
    state.newsSourceFilterIds.forEach((sourceId) => {
      if (facets.some((item) => item.source_id === sourceId)) return;
      const source = state.newsSourceRegistry.get(sourceId);
      if (source) facets.push({ ...source, count: 0 });
    });
  }
  if (!query) return facets.sort((left, right) => right.count - left.count || left.name.localeCompare(right.name));
  const tokens = query.split(" ").filter(Boolean);
  return facets.map((item) => ({ item, score: sourceFacetSearchScore(item, tokens) }))
    .filter(({ score }) => Number.isFinite(score))
    .sort((left, right) => left.score - right.score || right.item.count - left.item.count || left.item.name.localeCompare(right.item.name))
    .map(({ item }) => item);
}

function renderNewsSourceFilters() {
  const target = $("#newsSourceFilterOptions");
  if (!target) return;
  const visible = visibleNewsSourceFacets();
  target.innerHTML = visible.length ? visible.map((item) => `
    <label class="news-source-filter-option">
      <input type="checkbox" data-news-source-option="${item.source_id}" ${state.newsSourceFilterMode === "all" || state.newsSourceFilterIds.has(item.source_id) ? "checked" : ""} />
      <span>${escapeHtml(item.name)}<small>${escapeHtml([item.provider, item.adapter].filter(Boolean).join(" · "))}</small></span><b>${item.count}</b>
    </label>
  `).join("") : `<span class="news-source-filter-empty">没有匹配的新闻源</span>`;
  const label = state.newsSourceFilterMode === "all" ? "全部来源" : state.newsSourceFilterIds.size ? `已选 ${state.newsSourceFilterIds.size} 个来源` : "未选择来源";
  $("#newsSourceFilterButton span").textContent = label;
  updateNewsSyncScope();
}

function updateNewsSyncScope() {
  const selectedCount = state.newsSourceFilterIds.size;
  const emptySelection = state.newsSourceFilterMode === "selected" && selectedCount === 0;
  const label = state.newsSourceFilterMode === "all"
    ? translateText("同步全部来源并刷新")
    : translateText("同步已选 {count} 个来源并刷新", { count: selectedCount });
  const target = $("#newsSyncScopeLabel");
  if (target) target.textContent = label;
  const button = $("#syncNewsSources");
  if (button) button.disabled = Boolean(state.newsRefreshMode) || emptySelection;
}

function setNewsRefreshStatus(message = "", tone = "") {
  const target = $("#newsRefreshStatus");
  target.textContent = message;
  target.dataset.tone = tone;
}

function setNewsRefreshMode(mode = "") {
  state.newsRefreshMode = mode;
  const busy = Boolean(mode);
  const quickButton = $("#refreshNewsFeed");
  quickButton.disabled = busy;
  quickButton.classList.toggle("is-refreshing", mode === "feed");
  $("#newsSyncMenuButton").disabled = busy;
  $("#syncNewsSources").classList.toggle("is-refreshing", mode === "sources");
  updateNewsSyncScope();
}

function closeNewsSyncMenu() {
  $("#newsSyncMenu").hidden = true;
  $("#newsSyncMenuButton").setAttribute("aria-expanded", "false");
}

async function refreshVisibleNewsFeed() {
  if (state.newsRefreshMode || state.newsFeedLoading) return;
  setNewsRefreshMode("feed");
  setNewsRefreshStatus(translateText("正在刷新新闻列表..."), "watch");
  try {
    await loadNewsFeed({ reset: true });
    setNewsRefreshStatus(translateText("更新于 {time}", { time: formatTime(new Date().toISOString()) }), "ok");
  } catch (error) {
    console.error(error);
    setNewsRefreshStatus(translateText("新闻列表刷新失败"), "error");
  } finally {
    setNewsRefreshMode();
  }
}

async function syncVisibleNewsSources() {
  if (state.newsRefreshMode || state.currentUser?.role !== "admin") return;
  const selectedIds = state.newsSourceFilterMode === "selected" ? [...state.newsSourceFilterIds] : [];
  if (state.newsSourceFilterMode === "selected" && selectedIds.length === 0) {
    setNewsRefreshStatus(translateText("请先选择需要同步的新闻源"), "watch");
    return;
  }
  closeNewsSyncMenu();
  setNewsRefreshMode("sources");
  setNewsRefreshStatus(translateText("正在同步新闻源..."), "watch");
  try {
    const params = new URLSearchParams({ force: "true" });
    selectedIds.forEach((sourceId) => params.append("source_id", String(sourceId)));
    const results = await fetchJson(`/api/news-sources/refresh?${params}`, { method: "POST" });
    const fetched = results.reduce((total, item) => total + (item.fetched || 0), 0);
    const inserted = results.reduce((total, item) => total + (item.inserted || 0), 0);
    const failures = results.filter((item) => item.status === "failed" || item.error).length;
    await loadNewsFeed({ reset: true });
    if (!results.length) {
      setNewsRefreshStatus(translateText("当前范围没有可同步的新闻源"), "watch");
    } else {
      setNewsRefreshStatus(translateText("同步完成：{sources} 个来源，抓取 {fetched} 条，新增 {inserted} 条，失败 {failed} 个", {
        sources: results.length, fetched, inserted, failed: failures,
      }), failures ? "error" : "ok");
    }
  } catch (error) {
    console.error(error);
    setNewsRefreshStatus(translateText("新闻源同步失败，请查看刷新诊断"), "error");
  } finally {
    setNewsRefreshMode();
  }
}

function renderNewsPanelMode() {
  const feedMode = state.activeView === "news";
  $("#newsPanel").classList.toggle("news-feed-mode", feedMode);
  $("#newsPanelTitle").textContent = feedMode ? "全市场新闻" : "关键新闻";
  $("#newsPanelContext").textContent = feedMode
    ? state.newsScope === "global" ? "全部已入库新闻" : `${state.activeSymbol} 相关新闻`
    : "";
  document.querySelectorAll("[data-news-scope]").forEach((button) => button.classList.toggle("active", button.dataset.newsScope === state.newsScope));
}

async function applyNewsSourceFilter(sourceId, { exclusive = false } = {}) {
  const id = Number(sourceId);
  if (exclusive) {
    state.newsSourceFilterMode = "selected";
    state.newsSourceFilterIds = new Set([id]);
  } else {
    if (state.newsSourceFilterMode === "all") {
      state.newsSourceFilterMode = "selected";
      state.newsSourceFilterIds = new Set(state.newsSourceFacets.map((item) => item.source_id));
    }
    if (state.newsSourceFilterIds.has(id)) state.newsSourceFilterIds.delete(id);
    else state.newsSourceFilterIds.add(id);
    if (state.newsSourceFilterIds.size === state.newsSourceFacets.length) {
      state.newsSourceFilterMode = "all";
      state.newsSourceFilterIds.clear();
    }
  }
  persistNewsSourceFilters();
  renderNewsSourceFilters();
  await loadNewsFeed({ reset: true });
}

function renderNewsDetail(detail) {
  state.newsDetail = detail;
  const symbolTags = detail.symbol_tags || [];
  const annotation = detail.event_annotation;
  const impacts = detail.impact_links || [];
  const tagLabel = { primary: translateText("主要关联"), secondary: translateText("次要关联") };
  const statusLabel = { queued: translateText("等待标注"), running: translateText("正在标注"), waiting_provider: translateText("等待模型服务"), review: translateText("等待人工审核"), succeeded: translateText("标注完成"), failed: translateText("标注失败"), manual: translateText("人工结果"), unprocessed: translateText("尚未标注") };
  const canEdit = state.currentUser?.role === "admin";
  $("#newsDrawerTitle").textContent = detail.title;
  $("#newsDrawerBody").innerHTML = `
    <div class="meta-row">
      <span class="mini-badge ${sentimentClass[detail.sentiment]}">${detail.sentiment}</span>
      <span>${detail.source}</span>
      <span>影响 ${detail.impact_score}</span>
      <span>${formatTime(detail.published_at)}</span>
    </div>
    <p>${detail.classification_summary}</p>
    ${annotation ? `<section class="drawer-section news-event-profile">
      <strong>${escapeHtml(translateText("事件概况"))}</strong>
      <div class="news-event-profile-grid">
        <span><small>${escapeHtml(translateText("影响范围"))}</small><b>${escapeHtml(translateText(annotation.scope))}</b></span>
        <span><small>${escapeHtml(translateText("事件分类"))}</small><b>${escapeHtml(translateText(annotation.taxonomy))}</b></span>
        <span><small>${escapeHtml(translateText("影响周期"))}</small><b>${escapeHtml(translateText(annotation.horizon))}</b></span>
        <span><small>${escapeHtml(translateText("显著性 / 新颖度"))}</small><b>${annotation.significance} / ${annotation.novelty}</b></span>
      </div>
      <span>${escapeHtml(annotation.summary)}</span>
      <div class="news-event-topics">${[...(annotation.topics || []), ...(annotation.macro_factors || [])].map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>
    </section>` : ""}
    ${impacts.length ? `<section class="drawer-section">
      <strong>${escapeHtml(translateText("影响目标与传导路径"))}</strong>
      <div class="news-impact-list">${impacts.map((impact) => `<article class="news-impact-item ${impact.link_type}">
        <div><b>${escapeHtml(impact.symbol || impact.target_label)}</b><span class="news-impact-kind">${escapeHtml(translateText(impact.link_type === "derived" ? "推导影响" : impact.link_type === "direct" ? "直接提及" : "历史关联"))}</span><span>${impact.direction} · ${impact.impact_score}</span></div>
        <small>${escapeHtml(translateText("置信度"))} ${(Number(impact.confidence) * 100).toFixed(0)}% · ${escapeHtml(translateText("相关度"))} ${(Number(impact.relevance) * 100).toFixed(0)}% · ${escapeHtml(translateText(impact.horizon))}</small>
        ${impact.path?.length ? `<div class="news-impact-path">${impact.path.map((step) => `<span>${escapeHtml(step)}</span>`).join('<i data-lucide="arrow-right"></i>')}</div>` : ""}
        <small>${escapeHtml(impact.evidence || translateText("无证据说明"))}</small>
      </article>`).join("")}</div>
    </section>` : ""}
    <section class="drawer-section">
      <strong>关键点</strong>
      ${detail.key_points.map((item) => `<span>${item}</span>`).join("")}
    </section>
    <section class="drawer-section">
      <strong>关联标的</strong>
      <span>${statusLabel[detail.tagging_status] || detail.tagging_status}</span>
      ${detail.manual_override && detail.override_note ? `<span>${translateText("备注")}：${escapeHtml(detail.override_note)}</span>` : ""}
      ${symbolTags.length ? `<div class="news-detail-tags">${symbolTags.map((tag) => `<span class="news-detail-tag ${tag.relation}"><span class="news-detail-tag-row"><b>${escapeHtml(tag.symbol)} · ${tagLabel[tag.relation]} · ${(Number(tag.confidence) * 100).toFixed(0)}%</b>${tag.relation === "primary" ? `<button class="text-button" type="button" data-news-symbol-chart="${escapeHtml(tag.symbol)}">${translateText("查看 K 线")}</button>` : ""}</span><small>${escapeHtml(tag.evidence || translateText("无证据说明"))}</small></span>`).join("")}</div>` : `<span>${detail.manual_override ? translateText("人工确认无可靠关联标的") : (detail.related_symbols.map(escapeHtml).join(" / ") || translateText("暂无可靠关联标的"))}</span>`}
      ${canEdit ? `<button class="text-button" type="button" data-edit-news-symbols>${translateText("编辑关联标的")}</button>` : ""}
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

function renderNewsSymbolSelections() {
  const render = (items, relation) => [...items.values()].map((item) => `<button type="button" data-remove-news-symbol="${escapeHtml(item.id)}" data-relation="${relation}" title="移除 ${escapeHtml(item.symbol)}"><b>${escapeHtml(item.symbol)}</b><span>${escapeHtml(item.name)}</span><i data-lucide="x"></i></button>`).join("");
  $("#newsPrimarySymbols").innerHTML = render(state.newsPrimarySelection, "primary");
  $("#newsSecondarySymbols").innerHTML = render(state.newsSecondarySelection, "secondary");
  window.lucide?.createIcons();
}

function openNewsSymbolEditor() {
  const detail = state.newsDetail;
  if (!detail || state.currentUser?.role !== "admin") return;
  state.newsPrimarySelection = new Map();
  state.newsSecondarySelection = new Map();
  for (const tag of detail.symbol_tags || []) {
    const item = { id: tag.instrument_id, symbol: tag.symbol, name: tag.name };
    (tag.relation === "primary" ? state.newsPrimarySelection : state.newsSecondarySelection).set(item.id, item);
  }
  state.newsSymbolRelation = "primary";
  document.querySelectorAll("[data-news-symbol-relation]").forEach((button) => button.classList.toggle("active", button.dataset.newsSymbolRelation === "primary"));
  $("#newsSymbolArticleTitle").textContent = detail.title;
  $("#newsSymbolNote").value = detail.override_note || "";
  $("#newsSymbolSearch").value = "";
  $("#newsSymbolSearchResults").innerHTML = "";
  $("#restoreNewsSymbols").hidden = !detail.manual_override;
  renderNewsSymbolSelections();
  $("#newsSymbolModal").hidden = false;
  $("#newsSymbolSearch").focus();
  state.newsSymbolSearchGeneration += 1;
  searchNewsSymbols("", state.newsSymbolSearchGeneration).catch(console.error);
}

async function openNewsSymbolEditorForArticle(newsId) {
  if (!newsId || state.currentUser?.role !== "admin") return;
  const detail = await fetchJson(`/api/news-events/${newsId}`);
  state.newsDetail = detail;
  openNewsSymbolEditor();
}

function closeNewsSymbolEditor() {
  $("#newsSymbolModal").hidden = true;
  clearTimeout(state.newsSymbolSearchTimer);
}

async function searchNewsSymbols(query, generation) {
  const results = await fetchJson(`/api/instruments?query=${encodeURIComponent(query)}`);
  if (generation !== state.newsSymbolSearchGeneration) return;
  $("#newsSymbolSearchResults").innerHTML = results.slice(0, 20).map((item) => `<button class="news-symbol-result" type="button" data-add-news-symbol="${escapeHtml(item.id)}" data-symbol="${escapeHtml(item.symbol)}" data-name="${escapeHtml(item.name)}"><b>${escapeHtml(item.symbol)}</b><span>${escapeHtml(item.name)}</span><small>${escapeHtml(item.market)}</small></button>`).join("") || `<span class="empty-state">没有匹配的标的</span>`;
}

async function refreshNewsAfterSymbolEdit(detail) {
  renderNewsDetail(detail);
  await loadNewsFeed({ reset: true });
  await window.ProfessionalChart?.refreshNewsMarkers?.();
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
  state.activeView = viewConfig[viewName] ? viewName : "market";
  if (state.currentUser) syncMarketStreamForView(state.activeView);
  const modules = new Set(config.modules);
  $("#viewTitle").textContent = config.title;
  $("#terminalGrid").classList.toggle("view-wide", config.wide);
  document.body.classList.toggle("model-center-active", state.activeView === "models");
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === viewName);
  });
  document.querySelectorAll("[data-module]").forEach((section) => {
    const sectionModules = section.dataset.module.split(/\s+/);
    const visible = sectionModules.some((item) => modules.has(item));
    section.classList.toggle("module-hidden", !visible);
  });
  renderNewsPanelMode();
  if (options.updateHistory !== false && window.location.pathname !== config.path) {
    history.pushState({ view: viewName }, "", config.path);
  }
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
  if (state.activeView === "market") window.setTimeout(drawChart, 80);
}

import {
  renderBacktests,
  renderBacktestBenchmark,
  renderBacktestForwardConsistency,
  renderForwardTests,
  renderForwardLeaderboard,
  renderWalkForwardTests,
  renderWalkForwardLeaderboard,
  renderStressTests,
  renderStressLeaderboard,
  renderSensitivityTest,
  renderRobustnessReport,
  renderStrategyDrift,
  renderStrategyAllocation,
  renderTradeReadiness,
  renderRegimeRiskGate,
  renderFactorAdmission,
  renderFactorLeaderboard,
  renderFactorCorrelation,
  renderRegimeFactorFit,
  renderRegimeAttribution,
  renderParameterOptimization,
  setBacktestSparklineRenderer,
} from "./features/backtesting.js";

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
  window.ProfessionalChart?.setPaperTradingData(summary.positions, state.paperOrders, state.paperFills);
  const account = summary.account;
  const money = (value) => `${Number(value).toLocaleString(window.AppI18n?.getLocale() || "zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${account.currency}`;
  $("#paperAccountTabs").innerHTML = state.paperAccounts.map((item) => `<button class="${item.id === account.id ? "active" : ""}" type="button" data-paper-account="${escapeHtml(item.id)}">${item.currency}</button>`).join("");
  const metrics = [
    ["账户权益", account.equity, ""], ["可用现金", account.available_cash, ""], ["持仓市值", account.market_value, ""],
    ["未实现盈亏", account.unrealized_pnl, account.unrealized_pnl >= 0 ? "up" : "down"],
    ["已实现盈亏", account.realized_pnl, account.realized_pnl >= 0 ? "up" : "down"], ["资金使用率", account.utilization_percent, "percent"],
  ];
  $("#paperAccountMetrics").innerHTML = metrics.map(([label, value, kind]) => `<div><span>${label}</span><strong class="${kind === "up" || kind === "down" ? kind : ""}">${kind === "percent" ? `${Number(value).toFixed(2)}%` : money(value)}</strong></div>`).join("");
  $("#paperPositionCount").textContent = summary.positions.length;
  $("#paperOrderCount").textContent = state.paperOrders.filter((item) => ["pending", "triggered"].includes(item.status)).length;
  renderPaperTradingTable();
}

function paperStatusLabel(status) {
  return ({ pending: "待成交", triggered: "已触发", filled: "已成交", canceled: "已撤销", rejected: "已拒绝", expired: "已过期" })[status] || status;
}

function paperEmpty(message) {
  return `<div class="paper-empty"><i data-lucide="inbox"></i><strong>${message}</strong><span>模拟结果仅供研究，不代表真实成交。</span></div>`;
}

function renderPaperTradingTable() {
  if (!state.portfolio) return;
  const currency = state.portfolio.account.currency;
  const money = (value) => `${Number(value).toLocaleString(window.AppI18n?.getLocale() || "zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;
  let html = "";
  if (state.paperTab === "positions") {
    html = state.portfolio.positions.length ? `<table class="paper-table"><thead><tr><th>标的</th><th>方向</th><th>数量</th><th>持仓均价</th><th>最新价</th><th>市值</th><th>未实现盈亏</th><th>仓位占比</th><th>止盈 / 止损</th><th>行情</th><th></th></tr></thead><tbody>${state.portfolio.positions.map((item) => `<tr data-paper-position-symbol="${escapeHtml(item.instrument_id)}"><td><strong>${escapeHtml(item.symbol)}</strong><small>${escapeHtml(item.name)}</small></td><td><span class="paper-side ${item.side}">${item.side === "long" ? "多" : "空"}</span></td><td>${item.quantity} ${escapeHtml(item.unit_label)}</td><td>${money(item.average_price)}</td><td>${item.current_price === null ? "--" : money(item.current_price)}</td><td>${money(item.market_value)}</td><td class="${item.unrealized_pnl >= 0 ? "up" : "down"}"><strong>${item.unrealized_pnl >= 0 ? "+" : ""}${money(item.unrealized_pnl)}</strong><small>${item.unrealized_pnl_percent.toFixed(2)}%</small></td><td>${state.portfolio.account.market_value ? (item.market_value / state.portfolio.account.market_value * 100).toFixed(1) : "0.0"}%</td><td>${item.take_profit ? money(item.take_profit) : "--"} / ${item.stop_loss ? money(item.stop_loss) : "--"}</td><td><span class="paper-quote ${item.stale_price ? "stale" : ""}">${item.stale_price ? "过期" : "有效"}</span><small>${item.quote_as_of ? formatTime(item.quote_as_of) : "无报价"}</small></td><td><button class="icon-button" type="button" data-paper-close="${item.id}" title="平仓"><i data-lucide="circle-x"></i></button></td></tr>`).join("")}</tbody></table>` : paperEmpty("当前账户暂无持仓");
  } else if (state.paperTab === "orders" || state.paperTab === "history") {
    const active = state.paperTab === "orders";
    const rows = state.paperOrders.filter((item) => active ? ["pending", "triggered"].includes(item.status) : !["pending", "triggered"].includes(item.status));
    html = rows.length ? `<table class="paper-table"><thead><tr><th>时间</th><th>标的</th><th>方向</th><th>操作</th><th>类型</th><th>数量</th><th>限价 / 触发价</th><th>状态</th><th></th></tr></thead><tbody>${rows.map((item) => `<tr><td>${formatTime(item.submitted_at)}</td><td><strong>${escapeHtml(item.symbol)}</strong></td><td><span class="paper-side ${item.position_side}">${item.position_side === "long" ? "多" : "空"}</span></td><td>${item.effect === "open" ? "开仓" : "平仓"}</td><td>${({market:"市价",limit:"限价",stop:"止损",stop_limit:"止损限价"})[item.order_type]}</td><td>${item.quantity} ${escapeHtml(item.unit_label)}</td><td>${item.limit_price || "--"} / ${item.stop_price || "--"}</td><td><span class="paper-order-status ${item.status}">${paperStatusLabel(item.status)}</span>${item.rejection_reason ? `<small>${escapeHtml(item.rejection_reason)}</small>` : ""}</td><td>${active ? `<button class="icon-button" data-paper-cancel="${item.id}" title="撤单"><i data-lucide="x"></i></button>` : ""}</td></tr>`).join("")}</tbody></table>` : paperEmpty(active ? "当前没有待成交委托" : "暂无历史委托");
  } else if (state.paperTab === "fills") {
    html = state.paperFills.length ? `<table class="paper-table"><thead><tr><th>成交时间</th><th>标的</th><th>方向</th><th>操作</th><th>数量</th><th>成交价</th><th>成交额</th><th>手续费</th><th>已实现盈亏</th><th>来源</th></tr></thead><tbody>${state.paperFills.map((item) => `<tr><td>${formatTime(item.executed_at)}</td><td><strong>${escapeHtml(item.symbol)}</strong><small>模拟成交</small></td><td><span class="paper-side ${item.position_side}">${item.position_side === "long" ? "多" : "空"}</span></td><td>${item.effect === "open" ? "开仓" : "平仓"}</td><td>${item.quantity} ${escapeHtml(item.unit_label)}</td><td>${money(item.price)}</td><td>${money(item.gross_amount)}</td><td>${money(item.fee)}</td><td class="${item.realized_pnl >= 0 ? "up" : "down"}">${money(item.realized_pnl)}</td><td>${escapeHtml(item.source)}</td></tr>`).join("")}</tbody></table>` : paperEmpty("暂无模拟成交");
  } else {
    html = state.paperLedger.length ? `<table class="paper-table"><thead><tr><th>时间</th><th>类型</th><th>金额</th><th>余额</th><th>说明</th></tr></thead><tbody>${state.paperLedger.map((item) => `<tr><td>${formatTime(item.created_at)}</td><td>${escapeHtml(item.entry_type)}</td><td class="${item.amount >= 0 ? "up" : "down"}">${money(item.amount)}</td><td>${money(item.balance_after)}</td><td>${escapeHtml(item.detail)}</td></tr>`).join("")}</tbody></table>` : paperEmpty("暂无资金流水");
  }
  $("#paperTradingTable").innerHTML = html;
  window.lucide?.createIcons();
}

function hidePositionInstrumentResults() {
  const target = $("#positionInstrumentResults");
  target.hidden = true;
  $("#positionSymbol").setAttribute("aria-expanded", "false");
}

function renderPositionInstrumentResults(items, query = "") {
  const target = $("#positionInstrumentResults");
  const needle = query.trim().toLowerCase();
  const assetLabels = { stock: "股票", etf: "ETF", crypto: "加密货币", crypto_perpetual: "永续合约" };
  const rows = items.filter((item) => item.positionable && (
    !needle || `${item.id} ${item.symbol} ${item.name} ${item.market}`.toLowerCase().includes(needle)
  )).slice(0, 30);
  target.innerHTML = rows.map((item) => `
    <button class="instrument-search-result" type="button" role="option" data-position-instrument="${escapeHtml(item.id)}">
      <b>${escapeHtml(item.symbol)}</b><span>${escapeHtml(item.name)} · ${escapeHtml(item.market)}</span><small>${escapeHtml(assetLabels[item.asset_class] || item.asset_class)}</small>
    </button>
  `).join("") || `<div class="empty-cell">未找到可交易标的</div>`;
  state.positionOptionIndex = -1;
  target.hidden = false;
  $("#positionSymbol").setAttribute("aria-expanded", "true");
}

async function searchPositionInstruments(query = "") {
  const needle = query.trim();
  const rows = await fetchJson(`/api/instruments${needle.length >= 2 ? `?query=${encodeURIComponent(needle)}` : ""}`);
  const merged = new Map(state.positionInstruments.map((item) => [item.id, item]));
  rows.forEach((item) => merged.set(item.id, item));
  state.positionInstruments = [...merged.values()];
  renderPositionInstrumentResults(state.positionInstruments, needle);
}

function renderPositionUnits(instrument) {
  const select = $("#positionQuantityUnit");
  const units = instrument?.quantity_units || [];
  select.innerHTML = units.length
    ? units.map((item) => `<option value="${escapeHtml(item.code)}" data-step="${item.step}" data-multiplier="${item.multiplier}">${escapeHtml(item.label)}</option>`).join("")
    : '<option value="">单位</option>';
  select.disabled = !units.length;
  const step = units[0]?.step || 0.0001;
  $("#positionQuantity").step = String(step);
  $("#positionQuantity").min = String(step);
}

async function selectPositionInstrument(instrument, { fetchQuote = true, manual = false } = {}) {
  if (!instrument?.positionable) return;
  state.selectedPositionInstrument = instrument;
  state.positionSelectionManual = manual;
  $("#positionInstrumentId").value = instrument.id;
  $("#positionSymbol").value = `${instrument.symbol} · ${instrument.name}`;
  $("#positionPriceCurrency").textContent = instrument.currency;
  renderPositionUnits(instrument);
  hidePositionInstrumentResults();
  if (!fetchQuote) return;
  const generation = ++state.positionQuoteGeneration;
  const status = $("#positionQuoteStatus");
  status.classList.remove("error");
  const chartQuote = window.ProfessionalChart?.currentQuote?.();
  if (chartQuote?.instrument_id === instrument.id && Number(chartQuote.price) > 0) {
    $("#positionAveragePrice").value = Number(chartQuote.price).toFixed(chartQuote.price >= 1 ? 2 : 6);
    status.textContent = `${chartQuote.source} · K线最新价格 · ${formatTime(chartQuote.as_of)}`;
    updatePaperOrderEstimate();
  } else {
    status.textContent = "正在获取当前市场价格...";
  }
  try {
    const quote = await fetchJson(`/api/instruments/${encodeURIComponent(instrument.id)}/quote`);
    if (generation !== state.positionQuoteGeneration) return;
    $("#positionAveragePrice").value = Number(quote.price).toFixed(quote.price >= 1 ? 2 : 6);
    $("#positionPriceCurrency").textContent = quote.currency;
    status.textContent = `${quote.source} · ${quote.stale ? "延迟价格" : "当前价格"} · ${formatTime(quote.as_of)}`;
    updatePaperOrderEstimate();
  } catch (error) {
    if (generation !== state.positionQuoteGeneration) return;
    console.error(error);
    if (Number($("#positionAveragePrice").value) > 0) {
      status.textContent = "报价接口暂不可用，已使用K线最新价格";
    } else {
      status.classList.add("error");
      status.textContent = `当前价格获取失败，暂时无法提交模拟委托：${error.message || "行情不可用"}`;
    }
  }
}

function resetPositionForm() {
  $("#positionForm").reset();
  state.selectedPositionInstrument = null;
  state.positionSelectionManual = false;
  $("#positionInstrumentId").value = "";
  $("#positionPriceCurrency").textContent = "--";
  $("#positionQuoteStatus").className = "position-quote-status";
  $("#positionQuoteStatus").textContent = "选择标的后自动获取当前市场价格";
  renderPositionUnits(null);
  $("#paperOrderEffect").value = "open";
  $("#positionSide").value = "long";
  document.querySelectorAll("[data-paper-side]").forEach((button) => button.classList.toggle("active", button.dataset.paperSide === "long"));
  document.querySelectorAll("[data-paper-effect]").forEach((button) => button.classList.toggle("active", button.dataset.paperEffect === "open"));
  updatePaperOrderFields();
}

function updatePaperOrderFields() {
  const type = $("#paperOrderType").value;
  $("#paperLimitField").hidden = !["limit", "stop_limit"].includes(type);
  $("#paperStopField").hidden = !["stop", "stop_limit"].includes(type);
  updatePaperOrderEstimate();
}

function updatePaperOrderEstimate() {
  const account = state.paperAccounts.find((item) => item.id === state.paperAccountId);
  const quantity = Number($("#positionQuantity")?.value || 0);
  const unit = Number($("#positionQuantityUnit")?.selectedOptions?.[0]?.dataset.multiplier || 1);
  const type = $("#paperOrderType")?.value;
  const price = ["limit", "stop_limit"].includes(type) ? Number($("#paperLimitPrice")?.value || 0) : Number($("#positionAveragePrice")?.value || 0);
  const gross = quantity * unit * price;
  $("#paperOrderEstimate").innerHTML = gross && account ? `<span>预估名义价值</span><strong>${gross.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${account.currency}</strong><small>预估手续费 ${(gross * 0.0002).toFixed(2)} · 模拟市价滑点 1 bps</small>` : "选择标的并填写数量后显示预估金额";
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

function renderSnapshot() {
  const item = state.symbols.find((row) => row.symbol === state.activeSymbol);
  if (!item) return;
  if (!window.ProfessionalChart?.currentInstrumentId()) {
    $("#activeSymbol").textContent = `${item.symbol} · ${item.name}`;
    $("#lastPrice").textContent = formatPrice(item.price);
    $("#lastChange").textContent = `${item.change_percent > 0 ? "+" : ""}${item.change_percent.toFixed(2)}%`;
    $("#lastChange").className = item.change_percent >= 0 ? "up" : "down";
    $("#trendScore").textContent = item.trend_score;
  }
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

function themeColor(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function drawChart() {
  if (window.ProfessionalChart) {
    window.ProfessionalChart.resize();
    return;
  }
  const canvas = $("#priceChart");
  if (!(canvas instanceof HTMLCanvasElement)) return;
  const context = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.floor(rect.width * ratio);
  canvas.height = Math.floor(rect.height * ratio);
  context.scale(ratio, ratio);

  const width = rect.width;
  const height = rect.height;
  const pad = { top: 16, right: 58, bottom: 50, left: 12 };
  const eventLane = { top: height - 38, bottom: height - 8, y: height - 22 };
  context.clearRect(0, 0, width, height);
  context.fillStyle = themeColor("--app-chart", "#131820");
  context.fillRect(0, 0, width, height);

  const candles = state.candles;
  if (!candles.length) return;

  const eventGroups = groupChartEventsByCandle(candles, state.chartEvents, state.interval);
  const values = candles.flatMap((item) => [item.high, item.low, item.ma20, item.ma60]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const xStep = plotWidth / candles.length;
  const y = (value) => pad.top + (max - value) / range * plotHeight;
  const x = (index) => pad.left + index * xStep + xStep / 2;

  context.strokeStyle = themeColor("--app-border", "#252b35");
  context.lineWidth = 1;
  context.font = "12px Inter, sans-serif";
  context.fillStyle = themeColor("--app-text-muted", "#8b95a7");
  for (let i = 0; i <= 5; i += 1) {
    const yy = pad.top + plotHeight / 5 * i;
    context.beginPath();
    context.moveTo(pad.left, yy);
    context.lineTo(width - pad.right, yy);
    context.stroke();
    const label = max - range / 5 * i;
    context.fillText(label.toFixed(2), width - pad.right + 8, yy + 4);
  }

  const ma20Color = themeColor("--app-yellow", "#f3ba2f");
  const ma60Color = themeColor("--cyan", "#00bcd4");
  drawLine(context, candles.map((item, index) => [x(index), y(item.ma20)]), ma20Color, 1.4);
  drawLine(context, candles.map((item, index) => [x(index), y(item.ma60)]), ma60Color, 1.4);

  const candleWidth = Math.max(3, Math.min(10, xStep * 0.62));
  candles.forEach((item, index) => {
    const rising = item.close >= item.open;
    const color = rising ? themeColor("--app-green", "#22ab94") : themeColor("--app-red", "#f23645");
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

  context.fillStyle = themeColor("--app-text-muted", "#8b95a7");
  context.fillText("MA20", pad.left + 10, 18);
  context.fillStyle = ma20Color;
  context.fillRect(pad.left + 48, 10, 20, 3);
  context.fillStyle = themeColor("--app-text-muted", "#8b95a7");
  context.fillText("MA60", pad.left + 80, 18);
  context.fillStyle = ma60Color;
  context.fillRect(pad.left + 118, 10, 20, 3);

  context.strokeStyle = themeColor("--app-border", "#252b35");
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(pad.left, eventLane.top);
  context.lineTo(width - pad.right, eventLane.top);
  context.stroke();

  const chartGeometry = {
    width,
    height,
    pad,
    eventLane,
    plotBottom: pad.top + plotHeight,
    xStep,
    x,
    y,
  };
  drawEventLinkOverlay(context, candles, eventGroups, chartGeometry);
  drawEventMarkers(context, candles, eventGroups, chartGeometry);
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

function chartIntervalMilliseconds(interval) {
  return {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
  }[interval] || 15 * 60 * 1000;
}

function groupChartEventsByCandle(candles, events, interval) {
  if (!candles.length || !events.length) return [];

  const candleTimes = candles.map((candle) => new Date(candle.time).getTime());
  const intervalMs = chartIntervalMilliseconds(interval);
  const groups = new Map();

  events.forEach((event) => {
    const eventTime = new Date(event.published_at).getTime();
    if (!Number.isFinite(eventTime)) return;

    let low = 0;
    let high = candleTimes.length - 1;
    let candleIndex = -1;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      if (candleTimes[middle] <= eventTime) {
        candleIndex = middle;
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }

    if (candleIndex < 0 || eventTime >= candleTimes[candleIndex] + intervalMs) return;
    if (!groups.has(candleIndex)) groups.set(candleIndex, []);
    groups.get(candleIndex).push(event);
  });

  return [...groups.entries()]
    .map(([candleIndex, groupedEvents]) => ({
      candleIndex,
      events: groupedEvents.sort((left, right) => (
        right.impact_score - left.impact_score
        || new Date(right.published_at).getTime() - new Date(left.published_at).getTime()
      )),
    }))
    .sort((left, right) => left.candleIndex - right.candleIndex);
}

function chartEventColor(event) {
  const eventColors = {
    bullish: themeColor("--app-green", "#22ab94"),
    bearish: themeColor("--app-red", "#f23645"),
    neutral: themeColor("--app-text-muted", "#8b95a7"),
    mixed: themeColor("--app-yellow", "#f3ba2f"),
  };
  return eventColors[event?.sentiment] || themeColor("--app-text-muted", "#8b95a7");
}

function activeEventCandleIndex() {
  return state.activeEventCandleIndex ?? state.pinnedEventCandleIndex;
}

function drawEventLinkOverlay(context, candles, eventGroups, chart) {
  const candleIndex = activeEventCandleIndex();
  if (candleIndex === null) return;
  const group = eventGroups.find((item) => item.candleIndex === candleIndex);
  const candle = candles[candleIndex];
  if (!group || !candle) return;

  const color = chartEventColor(group.events[0]);
  const x = chart.x(candleIndex);
  const bodyTop = chart.y(Math.max(candle.open, candle.close));
  const bodyBottom = chart.y(Math.min(candle.open, candle.close));
  const endIndex = Math.min(candles.length - 1, candleIndex + 4);

  context.save();
  context.strokeStyle = color;
  context.globalAlpha = 0.5;
  context.lineWidth = 1;
  context.setLineDash([4, 4]);
  context.beginPath();
  context.moveTo(x, chart.pad.top);
  context.lineTo(x, chart.eventLane.top);
  context.stroke();
  context.setLineDash([]);

  context.globalAlpha = 0.9;
  context.lineWidth = 3;
  context.strokeRect(
    x - Math.max(3, chart.xStep * 0.35),
    bodyTop - 2,
    Math.max(6, chart.xStep * 0.7),
    Math.max(5, bodyBottom - bodyTop + 4),
  );

  if (endIndex > candleIndex) {
    context.globalAlpha = 0.95;
    context.lineWidth = 3;
    context.beginPath();
    for (let index = candleIndex; index <= endIndex; index += 1) {
      const pointX = chart.x(index);
      const pointY = chart.y(candles[index].close);
      if (index === candleIndex) context.moveTo(pointX, pointY);
      else context.lineTo(pointX, pointY);
    }
    context.stroke();
  }
  context.restore();
}

function drawEventMarkers(context, candles, eventGroups, chart) {
  state.eventMarkers = [];
  if (!eventGroups.length || !candles.length) return;
  const activeIndex = activeEventCandleIndex();
  eventGroups.forEach(({ candleIndex, events }) => {
    const primaryEvent = events[0];
    const x = chart.x(candleIndex);
    const y = chart.eventLane.y;
    const markerRadius = events.length > 1 ? 7 : primaryEvent.impact_score >= 75 ? 5.5 : 4.5;
    const color = chartEventColor(primaryEvent);

    context.strokeStyle = color;
    context.globalAlpha = 0.38;
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(x, chart.eventLane.top + 3);
    context.lineTo(x, y - markerRadius - 2);
    context.stroke();
    context.globalAlpha = 1;

    context.fillStyle = color;
    context.strokeStyle = themeColor("--app-chart", "#11161d");
    context.lineWidth = 2;
    context.beginPath();
    context.arc(x, y, markerRadius, 0, Math.PI * 2);
    context.fill();
    context.stroke();

    if (candleIndex === activeIndex) {
      context.strokeStyle = color;
      context.lineWidth = 2;
      context.beginPath();
      context.arc(x, y, markerRadius + 3.5, 0, Math.PI * 2);
      context.stroke();
    }

    if (events.length > 1) {
      context.fillStyle = "#ffffff";
      context.font = "600 9px Inter, sans-serif";
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(String(events.length), x, y + 0.5);
      context.textAlign = "start";
      context.textBaseline = "alphabetic";
    }

    state.eventMarkers.push({
      x,
      y,
      radius: Math.max(12, markerRadius + 5),
      events,
      candleIndex,
      trendEndIndex: Math.min(candles.length - 1, candleIndex + 4),
    });
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
  context.strokeStyle = values[values.length - 1] >= values[0]
    ? themeColor("--app-green", "#22ab94")
    : themeColor("--app-red", "#f23645");
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

function showEventTooltip(marker, pinned = false) {
  if (state.eventTooltipPinned && !pinned) return;
  const tooltip = $("#eventTooltip");
  const chartRect = $("#priceChart").getBoundingClientRect();
  const events = marker.events;
  state.eventTooltipPinned = pinned;
  tooltip.hidden = false;
  tooltip.dataset.pinned = pinned ? "true" : "false";
  tooltip.setAttribute("role", pinned ? "dialog" : "tooltip");
  tooltip.innerHTML = `
    <div class="event-tooltip-head">
      <strong>${events.length > 1 ? `${events.length} 条新闻事件` : "新闻事件"}</strong>
      <span>${pinned ? "点击查看详情" : ""}</span>
    </div>
    <div class="event-tooltip-list">
      ${events.map((item) => `
        <button class="event-tooltip-item" type="button" data-news-id="${escapeHtml(item.id)}">
          <strong>${escapeHtml(item.title)}</strong>
          <span>${escapeHtml(item.source)} · 影响 ${item.impact_score} · ${formatTime(item.published_at)}</span>
          <p>${escapeHtml(item.summary)}</p>
        </button>
      `).join("")}
    </div>
  `;
  const tooltipRect = tooltip.getBoundingClientRect();
  const preferredLeft = marker.x + 14;
  const left = preferredLeft + tooltipRect.width <= chartRect.width - 10
    ? preferredLeft
    : marker.x - tooltipRect.width - 14;
  const top = Math.min(
    Math.max(marker.y - tooltipRect.height / 2, 10),
    Math.max(10, chartRect.height - tooltipRect.height - 10),
  );
  tooltip.style.left = `${Math.max(10, left)}px`;
  tooltip.style.top = `${top}px`;
}

function hideEventTooltip(force = false) {
  if (state.eventTooltipPinned && !force) return;
  const tooltip = $("#eventTooltip");
  tooltip.hidden = true;
  tooltip.dataset.pinned = "false";
  state.eventTooltipPinned = false;
}

function eventMarkerForCandle(candleIndex) {
  return state.eventMarkers.find((marker) => marker.candleIndex === candleIndex) || null;
}

function eventMarkerForNews(newsId) {
  return state.eventMarkers.find((marker) => marker.events.some((item) => item.id === newsId)) || null;
}

function renderChartEventSummary(marker, pinned = false) {
  const summary = $("#chartEventSummary");
  if (!marker) {
    summary.hidden = true;
    summary.innerHTML = "";
    return;
  }

  const primaryEvent = marker.events[0];
  const extraCount = marker.events.length - 1;
  summary.hidden = false;
  summary.innerHTML = `
    <span class="chart-event-swatch ${sentimentClass[primaryEvent.sentiment] || "neutral"}"></span>
    <div class="chart-event-summary-copy">
      <span>${pinned ? "已固定" : "热点联动"} · ${formatTime(primaryEvent.published_at)}</span>
      <strong data-i18n-skip>${escapeHtml(primaryEvent.title)}</strong>
    </div>
    <div class="chart-event-summary-meta">
      <span>${escapeHtml(primaryEvent.source)}</span>
      <span>影响 ${primaryEvent.impact_score}</span>
      <span>${escapeHtml(primaryEvent.price_reaction)}</span>
      ${extraCount > 0 ? `<span>另有 ${extraCount} 条</span>` : ""}
    </div>
    <button class="text-button" type="button" data-news-id="${escapeHtml(primaryEvent.id)}">查看详情</button>
  `;
}

function syncLinkedNews(marker, scroll = false) {
  const ids = new Set(marker ? marker.events.map((item) => item.id) : []);
  let firstMatch = null;
  document.querySelectorAll("#newsTimeline .news-item").forEach((item) => {
    const active = ids.has(item.dataset.newsId);
    item.classList.toggle("linked-active", active);
    if (active && !firstMatch) firstMatch = item;
  });

  if (!scroll || !firstMatch) return;
  const panelRect = $("#newsPanel").getBoundingClientRect();
  const panelVisible = panelRect.bottom > 0 && panelRect.top < window.innerHeight;
  if (panelVisible) firstMatch.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function setChartEventInteraction(marker, { pinned = false, syncList = false } = {}) {
  if (!marker) return null;
  state.activeEventCandleIndex = marker.candleIndex;
  if (pinned) state.pinnedEventCandleIndex = marker.candleIndex;
  redrawChartIfVisible();
  const currentMarker = eventMarkerForCandle(marker.candleIndex);
  renderChartEventSummary(currentMarker, pinned || state.pinnedEventCandleIndex === marker.candleIndex);
  syncLinkedNews(currentMarker, syncList);
  return currentMarker;
}

function redrawChartIfVisible() {
  const rect = $("#priceChart").getBoundingClientRect();
  if (rect.width > 0 && rect.height > 0 && state.candles.length) drawChart();
}

function clearChartEventHover() {
  state.activeEventCandleIndex = null;
  redrawChartIfVisible();
  const pinnedMarker = state.pinnedEventCandleIndex === null
    ? null
    : eventMarkerForCandle(state.pinnedEventCandleIndex);
  renderChartEventSummary(pinnedMarker, Boolean(pinnedMarker));
  syncLinkedNews(pinnedMarker);
}

function clearChartEventSelection({ redraw = true } = {}) {
  state.activeEventCandleIndex = null;
  state.pinnedEventCandleIndex = null;
  hideEventTooltip(true);
  renderChartEventSummary(null);
  syncLinkedNews(null);
  if (redraw) redrawChartIfVisible();
}

function isCurrentSymbolRequest(symbol, generation) {
  return state.activeSymbol === symbol && state.symbolRequestGeneration === generation;
}

function beginSymbolRequest() {
  state.symbolRequestController?.abort();
  state.symbolRequestController = new AbortController();
  state.symbolRequestGeneration += 1;
  return {
    signal: state.symbolRequestController.signal,
    generation: state.symbolRequestGeneration,
  };
}

function loadSymbolResource(url, symbol, request, apply) {
  return fetchJson(url, { signal: request.signal })
    .then((payload) => {
      if (isCurrentSymbolRequest(symbol, request.generation)) apply(payload);
    })
    .catch((error) => {
      if (error.name !== "AbortError") console.error(error);
    });
}

function loadMarketSymbolContext(symbol, request) {
  return Promise.allSettled([
    loadSymbolResource(`/api/indicator-snapshots/${encodeURIComponent(symbol)}`, symbol, request, renderIndicatorSnapshotBundle),
    loadSymbolResource(`/api/news/${symbol}?limit=10`, symbol, request, (news) => {
      state.news = news;
      state.chartEvents = news.filter((item) => item.impact_score >= 50);
      clearChartEventSelection({ redraw: false });
      renderNews(news);
    }),
    loadSymbolResource(`/api/ai/insights/${symbol}`, symbol, request, (insights) => {
      state.aiInsights = insights;
      renderAiInsights(insights);
    }),
    loadSymbolResource(`/api/factors/${symbol}`, symbol, request, (factors) => {
      state.factors = factors;
      renderFactors(factors);
    }),
    loadSymbolResource(`/api/alerts?symbol=${symbol}`, symbol, request, (alerts) => {
      state.alerts = alerts;
      state.alertEvaluations = [];
      renderAlerts(alerts);
    }),
  ]);
}

async function loadNewsFeed({ reset = false, request = null } = {}) {
  if (state.newsFeedLoading) {
    if (reset) state.newsFeedPendingReset = true;
    return;
  }
  if (reset) state.newsFeedOffset = 0;
  const emptySourceSelection = state.newsSourceFilterMode === "selected" && state.newsSourceFilterIds.size === 0;
  state.newsFeedLoading = true;
  $("#loadMoreNews").disabled = true;
  $("#loadMoreNews").textContent = "正在加载...";
  try {
    const params = new URLSearchParams({ limit: String(state.newsFeedPageSize), offset: String(state.newsFeedOffset) });
    if (state.newsScope === "symbol") params.set("symbol", state.activeSymbol);
    if (state.newsSourceFilterMode === "selected") {
      state.newsSourceFilterIds.forEach((sourceId) => params.append("source_id", String(sourceId)));
    }
    const page = await fetchJson(`/api/news?${params}`, request ? { signal: request.signal } : {});
    state.newsSourceFacets = page.source_counts;
    page.source_counts.forEach((item) => state.newsSourceRegistry.set(item.source_id, item));
    state.news = emptySourceSelection ? [] : reset || page.offset === 0 ? page.items : [...state.news, ...page.items];
    state.newsFeedTotal = emptySourceSelection ? 0 : page.total;
    state.newsFeedOffset = state.news.length;
    state.chartEvents = [];
    persistNewsSourceFilters();
    renderNewsPanelMode();
    renderNewsSourceFilters();
    renderNews(state.news);
  } finally {
    state.newsFeedLoading = false;
    $("#loadMoreNews").disabled = false;
    $("#loadMoreNews").textContent = "加载更多";
  }
  if (state.newsFeedPendingReset) {
    state.newsFeedPendingReset = false;
    return loadNewsFeed({ reset: true });
  }
}

function loadNewsSymbolContext(symbol, request) {
  if (state.activeView === "news") return loadNewsFeed({ reset: true, request });
  return loadSymbolResource(`/api/news/${symbol}?limit=10`, symbol, request, (news) => {
    state.news = news;
    state.chartEvents = news.filter((item) => item.impact_score >= 50);
    renderNews(news);
  });
}

function loadAiSymbolContext(symbol, request) {
  return loadSymbolResource(`/api/ai/insights/${symbol}`, symbol, request, (insights) => {
    state.aiInsights = insights;
    renderAiInsights(insights);
  });
}

function loadAlertSymbolContext(symbol, request) {
  return loadSymbolResource(`/api/alerts?symbol=${symbol}`, symbol, request, (alerts) => {
    state.alerts = alerts;
    state.alertEvaluations = [];
    renderAlerts(alerts);
  });
}

async function loadResearchSymbolContext(symbol, request) {
  const activeFactor = $("#backtestFactor")?.value || "momentum-news";
  const options = { signal: request.signal };
  const [factors, backtests, forwardTests, forwardLeaderboard, walkForwardTests, walkForwardLeaderboard, stressTests, stressLeaderboard, backtestBenchmark, backtestForwardConsistency, robustnessReport, strategyDrift, strategyAllocation, tradeReadiness, regimeRiskGate, factorLeaderboard, factorCorrelation, regimeFactorFit, regimeAttribution] = await Promise.all([
    fetchJson(`/api/factors/${symbol}`, options),
    fetchJson(`/api/backtests/${symbol}`, options),
    fetchJson(`/api/forward-tests/${symbol}`, options),
    fetchJson(`/api/forward-leaderboard?symbol=${symbol}&limit=6`, options),
    fetchJson(`/api/walk-forward-tests/${symbol}`, options),
    fetchJson(`/api/walk-forward-leaderboard?symbol=${symbol}&limit=6`, options),
    fetchJson(`/api/stress-tests/${symbol}`, options),
    fetchJson(`/api/stress-leaderboard?symbol=${symbol}&limit=6`, options),
    fetchJson(`/api/backtest-benchmark/${symbol}?factor_id=${activeFactor}&interval=${state.interval}`, options),
    fetchJson(`/api/backtest-forward-consistency/${symbol}?factor_id=${activeFactor}`, options),
    fetchJson(`/api/robustness/${symbol}`, options),
    fetchJson(`/api/drift/${symbol}`, options),
    fetchJson(`/api/allocation/${symbol}`, options),
    fetchJson(`/api/trade-readiness/${symbol}?factor_id=${activeFactor}&interval=${state.interval}`, options),
    fetchJson(`/api/regime-risk-gate/${symbol}?factor_id=${activeFactor}&interval=${state.interval}`, options),
    fetchJson(`/api/factor-leaderboard/${symbol}`, options),
    fetchJson(`/api/factor-correlation/${symbol}`, options),
    fetchJson(`/api/regime-factor-fit/${symbol}?interval=${state.interval}&limit=6`, options),
    fetchJson(`/api/regime-attribution/${symbol}?factor_id=${activeFactor}`, options),
  ]);
  if (!isCurrentSymbolRequest(symbol, request.generation)) return;
  clearChartEventSelection({ redraw: false });
  state.factors = factors;
  state.backtests = backtests;
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
  renderFactors(factors);
  renderBacktests(backtests);
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
}

function loadSymbolDataForView(viewName, symbol, request) {
  if (viewName === "market") return loadMarketSymbolContext(symbol, request);
  if (viewName === "news") return loadNewsSymbolContext(symbol, request);
  if (viewName === "ai") return loadAiSymbolContext(symbol, request);
  if (viewName === "alerts") return loadAlertSymbolContext(symbol, request);
  if (viewName === "factors" || viewName === "backtest") {
    return loadResearchSymbolContext(symbol, request).catch((error) => {
      if (error.name !== "AbortError") console.error(error);
    });
  }
  return Promise.resolve();
}

function selectSymbol(symbol, { switchChart = true } = {}) {
  if (!symbol) return Promise.resolve();
  state.activeSymbol = String(symbol).toUpperCase();
  performance.mark("symbol:switch-start");
  applyFilters();
  renderSnapshot();
  const request = beginSymbolRequest();
  const chartPromise = switchChart && state.activeView === "market" && window.ProfessionalChart
    ? window.ProfessionalChart.selectLegacySymbol(state.activeSymbol)
    : Promise.resolve();
  const contextPromise = loadSymbolDataForView(state.activeView, state.activeSymbol, request);
  return Promise.allSettled([chartPromise, contextPromise]);
}

function openSymbolInMarket(symbol) {
  state.activeSymbol = String(symbol).toUpperCase();
  setActiveView("market");
  return loadMarketView();
}

async function loadWatchlist() {
  const [availableSymbols, watchlist] = await Promise.all([
    fetchJson("/api/symbols"),
    fetchJson("/api/watchlist"),
  ]);
  state.availableSymbols = availableSymbols;
  state.symbols = watchlist;
  const marketFilter = $("#watchlistMarketFilter");
  if (marketFilter) {
    const currentMarket = marketFilter.value;
    const markets = [...new Set(availableSymbols.map((item) => item.market))].sort();
    marketFilter.innerHTML = `<option value="">全部市场</option>${markets.map((market) => `<option value="${escapeHtml(market)}">${escapeHtml(market)}</option>`).join("")}`;
    marketFilter.value = currentMarket;
  }
  if (!state.symbols.some((item) => item.symbol === state.activeSymbol) && state.symbols.length) {
    state.activeSymbol = state.symbols[0].symbol;
  }
  applyFilters();
}

async function loadMarketWorkspace() {
  const [availableSymbols, watchlist, briefing] = await Promise.all([
    fetchJson("/api/symbols"),
    fetchJson("/api/watchlist"),
    fetchJson("/api/ai/briefing"),
    loadPortfolio(),
  ]);
  state.availableSymbols = availableSymbols;
  state.symbols = watchlist;
  state.aiBriefing = briefing;
  applyFilters();
  renderSnapshot();
  renderAiBriefing(briefing);
  const request = beginSymbolRequest();
  await loadMarketSymbolContext(state.activeSymbol, request);
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
  if (state.activeView === "dashboard") return;
  if (state.activeView === "watchlist") {
    const snapshots = new Map(payload.watchlist.map((item) => [item.symbol, item]));
    state.watchlistResources.forEach((item) => { item.snapshot = snapshots.get(item.symbol) || item.snapshot; });
    renderWatchlistResourceTable({
      page: state.watchlistResourcePage,
      page_size: state.watchlistResourcePageSize,
      total: state.watchlistResourceTotal,
      items: state.watchlistResources,
    });
    return;
  }
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

function syncMarketStreamForView(viewName) {
  const needsStream = ["dashboard", "market", "market-tools", "watchlist"].includes(viewName);
  if (needsStream) {
    if (!state.marketStream) connectMarketStream();
    return;
  }
  if (state.streamReconnectTimer) clearTimeout(state.streamReconnectTimer);
  state.streamReconnectTimer = null;
  const socket = state.marketStream;
  state.marketStream = null;
  socket?.close();
  renderStreamStatus("offline", "实时流未连接");
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
  state.paperAccounts = await fetchJson("/api/paper/accounts");
  if (!state.paperAccounts.length) return;
  if (!state.paperAccounts.some((item) => item.id === state.paperAccountId)) state.paperAccountId = state.paperAccounts[0].id;
  localStorage.setItem("open-trading.paper-account", state.paperAccountId);
  const accountId = encodeURIComponent(state.paperAccountId);
  const [summary, orders, fills, ledger] = await Promise.all([
    fetchJson(`/api/paper/portfolio?account_id=${accountId}`), fetchJson(`/api/paper/orders?account_id=${accountId}`),
    fetchJson(`/api/paper/fills?account_id=${accountId}`), fetchJson(`/api/paper/cash-ledger?account_id=${accountId}`),
  ]);
  state.paperOrders = orders; state.paperFills = fills; state.paperLedger = ledger;
  renderPortfolio(summary);
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
  const request = beginSymbolRequest();
  await loadResearchSymbolContext(state.activeSymbol, request);
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
  const request = beginSymbolRequest();
  await loadResearchSymbolContext(state.activeSymbol, request);
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
  const request = beginSymbolRequest();
  await Promise.all([
    loadEvents(), loadAutomationRuns(), loadNotifications(), loadHealth(),
    loadMarketBreadth(), loadAiBriefing(), loadNewsSymbolContext(state.activeSymbol, request),
    loadAlertSymbolContext(state.activeSymbol, request),
  ]);
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
  const clickedNews = event.target.closest("[data-news-id]");
  if (
    state.eventTooltipPinned
    && event.target !== $("#priceChart")
    && !$("#eventTooltip").contains(event.target)
    && !clickedNews
  ) {
    clearChartEventSelection();
  }

  const viewTarget = event.target.closest("[data-view]");
  if (viewTarget) {
    event.preventDefault();
    setActiveView(viewTarget.dataset.view, { focus: false });
    loadViewData(viewTarget.dataset.view).catch(console.error);
    return;
  }

  const watchlistInstrument = event.target.closest("[data-watchlist-instrument]");
  if (watchlistInstrument) {
    $("#watchlistResourceSymbol").value = watchlistInstrument.dataset.watchlistInstrument;
    $("#watchlistInstrumentResults").innerHTML = "";
    return;
  }

  const watchlistStatus = event.target.closest("[data-watchlist-status]");
  if (watchlistStatus) {
    state.watchlistResourceStatus = watchlistStatus.dataset.watchlistStatus;
    state.watchlistSelected.clear();
    document.querySelectorAll("[data-watchlist-status]").forEach((button) => button.classList.toggle("active", button === watchlistStatus));
    loadWatchlistResources(true).catch(console.error);
    return;
  }

  const watchlistSort = event.target.closest("[data-watchlist-sort]");
  if (watchlistSort) {
    const key = watchlistSort.dataset.watchlistSort;
    if (state.watchlistResourceSort === key) state.watchlistResourceDirection = state.watchlistResourceDirection === "asc" ? "desc" : "asc";
    else { state.watchlistResourceSort = key; state.watchlistResourceDirection = "asc"; }
    loadWatchlistResources(true).catch(console.error);
    return;
  }

  const watchlistEdit = event.target.closest("[data-watchlist-edit]");
  if (watchlistEdit) {
    openWatchlistDrawer(state.watchlistResources.find((item) => item.symbol === watchlistEdit.dataset.watchlistEdit));
    return;
  }

  const watchlistToggle = event.target.closest("[data-watchlist-toggle]");
  if (watchlistToggle) {
    fetchJson(`/api/watchlist/items/${watchlistToggle.dataset.watchlistToggle}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: watchlistToggle.dataset.watchlistEnable === "true" }) })
      .then(() => Promise.all([loadWatchlistResources(true), loadWatchlist(), loadDashboard()]))
      .catch(console.error);
    return;
  }

  const openChart = event.target.closest("[data-watchlist-open-chart]");
  if (openChart) {
    openSymbolInMarket(openChart.dataset.watchlistOpenChart).catch(console.error);
    return;
  }

  const groupDelete = event.target.closest("[data-watchlist-group-delete]");
  if (groupDelete) {
    if (!confirm("删除分组后，其中标的将移回默认分组。确认继续？")) return;
    fetchJson(`/api/watchlist/groups/${groupDelete.dataset.watchlistGroupDelete}`, { method: "DELETE" }).then(() => loadWatchlistResources()).catch((error) => { $("#watchlistGroupStatus").textContent = error.message; });
    return;
  }

  const groupRename = event.target.closest("[data-watchlist-group-rename]");
  if (groupRename) {
    const group = state.watchlistGroups.find((item) => item.id === groupRename.dataset.watchlistGroupRename);
    const name = prompt("输入新的分组名称", group?.name || "");
    if (!name?.trim()) return;
    fetchJson(`/api/watchlist/groups/${groupRename.dataset.watchlistGroupRename}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name.trim() }) }).then(() => loadWatchlistResources()).catch((error) => { $("#watchlistGroupStatus").textContent = error.message; });
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

  const marketToolTarget = event.target.closest("[data-market-tool-symbol]");
  if (marketToolTarget) {
    openSymbolInMarket(marketToolTarget.dataset.marketToolSymbol).catch(console.error);
    return;
  }

  const symbolTarget = event.target.closest("[data-symbol]");
  if (symbolTarget) selectSymbol(symbolTarget.dataset.symbol);

  const paperAccountTarget = event.target.closest("[data-paper-account]");
  if (paperAccountTarget) {
    state.paperAccountId = paperAccountTarget.dataset.paperAccount;
    loadPortfolio().catch(console.error);
    return;
  }
  const paperTabTarget = event.target.closest("[data-paper-tab]");
  if (paperTabTarget) {
    state.paperTab = paperTabTarget.dataset.paperTab;
    document.querySelectorAll("[data-paper-tab]").forEach((button) => button.classList.toggle("active", button === paperTabTarget));
    renderPaperTradingTable();
    return;
  }
  const paperCancelTarget = event.target.closest("[data-paper-cancel]");
  if (paperCancelTarget) {
    fetchJson(`/api/paper/orders/${paperCancelTarget.dataset.paperCancel}/cancel`, { method: "POST" }).then(loadPortfolio).catch((error) => window.alert(error.message));
    return;
  }
  const paperCloseTarget = event.target.closest("[data-paper-close]");
  if (paperCloseTarget) {
    if (window.confirm("确认按当前模拟市价全部平仓？")) fetchJson(`/api/paper/positions/${paperCloseTarget.dataset.paperClose}/close`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(loadPortfolio).catch((error) => window.alert(error.message));
    return;
  }
  const paperPositionRow = event.target.closest("[data-paper-position-symbol]");
  if (paperPositionRow) {
    window.ProfessionalChart?.selectInstrument?.(paperPositionRow.dataset.paperPositionSymbol);
    return;
  }

  const newsTarget = event.target.closest("[data-news-id]");
  if (newsTarget) {
    const linkedMarker = eventMarkerForNews(newsTarget.dataset.newsId);
    if (linkedMarker) setChartEventInteraction(linkedMarker, { pinned: true, syncList: true });
    hideEventTooltip(true);
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
  if (window.ProfessionalChart) return;
  if (state.eventTooltipPinned) return;
  const rect = $("#priceChart").getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const marker = state.eventMarkers.find((item) => Math.hypot(item.x - x, item.y - y) <= item.radius);
  $("#priceChart").style.cursor = marker ? "pointer" : "crosshair";
  if (marker) {
    const currentMarker = state.activeEventCandleIndex === marker.candleIndex
      ? marker
      : setChartEventInteraction(marker);
    showEventTooltip(currentMarker || marker);
  } else {
    hideEventTooltip();
    if (state.activeEventCandleIndex !== null) clearChartEventHover();
  }
});

$("#priceChart").addEventListener("click", (event) => {
  if (window.ProfessionalChart) return;
  const rect = $("#priceChart").getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const marker = state.eventMarkers.find((item) => Math.hypot(item.x - x, item.y - y) <= item.radius);
  if (!marker) {
    clearChartEventSelection();
    return;
  }
  const currentMarker = setChartEventInteraction(marker, { pinned: true, syncList: true });
  showEventTooltip(currentMarker || marker, true);
});

$("#priceChart").addEventListener("mouseleave", (event) => {
  if (window.ProfessionalChart) return;
  $("#priceChart").style.cursor = "crosshair";
  if (event.relatedTarget === $("#eventTooltip") || $("#eventTooltip").contains(event.relatedTarget)) return;
  hideEventTooltip();
  if (!state.eventTooltipPinned) clearChartEventHover();
});

$("#eventTooltip").addEventListener("mouseleave", (event) => {
  if (window.ProfessionalChart) return;
  if (event.relatedTarget === $("#priceChart")) return;
  hideEventTooltip();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && (state.eventTooltipPinned || activeEventCandleIndex() !== null)) {
    clearChartEventSelection();
  }
});

$("#newsTimeline").addEventListener("mouseover", (event) => {
  const newsItem = event.target.closest(".news-item[data-news-id]");
  if (!newsItem || state.eventTooltipPinned) return;
  const marker = eventMarkerForNews(newsItem.dataset.newsId);
  if (marker && state.activeEventCandleIndex !== marker.candleIndex) setChartEventInteraction(marker);
});

$("#newsTimeline").addEventListener("mouseout", (event) => {
  const newsItem = event.target.closest(".news-item[data-news-id]");
  if (!newsItem || newsItem.contains(event.relatedTarget) || state.eventTooltipPinned) return;
  clearChartEventHover();
});

$("#newsTimeline").addEventListener("click", (event) => {
  const editAssociation = event.target.closest("[data-edit-news-association]");
  if (editAssociation) {
    event.preventDefault();
    event.stopPropagation();
    openNewsSymbolEditorForArticle(editAssociation.dataset.editNewsAssociation).catch(console.error);
    return;
  }
  const source = event.target.closest("[data-news-source]");
  if (!source?.dataset.newsSource || state.activeView !== "news") return;
  event.stopPropagation();
  applyNewsSourceFilter(source.dataset.newsSource, { exclusive: true }).catch(console.error);
});

$("#newsSourceFilterButton").addEventListener("click", () => {
  const menu = $("#newsSourceFilterMenu");
  menu.hidden = !menu.hidden;
  $("#newsSourceFilterButton").setAttribute("aria-expanded", String(!menu.hidden));
  if (!menu.hidden) $("#newsSourceFilterSearch").focus();
});

$("#newsSourceFilterOptions").addEventListener("change", (event) => {
  const option = event.target.closest("[data-news-source-option]");
  if (option) applyNewsSourceFilter(option.dataset.newsSourceOption).catch(console.error);
});

$("#newsSourceFilterSearch").addEventListener("input", renderNewsSourceFilters);
$("#refreshNewsFeed").addEventListener("click", () => refreshVisibleNewsFeed());
$("#newsSyncMenuButton").addEventListener("click", () => {
  const menu = $("#newsSyncMenu");
  menu.hidden = !menu.hidden;
  $("#newsSyncMenuButton").setAttribute("aria-expanded", String(!menu.hidden));
});
$("#syncNewsSources").addEventListener("click", () => syncVisibleNewsSources());
$("#selectAllNewsSources").addEventListener("click", () => {
  state.newsSourceFilterMode = "all";
  state.newsSourceFilterIds.clear();
  persistNewsSourceFilters();
  renderNewsSourceFilters();
  loadNewsFeed({ reset: true }).catch(console.error);
});
$("#clearNewsSources").addEventListener("click", () => {
  state.newsSourceFilterMode = "selected";
  state.newsSourceFilterIds.clear();
  persistNewsSourceFilters();
  renderNewsSourceFilters();
  loadNewsFeed({ reset: true }).catch(console.error);
});
document.querySelectorAll("[data-news-scope]").forEach((button) => button.addEventListener("click", () => {
  state.newsScope = button.dataset.newsScope;
  renderNewsPanelMode();
  loadNewsFeed({ reset: true }).catch(console.error);
}));
$("#loadMoreNews").addEventListener("click", () => loadNewsFeed().catch(console.error));
document.addEventListener("click", (event) => {
  if (!event.target.closest("#newsSourceFilter")) {
    $("#newsSourceFilterMenu").hidden = true;
    $("#newsSourceFilterButton").setAttribute("aria-expanded", "false");
  }
  if (!event.target.closest("#newsSyncDropdown")) closeNewsSyncMenu();
});

$("#closeNewsDrawer").addEventListener("click", closeNewsDetail);
$("#newsDrawer").addEventListener("click", (event) => {
  if (event.target.id === "newsDrawer") closeNewsDetail();
});
$("#closeNewsSymbolModal").addEventListener("click", closeNewsSymbolEditor);
$("#cancelNewsSymbolModal").addEventListener("click", closeNewsSymbolEditor);
$("#newsSymbolModal").addEventListener("click", (event) => {
  if (event.target.id === "newsSymbolModal") closeNewsSymbolEditor();
});
$("#newsSymbolSearch").addEventListener("input", (event) => {
  clearTimeout(state.newsSymbolSearchTimer);
  const query = event.target.value.trim();
  const generation = ++state.newsSymbolSearchGeneration;
  state.newsSymbolSearchTimer = setTimeout(() => searchNewsSymbols(query, generation).catch(console.error), 250);
});
$("#newsSymbolForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.newsDetail) return;
  const submit = event.submitter || event.target.querySelector('[type="submit"]');
  if (submit) submit.disabled = true;
  try {
    const detail = await fetchJson(`/api/news-events/${state.newsDetail.id}/symbols`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        primary_instrument_ids: [...state.newsPrimarySelection.keys()],
        secondary_instrument_ids: [...state.newsSecondarySelection.keys()],
        note: $("#newsSymbolNote").value.trim(),
      }),
    });
    closeNewsSymbolEditor();
    await refreshNewsAfterSymbolEdit(detail);
  } finally {
    if (submit) submit.disabled = false;
  }
});
$("#restoreNewsSymbols").addEventListener("click", async () => {
  if (!state.newsDetail || !confirm("恢复后将解除人工锁定并重新运行自动识别，确认继续？")) return;
  const detail = await fetchJson(`/api/news-events/${state.newsDetail.id}/symbols/override`, { method: "DELETE" });
  closeNewsSymbolEditor();
  await refreshNewsAfterSymbolEdit(detail);
});
document.addEventListener("click", (event) => {
  if (event.target.closest("[data-edit-news-symbols]")) openNewsSymbolEditor();
  const chart = event.target.closest("[data-news-symbol-chart]");
  if (chart) {
    closeNewsDetail();
    openSymbolInMarket(chart.dataset.newsSymbolChart).catch(console.error);
  }
  const relation = event.target.closest("[data-news-symbol-relation]");
  if (relation) {
    state.newsSymbolRelation = relation.dataset.newsSymbolRelation;
    document.querySelectorAll("[data-news-symbol-relation]").forEach((button) => button.classList.toggle("active", button === relation));
  }
  const add = event.target.closest("[data-add-news-symbol]");
  if (add) {
    const item = { id: add.dataset.addNewsSymbol, symbol: add.dataset.symbol, name: add.dataset.name };
    const target = state.newsSymbolRelation === "primary" ? state.newsPrimarySelection : state.newsSecondarySelection;
    const other = state.newsSymbolRelation === "primary" ? state.newsSecondarySelection : state.newsPrimarySelection;
    const limit = state.newsSymbolRelation === "primary" ? 5 : 8;
    other.delete(item.id);
    if (!target.has(item.id) && target.size < limit) target.set(item.id, item);
    renderNewsSymbolSelections();
  }
  const remove = event.target.closest("[data-remove-news-symbol]");
  if (remove) {
    const target = remove.dataset.relation === "primary" ? state.newsPrimarySelection : state.newsSecondarySelection;
    target.delete(remove.dataset.removeNewsSymbol);
    renderNewsSymbolSelections();
  }
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

["#watchlistSymbol", "#watchlistResourceSymbol"].forEach((selector) => {
  $(selector).addEventListener("input", scheduleAvailableSymbolSearch);
  $(selector).addEventListener("focus", (event) => searchAvailableSymbols(event.target.value).catch(console.error));
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
  if (!state.selectedPositionInstrument || !$("#positionInstrumentId").value) {
    $("#positionStatus").textContent = "请从搜索结果中选择标的。";
    $("#positionSymbol").focus();
    return;
  }
  $("#positionStatus").textContent = "正在提交模拟委托...";
  try {
    const orderType = $("#paperOrderType").value;
    const payload = {
      account_id: state.paperAccountId,
      instrument_id: $("#positionInstrumentId").value,
      position_side: $("#positionSide").value,
      effect: $("#paperOrderEffect").value,
      order_type: orderType,
      quantity: Number($("#positionQuantity").value),
      quantity_unit: $("#positionQuantityUnit").value,
      time_in_force: $("#paperTimeInForce").value,
      limit_price: ["limit", "stop_limit"].includes(orderType) ? Number($("#paperLimitPrice").value) : null,
      stop_price: ["stop", "stop_limit"].includes(orderType) ? Number($("#paperStopPrice").value) : null,
      take_profit: Number($("#paperTakeProfit").value) || null,
      stop_loss: Number($("#paperStopLoss").value) || null,
    };
    const order = await fetchJson("/api/paper/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    $("#positionStatus").textContent = `${order.symbol} · ${paperStatusLabel(order.status)}`;
    resetPositionForm();
    await loadPortfolio();
    setTimeout(() => { $("#paperOrderDrawer").hidden = true; }, 500);
  } catch (error) {
    console.error(error);
    $("#positionStatus").textContent = error.message;
  }
});

$("#positionSymbol").addEventListener("focus", () => {
  searchPositionInstruments(state.selectedPositionInstrument ? "" : $("#positionSymbol").value).catch(console.error);
});
$("#positionSymbol").addEventListener("input", (event) => {
  state.selectedPositionInstrument = null;
  state.positionSelectionManual = true;
  $("#positionInstrumentId").value = "";
  renderPositionUnits(null);
  clearTimeout(state.positionSearchTimer);
  state.positionSearchTimer = setTimeout(() => searchPositionInstruments(event.target.value).catch(console.error), 220);
});
$("#positionSymbol").addEventListener("keydown", (event) => {
  const options = [...$("#positionInstrumentResults").querySelectorAll("[data-position-instrument]")];
  if (event.key === "Escape") hidePositionInstrumentResults();
  if (["ArrowDown", "ArrowUp"].includes(event.key) && options.length) {
    event.preventDefault();
    const direction = event.key === "ArrowDown" ? 1 : -1;
    state.positionOptionIndex = (state.positionOptionIndex + direction + options.length) % options.length;
    options.forEach((option, index) => {
      option.classList.toggle("active", index === state.positionOptionIndex);
      option.setAttribute("aria-selected", String(index === state.positionOptionIndex));
    });
    options[state.positionOptionIndex].scrollIntoView({ block: "nearest" });
  }
  if (event.key === "Enter" && !$("#positionInstrumentResults").hidden && options.length) {
    event.preventDefault();
    options[Math.max(0, state.positionOptionIndex)].click();
  }
});
$("#positionInstrumentResults").addEventListener("click", (event) => {
  const option = event.target.closest("[data-position-instrument]");
  if (!option) return;
  const instrument = state.positionInstruments.find((item) => item.id === option.dataset.positionInstrument);
  selectPositionInstrument(instrument, { manual: true }).catch(console.error);
});
$("#positionQuantityUnit").addEventListener("change", (event) => {
  const option = event.target.selectedOptions[0];
  if (option?.dataset.step) {
    $("#positionQuantity").step = option.dataset.step;
    $("#positionQuantity").min = option.dataset.step;
  }
  updatePaperOrderEstimate();
});
$("#positionQuantity").addEventListener("input", updatePaperOrderEstimate);
$("#paperLimitPrice").addEventListener("input", updatePaperOrderEstimate);
$("#paperOrderType").addEventListener("change", updatePaperOrderFields);
document.querySelectorAll("[data-paper-side]").forEach((button) => button.addEventListener("click", () => {
  $("#positionSide").value = button.dataset.paperSide;
  document.querySelectorAll("[data-paper-side]").forEach((item) => item.classList.toggle("active", item === button));
}));
document.querySelectorAll("[data-paper-effect]").forEach((button) => button.addEventListener("click", () => {
  $("#paperOrderEffect").value = button.dataset.paperEffect;
  document.querySelectorAll("[data-paper-effect]").forEach((item) => item.classList.toggle("active", item === button));
}));
function closePaperOrderDrawer() { $("#paperOrderDrawer").hidden = true; }
$("#openPaperOrderTicket").addEventListener("click", () => {
  $("#paperOrderDrawer").hidden = false;
  const current = state.positionInstruments.find((item) => item.id === window.ProfessionalChart?.currentInstrumentId());
  if (current) selectPositionInstrument(current).catch(console.error);
  window.lucide?.createIcons();
});
$("#closePaperOrderTicket").addEventListener("click", closePaperOrderDrawer);
$("#cancelPaperOrder").addEventListener("click", closePaperOrderDrawer);
$("#paperOrderDrawer").addEventListener("click", (event) => { if (event.target === $("#paperOrderDrawer")) closePaperOrderDrawer(); });
$("#runPaperMatching").addEventListener("click", async () => {
  const button = $("#runPaperMatching"); button.disabled = true;
  try { await fetchJson("/api/paper/matching/run", { method: "POST" }); await loadPortfolio(); }
  catch (error) { window.alert(error.message); }
  finally { button.disabled = false; }
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".position-instrument-picker")) hidePositionInstrumentResults();
});

$("#alertForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#alertStatus").textContent = "正在添加预警...";
  try {
    const alert = await fetchJson("/api/alerts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: window.ProfessionalChart?.currentInstrumentId() || state.activeSymbol,
        name: $("#alertName").value.trim(),
        metric: $("#alertMetric").value,
        interval: window.ProfessionalChart?.currentInterval() || state.interval,
        parameters: window.ProfessionalChart?.currentIndicatorParameters($("#alertMetric").value) || {},
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

$("#refreshButton").addEventListener("click", () => {
  if (state.activeView === "models") return window.ModelCenter?.refresh();
  return Promise.all([loadViewData(state.activeView, { force: true }), loadProviderStatus(), loadAppSettings()]);
});
$("#marketToolsRefresh").addEventListener("click", async (event) => {
  event.currentTarget.disabled = true;
  try { await loadMarketTools({ force: true }); }
  finally { event.currentTarget.disabled = false; }
});
$("#financialCalendarRefresh").addEventListener("click", async (event) => {
  event.currentTarget.disabled = true;
  $("#financialCalendarStatus").textContent = "正在同步";
  try {
    const result = await fetchJson("/api/financial-calendar/refresh", { method: "POST" });
    await loadFinancialCalendar();
    if (result.errors?.length) $("#financialCalendarStatus").title = result.errors.join("；");
  } catch (error) {
    $("#financialCalendarStatus").textContent = error.message;
  } finally { event.currentTarget.disabled = false; }
});
$("#financialCalendarKinds").addEventListener("click", (event) => {
  const button = event.target.closest("[data-calendar-kind]");
  if (!button || button.dataset.calendarKind === state.financialCalendarKind) return;
  state.financialCalendarKind = button.dataset.calendarKind;
  $("#financialCalendarKinds").querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  loadFinancialCalendar().catch(console.error);
});
$("#financialCalendarPrevious").addEventListener("click", () => {
  state.financialCalendarWeekStart = calendarAddDays(state.financialCalendarWeekStart, -7);
  state.financialCalendarSelectedDate = calendarDateKey(state.financialCalendarWeekStart);
  loadFinancialCalendar().catch(console.error);
});
$("#financialCalendarNext").addEventListener("click", () => {
  state.financialCalendarWeekStart = calendarAddDays(state.financialCalendarWeekStart, 7);
  state.financialCalendarSelectedDate = calendarDateKey(state.financialCalendarWeekStart);
  loadFinancialCalendar().catch(console.error);
});
$("#financialCalendarToday").addEventListener("click", () => {
  state.financialCalendarWeekStart = calendarWeekStart();
  state.financialCalendarSelectedDate = calendarDateKey(new Date());
  loadFinancialCalendar().catch(console.error);
});
$("#financialCalendarDays").addEventListener("click", (event) => {
  const button = event.target.closest("[data-calendar-date]");
  if (!button) return;
  state.financialCalendarSelectedDate = button.dataset.calendarDate;
  renderFinancialCalendarDays();
  $("#financialCalendarAgenda").querySelector(`[data-calendar-day-group="${button.dataset.calendarDate}"]`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
});
["#financialCalendarCountry", "#financialCalendarImportance", "#financialCalendarWatchlist"].forEach((selector) => $(selector).addEventListener("change", () => loadFinancialCalendar().catch(console.error)));
$("#financialCalendarSearch").addEventListener("input", () => {
  clearTimeout(state.financialCalendarSearchTimer);
  state.financialCalendarSearchTimer = setTimeout(() => loadFinancialCalendar().catch(console.error), 250);
});
$("#financialCalendarAgenda").addEventListener("click", (event) => {
  const target = event.target.closest("[data-calendar-event-id]");
  if (target) openFinancialCalendarEvent(target.dataset.calendarEventId).catch(console.error);
});
$("#closeFinancialCalendarDrawer").addEventListener("click", () => { $("#financialCalendarDrawer").hidden = true; });
$("#financialCalendarDrawer").addEventListener("click", (event) => { if (event.target.id === "financialCalendarDrawer") event.currentTarget.hidden = true; });
$("#financialCalendarDrawerBody").addEventListener("click", async (event) => {
  const analyze = event.target.closest("[data-calendar-analyze]");
  const reminder = event.target.closest("[data-calendar-reminder]");
  const symbol = event.target.closest("[data-calendar-symbol]");
  if (analyze) {
    analyze.disabled = true;
    await fetchJson(`/api/financial-calendar/${encodeURIComponent(analyze.dataset.calendarAnalyze)}/analyze`, { method: "POST" });
    await openFinancialCalendarEvent(analyze.dataset.calendarAnalyze);
  } else if (reminder) {
    const enabled = reminder.dataset.enabled !== "true";
    await fetchJson(`/api/financial-calendar/${encodeURIComponent(reminder.dataset.calendarReminder)}/reminder`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled, offsets: [30, 5] }),
    });
    await Promise.all([openFinancialCalendarEvent(reminder.dataset.calendarReminder), loadFinancialCalendar()]);
  } else if (symbol) {
    $("#financialCalendarDrawer").hidden = true;
    await openMarketToolSymbol(symbol.dataset.calendarSymbol);
  }
});
$("#marketOverviewTabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-market-overview-instrument]");
  if (!button || button.dataset.marketOverviewInstrument === state.marketOverviewInstrument) return;
  state.marketOverviewInstrument = button.dataset.marketOverviewInstrument;
  $("#marketOverviewTabs").querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  loadMarketOverview().catch(console.error);
});
$("#marketOverviewRanges").addEventListener("click", (event) => {
  const button = event.target.closest("[data-market-overview-range]");
  if (!button || button.dataset.marketOverviewRange === state.marketOverviewRange) return;
  state.marketOverviewRange = button.dataset.marketOverviewRange;
  $("#marketOverviewRanges").querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  loadMarketOverview().catch(console.error);
});
$("#marketRankingTabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-market-ranking]");
  if (!button) return;
  state.marketRanking = button.dataset.marketRanking;
  $("#marketRankingTabs").querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  renderMarketRanking();
});
$("#marketStripPrevious").addEventListener("click", () => scrollMarketStrip(-1));
$("#marketStripNext").addEventListener("click", () => scrollMarketStrip(1));
$("#marketStrip").addEventListener("scroll", updateMarketStripControls, { passive: true });
$("#marketStrip").addEventListener("wheel", (event) => {
  const viewport = event.currentTarget;
  if (viewport.scrollWidth <= viewport.clientWidth || Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
  event.preventDefault();
  viewport.scrollBy({ left: event.deltaY, behavior: "auto" });
}, { passive: false });
state.marketStripResizeObserver = new ResizeObserver(updateMarketStripControls);
state.marketStripResizeObserver.observe($("#marketStrip"));
$("#logoutButton").addEventListener("click", () => logout().catch((error) => console.error(error)));
window.addEventListener("resize", redrawChartIfVisible);
let marketToolsResizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(marketToolsResizeTimer);
  marketToolsResizeTimer = setTimeout(() => {
    if (state.activeView === "market-tools" && state.marketToolsSnapshots.length) renderMarketHeatmap(state.marketToolsSnapshots);
  }, 120);
});

$("#themeToggle").addEventListener("click", () => window.AppTheme.toggle(state.csrfToken).then(redrawChartIfVisible).catch(console.error));
document.addEventListener("app-theme-change", () => {
  window.setTimeout(redrawChartIfVisible, 20);
  if (state.marketOverviewBundle) window.setTimeout(() => renderMarketOverviewChart(state.marketOverviewBundle), 20);
});
document.addEventListener("app-language-change", () => {
  if (state.marketOverviewBundle) window.setTimeout(() => renderMarketOverviewChart(state.marketOverviewBundle), 20);
  if (state.financialCalendarItems.length) window.setTimeout(() => renderFinancialCalendar({ items: state.financialCalendarItems, total: state.financialCalendarItems.length, sources: [...new Set(state.financialCalendarItems.map((item) => item.source_name))], generated_at: new Date().toISOString() }), 20);
});
$("#refreshDashboard").addEventListener("click", () => loadDashboard().catch(console.error));
$("#dashboardRefreshNews").addEventListener("click", () => fetchJson("/api/news-sources/refresh?force=true", { method: "POST" }).then(() => Promise.all([loadDashboard(), loadEvents()])).catch(console.error));
$("#dashboardRunAutomation").addEventListener("click", () => runAutomation().then(loadDashboard).catch(console.error));
$("#newWatchlistItem").addEventListener("click", () => openWatchlistDrawer());
$("#manageWatchlistGroups").addEventListener("click", () => { $("#watchlistGroupModal").hidden = false; renderWatchlistGroupOptions(); });
$("#closeWatchlistGroups").addEventListener("click", () => { $("#watchlistGroupModal").hidden = true; });
$("#closeWatchlistDrawer").addEventListener("click", closeWatchlistDrawer);
$("#cancelWatchlistDrawer").addEventListener("click", closeWatchlistDrawer);
$("#watchlistDrawer").addEventListener("click", (event) => { if (event.target.id === "watchlistDrawer") closeWatchlistDrawer(); });
$("#watchlistResourceForm").addEventListener("submit", (event) => saveWatchlistResource(event).catch((error) => { $("#watchlistResourceStatus").textContent = error.message; }));
$("#watchlistGroupForm").addEventListener("submit", (event) => {
  event.preventDefault();
  fetchJson("/api/watchlist/groups", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("#watchlistGroupName").value.trim(), color: $("#watchlistGroupColor").value }) })
    .then(() => { event.target.reset(); return loadWatchlistResources(); })
    .catch((error) => { $("#watchlistGroupStatus").textContent = error.message; });
});
$("#refreshWatchlistResource").addEventListener("click", () => loadWatchlistResources().catch(console.error));
["#watchlistGroupFilter", "#watchlistMarketFilter", "#watchlistSignalFilter"].forEach((selector) => $(selector).addEventListener("change", () => loadWatchlistResources(true).catch(console.error)));
let watchlistSearchTimer;
$("#watchlistResourceSearch").addEventListener("input", () => { clearTimeout(watchlistSearchTimer); watchlistSearchTimer = setTimeout(() => loadWatchlistResources(true).catch(console.error), 220); });
$("#watchlistSelectAll").addEventListener("change", (event) => {
  state.watchlistResources.forEach((item) => { if (event.target.checked) state.watchlistSelected.add(item.symbol); else state.watchlistSelected.delete(item.symbol); });
  renderWatchlistResourceTable({ page: state.watchlistResourcePage, page_size: state.watchlistResourcePageSize, total: state.watchlistResourceTotal, items: state.watchlistResources });
});
document.addEventListener("change", (event) => {
  const target = event.target.closest("[data-watchlist-select]");
  if (!target) return;
  if (target.checked) state.watchlistSelected.add(target.dataset.watchlistSelect); else state.watchlistSelected.delete(target.dataset.watchlistSelect);
  target.closest("tr")?.classList.toggle("selected", target.checked);
  renderWatchlistSelection();
});
$("#watchlistBulkMove").addEventListener("click", () => { const groupId = $("#watchlistBulkGroup").value; if (groupId) runWatchlistBulk("move", { group_id: groupId }).catch(console.error); });
$("#watchlistBulkStatus").addEventListener("click", () => runWatchlistBulk(state.watchlistResourceStatus === "active" ? "disable" : "restore").catch(console.error));
$("#watchlistPreviousPage").addEventListener("click", () => { if (state.watchlistResourcePage > 1) { state.watchlistResourcePage -= 1; loadWatchlistResources().catch(console.error); } });
$("#watchlistNextPage").addEventListener("click", () => { if (state.watchlistResourcePage * state.watchlistResourcePageSize < state.watchlistResourceTotal) { state.watchlistResourcePage += 1; loadWatchlistResources().catch(console.error); } });

const pathView = Object.entries(viewConfig).find(([, config]) => config.path === window.location.pathname)?.[0];
const hashView = window.location.hash.slice(1);
const initialView = pathView || (viewConfig[hashView] ? hashView : "dashboard");
setActiveView(initialView, { updateHistory: false, focus: false });
window.addEventListener("popstate", () => {
  const view = Object.entries(viewConfig).find(([, config]) => config.path === window.location.pathname)?.[0] || "dashboard";
  setActiveView(view, { updateHistory: false, focus: false });
  loadViewData(view).catch(console.error);
});

async function loadMarketView() {
  const requestedSymbol = state.activeSymbol;
  const chartPromise = ensureProfessionalChartAssets()
    .then(() => window.ProfessionalChart?.init({ csrfToken: state.csrfToken, defaultSymbol: requestedSymbol }))
    .then(() => window.ProfessionalChart?.selectLegacySymbol(requestedSymbol));
  await Promise.allSettled([chartPromise, loadMarketWorkspace(), loadMarketBreadth()]);
}

function loadViewData(viewName, { force = false } = {}) {
  if (viewName === "dashboard") return loadDashboard();
  if (viewName === "watchlist") return Promise.all([loadWatchlist(), loadWatchlistResources()]);
  if (viewName === "market") return loadMarketView();
  if (viewName === "market-tools") return ensureMarketToolsAssets().then(() => loadMarketTools({ force }));
  if (viewName === "models") return window.ModelCenter?.init({ csrfToken: state.csrfToken, role: state.currentUser?.role });

  const request = beginSymbolRequest();
  const symbolTask = loadSymbolDataForView(viewName, state.activeSymbol, request);
  const globalTasks = {
    news: [],
    ai: [loadAiBriefing()],
    factors: [loadFactorDefinitions(), loadResearchLibraries()],
    backtest: [loadFactorDefinitions(), loadResearchLibraries()],
    alerts: [loadAutomationRuns(), loadAutomationSettings(), loadNotifications(), loadEvents()],
  }[viewName] || [];
  return Promise.allSettled([symbolTask, ...globalTasks]);
}

async function bootstrap() {
  loadStoredNewsSourceFilters();
  await loadCurrentUser();
  window.AppTheme?.updateControls();
  window.lucide?.createIcons();
  if (initialView === "models") {
    await loadViewData(initialView);
  } else if (initialView === "market") {
    const sharedPromise = Promise.all([loadProviderStatus(), loadAppSettings()]);
    const chartPromise = ensureProfessionalChartAssets()
      .then(() => window.ProfessionalChart?.init({ csrfToken: state.csrfToken, defaultSymbol: state.activeSymbol }));
    await Promise.all([sharedPromise, chartPromise]);
    loadMarketWorkspace().catch(console.error);
  } else if (initialView === "dashboard") {
    const sharedPromise = Promise.all([loadProviderStatus(), loadAppSettings()]);
    await Promise.all([sharedPromise, loadDashboard()]);
  } else if (initialView === "watchlist") {
    const sharedPromise = Promise.all([loadProviderStatus(), loadAppSettings()]);
    await Promise.all([sharedPromise, loadWatchlist(), loadWatchlistResources()]);
  } else if (initialView === "news") {
    await loadViewData(initialView);
    Promise.all([loadProviderStatus(), loadAppSettings()]).catch(console.error);
  } else {
    await Promise.all([loadProviderStatus(), loadAppSettings()]);
    await loadViewData(initialView);
  }
  syncMarketStreamForView(initialView);
}

window.addEventListener("professional-instrument-change", (event) => {
  const previousSymbol = state.activeSymbol;
  const instrumentSymbol = String(event.detail.symbol || "").toUpperCase();
  const legacySymbol = instrumentSymbol === "BTCUSDT" ? "BTCUSD" : instrumentSymbol === "ETHUSDT" ? "ETHUSD" : instrumentSymbol;
  state.chartInstrumentId = event.detail.id;
  const matchingPaperAccount = state.paperAccounts.find((item) => item.currency === event.detail.currency);
  if (matchingPaperAccount && matchingPaperAccount.id !== state.paperAccountId) {
    state.paperAccountId = matchingPaperAccount.id;
    loadPortfolio().catch(console.error);
  }
  if (!state.positionSelectionManual && event.detail.positionable) {
    const merged = new Map(state.positionInstruments.map((item) => [item.id, item]));
    merged.set(event.detail.id, event.detail);
    state.positionInstruments = [...merged.values()];
    selectPositionInstrument(event.detail).catch(console.error);
  }
  if (legacySymbol) state.activeSymbol = legacySymbol;
  if (state.symbols.length) {
    applyFilters();
    renderSnapshot();
  }
  if (state.activeView === "market" && previousSymbol !== state.activeSymbol) {
    const request = beginSymbolRequest();
    loadMarketSymbolContext(state.activeSymbol, request).catch(console.error);
  } else if (state.activeView === "market") {
    fetchJson(`/api/indicator-snapshots/${encodeURIComponent(event.detail.id)}`)
      .then(renderIndicatorSnapshotBundle)
      .catch((error) => {
        console.error(error);
        renderIndicators([]);
      });
  }
});

window.addEventListener("professional-chart-ready", () => {
  window.ProfessionalChart?.setPaperTradingData(state.portfolio?.positions || [], state.paperOrders, state.paperFills);
});

window.addEventListener("chart-news-marker-click", (event) => {
  openNewsDetail(event.detail.id).catch((error) => {
    console.error(error);
    $("#newsDrawerBody").innerHTML = `<p>新闻详情加载失败。</p>`;
  });
});

window.addEventListener("chart-news-group-click", (event) => {
  const marker = event.detail;
  const translate = (source, params = {}) => window.AppI18n?.t(source, params) || source;
  $("#newsDrawer").hidden = false;
  $("#newsDrawerTitle").textContent = translate("{count} 条新闻事件", { count: marker.count });
  $("#newsDrawerBody").innerHTML = `
    <div class="event-tooltip-list chart-news-group-list">
      ${(marker.events || []).map((item) => `
        <button class="event-tooltip-item" type="button" data-news-id="${escapeHtml(item.id)}">
          <strong>${escapeHtml(item.title)}</strong>
          <span>${escapeHtml(item.source)} · ${escapeHtml(translate("影响 {score}", { score: item.impact_score }))} · ${formatTime(item.published_at)}</span>
          <p>${escapeHtml(item.summary)}</p>
        </button>
      `).join("")}
    </div>
  `;
});

bootstrap().catch((error) => {
    console.error(error);
    $("#watchlistBody").innerHTML = `<tr><td colspan="8">数据加载失败</td></tr>`;
  });
