import { formatPrice, formatTime } from "../core/format.js";
import { escapeHtml, fetchJson } from "../core/http.js";
import { state } from "../core/state.js";
import { $, sentimentClass, signalClass, signalText } from "../core/ui.js";

export function marketVolumeValue(value) {
  const match = String(value || "").trim().match(/^([\d.]+)\s*([KMBT])?$/i);
  if (!match) return Math.max(1, Number.parseFloat(value) || 1);
  return Number(match[1]) * ({ K: 1e3, M: 1e6, B: 1e9, T: 1e12 }[match[2]?.toUpperCase()] || 1);
}

export function marketHeatClass(change) {
  const strength = Math.max(1, Math.min(4, Math.ceil(Math.abs(change) / 0.75)));
  return `heat-${change >= 0 ? "up" : "down"}-${strength}`;
}

export function renderMarketHeatmap(snapshots) {
  const target = $("#nativeMarketHeatmap");
  if (!snapshots.length) {
    target.innerHTML = `<div class="market-tool-empty">暂无市场快照数据</div>`;
    return;
  }
  if (!window.d3?.hierarchy) {
    target.innerHTML = `<div class="market-tool-empty">市场热力图暂不可用</div>`;
    return;
  }
  if (target.clientWidth < 40 || target.clientHeight < 40) {
    if (state.activeView === "market-tools") window.requestAnimationFrame(() => renderMarketHeatmap(snapshots));
    return;
  }
  const grouped = Object.values(snapshots.reduce((result, snapshot) => {
    const market = snapshot.market || "Other";
    result[market] ||= { name: market, children: [] };
    result[market].children.push({ ...snapshot, rawVolume: Math.max(1, marketVolumeValue(snapshot.volume)) });
    return result;
  }, {}));
  grouped.forEach((group) => {
    const total = group.children.reduce((sum, item) => sum + item.rawVolume, 0);
    group.children.forEach((item) => { item.value = (item.rawVolume / total) * group.children.length; });
  });
  const root = window.d3.hierarchy({ children: grouped }).sum((item) => item.value || 0).sort((a, b) => b.value - a.value);
  window.d3.treemap().size([target.clientWidth, target.clientHeight]).paddingOuter(2).paddingInner(2)
    .paddingTop((node) => node.depth === 1 ? 22 : 0).round(true)(root);
  const groups = root.children.map((group) => `<span class="treemap-group-label" style="left:${group.x0}px;top:${group.y0}px;width:${group.x1 - group.x0}px">${escapeHtml(group.data.name)}</span>`).join("");
  const leaves = root.leaves().map((leaf) => {
    const snapshot = leaf.data;
    const width = leaf.x1 - leaf.x0;
    const height = leaf.y1 - leaf.y0;
    const compact = width < 105 || height < 78 ? " compact" : "";
    const tiny = width < 68 || height < 50 ? " tiny" : "";
    const change = Number(snapshot.change_percent);
    return `<button class="native-heatmap-tile ${marketHeatClass(change)}${compact}${tiny}" type="button" data-market-tool-symbol="${escapeHtml(snapshot.symbol)}" title="${escapeHtml(snapshot.name)} · ${change > 0 ? "+" : ""}${change.toFixed(2)}%" style="left:${leaf.x0}px;top:${leaf.y0}px;width:${width}px;height:${height}px">
      <strong>${escapeHtml(snapshot.symbol)}</strong><span>${escapeHtml(snapshot.name)}</span><b>${change > 0 ? "+" : ""}${change.toFixed(2)}%</b><small>${escapeHtml(snapshot.volume)}</small>
    </button>`;
  }).join("");
  target.innerHTML = groups + leaves;
}

export function renderMarketOverviewGroups(breadth) {
  const groups = breadth?.groups || [];
  $("#marketOverviewGroups").innerHTML = groups.length ? groups.slice(0, 5).map((group) => `
    <div><span>${escapeHtml(group.name)}</span><strong class="${group.average_change >= 0 ? "up" : "down"}">${group.average_change > 0 ? "+" : ""}${Number(group.average_change).toFixed(2)}%</strong><small>${group.advancers} / ${group.decliners}</small></div>
  `).join("") : `<div class="market-tool-empty compact">暂无市场分组数据</div>`;
}

export function rankedMarketSnapshots() {
  const snapshots = [...state.marketToolsSnapshots];
  if (state.marketRanking === "losers") return snapshots.sort((a, b) => a.change_percent - b.change_percent);
  if (state.marketRanking === "active") return snapshots.sort((a, b) => marketVolumeValue(b.volume) - marketVolumeValue(a.volume));
  return snapshots.sort((a, b) => b.change_percent - a.change_percent);
}

