const newsSourceState = { me: null, csrf: "", sources: [], monitors: [], runs: [], status: "", editing: null, currentDiagnostic: null, bulkRefreshing: false, poller: null };
const $ = (selector) => document.querySelector(selector);
const statusLabels = { running: "运行中", succeeded: "成功", failed: "失败", skipped: "已跳过", healthy: "正常", idle: "等待首次运行", paused: "已停用", unconfigured: "缺少凭据", unsupported: "仅目录", error: "异常" };

async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("X-CSRF-Token", newsSourceState.csrf);
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) { window.location.replace("/login"); throw new Error("登录已失效"); }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "请求失败");
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function formatTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat(document.documentElement.lang === "en-US" ? "en-US" : "zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);
}

function relativeTime(value) {
  if (!value) return "";
  const delta = new Date(value).getTime() - Date.now();
  const absolute = Math.abs(delta);
  if (absolute < 60000) return delta >= 0 ? "即将执行" : "刚刚";
  if (absolute < 3600000) return `${Math.round(absolute / 60000)} 分钟${delta >= 0 ? "后" : "前"}`;
  return `${Math.round(absolute / 3600000)} 小时${delta >= 0 ? "后" : "前"}`;
}

function formatInterval(seconds) {
  if (seconds % 3600 === 0) return `${seconds / 3600} 小时`;
  return `${Math.round(seconds / 60)} 分钟`;
}

function showNotice(message, status = "") {
  $("#newsSourceNotice").className = `resource-notice ${status}`;
  $("#newsSourceNotice").textContent = message;
}

function diagnosticValue(value) {
  return value === null || value === undefined || value === "" ? "--" : String(value);
}

function openDiagnostic(diagnostic, title = "RSS 连接诊断") {
  if (!diagnostic) return;
  newsSourceState.currentDiagnostic = diagnostic;
  const evidence = diagnostic.evidence || {};
  const success = diagnostic.category === "success";
  $("#newsSourceDiagnosticTitle").textContent = title;
  $("#newsSourceDiagnosticBody").innerHTML = `<div class="diagnostic-summary">
    <section class="diagnostic-hero ${success ? "success" : ""}">
      <span class="diagnostic-code">${escapeHtml(diagnostic.code)}</span>
      <div><strong>${escapeHtml(diagnostic.message)}</strong><span>${escapeHtml(diagnostic.suggestion)}</span></div>
    </section>
    <section class="diagnostic-section"><strong>故障定位</strong><div class="diagnostic-grid">
      <div class="diagnostic-field"><span>阶段</span><strong>${escapeHtml(diagnostic.stage)}</strong></div>
      <div class="diagnostic-field"><span>分类</span><strong>${escapeHtml(diagnostic.category)}</strong></div>
      <div class="diagnostic-field"><span>可重试</span><strong>${diagnostic.retryable ? "是" : "否"}</strong></div>
      <div class="diagnostic-field"><span>请求次数</span><strong>${diagnostic.attempt_count || 0}</strong></div>
    </div></section>
    <section class="diagnostic-section"><strong>脱敏响应信息</strong><div class="diagnostic-grid">
      <div class="diagnostic-field"><span>HTTP 状态</span><strong>${escapeHtml(diagnosticValue(evidence.http_status))}</strong></div>
      <div class="diagnostic-field"><span>最终域名</span><strong>${escapeHtml(diagnosticValue(evidence.final_host))}</strong></div>
      <div class="diagnostic-field"><span>Content-Type</span><strong>${escapeHtml(diagnosticValue(evidence.content_type))}</strong></div>
      <div class="diagnostic-field"><span>响应大小</span><strong>${evidence.content_length === null || evidence.content_length === undefined ? "--" : `${evidence.content_length} bytes`}</strong></div>
      <div class="diagnostic-field"><span>Feed 类型</span><strong>${escapeHtml(diagnosticValue(evidence.feed_type))}</strong></div>
      <div class="diagnostic-field"><span>有效条目</span><strong>${escapeHtml(diagnosticValue(evidence.valid_entries))} / ${escapeHtml(diagnosticValue(evidence.total_entries))}</strong></div>
      <div class="diagnostic-field"><span>解析位置</span><strong>${evidence.parser_line === null || evidence.parser_line === undefined ? "--" : `${evidence.parser_line}:${evidence.parser_column || 0}`}</strong></div>
      <div class="diagnostic-field"><span>内容 SHA-256</span><strong>${escapeHtml(diagnosticValue(evidence.content_sha256))}</strong></div>
    </div></section>
    <section class="diagnostic-section"><strong>请求尝试</strong><div>${(diagnostic.attempts || []).length ? diagnostic.attempts.map((attempt) => `<div class="diagnostic-attempt"><span>第 ${attempt.attempt} 次</span><strong class="${attempt.outcome}">${attempt.outcome === "succeeded" ? "成功" : escapeHtml(attempt.code || "失败")}</strong><span>${attempt.duration_ms} ms</span></div>`).join("") : "<span>没有请求尝试记录。</span>"}</div></section>
  </div>`;
  $("#newsSourceDiagnosticDrawer").hidden = false;
  window.lucide?.createIcons();
}

