const sourceState = {
  me: null, csrf: "", tab: "sources", sources: [], routes: [], checks: [], revisions: [], calendarStatuses: [],
  summary: null, editingSource: null, editingRoute: null, routeMembers: [],
};
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
const splitList = (value) => [...new Set(String(value || "").split(",").map((item) => item.trim()).filter(Boolean))];

async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("X-CSRF-Token", sourceState.csrf);
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) { location.replace("/login"); throw new Error("登录已失效"); }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    throw new Error(typeof detail === "object" ? detail.message || JSON.stringify(detail) : detail || "请求失败");
  }
  return payload;
}

function notice(message = "", status = "") {
  $("#sourceNotice").className = `resource-notice ${status}`;
  $("#sourceNotice").textContent = message;
}

function healthLabel(status) {
  return { healthy: "正常", degraded: "降级", offline: "离线", disabled: "停用", untested: "未检测" }[status] || status;
}

function renderMetrics() {
  const item = sourceState.summary;
  if (!item) return;
  $("#sourceMetrics").innerHTML = [
    ["数据源", item.total_sources, `${item.enabled_sources} 个启用`],
    ["健康", item.healthy_sources, "检测通过"],
    ["关注", item.degraded_sources, "降级或未检测"],
    ["离线", item.offline_sources, "已触发熔断"],
    ["路由", item.routes, `发布版本 ${item.active_revision}`],
  ].map(([label, value, detail]) => `<article><span>${label}</span><strong>${value}</strong><small>${detail}</small></article>`).join("");
}

function sourceRows() {
  const query = $("#sourceSearch").value.trim().toLowerCase();
  const domain = $("#sourceDomain").value;
  return sourceState.sources.filter((item) => (
    (sourceState.tab !== "calendar" || item.capabilities.includes("economic_calendar") || item.capabilities.includes("earnings_calendar"))
    && (!domain || item.domain === domain)
    && (!query || `${item.name} ${item.provider} ${item.adapter} ${item.markets.join(" ")}`.toLowerCase().includes(query))
  ));
}