export function renderMarketRanking() {
  const snapshots = rankedMarketSnapshots();
  $("#marketQuoteBody").innerHTML = snapshots.length ? snapshots.map((snapshot) => `
    <tr data-market-tool-symbol="${escapeHtml(snapshot.symbol)}">
      <td><strong>${escapeHtml(snapshot.symbol)}</strong><small>${escapeHtml(snapshot.name)}</small></td>
      <td>${formatPrice(snapshot.price)}</td>
      <td class="${snapshot.change_percent >= 0 ? "up" : "down"}">${snapshot.change_percent > 0 ? "+" : ""}${Number(snapshot.change_percent).toFixed(2)}%</td>
      <td>${escapeHtml(snapshot.volume)}</td>
      <td><span class="badge ${signalClass[snapshot.signal] || "neutral"}">${escapeHtml(signalText[snapshot.signal] || snapshot.signal)}</span></td>
    </tr>`).join("") : `<tr><td colspan="5">暂无市场快照数据</td></tr>`;
  window.AppI18n?.translate($("#marketQuoteBody"));
}

export function renderMarketTopStories(news) {
  $("#marketNewsCount").textContent = `${news.length} 条`;
  $("#marketTopStories").innerHTML = news.length ? news.map((item, index) => `
    <button class="market-story" type="button" data-news-id="${escapeHtml(item.id)}">
      <span class="market-story-rank">${String(index + 1).padStart(2, "0")}</span>
      <span class="market-story-copy"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.source)} · ${formatTime(item.published_at)}</small><span>${escapeHtml(item.summary)}</span></span>
      <span class="market-story-impact ${sentimentClass[item.sentiment] || "flat"}">${item.impact_score}</span>
    </button>`).join("") : `<div class="market-tool-empty">暂无热点新闻</div>`;
  window.AppI18n?.translate($("#marketToolsView"));
}

export function calendarDateKey(value) {
  const date = value instanceof Date ? value : new Date(value);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function calendarWeekStart(value = new Date()) {
  const date = new Date(value);
  date.setHours(0, 0, 0, 0);
  const day = date.getDay() || 7;
  date.setDate(date.getDate() - day + 1);
  return date;
}

export function calendarAddDays(value, days) {
  const date = new Date(value);
  date.setDate(date.getDate() + days);
  return date;
}

export function renderFinancialCalendarDays() {
  const start = state.financialCalendarWeekStart || calendarWeekStart();
  const selected = state.financialCalendarSelectedDate || calendarDateKey(new Date());
  const today = calendarDateKey(new Date());
  const locale = window.AppI18n?.getLocale() || "zh-CN";
  $("#financialCalendarDays").innerHTML = Array.from({ length: 7 }, (_, index) => {
    const date = calendarAddDays(start, index);
    const key = calendarDateKey(date);
    const weekday = new Intl.DateTimeFormat(locale, { weekday: "short" }).format(date);
    return `<button class="${key === selected ? "active " : ""}${key === today ? "today" : ""}" type="button" data-calendar-date="${key}" role="tab" aria-selected="${key === selected}"><span>${escapeHtml(weekday)}</span><strong>${date.getDate()}</strong></button>`;
  }).join("");
}

export function calendarImportanceText(value) {
  return { high: "高影响", medium: "中影响", low: "低影响" }[value] || value;
}

export function calendarStatusText(value) {
  return { scheduled: "待公布", awaiting_result: "等待回填", released: "已公布", revised: "已修订", tentative: "时间待定", cancelled: "已取消" }[value] || value;
}

export function calendarValue(item, field) {
  const value = item[field];
  return value == null || value === "" ? "--" : `${value}${item.unit && !String(value).includes(item.unit) ? item.unit : ""}`;
}

export function renderFinancialCalendar(page) {
  state.financialCalendarItems = page.items || [];
  state.financialCalendarItems.forEach((item) => { if (item.country) state.financialCalendarCountries.add(item.country); });
  const country = $("#financialCalendarCountry").value;
  $("#financialCalendarCountry").innerHTML = `<option value="">全部国家和地区</option>${[...state.financialCalendarCountries].sort().map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join("")}`;
  $("#financialCalendarCountry").value = country;
  $("#financialCalendarStatus").textContent = page.warning || `${page.total} 条 · ${page.sources?.join(" / ") || "暂无来源"}`;
  $("#financialCalendarStatus").title = page.warning || `更新于 ${formatTime(page.generated_at)}`;
  const locale = window.AppI18n?.getLocale() || "zh-CN";
  const groups = new Map();
  state.financialCalendarItems.forEach((item) => {
    const key = calendarDateKey(item.scheduled_at);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  });
  $("#financialCalendarAgenda").innerHTML = groups.size ? [...groups.entries()].map(([key, items]) => {
    const label = new Intl.DateTimeFormat(locale, { month: "long", day: "numeric", weekday: "long" }).format(new Date(`${key}T12:00:00`));
    return `<section data-calendar-day-group="${key}"><div class="financial-calendar-day-label">${escapeHtml(label)} · ${items.length} 条</div>${items.map((item) => {
      const time = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(item.scheduled_at));
      const region = item.symbol || item.country || "GLOBAL";
      return `<button class="financial-calendar-event" type="button" data-calendar-event-id="${escapeHtml(item.id)}">
        <span class="calendar-event-time"><i class="calendar-impact-dot ${item.importance}"></i><span>${escapeHtml(time)}<small>${escapeHtml(region)}</small></span></span>
        <span class="calendar-event-title"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.reference || item.category)} · ${calendarStatusText(item.status)}${item.source_count > 1 ? ` · ${item.source_count} 个来源` : ""}${item.conflicts?.length ? " · 数据冲突" : ""}${item.reminder_enabled ? " · 已提醒" : ""}</small></span>
        <span class="calendar-event-value actual ${item.surprise_direction === "above" ? "up" : item.surprise_direction === "below" ? "down" : ""}">${escapeHtml(calendarValue(item, "actual"))}</span>
        <span class="calendar-event-value forecast">${escapeHtml(calendarValue(item, "forecast"))}</span>
        <span class="calendar-event-value previous">${escapeHtml(calendarValue(item, "previous"))}</span>
        <span class="calendar-importance ${item.importance}">${calendarImportanceText(item.importance)}</span>
      </button>`;
    }).join("")}</section>`;
  }).join("") : `<div class="market-tool-empty">当前范围没有匹配的财经事件</div>`;
  renderFinancialCalendarDays();
  window.AppI18n?.translate($("#marketToolsView"));
}

