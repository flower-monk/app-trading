const state = { me: null, csrf: "", agents: [], providers: [], runs: [], proposals: [], activeAgent: null, section: "registry" };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const templates = {
  market_monitor: "行情盯盘", news_analyst: "新闻分析", risk_sentinel: "风险哨兵",
  factor_research: "因子研究", briefing: "全局简报",
};
const triggerLabels = { manual: "手动运行", schedule: "周期调度", high_impact_news: "高影响新闻", alert_triggered: "预警触发", backtest_completed: "回测完成" };

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
function fmt(time) { return time ? new Date(time).toLocaleString("zh-CN", { hour12: false }) : "--"; }

function renderMetrics() {
  const active = state.agents.filter((item) => item.enabled).length;
  const queued = state.runs.filter((item) => ["queued", "running"].includes(item.status)).length;
  const failed = state.runs.filter((item) => item.status === "failed").length;
  const pending = state.proposals.filter((item) => item.status === "pending").length;
  const connected = state.providers.filter((item) => item.enabled && item.secret_configured).length;
  $("#agentMetrics").innerHTML = `<article><span>启用 Agent</span><strong>${active}/${state.agents.length}</strong></article><article><span>队列任务</span><strong>${queued}</strong></article><article><span>近期失败</span><strong>${failed}</strong></article><article><span>待审批建议</span><strong>${pending}</strong></article><article><span>模型连接就绪</span><strong>${connected}/${state.providers.length}</strong></article>`;
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
  $("#agentProvider").value = agent.provider_id || ""; $("#agentEnabled").checked = agent.enabled; $("#agentFallback").checked = agent.fallback_to_rules;
  $("#agentTemplate").value = templates[agent.template_type]; $("#agentSettings").value = JSON.stringify(agent.settings, null, 2);
  $("#agentSystemPrompt").value = agent.system_prompt; $("#agentTaskPrompt").value = agent.task_prompt; $("#agentScope").value = agent.symbol_scope;
  $("#agentSymbols").value = agent.symbols.join(","); $("#agentInterval").value = agent.interval_seconds; $("#agentSchedule").checked = agent.schedule_enabled;
  $("#agentChannels").value = agent.notification_channel_ids.join(",");
  $("#triggerOptions").innerHTML = Object.entries(triggerLabels).map(([key, label]) => `<label><input type="checkbox" value="${key}" ${agent.triggers.includes(key) ? "checked" : ""} />${label}</label>`).join("");
  renderAgentList();
}

function renderProviders() {
  $("#agentProvider").innerHTML = `<option value="">不使用模型</option>${state.providers.map((item) => `<option value="${esc(item.id)}">${esc(item.name)} · ${esc(item.model)}</option>`).join("")}`;
  $("#providerList").innerHTML = state.providers.length ? state.providers.map((item) => `<article class="provider-item"><div><div><strong>${esc(item.name)}</strong> <span class="status-pill ${item.secret_configured ? "succeeded" : "failed"}">${item.secret_configured ? "密钥就绪" : "密钥缺失"}</span></div><p>${esc(item.base_url)}</p><div class="provider-meta"><span>模型 ${esc(item.model)}</span><span>密钥变量 ${esc(item.api_key_env)}</span><span>超时 ${item.timeout_seconds}s</span><span>${item.enabled ? "启用" : "停用"}</span></div></div><div class="proposal-actions admin-write"><button class="text-button" type="button" data-test-provider="${esc(item.id)}">测试连接</button><button class="text-button" type="button" data-toggle-provider="${esc(item.id)}" data-enabled="${!item.enabled}">${item.enabled ? "停用" : "启用"}</button></div></article>`).join("") : `<div class="agent-empty">尚未配置模型服务；规则 Agent 可正常运行。</div>`;
}

function renderRuns() {
  $("#runTable").innerHTML = state.runs.length ? `<table><thead><tr><th>Agent</th><th>触发</th><th>状态</th><th>标的</th><th>模式 / 模型</th><th>耗时</th><th>完成时间</th><th></th></tr></thead><tbody>${state.runs.map((item) => `<tr><td>${esc(item.agent_name)}${item.test ? " · 测试" : ""}</td><td>${esc(triggerLabels[item.trigger] || item.trigger)}</td><td><span class="status-pill ${item.status}">${esc(item.status)}</span></td><td>${esc(item.symbol || "全局")}</td><td>${esc(item.runtime_mode)}${item.model ? ` / ${esc(item.model)}` : ""}</td><td>${item.latency_ms}ms</td><td>${fmt(item.finished_at || item.created_at)}</td><td><button class="icon-button" type="button" data-run-id="${esc(item.id)}" title="查看详情">⌕</button></td></tr>`).join("")}</tbody></table>` : `<div class="agent-empty">暂无运行记录。</div>`;
}