function intervalLabel(seconds) {
  if (seconds >= 3600 && seconds % 3600 === 0) return `${seconds / 3600} 小时`;
  if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60} 分钟`;
  return `${seconds} 秒`;
}

function calendarStatus(item) {
  return sourceState.calendarStatuses.find((status) => status.source_id === item.id) || null;
}

function shortTime(value) {
  return value ? new Date(value).toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "尚未同步";
}

function renderSources() {
  const rows = sourceRows();
  $("#sourceTabContent").innerHTML = rows.length ? `<div class="source-table-wrap"><table class="source-table"><thead><tr><th>数据源</th><th>类型</th><th>覆盖</th><th>能力</th><th>健康</th><th>凭据</th><th>${sourceState.tab === "calendar" ? "同步周期" : "延迟"}</th><th>操作</th></tr></thead><tbody>${rows.map((item) => `
    <tr>
      <td><div class="source-name-cell"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.provider)} · ${escapeHtml(item.adapter)}</small></div></td>
      <td>${escapeHtml({ market: "行情", index: "指数", content: "内容" }[item.domain] || item.domain)}</td>
      <td>${escapeHtml(item.markets.join(" / ") || "GLOBAL")}</td>
      <td><div class="source-capabilities">${item.capabilities.slice(0, 5).map((value) => `<span class="source-chip">${escapeHtml(value)}</span>`).join("")}</div></td>
      <td><span class="source-health ${item.health_status}">${healthLabel(item.health_status)}</span><small>${sourceState.tab === "calendar" ? `${shortTime(calendarStatus(item)?.last_run_at)} · ${calendarStatus(item)?.fetched || 0} 条${calendarStatus(item)?.conflicts ? ` · ${calendarStatus(item).conflicts} 冲突` : ""}` : (item.last_error ? escapeHtml(item.last_error.slice(0, 80)) : "--")}</small></td>
      <td>${item.api_key_env ? (item.credential_configured ? "已配置" : "未配置") : "无需凭据"}</td>
      <td>${sourceState.tab === "calendar" ? `${intervalLabel(item.sync_interval_seconds)}<small>${escapeHtml(item.timezone)}${calendarStatus(item)?.last_error ? ` · ${escapeHtml(calendarStatus(item).last_error.slice(0, 60))}` : ""}</small>` : (item.last_latency_ms == null ? "--" : `${Math.round(item.last_latency_ms)} ms`)}</td>
      <td><div class="source-actions"><button class="icon-button" type="button" data-source-test="${escapeHtml(item.id)}" title="连接检测"><i data-lucide="plug-zap"></i></button><button class="icon-button" type="button" data-source-refresh="${escapeHtml(item.id)}" title="立即刷新"><i data-lucide="refresh-cw"></i></button><button class="icon-button admin-write" type="button" data-source-edit="${escapeHtml(item.id)}" title="编辑"><i data-lucide="pencil"></i></button><button class="icon-button admin-write" type="button" data-source-toggle="${escapeHtml(item.id)}" title="${item.enabled ? "停用" : "启用"}"><i data-lucide="${item.enabled ? "pause" : "play"}"></i></button></div></td>
    </tr>`).join("")}</tbody></table></div>` : `<div class="source-empty">没有匹配的数据源</div>`;
  lucide.createIcons();
}

function renderRoutes() {
  $("#sourceTabContent").innerHTML = `<div class="route-grid">${sourceState.routes.map((route) => `
    <article class="route-card"><header><div><strong>${escapeHtml(route.market)} · ${escapeHtml(route.capability)}</strong><small>${route.mode === "aggregate" ? "聚合路由" : "主备降级"} · v${route.version}</small></div><button class="icon-button admin-write" type="button" data-route-edit="${escapeHtml(route.id)}" title="编辑路由"><i data-lucide="settings-2"></i></button></header>
    <div class="route-chain">${route.members.map((member, index) => `${index ? '<i class="route-arrow" data-lucide="arrow-right"></i>' : ""}<div class="route-node"><strong>${escapeHtml(member.source_name)}</strong><span class="source-health ${member.health_status}">${healthLabel(member.health_status)}</span><small>${escapeHtml(member.role)}</small></div>`).join("") || "暂无成员"}</div></article>`).join("")}</div>`;
  lucide.createIcons();
}

function renderHealth() {
  const names = Object.fromEntries(sourceState.sources.map((item) => [item.id, item.name]));
  $("#sourceTabContent").innerHTML = sourceState.checks.length ? `<div class="source-table-wrap"><table class="source-table"><thead><tr><th>检测时间</th><th>数据源</th><th>结果</th><th>延迟</th><th>说明</th></tr></thead><tbody>${sourceState.checks.map((item) => `<tr><td>${new Date(item.checked_at).toLocaleString()}</td><td>${escapeHtml(names[item.source_id] || item.source_id)}</td><td><span class="source-health ${item.status === "pass" ? "healthy" : "offline"}">${item.status === "pass" ? "通过" : "失败"}</span></td><td>${Math.round(item.latency_ms)} ms</td><td>${escapeHtml(item.message)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="source-empty">暂无健康检测记录</div>`;
}

function renderRevisions() {
  $("#sourceTabContent").innerHTML = sourceState.revisions.length ? `<div class="source-table-wrap"><table class="source-table"><thead><tr><th>路由</th><th>版本</th><th>状态</th><th>来源链</th><th>发布人</th><th>发布时间</th><th>操作</th></tr></thead><tbody>${sourceState.revisions.map((item) => `<tr><td>${escapeHtml(item.route_id)}</td><td>v${item.version}</td><td>${escapeHtml(item.status)}</td><td>${escapeHtml((item.config.source_ids || []).join(" → "))}</td><td>${escapeHtml(item.created_by)}</td><td>${new Date(item.created_at).toLocaleString()}</td><td><button class="text-button admin-write" type="button" data-revision-rollback="${escapeHtml(item.id)}">回滚到此配置</button></td></tr>`).join("")}</tbody></table></div>` : `<div class="source-empty">尚未发布自定义路由版本</div>`;
}

function renderActiveTab() {
  $("#newDataSource").hidden = !["sources", "calendar"].includes(sourceState.tab) || sourceState.me?.user.role !== "admin";
  $("#sourceSearch").closest("label").hidden = !["sources", "calendar"].includes(sourceState.tab);
  $("#sourceDomain").hidden = sourceState.tab !== "sources";
  if (sourceState.tab === "routes") renderRoutes();
  else if (sourceState.tab === "health") renderHealth();
  else if (sourceState.tab === "revisions") renderRevisions();
  else renderSources();
  window.AppI18n?.translate($("#sourceTabContent"));
}

async function loadAll() {
  const [summary, sources, routes, checks, revisions, calendarStatuses] = await Promise.all([
    api("/api/data-sources/summary"), api("/api/data-sources"), api("/api/data-source-routes"),
    api("/api/data-sources/health-checks?limit=100"), api("/api/data-source-config/revisions?limit=100"),
    api("/api/financial-calendar/sources/status"),
  ]);
  Object.assign(sourceState, { summary, sources, routes, checks, revisions, calendarStatuses });
  renderMetrics(); renderActiveTab();
}

function fillSourceForm(item = null) {
  sourceState.editingSource = item;
  $("#sourceFormTitle").textContent = item ? "编辑数据源" : "新增数据源";
  $("#sourceName").value = item?.name || "";
  $("#sourceFormDomain").value = item?.domain || "market";
  $("#sourceProvider").value = item?.provider || "";
  $("#sourceAdapter").value = item?.adapter || "finnhub";
  $("#sourceEndpoint").value = item?.endpoint || "";
  $("#sourceMarkets").value = item?.markets.join(", ") || "";
  $("#sourceAssets").value = item?.asset_classes.join(", ") || "";
  $("#sourceCapabilities").value = item?.capabilities.join(", ") || "";
  $("#sourceAuthType").value = item?.auth_type || "none";
  $("#sourceApiKeyEnv").value = item?.api_key_env || "";
  $("#sourceTimeout").value = item?.timeout_seconds ?? 8;
  $("#sourceCacheTtl").value = item?.cache_ttl_seconds ?? 60;
  $("#sourceStaleTtl").value = item?.stale_ttl_seconds ?? 900;
  $("#sourceSyncInterval").value = item?.sync_interval_seconds ?? 900;
  $("#sourceTimezone").value = item?.timezone || "UTC";
  $("#sourcePriority").value = item?.priority ?? 50;
  $("#sourceConfig").value = JSON.stringify(item?.config || {}, null, 2);
  $("#sourceEnabled").checked = item?.enabled ?? true;
  $("#sourceFormDomain").disabled = Boolean(item);
  $("#sourceAdapter").disabled = Boolean(item);
  $("#sourceProvider").disabled = Boolean(item);
  $("#sourceModal").hidden = false;
}

function closeSourceModal() { $("#sourceModal").hidden = true; sourceState.editingSource = null; }

async function saveSource(event) {
  event.preventDefault();
  let config;
  try { config = JSON.parse($("#sourceConfig").value || "{}"); } catch { notice("扩展配置必须是有效 JSON", "error"); return; }
  const base = {
    name: $("#sourceName").value.trim(), endpoint: $("#sourceEndpoint").value.trim(), enabled: $("#sourceEnabled").checked,
    markets: splitList($("#sourceMarkets").value), asset_classes: splitList($("#sourceAssets").value), capabilities: splitList($("#sourceCapabilities").value),
    timeout_seconds: Number($("#sourceTimeout").value), cache_ttl_seconds: Number($("#sourceCacheTtl").value), stale_ttl_seconds: Number($("#sourceStaleTtl").value),
    sync_interval_seconds: Number($("#sourceSyncInterval").value), timezone: $("#sourceTimezone").value.trim() || "UTC",
    priority: Number($("#sourcePriority").value), config,
    auth_type: $("#sourceAuthType").value, api_key_env: $("#sourceApiKeyEnv").value.trim(),
  };
  if (sourceState.editingSource) {
    await api(`/api/data-sources/${encodeURIComponent(sourceState.editingSource.id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(base) });
  } else {
    const domain = $("#sourceFormDomain").value;
    const capabilities = base.capabilities;
    const category = domain === "content" ? "news" : capabilities.some((item) => item.endsWith("_calendar")) ? "calendar" : domain === "indicator" ? "indicator" : "market";
    await api("/api/data-sources", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...base, domain, category, adapter: $("#sourceAdapter").value, provider: $("#sourceProvider").value.trim(), reliability_weight: 0.8 }) });
  }
  closeSourceModal(); notice("数据源配置已保存。", "success"); await loadAll();
}

function renderRouteEditor() {
  const profiles = Object.fromEntries(sourceState.sources.map((item) => [item.id, item]));
  $("#routeMemberEditor").innerHTML = sourceState.routeMembers.length ? sourceState.routeMembers.map((id, index) => { const item = profiles[id]; return `<div class="route-member-row"><b>${index + 1}</b><span><strong>${escapeHtml(item?.name || id)}</strong><small>${index === 0 && $("#routeMode").value === "fallback" ? "主数据源" : $("#routeMode").value === "aggregate" ? "聚合成员" : "备用数据源"}</small></span><div class="route-member-actions"><button class="icon-button" type="button" data-route-up="${index}" title="上移" ${index === 0 ? "disabled" : ""}><i data-lucide="arrow-up"></i></button><button class="icon-button" type="button" data-route-down="${index}" title="下移" ${index === sourceState.routeMembers.length - 1 ? "disabled" : ""}><i data-lucide="arrow-down"></i></button><button class="icon-button" type="button" data-route-remove="${index}" title="移除"><i data-lucide="x"></i></button></div></div>`; }).join("") : `<div class="source-empty">至少加入一个数据源</div>`;
  lucide.createIcons();
}

function openRouteEditor(route) {
  sourceState.editingRoute = route;
  sourceState.routeMembers = route.members.map((item) => item.source_id);
  $("#routeFormTitle").textContent = `${route.market} · ${route.capability}`;
  $("#routeVersion").textContent = `当前版本 v${route.version}`;
  $("#routeMode").value = route.mode;
  const candidates = sourceState.sources.filter((item) => item.enabled && item.capabilities.includes(route.capability));
  $("#routeSourcePicker").innerHTML = candidates.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${healthLabel(item.health_status)}</option>`).join("");
  renderRouteEditor(); $("#routeModal").hidden = false;
}

function closeRouteModal() { $("#routeModal").hidden = true; sourceState.editingRoute = null; sourceState.routeMembers = []; }

async function publishRoute(event) {
  event.preventDefault();
  if (!sourceState.routeMembers.length) { notice("路由至少需要一个数据源。", "error"); return; }
  const route = sourceState.editingRoute;
  $("#routeValidationHint").textContent = "正在执行连接和能力检测，当前路由继续提供服务...";
  try {
    await api(`/api/data-source-routes/${route.market}/${route.capability}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source_ids: sourceState.routeMembers, mode: $("#routeMode").value, enabled: true, expected_version: route.version }) });
    closeRouteModal(); notice("检测通过，新路由已原子发布。", "success"); await loadAll();
  } catch (error) {
    $("#routeValidationHint").textContent = `发布失败：${error.message}。旧路由保持有效。`;
    notice("数据源检测失败，未修改线上路由。", "error");
  }
}

document.addEventListener("click", async (event) => {
  const tab = event.target.closest("[data-source-tab]");
  if (tab) { sourceState.tab = tab.dataset.sourceTab; document.querySelectorAll("[data-source-tab]").forEach((item) => item.classList.toggle("active", item === tab)); renderActiveTab(); return; }
  const edit = event.target.closest("[data-source-edit]"); if (edit) { fillSourceForm(sourceState.sources.find((item) => item.id === edit.dataset.sourceEdit)); return; }
  const test = event.target.closest("[data-source-test]"); if (test) { notice("正在检测数据源..."); try { const item = await api(`/api/data-sources/${encodeURIComponent(test.dataset.sourceTest)}/test`, { method: "POST" }); notice(item.last_error || "连接检测通过。", item.health_status === "healthy" ? "success" : "error"); await loadAll(); } catch (error) { notice(error.message, "error"); } return; }
  const refresh = event.target.closest("[data-source-refresh]"); if (refresh) { notice("正在刷新数据源..."); try { await api(`/api/data-sources/${encodeURIComponent(refresh.dataset.sourceRefresh)}/refresh`, { method: "POST" }); notice("数据源刷新完成。", "success"); await loadAll(); } catch (error) { notice(error.message, "error"); } return; }
  const toggle = event.target.closest("[data-source-toggle]"); if (toggle) { const item = sourceState.sources.find((value) => value.id === toggle.dataset.sourceToggle); await api(`/api/data-sources/${encodeURIComponent(item.id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: !item.enabled }) }); await loadAll(); return; }
  const routeEdit = event.target.closest("[data-route-edit]"); if (routeEdit) { openRouteEditor(sourceState.routes.find((item) => item.id === routeEdit.dataset.routeEdit)); return; }
  const up = event.target.closest("[data-route-up]"); if (up) { const index = Number(up.dataset.routeUp); [sourceState.routeMembers[index - 1], sourceState.routeMembers[index]] = [sourceState.routeMembers[index], sourceState.routeMembers[index - 1]]; renderRouteEditor(); return; }
  const down = event.target.closest("[data-route-down]"); if (down) { const index = Number(down.dataset.routeDown); [sourceState.routeMembers[index + 1], sourceState.routeMembers[index]] = [sourceState.routeMembers[index], sourceState.routeMembers[index + 1]]; renderRouteEditor(); return; }
  const remove = event.target.closest("[data-route-remove]"); if (remove) { sourceState.routeMembers.splice(Number(remove.dataset.routeRemove), 1); renderRouteEditor(); return; }
  const rollback = event.target.closest("[data-revision-rollback]"); if (rollback && confirm("确认发布该历史路由配置？")) { await api(`/api/data-source-config/revisions/${encodeURIComponent(rollback.dataset.revisionRollback)}/rollback`, { method: "POST" }); notice("历史路由已作为新版本发布。", "success"); await loadAll(); }
});

