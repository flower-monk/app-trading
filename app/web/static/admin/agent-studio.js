(function () {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const state = { workflows: [], tools: [], runs: [], approvals: [], active: null, selectedNode: null, cy: null, connectSource: null, traceCy: null };
  const colors = { trigger: "#4f8fdb", resource: "#31a37c", agent: "#936fd1", condition: "#d49b3c", transform: "#7b8794", approval: "#d0715d", output: "#36a6b0" };

  function api(path, options) { return window.AdminCenter.api(path, options); }
  function notice(message, type = "") { const node = $("#studioNotice"); node.textContent = message; node.className = `resource-notice ${type}`; window.setTimeout(() => { node.textContent = ""; node.className = "resource-notice"; }, 5000); }
  function json(value) { return JSON.stringify(value ?? {}, null, 2); }

  function renderWorkflows() {
    const query = $("#workflowSearch").value.trim().toLowerCase();
    const items = state.workflows.filter((item) => `${item.name} ${item.objective}`.toLowerCase().includes(query));
    $("#workflowList").innerHTML = items.length ? items.map((item) => `<button class="${item.status} ${state.active?.id === item.id ? "active" : ""}" data-workflow-id="${esc(item.id)}"><i></i><span><strong>${esc(item.name)}</strong><small>${esc(item.objective || "未填写目标")}</small></span><small>${item.published_version ? `v${item.published_version}` : "草稿"}</small></button>`).join("") : '<div class="inspector-empty">暂无工作流</div>';
  }

  function graphElements(graph) {
    return [
      ...graph.nodes.map((node) => ({ data: { id: node.id, label: node.name, kind: node.kind, type: node.type }, position: { x: node.x, y: node.y } })),
      ...graph.edges.map((edge) => ({ data: { id: edge.id, source: edge.source, target: edge.target, label: edge.source_port === "success" ? "" : edge.source_port } })),
    ];
  }

  function mountGraph() {
    if (!state.active) return;
    state.cy?.destroy();
    $("#workflowCanvas").innerHTML = "";
    state.cy = cytoscape({
      container: $("#workflowCanvas"), elements: graphElements(state.active.graph),
      style: [
        { selector: "node", style: { "shape": "round-rectangle", "width": 150, "height": 48, "background-color": "#171e27", "border-width": 2, "border-color": "data(kind)", "label": "data(label)", "color": "#e7edf5", "font-size": 12, "text-valign": "center", "text-halign": "center" } },
        ...Object.entries(colors).map(([kind, color]) => ({ selector: `node[kind = "${kind}"]`, style: { "border-color": color } })),
        { selector: "node:selected", style: { "border-width": 3, "background-color": "#243141" } },
        { selector: "edge", style: { "width": 2, "line-color": "#526174", "target-arrow-color": "#526174", "target-arrow-shape": "triangle", "curve-style": "bezier", "label": "data(label)", "color": "#aeb9c7", "font-size": 10, "text-background-color": "#151b22", "text-background-opacity": 1 } },
      ], layout: { name: "preset", fit: true, padding: 50 }, minZoom: 0.35, maxZoom: 1.8,
    });
    state.cy.on("tap", "node", (event) => {
      const id = event.target.id();
      if (state.connectSource && state.connectSource !== id) { addEdge(state.connectSource, id); return; }
      if ($("#connectNodes").classList.contains("active")) { state.connectSource = id; notice(`已选择起点 ${event.target.data("label")}，请选择目标节点。`); return; }
      state.selectedNode = state.active.graph.nodes.find((item) => item.id === id); renderInspector();
    });
    state.cy.on("dragfree", "node", () => syncPositions());
  }

  function syncPositions() { if (!state.cy || !state.active) return; state.active.graph.nodes.forEach((node) => { const position = state.cy.getElementById(node.id).position(); node.x = Math.round(position.x); node.y = Math.round(position.y); }); }

  function selectWorkflow(id) {
    state.active = state.workflows.find((item) => item.id === id); state.selectedNode = null;
    $("#workflowTitle").textContent = state.active.name;
    $("#workflowMeta").textContent = `${state.active.status === "published" ? "已发布" : "草稿"} · 草稿修订 ${state.active.draft_version}${state.active.published_version ? ` · 线上 v${state.active.published_version}` : ""}`;
    $("#workflowSummary").textContent = state.active.objective || "尚未填写工作流目标";
    $("#workflowSummary").className = "workflow-summary";
    renderWorkflows(); mountGraph(); renderInspector();
  }

  function permissionFor(node) { if (node.kind === "output") return ["L1", "信息产物通过校验后可自动发布"]; if (node.kind === "approval") return ["L2", "执行将在此暂停并等待管理员审批"]; return ["L0", "只读或计算节点，可自动执行"]; }
  function renderInspector() {
    const node = state.selectedNode;
    if (!node) { $("#nodeInspector").innerHTML = '<div class="inspector-empty">选择画布中的节点</div>'; return; }
    const [level, text] = permissionFor(node);
    const agentOptions = (window.AgentStudioAgents || []).map((item) => `<option value="${esc(item.id)}" ${node.config.agent_id === item.id ? "selected" : ""}>${esc(item.name)}</option>`).join("");
    $("#nodeInspector").innerHTML = `<form id="nodeForm" class="inspector-form"><div class="permission-banner ${level.toLowerCase()}"><strong>${level} 执行权限</strong><br>${text}</div><label><span>节点名称</span><input id="nodeName" value="${esc(node.name)}" /></label><label><span>节点类型</span><input value="${esc(node.kind)} / ${esc(node.type)}" disabled /></label>${node.kind === "trigger" ? `<label><span>触发事件</span><select id="nodeTrigger">${["manual", "schedule", "news_ingested", "high_impact_news", "alert_triggered", "backtest_completed", "calendar_event_upcoming", "calendar_event_released"].map((item) => `<option ${node.type === item ? "selected" : ""}>${item}</option>`).join("")}</select></label>` : ""}${node.kind === "agent" ? `<label><span>能力 Agent</span><select id="nodeAgent">${agentOptions}</select></label>` : ""}${node.kind === "condition" ? `<label><span>字段路径</span><input id="conditionField" value="${esc(node.config.field || "$results.agent.confidence")}" /></label><label><span>运算符</span><select id="conditionOperator">${["eq", "ne", "gt", "gte", "lt", "contains"].map((op) => `<option ${node.config.operator === op ? "selected" : ""}>${op}</option>`).join("")}</select></label><label><span>比较值 JSON</span><input id="conditionValue" value='${esc(json(node.config.value ?? 0))}' /></label>` : ""}<label><span>节点配置 JSON</span><textarea id="nodeConfig" rows="12">${esc(json(node.config))}</textarea></label><button class="primary-button" type="submit">应用节点配置</button><button id="deleteNode" class="text-button" type="button">删除节点</button></form>`;
    $("#nodeForm").addEventListener("submit", (event) => { event.preventDefault(); try { node.name = $("#nodeName").value.trim(); node.config = JSON.parse($("#nodeConfig").value || "{}"); if ($("#nodeTrigger")) node.type = $("#nodeTrigger").value; if ($("#nodeAgent")) node.config.agent_id = $("#nodeAgent").value; if ($("#conditionField")) { node.config.field = $("#conditionField").value; node.config.operator = $("#conditionOperator").value; node.config.value = JSON.parse($("#conditionValue").value); } mountGraph(); state.selectedNode = node; renderInspector(); notice("节点配置已应用，保存工作流后生效。", "ok"); } catch (error) { notice(`配置无效：${error.message}`, "error"); } });
    $("#deleteNode").addEventListener("click", () => { if (node.kind === "trigger") return notice("触发节点不能删除。", "error"); state.active.graph.nodes = state.active.graph.nodes.filter((item) => item.id !== node.id); state.active.graph.edges = state.active.graph.edges.filter((edge) => edge.source !== node.id && edge.target !== node.id); state.selectedNode = null; mountGraph(); renderInspector(); });
  }

  function addNode(kind, type) {
    if (!state.active) return notice("请先选择工作流。", "error");
    const names = { resource: "资源查询", agent: "能力 Agent", condition: "条件判断", approval: "人工审批", output: type === "risk_signal" ? "发布风险提示" : "发布K线标记" };
    const id = `${kind}-${Date.now().toString(36)}`;
    const config = kind === "agent" ? { agent_id: window.AgentStudioAgents?.[0]?.id || "" } : kind === "condition" ? { field: "$results.agent.confidence", operator: "gte", value: 0.7 } : kind === "output" ? { severity: type === "risk_signal" ? "risk" : "info", valid_minutes: 120 } : { arguments: { symbol: "$input.symbol" } };
    const node = { id, kind, type, name: names[kind], x: 300 + state.active.graph.nodes.length * 25, y: 180 + state.active.graph.nodes.length * 18, config };
    state.active.graph.nodes.push(node); state.selectedNode = node; mountGraph(); state.cy.getElementById(id).select(); renderInspector();
  }

  function addEdge(source, target) {
    if (state.active.graph.edges.some((edge) => edge.source === source && edge.target === target)) return notice("该连接已存在。", "error");
    const sourceNode = state.active.graph.nodes.find((node) => node.id === source);
    let port = "success";
    if (sourceNode.kind === "condition") port = state.active.graph.edges.some((edge) => edge.source === source && edge.source_port === "true") ? "false" : "true";
    state.active.graph.edges.push({ id: `edge-${Date.now().toString(36)}`, source, target, source_port: port, condition: null });
    state.connectSource = null; $("#connectNodes").classList.remove("active"); mountGraph(); notice(`已连接 ${source} → ${target}`);
  }

  async function saveWorkflow() { if (!state.active) return; syncPositions(); const updated = await api(`/api/agent-studio/workflows/${state.active.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ graph: state.active.graph }) }); const index = state.workflows.findIndex((item) => item.id === updated.id); state.workflows[index] = updated; selectWorkflow(updated.id); return updated; }
  async function validateActive() { await saveWorkflow(); const result = await api(`/api/agent-studio/workflows/${state.active.id}/validate`, { method: "POST" }); const node = $("#workflowSummary"); node.className = `workflow-summary ${result.valid ? "valid" : "invalid"}`; node.textContent = result.valid ? `校验通过 · ${result.summary}${result.warnings.length ? ` · ${result.warnings.join("；")}` : ""}` : `校验失败 · ${result.errors.join("；")}`; return result; }

  function renderRuns() { $("#workflowRuns").innerHTML = state.runs.length ? `<table><thead><tr><th>工作流</th><th>版本</th><th>触发</th><th>状态</th><th>标的</th><th>开始时间</th><th></th></tr></thead><tbody>${state.runs.map((run) => `<tr><td>${esc(run.workflow_name)}${run.test ? " · 测试" : ""}</td><td>v${run.workflow_version}</td><td>${esc(run.trigger)}</td><td><span class="status-pill ${esc(run.status)}">${esc(run.status)}</span></td><td>${esc(run.symbol || "全局")}</td><td>${esc(run.started_at || run.created_at)}</td><td><button class="icon-button" data-trace-run="${esc(run.id)}" title="查看调用链"><i data-lucide="git-fork"></i></button></td></tr>`).join("")}</tbody></table>` : '<div class="inspector-empty">暂无工作流运行</div>'; window.lucide?.createIcons(); }
  function renderApprovals() { $("#workflowApprovals").innerHTML = state.approvals.length ? state.approvals.map((item) => `<article class="approval-card"><span class="status-pill ${item.status}">${esc(item.status)}</span><h3>${esc(item.title)}</h3><p>运行 ${esc(item.run_id)}</p><div class="workflow-actions">${item.status === "pending" ? `<button class="text-button" data-reject-approval="${item.id}">拒绝</button><button class="primary-button" data-approve-approval="${item.id}">批准并继续</button>` : ""}</div></article>`).join("") : '<div class="inspector-empty">暂无审批中断</div>'; }
  function renderTools() { $("#toolCatalog").innerHTML = state.tools.map((tool) => `<article class="tool-card"><div class="tool-meta"><span>${esc(tool.permission_level)}</span><span>${tool.side_effect ? "有副作用" : "只读"}</span><span>v${esc(tool.version)}</span></div><h3>${esc(tool.title)}</h3><p>${esc(tool.description)}</p><code>${esc(tool.name)}</code></article>`).join(""); }

  async function openTrace(runId) {
    const trace = await api(`/api/agent-studio/runs/${runId}/trace`); $("#traceDrawer").hidden = false; $("#traceMeta").textContent = `${trace.run.workflow_name} v${trace.run.workflow_version} · ${trace.run.status} · ${trace.run.trace_id}`;
    const statuses = Object.fromEntries(trace.spans.map((span) => [span.node_id, span.status]));
    state.traceCy?.destroy(); state.traceCy = cytoscape({ container: $("#traceCanvas"), elements: graphElements(trace.graph), style: [{ selector: "node", style: { shape: "round-rectangle", width: 145, height: 42, label: "data(label)", color: "#e7edf5", "font-size": 11, "background-color": "#18202a", "border-width": 2, "border-color": "#596779" } }, { selector: "edge", style: { width: 2, "line-color": "#526174", "target-arrow-color": "#526174", "target-arrow-shape": "triangle", "curve-style": "bezier" } }], layout: { name: "dagre", rankDir: "LR", padding: 25 } });
    Object.entries(statuses).forEach(([id, status]) => state.traceCy.getElementById(id).style("border-color", status === "succeeded" ? "#31a37c" : status === "failed" ? "#d0715d" : "#d49b3c"));
    $("#traceTimeline").innerHTML = trace.spans.map((span) => `<article class="trace-row"><span class="status-pill ${esc(span.status)}">${esc(span.kind)}</span><div><strong>${esc(span.node_name)}</strong><p>${esc(span.decision_summary || span.error || span.operation)}</p></div><time>${span.latency_ms} ms</time></article>`).join("");
  }

  async function reload() { [state.workflows, state.tools, state.runs, state.approvals, window.AgentStudioAgents] = await Promise.all([api("/api/agent-studio/workflows"), api("/api/agent-studio/tools"), api("/api/agent-studio/runs"), api("/api/agent-studio/approvals"), api("/api/agents")]); renderWorkflows(); renderRuns(); renderApprovals(); renderTools(); if (state.active) selectWorkflow(state.active.id); window.lucide?.createIcons(); }

  $$("[data-view]").forEach((button) => button.addEventListener("click", () => { $$("[data-view]").forEach((item) => item.classList.toggle("active", item === button)); $$("[data-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.panel === button.dataset.view)); }));
  $("#workflowSearch").addEventListener("input", renderWorkflows);
  $("#workflowList").addEventListener("click", (event) => { const button = event.target.closest("[data-workflow-id]"); if (button) selectWorkflow(button.dataset.workflowId); });
  $("#nodePalette").addEventListener("click", (event) => { const button = event.target.closest("[data-add-kind]"); if (button) addNode(button.dataset.addKind, button.dataset.addType); });
  $("#connectNodes").addEventListener("click", () => { state.connectSource = null; $("#connectNodes").classList.toggle("active"); notice($("#connectNodes").classList.contains("active") ? "依次选择起点和目标节点。" : "已取消连接模式。"); });
  $("#autoLayout").addEventListener("click", () => { state.cy?.layout({ name: "dagre", rankDir: "LR", padding: 40 }).run(); syncPositions(); });
  $("#validateWorkflow").addEventListener("click", () => validateActive().catch((error) => notice(error.message, "error")));
  $("#publishWorkflow").addEventListener("click", async () => { try { const result = await validateActive(); if (!result.valid) return; const published = await api(`/api/agent-studio/workflows/${state.active.id}/publish`, { method: "POST" }); await reload(); selectWorkflow(published.id); notice(`已发布 v${published.published_version}`, "ok"); } catch (error) { notice(error.message, "error"); } });
  $("#testWorkflow").addEventListener("click", async () => { if (!state.active) return; const symbol = window.prompt("测试标的", "BTCUSD"); if (symbol === null) return; try { await saveWorkflow(); const run = await api(`/api/agent-studio/workflows/${state.active.id}/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol, test: true }) }); await reload(); notice(`测试完成：${run.status}`, run.status === "succeeded" ? "ok" : "error"); if (run.status !== "queued") openTrace(run.id); } catch (error) { notice(error.message, "error"); } });
  $("#newWorkflow").addEventListener("click", () => { $("#workflowModal").hidden = false; }); $$('[data-close-workflow]').forEach((button) => button.addEventListener("click", () => { $("#workflowModal").hidden = true; }));
  $("#workflowForm").addEventListener("submit", async (event) => { event.preventDefault(); try { const created = await api("/api/agent-studio/workflows", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("#newWorkflowName").value, objective: $("#newWorkflowObjective").value, template: $("#newWorkflowTemplate").value }) }); $("#workflowModal").hidden = true; event.target.reset(); await reload(); selectWorkflow(created.id); } catch (error) { notice(error.message, "error"); } });
  $("#workflowRuns").addEventListener("click", (event) => { const button = event.target.closest("[data-trace-run]"); if (button) openTrace(button.dataset.traceRun).catch((error) => notice(error.message, "error")); });
  $("#workflowApprovals").addEventListener("click", async (event) => { const approve = event.target.closest("[data-approve-approval]"); const reject = event.target.closest("[data-reject-approval]"); if (!approve && !reject) return; try { await api(`/api/agent-studio/approvals/${(approve || reject).dataset[approve ? "approveApproval" : "rejectApproval"]}/${approve ? "approve" : "reject"}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); await reload(); } catch (error) { notice(error.message, "error"); } });
  $("#refreshRuns").addEventListener("click", reload); $("#closeTrace").addEventListener("click", () => { $("#traceDrawer").hidden = true; });
  window.AdminCenter.ready.then(reload).then(() => { const runId = new URLSearchParams(location.search).get("run"); if (runId) { $$("[data-view]").find((item) => item.dataset.view === "runs")?.click(); openTrace(runId).catch((error) => notice(error.message, "error")); } }).catch((error) => notice(error.message, "error"));
})();