function renderProposals() {
  const status = $("#proposalStatus").value; const items = state.proposals.filter((item) => !status || item.status === status);
  $("#proposalList").innerHTML = items.length ? items.map((item) => `<article class="proposal-item"><div><div><strong>${esc(item.title)}</strong> <span class="status-pill ${item.status}">${esc(item.status)}</span></div><p>${esc(item.resource_type)} · ${esc(item.action)} · ${fmt(item.created_at)}</p><div class="provider-meta"><span>${esc(JSON.stringify(item.payload).slice(0, 180))}</span></div></div>${item.status === "pending" ? `<div class="proposal-actions admin-write"><button class="text-button" type="button" data-reject-proposal="${esc(item.id)}">拒绝</button><button class="primary-button" type="button" data-approve-proposal="${esc(item.id)}">批准执行</button></div>` : ""}</article>`).join("") : `<div class="agent-empty">当前没有审批建议。</div>`;
}

function switchSection(section) {
  state.section = section; const titles = { registry: "Agent 配置", runs: "运行记录", proposals: "审批建议", providers: "模型服务" };
  $("#sectionTitle").textContent = titles[section]; $$(".agent-nav button").forEach((button) => button.classList.toggle("active", button.dataset.section === section));
  $$(".agent-section").forEach((panel) => panel.classList.toggle("active", panel.id === `${section}Section`));
}

async function reload() {
  [state.agents, state.providers, state.runs, state.proposals] = await Promise.all([api("/api/agents"), api("/api/agent-providers"), api("/api/agents/runs?limit=100"), api("/api/agent-proposals?limit=100")]);
  renderMetrics(); renderProviders(); renderRuns(); renderProposals();
  if (state.activeAgent) state.activeAgent = state.agents.find((item) => item.id === state.activeAgent.id) || null;
  renderAgentList(); if (state.activeAgent) fillAgentEditor(state.activeAgent);
}

function agentPayload() {
  let settings; try { settings = JSON.parse($("#agentSettings").value || "{}"); } catch { throw new Error("模板专属参数必须是有效 JSON"); }
  return { name: $("#agentName").value.trim(), description: $("#agentDescription").value.trim(), runtime_mode: $("#agentMode").value,
    provider_id: $("#agentProvider").value || null, enabled: $("#agentEnabled").checked, fallback_to_rules: $("#agentFallback").checked,
    settings, system_prompt: $("#agentSystemPrompt").value, task_prompt: $("#agentTaskPrompt").value, symbol_scope: $("#agentScope").value,
    symbols: $("#agentSymbols").value.split(",").map((item) => item.trim()).filter(Boolean), interval_seconds: Number($("#agentInterval").value),
    schedule_enabled: $("#agentSchedule").checked, triggers: $$("#triggerOptions input:checked").map((item) => item.value),
    notification_channel_ids: $("#agentChannels").value.split(",").map((item) => item.trim()).filter(Boolean) };
}

async function bootstrap() {
  const session = await api("/api/auth/me"); state.me = session.user; state.csrf = session.csrf_token;
  $("#agentUserLabel").textContent = `${session.user.display_name} · ${session.user.role === "admin" ? "管理员" : "只读"}`; document.body.classList.toggle("viewer-mode", session.user.role !== "admin");
  $("#newAgentTemplate").innerHTML = Object.entries(templates).map(([key, label]) => `<option value="${key}">${label}</option>`).join(""); await reload();
  if (state.agents.length) fillAgentEditor(state.agents[0]);
}