export async function loadFinancialCalendar() {
  state.financialCalendarWeekStart ||= calendarWeekStart();
  state.financialCalendarSelectedDate ||= calendarDateKey(new Date());
  renderFinancialCalendarDays();
  $("#financialCalendarStatus").textContent = "正在加载";
  const start = calendarDateKey(state.financialCalendarWeekStart);
  const end = calendarDateKey(calendarAddDays(state.financialCalendarWeekStart, 6));
  const params = new URLSearchParams({ start, end, kind: state.financialCalendarKind });
  const country = $("#financialCalendarCountry").value;
  const importance = $("#financialCalendarImportance").value;
  const query = $("#financialCalendarSearch").value.trim();
  if (country) params.append("country", country);
  if (importance) params.set("importance", importance);
  if (query) params.set("query", query);
  if ($("#financialCalendarWatchlist").checked) params.set("watchlist_only", "true");
  try {
    renderFinancialCalendar(await fetchJson(`/api/financial-calendar?${params}`));
  } catch (error) {
    console.error(error);
    $("#financialCalendarStatus").textContent = "加载失败";
    $("#financialCalendarAgenda").innerHTML = `<div class="market-tool-empty">财经日历暂不可用</div>`;
  }
}

export function renderCalendarAnalysis(analysis) {
  if (!analysis) return `<div class="drawer-section"><span>Agent 分析</span><p>尚未生成事件影响分析。</p></div>`;
  if (analysis.status === "queued") return `<div class="drawer-section"><span>Agent 分析</span><p>分析任务已进入队列，稍后刷新详情查看结果。</p></div>`;
  return `<div class="drawer-section"><span>Agent 分析 · ${escapeHtml(analysis.risk_level)} · ${analysis.confidence}%</span><p>${escapeHtml(analysis.summary)}</p>
    <ul class="calendar-analysis-list">${[...(analysis.affected_markets || []).map((item) => `影响市场：${item}`), ...(analysis.scenarios || []), ...(analysis.risks || []).map((item) => `风险：${item}`)].map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`;
}