$("#newDataSource").addEventListener("click", () => fillSourceForm());
$("#closeSourceModal").addEventListener("click", closeSourceModal); $("#cancelSourceModal").addEventListener("click", closeSourceModal); $("#sourceForm").addEventListener("submit", (event) => saveSource(event).catch((error) => notice(error.message, "error")));
$("#closeRouteModal").addEventListener("click", closeRouteModal); $("#cancelRouteModal").addEventListener("click", closeRouteModal); $("#routeForm").addEventListener("submit", publishRoute);
$("#routeMode").addEventListener("change", renderRouteEditor);
$("#addRouteSource").addEventListener("click", () => { const id = $("#routeSourcePicker").value; if (id && !sourceState.routeMembers.includes(id)) { sourceState.routeMembers.push(id); renderRouteEditor(); } });
$("#sourceSearch").addEventListener("input", renderSources); $("#sourceDomain").addEventListener("change", renderSources); $("#refreshSourceControl").addEventListener("click", () => loadAll().catch((error) => notice(error.message, "error")));
$("#sourceThemeToggle").addEventListener("click", () => window.AppTheme.toggle(sourceState.csrf));
$("#sourceLogout").addEventListener("click", () => api("/api/auth/logout", { method: "POST" }).finally(() => location.replace("/login")));

(async () => {
  sourceState.me = await api("/api/auth/me"); sourceState.csrf = sourceState.me.csrf_token; $("#sourceUserLabel").textContent = sourceState.me.user.display_name;
  document.body.classList.toggle("viewer-mode", sourceState.me.user.role !== "admin"); await loadAll(); lucide.createIcons();
})().catch((error) => notice(error.message, "error"));