function closeDiagnostic() {
  $("#newsSourceDiagnosticDrawer").hidden = true;
}

function joinedSources() {
  const monitorById = new Map(newsSourceState.monitors.map((item) => [item.source_id, item]));
  return newsSourceState.sources.map((source) => ({ ...source, monitor: monitorById.get(source.id) }));
}

function visibleSources() {
  const query = $("#newsSourceSearch").value.trim().toLowerCase();
  const adapter = $("#newsSourceAdapter").value;
  return joinedSources().filter((source) => {
    const status = source.monitor?.status || "idle";
    if (newsSourceState.status === "healthy" && !["healthy", "running", "idle"].includes(status)) return false;
    if (newsSourceState.status === "attention" && !["error", "unconfigured", "unsupported"].includes(status)) return false;
    if (newsSourceState.status === "paused" && status !== "paused") return false;
    if (adapter && source.adapter !== adapter) return false;
    return !query || `${source.name} ${source.provider} ${source.adapter} ${source.url}`.toLowerCase().includes(query);
  });
}

function renderMetrics() {
  const monitors = newsSourceState.monitors;
  const healthy = monitors.filter((item) => ["healthy", "running", "idle"].includes(item.status)).length;
  const attention = monitors.filter((item) => ["error", "unconfigured", "unsupported"].includes(item.status)).length;
  const active = monitors.filter((item) => item.enabled && item.ingestible).length;
  const runs = monitors.reduce((sum, item) => sum + item.runs_24h, 0);
  const rates = monitors.filter((item) => item.success_rate_24h !== null).map((item) => item.success_rate_24h);
  const successRate = rates.length ? `${(rates.reduce((a, b) => a + b, 0) / rates.length).toFixed(1)}%` : "--";
  $("#newsSourceMetrics").innerHTML = `<article><span>来源总数</span><strong>${monitors.length}</strong></article><article><span>独立轮询</span><strong>${active}</strong></article><article><span>正常 / 需处理</span><strong>${healthy} / ${attention}</strong></article><article><span>24 小时运行 / 成功率</span><strong>${runs} / ${successRate}</strong></article>`;
  $("#allSourceCount").textContent = monitors.length;
  $("#healthySourceCount").textContent = healthy;
  $("#attentionSourceCount").textContent = attention;
  $("#pausedSourceCount").textContent = monitors.filter((item) => item.status === "paused").length;
}