export async function openFinancialCalendarEvent(eventId) {
  $("#financialCalendarDrawer").hidden = false;
  $("#financialCalendarDrawerBody").innerHTML = `<p>正在加载...</p>`;
  const detail = await fetchJson(`/api/financial-calendar/${encodeURIComponent(eventId)}`);
  state.financialCalendarDetail = detail;
  const item = detail.event;
  const sourceRows = (detail.source_snapshots || []).map((source) => `<div><strong>${escapeHtml(source.source_name)}</strong><small>${escapeHtml(new Date(source.fetched_at).toLocaleString())}</small></div>`).join("");
  const provenanceRows = Object.entries(item.provenance || {}).map(([field, source]) => `<span>${escapeHtml(field)} · ${escapeHtml(source)}</span>`).join("");
  $("#financialCalendarDrawerTitle").textContent = item.title;
  $("#financialCalendarDrawerBody").innerHTML = `
    <div class="calendar-detail-grid">
      <div><span>公布时间</span><strong>${escapeHtml(new Date(item.scheduled_at).toLocaleString())}</strong></div><div><span>状态</span><strong>${calendarStatusText(item.status)}</strong></div>
      <div><span>实际值</span><strong>${escapeHtml(calendarValue(item, "actual"))}</strong></div><div><span>市场预期</span><strong>${escapeHtml(calendarValue(item, "forecast"))}</strong></div>
      <div><span>前值</span><strong>${escapeHtml(calendarValue(item, "previous"))}</strong></div><div><span>重要度</span><strong>${calendarImportanceText(item.importance)}</strong></div>
      <div><span>国家 / 标的</span><strong>${escapeHtml(item.symbol || item.country || "GLOBAL")}</strong></div><div><span>数据来源</span><strong>${escapeHtml(item.source_name)}</strong></div>
    </div>
    <div class="drawer-section"><span>来源与血缘 · ${item.source_count || 1} 个来源</span><div class="calendar-source-list">${sourceRows || `<div><strong>${escapeHtml(item.source_name)}</strong></div>`}</div>${provenanceRows ? `<div class="calendar-provenance">${provenanceRows}</div>` : ""}${item.conflicts?.length ? `<p class="calendar-conflict">冲突字段：${escapeHtml(item.conflicts.join("、"))}</p>` : ""}</div>
    <div class="drawer-section"><span>预期观察</span><p>${item.status === "scheduled" ? `关注实际值相对预期 ${escapeHtml(item.forecast || "--")} 与前值 ${escapeHtml(item.previous || "--")} 的偏离。` : item.status === "awaiting_result" ? "事件已到发布时间，当前来源尚未返回实际值，系统将在后续同步中继续追补。" : `预期差：${escapeHtml(item.surprise_direction || "暂无可比预期")}`}</p></div>
    ${renderCalendarAnalysis(detail.analysis)}
    <div class="calendar-detail-actions">
      <button class="primary-button" type="button" data-calendar-analyze="${escapeHtml(item.id)}"><i data-lucide="sparkles"></i>${detail.analysis ? "重新分析" : "Agent 分析"}</button>
      <button class="text-button" type="button" data-calendar-reminder="${escapeHtml(item.id)}" data-enabled="${item.reminder_enabled}"><i data-lucide="bell"></i>${item.reminder_enabled ? "关闭提醒" : "开启提醒"}</button>
      ${item.symbol ? `<button class="text-button" type="button" data-calendar-symbol="${escapeHtml(item.symbol)}"><i data-lucide="candlestick-chart"></i>查看行情</button>` : ""}
    </div>`;
  window.lucide?.createIcons();
  window.AppI18n?.translate($("#financialCalendarDrawer"));
}

export function marketOverviewColors() {
  const style = getComputedStyle(document.documentElement);
  return {
    background: style.getPropertyValue("--app-surface").trim(),
    text: style.getPropertyValue("--app-text-muted").trim(),
    line: style.getPropertyValue("--app-border").trim(),
    accent: style.getPropertyValue("--app-green").trim(),
  };
}