document.addEventListener("click", (event) => {
  const nav = event.target.closest("[data-section]"); if (nav) switchSection(nav.dataset.section);
  const agent = event.target.closest("[data-agent-id]"); if (agent) fillAgentEditor(state.agents.find((item) => item.id === agent.dataset.agentId));
  const tab = event.target.closest("[data-editor-tab]"); if (tab) { $$("[data-editor-tab]").forEach((item) => item.classList.toggle("active", item === tab)); $$("[data-editor-panel]").forEach((item) => item.classList.toggle("active", item.dataset.editorPanel === tab.dataset.editorTab)); }
  const run = event.target.closest("[data-run-id]"); if (run) { const item = state.runs.find((row) => row.id === run.dataset.runId); $("#runDetail").innerHTML = `<dl class="resource-detail"><div><dt>Agent</dt><dd>${esc(item.agent_name)}</dd></div><div><dt>状态</dt><dd>${esc(item.status)}</dd></div><div><dt>错误</dt><dd>${esc(item.error || "--")}</dd></div><div><dt>Token</dt><dd>${item.prompt_tokens} / ${item.completion_tokens}</dd></div></dl><h3>结构化输出</h3><pre class="run-json">${esc(JSON.stringify(item.output, null, 2))}</pre>`; $("#runDrawer").hidden = false; }
  const testProvider = event.target.closest("[data-test-provider]"); if (testProvider) api(`/api/agent-providers/${testProvider.dataset.testProvider}/test`, { method: "POST" }).then((result) => notice(`连接成功，耗时 ${result.latency_ms}ms。`)).catch((error) => notice(error.message, "error"));
  const toggleProvider = event.target.closest("[data-toggle-provider]"); if (toggleProvider) api(`/api/agent-providers/${toggleProvider.dataset.toggleProvider}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: toggleProvider.dataset.enabled === "true" }) }).then(reload).catch((error) => notice(error.message, "error"));
  const approve = event.target.closest("[data-approve-proposal]"); if (approve && confirm("批准后将立即创建对应资源，确认继续？")) api(`/api/agent-proposals/${approve.dataset.approveProposal}/approve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(reload).then(() => notice("建议已批准并执行。" )).catch((error) => notice(error.message, "error"));
  const reject = event.target.closest("[data-reject-proposal]"); if (reject) api(`/api/agent-proposals/${reject.dataset.rejectProposal}/reject`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(reload).then(() => notice("建议已拒绝。" )).catch((error) => notice(error.message, "error"));
});

$("#agentForm").addEventListener("submit", async (event) => { event.preventDefault(); if (!state.activeAgent) return; $("#agentSaveStatus").textContent = "正在保存..."; try { const updated = await api(`/api/agents/${state.activeAgent.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(agentPayload()) }); await reload(); fillAgentEditor(updated); $("#agentSaveStatus").textContent = "配置已保存"; } catch (error) { $("#agentSaveStatus").textContent = error.message; } });
$("#runAgent").addEventListener("click", async () => { if (!state.activeAgent) return; try { await api(`/api/agents/${state.activeAgent.id}/run`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); await reload(); switchSection("runs"); notice("Agent 已进入运行队列。"); } catch (error) { notice(error.message, "error"); } });
$("#testAgent").addEventListener("click", async () => { if (!state.activeAgent) return; notice("正在执行隔离测试...", "watch"); try { const run = await api(`/api/agents/${state.activeAgent.id}/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); await reload(); switchSection("runs"); notice(run.status === "succeeded" ? "测试运行完成。" : `测试失败：${run.error}`, run.status === "succeeded" ? "ok" : "error"); } catch (error) { notice(error.message, "error"); } });
$("#cloneAgent").addEventListener("click", async () => { if (!state.activeAgent) return; try { const clone = await api(`/api/agents/${state.activeAgent.id}/clone`, { method: "POST" }); await reload(); fillAgentEditor(clone); notice("已创建停用的 Agent 副本。"); } catch (error) { notice(error.message, "error"); } });
$("#agentCreateForm").addEventListener("submit", async (event) => { event.preventDefault(); try { const created = await api("/api/agents", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ template_type: $("#newAgentTemplate").value, name: $("#newAgentName").value, description: $("#newAgentDescription").value }) }); $("#agentModal").hidden = true; await reload(); fillAgentEditor(created); notice("Agent 已创建。"); } catch (error) { notice(error.message, "error"); } });
$("#providerForm").addEventListener("submit", async (event) => { event.preventDefault(); try { await api("/api/agent-providers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("#providerName").value, base_url: $("#providerUrl").value, model: $("#providerModel").value, api_key_env: $("#providerEnv").value }) }); $("#providerModal").hidden = true; event.target.reset(); await reload(); notice("模型服务已添加。"); } catch (error) { notice(error.message, "error"); } });
$("#newAgent").addEventListener("click", () => { $("#agentModal").hidden = false; }); $("#closeAgentModal").addEventListener("click", () => { $("#agentModal").hidden = true; }); $("#cancelAgentModal").addEventListener("click", () => { $("#agentModal").hidden = true; });
$("#newProvider").addEventListener("click", () => { $("#providerModal").hidden = false; }); $("#closeProviderModal").addEventListener("click", () => { $("#providerModal").hidden = true; }); $("#cancelProviderModal").addEventListener("click", () => { $("#providerModal").hidden = true; });
$("#closeRunDrawer").addEventListener("click", () => { $("#runDrawer").hidden = true; }); $("#agentSearch").addEventListener("input", renderAgentList); $("#agentStatus").addEventListener("change", renderAgentList); $("#proposalStatus").addEventListener("change", renderProposals); $("#refreshRuns").addEventListener("click", reload);
$("#agentLogout").addEventListener("click", async () => { await api("/api/auth/logout", { method: "POST" }); window.location.replace("/login"); });
bootstrap().catch((error) => notice(error.message, "error")); setInterval(() => { if (["runs", "proposals"].includes(state.section)) reload().catch(() => {}); }, 5000);