function renderTable() {
  const sources = visibleSources();
  const canWrite = newsSourceState.me?.role === "admin";
  $("#newsSourceTableBody").innerHTML = sources.length ? sources.map((source) => {
    const monitor = source.monitor || {};
    const status = monitor.status || "idle";
    const result = monitor.last_status ? `${monitor.last_fetched || 0} 抓取 / ${monitor.last_inserted || 0} 新增` : "尚无运行数据";
    const rate = monitor.success_rate_24h === null || monitor.success_rate_24h === undefined ? "--" : `${monitor.success_rate_24h.toFixed(1)}%`;
    return `<tr>
      <td><div class="schedule-task-name"><strong>${escapeHtml(source.name)}</strong><span>${escapeHtml(source.provider)} · ${escapeHtml(source.adapter)}</span></div></td>
      <td><span class="source-monitor-status ${status}"><i></i>${statusLabels[status] || status}</span>${monitor.last_error ? `<button class="source-diagnostic-link" type="button" data-monitor-diagnostic="${source.id}" title="查看完整诊断">${escapeHtml(monitor.last_diagnostic?.code || monitor.last_error)}</button>` : ""}</td>
      <td><div class="schedule-time"><strong>${formatInterval(source.poll_interval_seconds)}</strong><span>${source.poll_interval_seconds} 秒</span></div></td>
      <td><div class="schedule-time"><strong>${formatTime(monitor.last_run_at)}</strong><span>${monitor.last_duration_ms === null || monitor.last_duration_ms === undefined ? "--" : `${monitor.last_duration_ms} ms`}</span></div></td>
      <td><div class="schedule-time"><strong>${result}</strong><span>${monitor.consecutive_failures ? `连续失败 ${monitor.consecutive_failures} 次` : monitor.last_status || "--"}</span></div></td>
      <td><div class="schedule-time"><strong>${monitor.runs_24h || 0} 次</strong><span>成功率 ${rate}</span></div></td>
      <td><div class="schedule-time"><strong>${formatTime(monitor.next_run_at)}</strong><span>${relativeTime(monitor.next_run_at)}</span></div></td>
      <td><div class="schedule-row-actions"><button class="icon-button admin-write" type="button" data-refresh-source="${source.id}" title="立即刷新" ${canWrite && monitor.ingestible ? "" : "disabled"}><i data-lucide="play"></i></button><button class="icon-button admin-write" type="button" data-test-source="${source.id}" title="连接检测" ${canWrite && source.adapter === "rss" ? "" : "disabled"}><i data-lucide="plug-zap"></i></button><button class="icon-button" type="button" data-source-runs="${source.id}" title="运行历史"><i data-lucide="history"></i></button><button class="icon-button admin-write" type="button" data-edit-source="${source.id}" title="编辑" ${canWrite ? "" : "disabled"}><i data-lucide="pencil"></i></button><button class="icon-button admin-write ${source.enabled ? "danger-action" : ""}" type="button" data-toggle-source="${source.id}" data-enable="${source.enabled ? "false" : "true"}" title="${source.enabled ? "停用" : "启用"}" ${canWrite ? "" : "disabled"}><i data-lucide="${source.enabled ? "pause" : "power"}"></i></button></div></td>
    </tr>`;
  }).join("") : `<tr><td colspan="8" class="empty-cell">当前条件下没有新闻源。</td></tr>`;
  $("#visibleNewsSourceCount").textContent = `${sources.length} 个来源`;
  $("#newsSourceUpdatedAt").textContent = `更新于 ${formatTime(new Date().toISOString())}`;
  window.lucide?.createIcons();
}

function renderAll() { renderMetrics(); renderTable(); }

async function loadSources({ quiet = false } = {}) {
  const overview = await api("/api/news-sources/overview");
  newsSourceState.sources = overview.sources;
  newsSourceState.monitors = overview.monitors;
  renderAll();
  if (!quiet) showNotice("新闻源状态已更新。", "ok");
}

function bestIntervalUnit(seconds) { return seconds % 3600 === 0 ? 3600 : 60; }

