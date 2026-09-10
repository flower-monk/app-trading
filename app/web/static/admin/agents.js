const state = { me: null, csrf: "", agents: [], providers: [], channels: [], runs: [], proposals: [], symbols: [], tagging: null, activeAgent: null, editingProvider: null, reviewingProposal: null, section: "registry" };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const templates = {
  market_monitor: "行情盯盘", news_tagger: "新闻标注流水线", news_event_extractor: "事件抽取",
  news_impact_mapper: "影响映射", news_impact_validator: "影响校验", news_analyst: "新闻分析", risk_sentinel: "风险哨兵",
  factor_research: "因子研究", briefing: "全局简报",
};
const triggerLabels = { manual: "手动运行", schedule: "周期调度", news_ingested: "新闻入库", high_impact_news: "高影响新闻", alert_triggered: "预警触发", backtest_completed: "回测完成" };

async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("X-CSRF-Token", state.csrf);
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) { window.location.replace("/login"); throw new Error("登录已失效"); }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "请求失败");
  return payload;
}
function esc(value) { return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;"); }
function notice(message, type = "ok") { const node = $("#agentNotice"); node.textContent = message; node.className = `resource-notice ${type}`; if (message) setTimeout(() => { if (node.textContent === message) node.textContent = ""; }, 5000); }
function fmt(time) { return time ? new Date(time).toLocaleString(window.AppI18n?.getLocale() || "zh-CN", { hour12: false }) : "--"; }

function renderMetrics() {
  const active = state.agents.filter((item) => item.enabled).length;
  const queued = state.runs.filter((item) => ["queued", "running"].includes(item.status)).length;
  const failed = state.runs.filter((item) => item.status === "failed").length;
  const pending = state.proposals.filter((item) => item.status === "pending").length;
  const connected = state.channels.filter((item) => item.enabled && item.secret_configured && item.health_status !== "unavailable").length;
  const tagQueue = (state.tagging?.queued || 0) + (state.tagging?.running || 0) + (state.tagging?.waiting_provider || 0);
  $("#agentMetrics").innerHTML = `<article><span>启用 Agent</span><strong>${active}/${state.agents.length}</strong></article><article><span>Agent 队列</span><strong>${queued}</strong></article><article><span>新闻标注队列</span><strong>${tagQueue}</strong></article><article><span>近期失败</span><strong>${failed}</strong></article><article><span>待审批建议</span><strong>${pending}</strong></article><article><span>模型渠道就绪</span><strong>${connected}/${state.channels.length}</strong></article>`;
  $("#navAgentCount").textContent = state.agents.length; $("#navRunCount").textContent = state.runs.length; $("#navProposalCount").textContent = pending; $("#navProviderCount").textContent = state.providers.length;
}

function renderAgentList() {
  const query = $("#agentSearch").value.trim().toLowerCase(); const status = $("#agentStatus").value;
  const items = state.agents.filter((item) => (!query || `${item.name} ${item.description}`.toLowerCase().includes(query)) && (!status || (status === "enabled") === item.enabled));
  const group = (builtin) => items.filter((item) => item.builtin === builtin);
  const renderGroup = (title, rows) => rows.length ? `<div class="agent-group-title">${title}</div>${rows.map((item) => `<button class="agent-list-item ${item.enabled ? "enabled" : ""} ${state.activeAgent?.id === item.id ? "active" : ""}" type="button" data-agent-id="${esc(item.id)}"><i class="status-dot"></i><span><strong>${esc(item.name)}</strong><small>${esc(templates[item.template_type])} · ${item.runtime_mode.toUpperCase()}</small></span><b>${item.schedule_enabled ? `${item.interval_seconds}s` : "手动"}</b></button>`).join("")}` : "";
  $("#agentList").innerHTML = renderGroup("系统内置", group(true)) + renderGroup("自定义实例", group(false)) || `<div class="agent-empty">没有匹配的 Agent。</div>`;
}

function fillAgentEditor(agent) {
  state.activeAgent = agent; $("#agentEmpty").hidden = true; $("#agentEditorContent").hidden = false;
  $("#agentTypeLabel").textContent = `${agent.builtin ? "内置" : "自定义"} · ${templates[agent.template_type]}`;
  $("#agentNameHeading").textContent = agent.name; $("#agentDescriptionHeading").textContent = agent.description;
  $("#agentName").value = agent.name; $("#agentDescription").value = agent.description; $("#agentMode").value = agent.runtime_mode;
  $("#agentProvider").value = agent.model_route_id || ""; $("#agentEnabled").checked = agent.enabled; $("#agentFallback").checked = agent.fallback_to_rules;
  $("#agentTemplate").value = templates[agent.template_type]; $("#agentSettings").value = JSON.stringify(agent.settings, null, 2);
  $("#agentSystemPrompt").value = agent.system_prompt; $("#agentTaskPrompt").value = agent.task_prompt; $("#agentScope").value = agent.symbol_scope;
  $("#agentSymbols").value = agent.symbols.join(","); $("#agentInterval").value = agent.interval_seconds; $("#agentSchedule").checked = agent.schedule_enabled;
  $("#agentChannels").value = agent.notification_channel_ids.join(",");
  $("#triggerOptions").innerHTML = Object.entries(triggerLabels).map(([key, label]) => `<label><input type="checkbox" value="${key}" ${agent.triggers.includes(key) ? "checked" : ""} />${label}</label>`).join("");
  renderAgentList();
}

function renderProviders() {
  $("#agentProvider").innerHTML = `<option value="">不使用模型</option>${state.providers.map((item) => `<option value="${esc(item.id)}">${esc(item.name)} · ${esc(item.alias)}</option>`).join("")}`;
  $("#providerList").innerHTML = state.providers.length ? state.providers.map((item) => {
    const healthy = item.targets.filter((target) => !["disabled", "unavailable"].includes(target.health_status)).length;
    return `<article class="provider-item"><div><div><strong>${esc(item.name)}</strong> <span class="status-pill ${item.enabled && healthy ? "succeeded" : "failed"}">${item.enabled ? `${healthy}/${item.targets.length} 可用` : "已停用"}</span></div><p>${esc(item.description || "逻辑模型路由")}</p><div class="provider-meta"><span>别名 ${esc(item.alias)}</span><span>目标 ${item.targets.length}</span><span>温度 ${item.temperature}</span><span>最大输出 ${item.max_tokens}</span></div></div><div class="proposal-actions admin-write"><a class="text-button" href="/models?tab=playground&route=${encodeURIComponent(item.id)}">测试聊天</a><a class="primary-button" href="/models?tab=routes&route=${encodeURIComponent(item.id)}">管理路由</a></div></article>`;
  }).join("") : `<div class="agent-empty">尚未配置逻辑模型；规则 Agent 可正常运行。<a href="/models?tab=routes">前往模型管理</a></div>`;
}

function openProviderModal(provider = null) {
  state.editingProvider = provider;
  $("#providerFormTitle").textContent = provider ? "编辑模型服务" : "新增模型服务";
  $("#providerName").value = provider?.name || "";
  $("#providerUrl").value = provider?.base_url || "";
  $("#providerModel").value = provider?.model || "";
  $("#providerEnv").value = provider?.api_key_env || "";
  $("#providerApiKey").value = "";
  const status = $("#providerCredentialStatus");
  status.hidden = !provider;
  if (provider) status.innerHTML = `<strong>密钥状态</strong><span>${provider.secret_configured ? `${esc(provider.credential_origin)} · ${esc(provider.secret_hint || "已配置")}` : "未配置"} · 检测 ${esc(provider.credential_test_status)}</span><a href="/credentials">凭据中心</a>`;
  $("#providerModal").hidden = false;
}

function renderRuns() {
  $("#runTable").innerHTML = state.runs.length ? `<table><thead><tr><th>Agent</th><th>触发</th><th>状态</th><th>标的</th><th>模式 / 模型</th><th>耗时</th><th>完成时间</th><th></th></tr></thead><tbody>${state.runs.map((item) => `<tr><td>${esc(item.agent_name)}${item.test ? " · 测试" : ""}</td><td>${esc(triggerLabels[item.trigger] || item.trigger)}</td><td><span class="status-pill ${item.status}">${esc(item.status)}</span></td><td>${esc(item.symbol || "全局")}</td><td>${esc(item.runtime_mode)}${item.model ? ` / ${esc(item.model)}` : ""}</td><td>${item.latency_ms}ms</td><td>${fmt(item.finished_at || item.created_at)}</td><td><button class="icon-button admin-write" type="button" data-run-id="${esc(item.id)}" title="查看详情"><i data-lucide="search"></i></button></td></tr>`).join("")}</tbody></table>` : `<div class="agent-empty">暂无运行记录。</div>`;
  window.lucide?.createIcons();
}

function runJson(value) {
  return JSON.stringify(value ?? null, null, 2);
}

function runInputSection(title, value, id) {
  return `<section class="run-detail-section"><div class="run-detail-section-head"><h3>${esc(title)}</h3><button class="icon-button" type="button" data-copy-run-target="${esc(id)}" title="复制"><i data-lucide="copy"></i></button></div><pre id="${esc(id)}" class="run-json">${esc(typeof value === "string" ? value : runJson(value))}</pre></section>`;
}

function renderRunDetail(item) {
  const snapshot = item.input_snapshot;
  const execution = snapshot?.execution || {};
  const truncated = snapshot?.truncated_fields || [];
  let inputContent;
  if (!snapshot) {
    inputContent = `<div class="run-snapshot-empty"><strong>历史运行仅保留触发输入</strong><p>该运行创建于输入快照功能上线前，无法还原当时的规则基线和实际模型提示词。</p></div>${runInputSection("触发输入", item.trigger_input, "runTriggerInput")}`;
  } else {
    const request = snapshot.model_request;
    inputContent = `
      ${truncated.length ? `<div class="run-snapshot-warning"><strong>部分输入已截断</strong><span>${esc(truncated.join(", "))}</span></div>` : ""}
      ${runInputSection("触发输入", snapshot.trigger || item.trigger_input, "runTriggerInput")}
      ${runInputSection("Agent 配置快照", snapshot.agent_config, "runAgentConfig")}
      ${runInputSection("解析后上下文", snapshot.resolved_context, "runResolvedContext")}
      ${runInputSection("规则基线", snapshot.rule_baseline, "runRuleBaseline")}
      ${request ? `<section class="run-detail-section"><div class="run-detail-section-head"><h3>模型请求</h3></div><dl class="resource-detail run-request-meta"><div><dt>逻辑路由</dt><dd>${esc(request.route_alias || request.route_id || request.provider_id || "--")}</dd></div><div><dt>生成参数</dt><dd>temperature=${esc(request.temperature)} · max_tokens=${esc(request.max_tokens)}</dd></div><div><dt>模型执行</dt><dd>${execution.model_attempted ? esc(execution.fallback_used ? "调用失败，已规则降级" : execution.model || "已调用") : "未调用"}</dd></div></dl></section>${runInputSection("System Prompt", request.system_prompt, "runSystemPrompt")}${runInputSection("User Prompt", request.user_prompt, "runUserPrompt")}` : `<div class="run-snapshot-empty"><strong>本次运行未调用模型</strong><p>当前为规则模式，或该任务被指定为强制规则回标。</p></div>`}
    `;
  }
  $("#runDetail").innerHTML = `
    <dl class="resource-detail"><div><dt>Agent</dt><dd>${esc(item.agent_name)}</dd></div><div><dt>状态</dt><dd>${esc(item.status)}</dd></div><div><dt>触发方式</dt><dd>${esc(triggerLabels[item.trigger] || item.trigger)}</dd></div><div><dt>模式 / 模型</dt><dd>${esc(item.runtime_mode)}${item.model ? ` / ${esc(item.model)}` : ""}</dd></div><div><dt>Token</dt><dd>${item.prompt_tokens} / ${item.completion_tokens}</dd></div><div><dt>耗时</dt><dd>${item.latency_ms}ms</dd></div><div><dt>错误</dt><dd>${esc(item.error || execution.model_error || "--")}</dd></div></dl>
    <div class="run-detail-tabs" role="tablist"><button class="active" type="button" data-run-detail-tab="input">执行输入</button><button type="button" data-run-detail-tab="output">结构化输出</button></div>
    <div class="run-detail-panel active" data-run-detail-panel="input">${inputContent}</div>
    <div class="run-detail-panel" data-run-detail-panel="output">${runInputSection("结构化输出", item.output, "runStructuredOutput")}</div>`;
  window.lucide?.createIcons();
}

async function openRunDetail(runId) {
  $("#runDrawer").hidden = false;
  $("#runDetail").innerHTML = `<div class="run-detail-loading">正在加载运行输入...</div>`;
  try {
    renderRunDetail(await api(`/api/agents/runs/${encodeURIComponent(runId)}`));
  } catch (error) {
    $("#runDetail").innerHTML = `<div class="run-snapshot-empty"><strong>运行详情加载失败</strong><p>${esc(error.message)}</p></div>`;
  }
}

function renderProposals() {
  const status = $("#proposalStatus").value; const items = state.proposals.filter((item) => !status || item.status === status);
  $("#proposalList").innerHTML = items.length ? items.map((item) => {
    const newsTags = item.resource_type === "news-tags";
    const impactGraph = item.resource_type === "impact-graph";
    const detail = newsTags ? `<p>${esc(item.payload.source || "未知来源")} · ${fmt(item.created_at)}</p><div class="tag-review-tags">${(item.payload.tags || []).map((tag) => `<span class="news-tag-chip ${esc(tag.relation)}">${esc(tag.symbol)} · ${tag.relation === "primary" ? "主要" : "次要"} · ${(Number(tag.confidence) * 100).toFixed(0)}%</span>`).join("")}</div>` : impactGraph ? `<p>${esc((item.payload.path || []).join(" → "))}</p><div class="provider-meta"><span>置信度 ${(Number(item.payload.confidence || 0) * 100).toFixed(0)}%</span><span>${esc(item.payload.evidence)}</span><a href="/admin/impact-graph">影响关系管理</a></div>` : `<p>${esc(item.resource_type)} · ${esc(item.action)} · ${fmt(item.created_at)}</p><div class="provider-meta"><span>${esc(JSON.stringify(item.payload).slice(0, 180))}</span></div>`;
    const approveAction = newsTags ? `data-edit-news-tags="${esc(item.id)}"` : `data-approve-proposal="${esc(item.id)}"`;
    return `<article class="proposal-item"><div><div><strong>${esc(item.title)}</strong> <span class="status-pill ${item.status}">${esc(item.status)}</span></div>${detail}</div>${item.status === "pending" ? `<div class="proposal-actions admin-write"><button class="text-button" type="button" data-reject-proposal="${esc(item.id)}">拒绝</button><button class="primary-button" type="button" ${approveAction}>${newsTags ? "审核标的" : "批准执行"}</button></div>` : ""}</article>`;
  }).join("") : `<div class="agent-empty">当前没有审批建议。</div>`;
  const tag = state.tagging;
  $("#taggingStatus").innerHTML = tag ? `<span>已自动接受 ${tag.accepted_tags}</span><span>待审核 ${tag.pending_tags}</span><span>等待模型 ${tag.waiting_provider}</span><span>失败 ${tag.failed}</span><span>自动接受率 ${tag.auto_accept_rate == null ? "--" : `${tag.auto_accept_rate}%`}</span>` : "";
  $("#backfillNewsTags").disabled = Boolean(tag?.running);
}

function openTagReview(proposal) {
  state.reviewingProposal = proposal;
  const tags = proposal.payload.tags || [];
  $("#tagReviewArticle").textContent = `${proposal.payload.source || "未知来源"} · ${proposal.payload.title || proposal.title}`;
  $("#tagReviewPrimary").value = tags.filter((item) => item.relation === "primary").map((item) => item.symbol).join(",");
  $("#tagReviewSecondary").value = tags.filter((item) => item.relation === "secondary").map((item) => item.symbol).join(",");
  $("#tagReviewNote").value = "";
  $("#tagReviewModal").hidden = false;
}

function switchSection(section) {
  state.section = section; const titles = { registry: "Agent 配置", runs: "运行记录", proposals: "审批建议", providers: "模型服务" };
  $("#sectionTitle").textContent = titles[section]; $$(".agent-nav button").forEach((button) => button.classList.toggle("active", button.dataset.section === section));
  $$(".agent-section").forEach((panel) => panel.classList.toggle("active", panel.id === `${section}Section`));
  const params = new URLSearchParams(window.location.search); params.set("section", section); history.replaceState({}, "", `/admin/agents?${params}`);
}

async function reload() {
  const modelRequests = state.me?.role === "admin" ? [api("/api/model-routes"), api("/api/model-channels")] : [Promise.resolve([]), Promise.resolve([])];
  [state.agents, state.providers, state.channels, state.runs, state.proposals, state.tagging] = await Promise.all([api("/api/agents"), ...modelRequests, api("/api/agents/runs?limit=100"), api("/api/agent-proposals?limit=100"), api("/api/news-tagging/status")]);
  renderMetrics(); renderProviders(); renderRuns(); renderProposals();
  if (state.activeAgent) state.activeAgent = state.agents.find((item) => item.id === state.activeAgent.id) || null;
  renderAgentList(); if (state.activeAgent) fillAgentEditor(state.activeAgent);
}

function agentPayload() {
  let settings; try { settings = JSON.parse($("#agentSettings").value || "{}"); } catch { throw new Error("模板专属参数必须是有效 JSON"); }
  return { name: $("#agentName").value.trim(), description: $("#agentDescription").value.trim(), runtime_mode: $("#agentMode").value,
    model_route_id: $("#agentProvider").value || null, enabled: $("#agentEnabled").checked, fallback_to_rules: $("#agentFallback").checked,
    settings, system_prompt: $("#agentSystemPrompt").value, task_prompt: $("#agentTaskPrompt").value, symbol_scope: $("#agentScope").value,
    symbols: $("#agentSymbols").value.split(",").map((item) => item.trim()).filter(Boolean), interval_seconds: Number($("#agentInterval").value),
    schedule_enabled: $("#agentSchedule").checked, triggers: $$("#triggerOptions input:checked").map((item) => item.value),
    notification_channel_ids: $("#agentChannels").value.split(",").map((item) => item.trim()).filter(Boolean) };
}

async function bootstrap() {
  const session = await api("/api/auth/me"); state.me = session.user; state.csrf = session.csrf_token;
  window.AppTheme?.apply(session.preferences?.theme || "system"); window.AppTheme?.updateControls(); window.lucide?.createIcons();
  $("#agentUserLabel").textContent = `${session.user.display_name} · ${session.user.role === "admin" ? "管理员" : "只读"}`; document.body.classList.toggle("viewer-mode", session.user.role !== "admin");
  $("#newAgentTemplate").innerHTML = Object.entries(templates).map(([key, label]) => `<option value="${key}">${label}</option>`).join(""); state.symbols = await api("/api/symbols"); $("#instrumentOptions").innerHTML = state.symbols.map((item) => `<option value="${esc(item.symbol)}">${esc(item.name)}</option>`).join(""); await reload();
  if (state.agents.length) fillAgentEditor(state.agents[0]);
  const requestedSection = new URLSearchParams(window.location.search).get("section"); if (["registry", "runs", "proposals", "providers"].includes(requestedSection)) switchSection(requestedSection);
}

document.addEventListener("click", async (event) => {
  const nav = event.target.closest("[data-section]"); if (nav) switchSection(nav.dataset.section);
  const agent = event.target.closest("[data-agent-id]"); if (agent) fillAgentEditor(state.agents.find((item) => item.id === agent.dataset.agentId));
  const tab = event.target.closest("[data-editor-tab]"); if (tab) { $$("[data-editor-tab]").forEach((item) => item.classList.toggle("active", item === tab)); $$("[data-editor-panel]").forEach((item) => item.classList.toggle("active", item.dataset.editorPanel === tab.dataset.editorTab)); }
  const run = event.target.closest("[data-run-id]"); if (run) await openRunDetail(run.dataset.runId);
  const detailTab = event.target.closest("[data-run-detail-tab]"); if (detailTab) { $$('[data-run-detail-tab]').forEach((item) => item.classList.toggle("active", item === detailTab)); $$('[data-run-detail-panel]').forEach((item) => item.classList.toggle("active", item.dataset.runDetailPanel === detailTab.dataset.runDetailTab)); }
  const copy = event.target.closest("[data-copy-run-target]"); if (copy) { const target = document.getElementById(copy.dataset.copyRunTarget); if (target) { await navigator.clipboard.writeText(target.textContent); copy.title = "已复制"; window.setTimeout(() => { copy.title = "复制"; }, 1600); } }
  const editNewsTags = event.target.closest("[data-edit-news-tags]"); if (editNewsTags) openTagReview(state.proposals.find((item) => item.id === editNewsTags.dataset.editNewsTags));
  const approve = event.target.closest("[data-approve-proposal]"); if (approve && confirm("批准后将立即创建对应资源，确认继续？")) api(`/api/agent-proposals/${approve.dataset.approveProposal}/approve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(reload).then(() => notice("建议已批准并执行。" )).catch((error) => notice(error.message, "error"));
  const reject = event.target.closest("[data-reject-proposal]"); if (reject) api(`/api/agent-proposals/${reject.dataset.rejectProposal}/reject`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(reload).then(() => notice("建议已拒绝。" )).catch((error) => notice(error.message, "error"));
});

$("#tagReviewForm").addEventListener("submit", async (event) => { event.preventDefault(); if (!state.reviewingProposal) return; const parse = (value) => [...new Set(value.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean))]; try { await api(`/api/agent-proposals/${state.reviewingProposal.id}/approve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note: $("#tagReviewNote").value, payload_override: { primary_symbols: parse($("#tagReviewPrimary").value), secondary_symbols: parse($("#tagReviewSecondary").value) } }) }); $("#tagReviewModal").hidden = true; state.reviewingProposal = null; await reload(); notice("新闻标的已审核并应用。"); } catch (error) { notice(error.message, "error"); } });
$("#backfillNewsTags").addEventListener("click", async () => { try { const result = await api("/api/news-tagging/backfill", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ days: 30, batch_size: 8, scope: "unlinked", engine: "rule" }) }); await reload(); notice(`已加入 ${result.queued} 条空关联新闻规则回标任务。`); } catch (error) { notice(error.message, "error"); } });
function closeTagReview() { $("#tagReviewModal").hidden = true; state.reviewingProposal = null; }
$("#closeTagReviewModal").addEventListener("click", closeTagReview); $("#cancelTagReviewModal").addEventListener("click", closeTagReview);

