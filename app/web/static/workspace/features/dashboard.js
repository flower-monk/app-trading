import { formatPrice, formatTime } from "../core/format.js";
import { escapeHtml, fetchJson } from "../core/http.js";
import { state } from "../core/state.js";
import { $, signalClass, signalText } from "../core/ui.js";

function emptyTableRow(columns, message) {
  return `<tr><td colspan="${columns}" class="empty-cell">${escapeHtml(message)}</td></tr>`;
}

export function renderDashboard(overview) {
  state.dashboardOverview = overview;
  $("#dashboardGeneratedAt").textContent = `更新于 ${formatTime(overview.generated_at)} · ${overview.health.length} 个组件`;
  $("#dashboardMetrics").innerHTML = overview.metrics.map((metric) => `
    <a class="dashboard-metric" data-status="${metric.status}" href="${escapeHtml(metric.href)}" title="${escapeHtml(metric.detail)}">
      <span>${escapeHtml(metric.label)}</span>
      <strong>${escapeHtml(metric.value)}</strong>
      <small>${escapeHtml(metric.detail)}</small>
    </a>
  `).join("");
  $("#dashboardHealthBody").innerHTML = overview.health.length ? overview.health.map((item) => `
    <tr><td>${escapeHtml(item.name)}</td><td><span class="status-dot ${item.status}"></span>${item.status.toUpperCase()}</td><td>${escapeHtml(item.message)}</td><td>${item.count ?? "--"}</td></tr>
  `).join("") : emptyTableRow(4, "暂无健康状态");
  $("#dashboardAgentRunsBody").innerHTML = overview.agent_runs.length ? overview.agent_runs.map((item) => `
    <tr><td>${escapeHtml(item.agent_name)}</td><td>${escapeHtml(item.trigger)}</td><td><span class="status-dot ${item.status}"></span>${escapeHtml(item.status)}</td><td>${item.latency_ms || 0}ms</td></tr>
  `).join("") : emptyTableRow(4, "暂无 Agent 运行记录");
  $("#dashboardAutomationRunsBody").innerHTML = overview.automation_runs.length ? overview.automation_runs.map((item) => `
    <tr><td>${formatTime(item.finished_at)}</td><td><span class="status-dot ${item.status}"></span>${escapeHtml(item.status)}</td><td>${item.inserted_news}</td><td>${item.triggered_alerts}</td></tr>
  `).join("") : emptyTableRow(4, "暂无自动化运行记录");
  $("#dashboardEvents").innerHTML = overview.events.length ? overview.events.slice(0, 7).map((item) => `
    <article class="dashboard-event"><span class="status-dot ${item.severity === "info" ? "ok" : item.severity === "watch" ? "warn" : "error"}"></span><div><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.message)}</p></div><time>${formatTime(item.created_at)}</time></article>
  `).join("") : `<div class="empty-cell">暂无系统事件</div>`;
  $("#dashboardWatchlistBody").innerHTML = overview.watchlist.length ? overview.watchlist.map((item) => `
    <tr data-symbol="${escapeHtml(item.symbol)}"><td><strong>${escapeHtml(item.symbol)}</strong><br><span class="flat">${escapeHtml(item.name)}</span></td><td>${escapeHtml(item.market)}</td><td>${formatPrice(item.price)}</td><td class="${item.change_percent >= 0 ? "up" : "down"}">${item.change_percent > 0 ? "+" : ""}${item.change_percent.toFixed(2)}%</td><td><span class="badge ${signalClass[item.signal]}">${signalText[item.signal]}</span></td></tr>
  `).join("") : emptyTableRow(5, "Watchlist 暂无活动标的");
}

export async function loadDashboard() {
  const overview = await fetchJson("/api/dashboard/overview");
  renderDashboard(overview);
}