function openSourceModal(source = null) {
  newsSourceState.editing = source;
  const interval = source?.poll_interval_seconds || 900;
  const unit = bestIntervalUnit(interval);
  $("#newsSourceFormTitle").textContent = source ? `编辑 ${source.name}` : "新建新闻源";
  $("#editSourceName").value = source?.name || "";
  $("#editSourceProvider").value = source?.provider || "custom";
  $("#editSourceUrl").value = source?.url || "";
  $("#editSourceType").value = source?.type || "rss";
  $("#editSourceAdapter").value = source?.adapter || "rss";
  $("#editSourceIntervalUnit").value = String(unit);
  $("#editSourceInterval").value = interval / unit;
  $("#editSourceEnabled").checked = source?.enabled ?? true;
  $("#editSourceSymbols").value = source?.symbols?.join(", ") || "";
  $("#newsSourceModal").hidden = false;
  window.lucide?.createIcons();
}

function closeSourceModal() { $("#newsSourceModal").hidden = true; newsSourceState.editing = null; }

async function saveSource(event) {
  event.preventDefault();
  const source = newsSourceState.editing;
  const payload = {
    name: $("#editSourceName").value.trim(), provider: $("#editSourceProvider").value.trim(),
    url: $("#editSourceUrl").value.trim(), type: $("#editSourceType").value,
    adapter: $("#editSourceAdapter").value, enabled: $("#editSourceEnabled").checked,
    symbols: $("#editSourceSymbols").value.split(/[,，\s]+/).filter(Boolean),
    poll_interval_seconds: Math.round(Number($("#editSourceInterval").value) * Number($("#editSourceIntervalUnit").value)),
  };
  await api(source ? `/api/news-sources/${source.id}` : "/api/news-sources", { method: source ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  closeSourceModal();
  await loadSources({ quiet: true });
  showNotice(source ? "新闻源配置已保存。" : "新闻源已创建，独立调度将在到期时运行。", "ok");
}

async function refreshSource(sourceId) {
  showNotice("正在刷新新闻源...", "watch");
  const result = await api(`/api/news-sources/${sourceId}/refresh`, { method: "POST" });
  await loadSources({ quiet: true });
  showNotice(result.error || `刷新完成：抓取 ${result.fetched} 条，新增 ${result.inserted} 条。`, result.error ? "error" : "ok");
  if (result.error && result.diagnostic) openDiagnostic(result.diagnostic, "RSS 刷新失败诊断");
}

async function refreshAllSources() {
  if (newsSourceState.bulkRefreshing) return;
  const monitorById = new Map(newsSourceState.monitors.map((item) => [item.source_id, item]));
  const sources = newsSourceState.sources.filter((source) => {
    const monitor = monitorById.get(source.id);
    return source.enabled && monitor?.ingestible && monitor.configured;
  });
  if (!sources.length) {
    showNotice("没有可刷新的新闻源。", "watch");
    return;
  }
  newsSourceState.bulkRefreshing = true;
  const button = $("#refreshAllNewsSources");
  button.disabled = true;
  let cursor = 0;
  let completed = 0;
  const results = [];
  const worker = async () => {
    while (cursor < sources.length) {
      const source = sources[cursor];
      cursor += 1;
      let result;
      try {
        result = await api(`/api/news-sources/${source.id}/refresh`, { method: "POST" });
      } catch (error) {
        result = { source_id: source.id, status: "failed", error: error.message };
      }
      results.push(result);
      completed += 1;
      const failures = results.filter((item) => item.status === "failed" || item.error).length;
      showNotice(`全部刷新 ${completed} / ${sources.length} · 成功 ${completed - failures} · 失败 ${failures}`, failures ? "watch" : "ok");
    }
  };
  try {
    await Promise.all(Array.from({ length: Math.min(2, sources.length) }, worker));
    await loadSources({ quiet: true });
    const failures = results.filter((item) => item.status === "failed" || item.error);
    const inserted = results.reduce((total, item) => total + (item.inserted || 0), 0);
    showNotice(`全部刷新完成：成功 ${results.length - failures.length}，失败 ${failures.length}，新增 ${inserted} 条。`, failures.length ? "error" : "ok");
    if (failures[0]?.diagnostic) openDiagnostic(failures[0].diagnostic, "批量刷新失败诊断");
  } finally {
    newsSourceState.bulkRefreshing = false;
    button.disabled = false;
  }
}

async function testSource(sourceId) {
  const source = newsSourceState.sources.find((item) => item.id === sourceId);
  showNotice(`正在检测 ${source?.name || "RSS 新闻源"}...`, "watch");
  const result = await api(`/api/news-sources/${sourceId}/test`, { method: "POST" });
  await loadSources({ quiet: true });
  showNotice(result.status === "succeeded" ? `连接检测通过，发现 ${result.fetched} 个有效条目。` : result.diagnostic.message, result.status === "succeeded" ? "ok" : "error");
  openDiagnostic(result.diagnostic, `${source?.name || "RSS 新闻源"} · 连接诊断`);
}

async function toggleSource(sourceId, enabled) {
  await api(`/api/news-sources/${sourceId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled }) });
  await loadSources({ quiet: true });
  showNotice(enabled ? "新闻源已启用。" : "新闻源已停用。", "ok");
}

async function openRuns(sourceId) {
  const source = newsSourceState.sources.find((item) => item.id === sourceId);
  $("#newsSourceRunsTitle").textContent = `${source?.name || "新闻源"} · 运行历史`;
  $("#newsSourceRunsBody").innerHTML = `<p>正在加载运行记录...</p>`;
  $("#newsSourceRunsDrawer").hidden = false;
  const runs = await api(`/api/news-sources/${sourceId}/runs?limit=30`);
  newsSourceState.runs = runs;
  $("#newsSourceRunsBody").innerHTML = runs.length ? runs.map((run) => `<article class="source-run ${run.status}"><div><strong>${statusLabels[run.status] || run.status}</strong><span>${escapeHtml(run.trigger)} · ${formatTime(run.started_at)}</span></div><div class="source-run-actions"><span>抓取 ${run.fetched} / 新增 ${run.inserted}</span><span>${run.duration_ms === null ? "--" : `${run.duration_ms} ms`}</span>${run.diagnostic ? `<button class="source-run-diagnostic" type="button" data-run-diagnostic="${escapeHtml(run.id)}">查看诊断</button>` : ""}</div>${run.error ? `<small>${escapeHtml(run.error)}</small>` : ""}</article>`).join("") : `<p>暂无运行记录。</p>`;
}

async function bootstrap() {
  const session = await api("/api/auth/me");
  newsSourceState.me = session.user;
  newsSourceState.csrf = session.csrf_token;
  window.AppTheme?.apply(session.preferences?.theme || "system");
  window.AppTheme?.updateControls();
  $("#newsSourceUserLabel").textContent = `${session.user.display_name} · ${session.user.role === "admin" ? "管理员" : "只读"}`;
  document.body.classList.toggle("viewer-mode", session.user.role !== "admin");
  await loadSources({ quiet: true });
  newsSourceState.poller = window.setInterval(() => { if (!document.hidden) loadSources({ quiet: true }).catch(() => {}); }, 15000);
}

document.addEventListener("click", (event) => {
  const status = event.target.closest("[data-source-status]");
  if (status) { newsSourceState.status = status.dataset.sourceStatus; document.querySelectorAll("[data-source-status]").forEach((item) => item.classList.toggle("active", item === status)); renderTable(); return; }
  const refresh = event.target.closest("[data-refresh-source]");
  if (refresh) { refreshSource(Number(refresh.dataset.refreshSource)).catch((error) => showNotice(error.message, "error")); return; }
  const test = event.target.closest("[data-test-source]");
  if (test) { testSource(Number(test.dataset.testSource)).catch((error) => showNotice(error.message, "error")); return; }
  const monitorDiagnostic = event.target.closest("[data-monitor-diagnostic]");
  if (monitorDiagnostic) {
    const monitor = newsSourceState.monitors.find((item) => item.source_id === Number(monitorDiagnostic.dataset.monitorDiagnostic));
    if (monitor?.last_diagnostic) openDiagnostic(monitor.last_diagnostic, `${monitor.name} · 最近失败诊断`);
    else if (monitor?.last_error) openDiagnostic({ stage: "request", category: "unknown", code: "LEGACY_ERROR", message: monitor.last_error, suggestion: "重新运行连接检测以生成结构化诊断。", attempts: [], evidence: {} }, `${monitor.name} · 历史错误`);
    return;
  }
  const runDiagnostic = event.target.closest("[data-run-diagnostic]");
  if (runDiagnostic) {
    const run = newsSourceState.runs.find((item) => item.id === runDiagnostic.dataset.runDiagnostic);
    if (run?.diagnostic) openDiagnostic(run.diagnostic, `运行 ${run.id} · 诊断`);
    return;
  }
  const runs = event.target.closest("[data-source-runs]");
  if (runs) { openRuns(Number(runs.dataset.sourceRuns)).catch((error) => showNotice(error.message, "error")); return; }
  const edit = event.target.closest("[data-edit-source]");
  if (edit) { openSourceModal(newsSourceState.sources.find((item) => item.id === Number(edit.dataset.editSource))); return; }
  const toggle = event.target.closest("[data-toggle-source]");
  if (toggle && window.confirm(`确认${toggle.dataset.enable === "true" ? "启用" : "停用"}该新闻源？`)) toggleSource(Number(toggle.dataset.toggleSource), toggle.dataset.enable === "true").catch((error) => showNotice(error.message, "error"));
});

$("#newsSourceSearch").addEventListener("input", renderTable);
$("#newsSourceAdapter").addEventListener("change", renderTable);
$("#reloadNewsSources").addEventListener("click", () => loadSources().catch((error) => showNotice(error.message, "error")));
$("#refreshAllNewsSources").addEventListener("click", () => refreshAllSources().catch((error) => showNotice(error.message, "error")));
$("#createNewsSource").addEventListener("click", () => openSourceModal());
$("#newsSourceForm").addEventListener("submit", (event) => saveSource(event).catch((error) => showNotice(error.message, "error")));
[$("#closeNewsSourceModal"), $("#cancelNewsSourceModal")].forEach((button) => button.addEventListener("click", closeSourceModal));
$("#newsSourceModal").addEventListener("click", (event) => { if (event.target.id === "newsSourceModal") closeSourceModal(); });
$("#closeNewsSourceRuns").addEventListener("click", () => { $("#newsSourceRunsDrawer").hidden = true; });
$("#newsSourceRunsDrawer").addEventListener("click", (event) => { if (event.target.id === "newsSourceRunsDrawer") event.currentTarget.hidden = true; });
$("#closeNewsSourceDiagnostic").addEventListener("click", closeDiagnostic);
$("#newsSourceDiagnosticDrawer").addEventListener("click", (event) => { if (event.target.id === "newsSourceDiagnosticDrawer") closeDiagnostic(); });
$("#copyNewsSourceDiagnostic").addEventListener("click", async () => {
  if (!newsSourceState.currentDiagnostic) return;
  await navigator.clipboard.writeText(JSON.stringify(newsSourceState.currentDiagnostic, null, 2));
  showNotice("诊断信息已复制。", "ok");
});
$("#newsSourceThemeToggle").addEventListener("click", () => window.AppTheme.toggle(newsSourceState.csrf).catch((error) => showNotice(error.message, "error")));
$("#newsSourceLogout").addEventListener("click", async () => { await api("/api/auth/logout", { method: "POST" }); window.location.replace("/login"); });
bootstrap().catch((error) => showNotice(error.message, "error"));