$("#agentForm").addEventListener("submit", async (event) => { event.preventDefault(); if (!state.activeAgent) return; $("#agentSaveStatus").textContent = "正在保存..."; try { const updated = await api(`/api/agents/${state.activeAgent.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(agentPayload()) }); await reload(); fillAgentEditor(updated); $("#agentSaveStatus").textContent = "配置已保存"; } catch (error) { $("#agentSaveStatus").textContent = error.message; } });
$("#runAgent").addEventListener("click", async () => { if (!state.activeAgent) return; try { await api(`/api/agents/${state.activeAgent.id}/run`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); await reload(); switchSection("runs"); notice("Agent 已进入运行队列。"); } catch (error) { notice(error.message, "error"); } });
$("#testAgent").addEventListener("click", async () => { if (!state.activeAgent) return; notice("正在执行隔离测试...", "watch"); try { const run = await api(`/api/agents/${state.activeAgent.id}/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); await reload(); switchSection("runs"); notice(run.status === "succeeded" ? "测试运行完成。" : `测试失败：${run.error}`, run.status === "succeeded" ? "ok" : "error"); } catch (error) { notice(error.message, "error"); } });
$("#cloneAgent").addEventListener("click", async () => { if (!state.activeAgent) return; try { const clone = await api(`/api/agents/${state.activeAgent.id}/clone`, { method: "POST" }); await reload(); fillAgentEditor(clone); notice("已创建停用的 Agent 副本。"); } catch (error) { notice(error.message, "error"); } });
$("#agentCreateForm").addEventListener("submit", async (event) => { event.preventDefault(); try { const created = await api("/api/agents", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ template_type: $("#newAgentTemplate").value, name: $("#newAgentName").value, description: $("#newAgentDescription").value }) }); $("#agentModal").hidden = true; await reload(); fillAgentEditor(created); notice("Agent 已创建。"); } catch (error) { notice(error.message, "error"); } });
$("#providerForm").addEventListener("submit", async (event) => { event.preventDefault(); try { const payload = { name: $("#providerName").value, base_url: $("#providerUrl").value, model: $("#providerModel").value, api_key_env: $("#providerEnv").value }; if ($("#providerApiKey").value) payload.api_key = $("#providerApiKey").value; await api(state.editingProvider ? `/api/agent-providers/${state.editingProvider.id}` : "/api/agent-providers", { method: state.editingProvider ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); $("#providerModal").hidden = true; state.editingProvider = null; event.target.reset(); await reload(); notice("模型服务已保存。"); } catch (error) { notice(error.message, "error"); } });
$("#newAgent").addEventListener("click", () => { $("#agentModal").hidden = false; }); $("#closeAgentModal").addEventListener("click", () => { $("#agentModal").hidden = true; }); $("#cancelAgentModal").addEventListener("click", () => { $("#agentModal").hidden = true; });
$("#newProvider").addEventListener("click", () => { window.location.href = "/admin/models?tab=channels"; }); $("#closeProviderModal").addEventListener("click", () => { $("#providerModal").hidden = true; state.editingProvider = null; }); $("#cancelProviderModal").addEventListener("click", () => { $("#providerModal").hidden = true; state.editingProvider = null; });
$("#closeRunDrawer").addEventListener("click", () => { $("#runDrawer").hidden = true; }); $("#agentSearch").addEventListener("input", renderAgentList); $("#agentStatus").addEventListener("change", renderAgentList); $("#proposalStatus").addEventListener("change", renderProposals); $("#refreshRuns").addEventListener("click", reload);
$("#agentLogout").addEventListener("click", async () => { await api("/api/auth/logout", { method: "POST" }); window.location.replace("/login"); });
$("#agentThemeToggle").addEventListener("click", () => window.AppTheme.toggle(state.csrf).catch((error) => notice(error.message, "error")));
bootstrap().catch((error) => notice(error.message, "error")); setInterval(() => { if (["runs", "proposals"].includes(state.section)) reload().catch(() => {}); }, 5000);