export function renderMarketOverviewChart(bundle) {
  const target = $("#marketOverviewChart");
  state.marketOverviewChart?.remove();
  state.marketOverviewChart = null;
  target.replaceChildren();
  if (!bundle?.candles?.length || !window.LightweightCharts) {
    target.innerHTML = `<div class="market-tool-empty">指数趋势暂不可用</div>`;
    return;
  }
  const colors = marketOverviewColors();
  const chart = LightweightCharts.createChart(target, {
    autoSize: true,
    layout: { background: { type: LightweightCharts.ColorType.Solid, color: colors.background }, textColor: colors.text, attributionLogo: false },
    localization: { locale: window.AppI18n?.getLocale() || "zh-CN" },
    grid: { vertLines: { visible: false }, horzLines: { color: colors.line } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Magnet },
    rightPriceScale: { visible: false },
    timeScale: { borderVisible: false, timeVisible: false, secondsVisible: false, rightOffset: 2, barSpacing: 5, minBarSpacing: 1 },
    handleScroll: false,
    handleScale: false,
  });
  const series = chart.addSeries(LightweightCharts.LineSeries, { color: colors.accent, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerRadius: 3 });
  series.setData(bundle.candles.map((candle) => ({ time: candle.time, value: candle.close })));
  chart.timeScale().fitContent();
  state.marketOverviewChart = chart;
  const first = bundle.candles[0].close;
  const last = bundle.candles.at(-1).close;
  const change = first ? ((last - first) / first) * 100 : 0;
  $("#marketOverviewName").textContent = bundle.instrument.name;
  $("#marketOverviewSymbol").textContent = bundle.instrument.symbol;
  $("#marketOverviewPrice").textContent = formatPrice(last);
  $("#marketOverviewChange").className = change >= 0 ? "up" : "down";
  $("#marketOverviewChange").textContent = `${change > 0 ? "+" : ""}${change.toFixed(2)}%`;
  $("#marketOverviewStatus").textContent = `${bundle.status?.provider_label || bundle.instrument.market} · ${bundle.interval.toUpperCase()} · ${bundle.range}${bundle.status?.stale ? " · 缓存" : bundle.instrument.delayed ? " · 延迟" : ""}`;
  $("#marketOverviewStatus").title = bundle.status?.fallback_reason || bundle.status?.message || "";
}

export async function loadMarketOverview({ force = false } = {}) {
  const key = `${state.marketOverviewInstrument}:${state.marketOverviewRange}`;
  const cached = state.marketOverviewCache.get(key);
  if (!force && cached && Date.now() - cached.savedAt < 60_000) {
    state.marketOverviewBundle = cached.bundle;
    renderMarketOverviewChart(cached.bundle);
    return;
  }
  state.marketOverviewController?.abort();
  const controller = new AbortController();
  state.marketOverviewController = controller;
  $("#marketOverviewStatus").textContent = "正在加载";
  try {
    const bundle = await fetchJson("/api/chart/query", {
      method: "POST",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instrument_id: state.marketOverviewInstrument, interval: "1d", range: state.marketOverviewRange, indicators: [], news_mode: "off", include_series: false, cache_policy: force ? "refresh" : "prefer_cache", limit: 500 }),
    });
    if (controller !== state.marketOverviewController) return;
    state.marketOverviewBundle = bundle;
    state.marketOverviewCache.set(key, { savedAt: Date.now(), bundle });
    renderMarketOverviewChart(bundle);
  } catch (error) {
    if (error.name === "AbortError") return;
    console.error(error);
    $("#marketOverviewStatus").textContent = "加载失败";
    $("#marketOverviewChart").innerHTML = `<div class="market-tool-empty">指数趋势暂不可用</div>`;
  }
}

export function renderMarketTools(screener, breadth, news) {
  state.marketToolsSnapshots = (screener.items || []).map((item) => item.snapshot);
  state.marketToolsBreadth = breadth;
  state.marketToolsNews = news;
  $("#marketToolsGeneratedAt").textContent = `更新于 ${formatTime(screener.generated_at || breadth.generated_at)}`;
  const provenance = screener.provenance || breadth.provenance || [];
  const sourceLabel = provenance.length
    ? provenance.map((item) => `${item.market} · ${item.source_name}${item.fallback_reason ? " · 降级" : ""}`).join(" / ")
    : "暂无来源";
  $("#marketHeatmapSource").textContent = sourceLabel;
  $("#marketHeatmapSource").title = provenance.map((item) => item.fallback_reason || "").filter(Boolean).join("；");
  $("#marketQuoteSource").textContent = sourceLabel;
  $("#marketQuoteSource").title = $("#marketHeatmapSource").title;
  renderMarketHeatmap(state.marketToolsSnapshots);
  renderMarketOverviewGroups(breadth);
  renderMarketRanking();
  renderMarketTopStories(news);
}

export async function loadMarketTools({ force = false } = {}) {
  $("#marketToolsGeneratedAt").textContent = "正在加载";
  const [screenerResult, breadthResult, newsResult] = await Promise.allSettled([
    fetchJson("/api/screener?limit=100"),
    fetchJson("/api/market-breadth"),
    fetchJson("/api/market-news?limit=10"),
  ]);
  const screener = screenerResult.status === "fulfilled" ? screenerResult.value : { generated_at: new Date().toISOString(), items: [] };
  const breadth = breadthResult.status === "fulfilled" ? breadthResult.value : { generated_at: screener.generated_at, groups: [] };
  const news = newsResult.status === "fulfilled" ? newsResult.value : [];
  renderMarketTools(screener, breadth, news);
  await Promise.allSettled([loadMarketOverview({ force }), loadFinancialCalendar()]);
}
