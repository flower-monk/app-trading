(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const escapeHtml = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  const labels = { pending: "待审批", approved: "已审批", rejected: "已拒绝", disabled: "已停用", failed: "校验失败" };
  const directions = { positive: "正面", negative: "负面", neutral: "中性", mixed: "混合", uncertain: "不确定" };
  const nodeTypes = {
    event: ["事件", "#0f766e"], topic: ["主题", "#2563eb"], macro_factor: ["宏观因子", "#d97706"],
    country: ["国家", "#0891b2"], region: ["地区", "#0891b2"], sector: ["行业", "#ea580c"],
    industry: ["产业", "#ea580c"], instrument: ["标的", "#059669"], index: ["指数", "#16a34a"],
    etf: ["ETF", "#22c55e"], commodity: ["商品", "#ca8a04"], currency: ["货币", "#0d9488"],
    market: ["市场", "#475569"],
  };
  const state = {
    view: null,
    network: null,
    activeTab: location.hash.slice(1) || "graph",
    focusNodeId: "",
    graphTimer: 0,
    diagnosticTimer: 0,
    resizeTimer: 0,
    lastTap: { id: "", at: 0 },
    diagnostics: { total: 0, limit: 25, offset: 0, facets: [] },
  };

  function typeLabel(type) {
    return nodeTypes[type]?.[0] || type;
  }

  function statusFilterAllows(status) {
    const selected = $("#impactGraphStatus").value;
    return selected === "active" ? ["approved", "pending"].includes(status) : status === selected;
  }

  function graphUrl() {
    const params = new URLSearchParams();
    params.append("status", "approved");
    params.append("status", "pending");
    const query = $("#impactGraphSearch").value.trim();
    if (query) params.set("query", query);
    return `/api/news-impact-graph/view?${params}`;
  }

  function renderCounts(view) {
    $("#impactApprovedCount").textContent = view.counts.approved || 0;
    $("#impactPendingCount").textContent = view.counts.pending || 0;
    $("#impactFailedCount").textContent = view.counts.failed || 0;
    $("#impactNodeCount").textContent = view.nodes.length;
    $("#impactEdgeCount").textContent = view.edges.length;
    $("#impactGraphBadge").textContent = view.edges.length;
  }

  function defaultInspector() {
    $("#impactNetworkDetail").innerHTML = '<div class="impact-detail-empty"><i data-lucide="mouse-pointer-2"></i><strong>关系检查器</strong><p>选择节点或关系后查看证据、权重与审批记录。</p></div>';
    window.lucide?.createIcons();
  }

  function reviewActions(edge) {
    if (edge.status !== "pending") return "";
    const proposal = escapeHtml(edge.proposal_id || "");
    const edgeId = escapeHtml(edge.proposal_id ? "" : edge.id);
    return `<div class="impact-detail-actions"><button class="text-button" type="button" data-detail-review="reject" data-proposal-id="${proposal}" data-edge-id="${edgeId}">拒绝</button><button class="primary-button" type="button" data-detail-review="approve" data-proposal-id="${proposal}" data-edge-id="${edgeId}">批准并应用</button></div>`;
  }

  function showEdgeDetail(edge) {
    const source = state.view.nodes.find((item) => item.id === edge.source) || {};
    const target = state.view.nodes.find((item) => item.id === edge.target) || {};
    const path = edge.path?.length ? edge.path : [source.label, edge.relation, target.label];
    const warnings = [...(edge.validation_errors || []), ...(edge.normalization_warnings || [])];
    $("#impactNetworkDetail").innerHTML = `
      <div class="impact-detail-title"><div><strong>${escapeHtml(source.label)} → ${escapeHtml(target.label)}</strong><small>${escapeHtml(source.entity_id)} → ${escapeHtml(target.entity_id)}</small></div><span class="impact-direction-badge ${escapeHtml(edge.direction)}">${escapeHtml(directions[edge.direction] || edge.direction)}</span></div>
      <div class="impact-detail-grid">
        <div><span>传导路径</span><b class="impact-detail-path">${path.map((item, index) => `${index ? "<i>›</i>" : ""}<em>${escapeHtml(item)}</em>`).join("")}</b></div>
        <div><span>关系强度</span><b>${Math.round(Number(edge.weight) * 100)}%${edge.confidence == null ? "" : ` · 置信度 ${Math.round(Number(edge.confidence) * 100)}%`}</b></div>
        <div><span>状态</span><b><span class="impact-status-badge">${escapeHtml(labels[edge.status] || edge.status)}</span></b></div>
        <div><span>证据</span><b>${escapeHtml(edge.evidence || "暂无")}</b></div>
        <div><span>来源依据</span><b>${escapeHtml(edge.provenance || "暂无")}</b></div>
        ${warnings.length ? `<div><span>校验提示</span><b>${warnings.map(escapeHtml).join("；")}</b></div>` : ""}
        ${edge.created_at ? `<div><span>创建记录</span><b>${escapeHtml(edge.created_by || "Agent")} · ${escapeHtml(formatDate(edge.created_at))}</b></div>` : ""}
      </div>${reviewActions(edge)}`;
  }

  function showNodeDetail(node) {
    $("#impactNetworkDetail").innerHTML = `
      <div class="impact-detail-title"><div><strong>${escapeHtml(node.label)}</strong><small>${escapeHtml(node.entity_id)}</small></div><span class="impact-status-badge">${escapeHtml(typeLabel(node.type))}</span></div>
      <div class="impact-detail-grid">
        <div><span>连接关系</span><b>${node.edge_count || 0} 条</b></div>
        <div><span>上游影响</span><b>${node.in_degree || 0} 条</b></div>
        <div><span>下游传导</span><b>${node.out_degree || 0} 条</b></div>
      </div>`;
  }

  function graphStyle() {
    const theme = getComputedStyle(document.documentElement);
    const text = theme.getPropertyValue("--text").trim() || "#111827";
    const panel = theme.getPropertyValue("--panel").trim() || "#ffffff";
    return [
      { selector: "node", style: {
        "background-color": "#2563eb", "border-width": 2, "border-color": panel,
        width: "mapData(edge_count, 1, 12, 30, 48)", height: "mapData(edge_count, 1, 12, 30, 48)",
        label: "data(label)", color: text, "font-size": 10, "font-weight": 500,
        "text-wrap": "ellipsis", "text-max-width": 112, "text-valign": "bottom", "text-margin-y": 7,
        "text-background-color": panel, "text-background-opacity": .8, "text-background-padding": 2,
        "min-zoomed-font-size": 7,
      }},
      { selector: 'node[type = "event"]', style: { "background-color": "#0f766e", shape: "hexagon" }},
      { selector: 'node[type = "macro_factor"]', style: { "background-color": "#d97706", shape: "diamond" }},
      { selector: 'node[type = "country"], node[type = "region"]', style: { "background-color": "#0891b2", shape: "diamond" }},
      { selector: 'node[type = "sector"], node[type = "industry"]', style: { "background-color": "#ea580c", shape: "round-rectangle" }},
      { selector: 'node[type = "instrument"], node[type = "index"], node[type = "etf"]', style: { "background-color": "#059669", shape: "round-rectangle" }},
      { selector: 'node[type = "commodity"]', style: { "background-color": "#ca8a04", shape: "barrel" }},
      { selector: 'node[type = "currency"]', style: { "background-color": "#0d9488" }},
      { selector: 'node[type = "market"]', style: { "background-color": "#475569", shape: "round-rectangle" }},
      { selector: "edge", style: {
        width: "mapData(weight, 0, 1, 1, 3)", "line-color": "#16a34a", "target-arrow-color": "#16a34a",
        "target-arrow-shape": "triangle", "arrow-scale": .75, "curve-style": "bezier", opacity: .58,
        label: "", "font-size": 9, color: text, "text-background-color": panel,
        "text-background-opacity": .94, "text-background-padding": 3, "text-rotation": "autorotate",
      }},
      { selector: 'edge[direction = "negative"]', style: { "line-color": "#dc2626", "target-arrow-color": "#dc2626" }},
      { selector: 'edge[direction = "mixed"], edge[direction = "uncertain"], edge[direction = "neutral"]', style: { "line-color": "#64748b", "target-arrow-color": "#64748b" }},
      { selector: 'edge[status = "pending"]', style: { "line-style": "dashed", "line-color": "#d97706", "target-arrow-color": "#d97706" }},
      { selector: "edge:selected, edge.edge-hover", style: { label: "data(relation)", opacity: 1, width: 3, "z-index": 10 }},
      { selector: "node:selected, node.context", style: { "border-width": 4, "border-color": "#93c5fd", "z-index": 10 }},
      { selector: "edge.context", style: { opacity: 1, width: "mapData(weight, 0, 1, 2, 4)", "z-index": 9 }},
      { selector: ".dimmed", style: { opacity: .12 }},
      { selector: ".filtered-hidden, .focus-hidden", style: { display: "none" }},
    ];
  }

  function runLayout(fit = true) {
    if (!state.network) return;
    const visible = state.network.elements().not(".filtered-hidden").not(".focus-hidden");
    if (!visible.length) return;
    $("#impactGraphLoading").hidden = false;
    visible.layout({
      name: "dagre", rankDir: "LR", rankSep: 105, nodeSep: 54, edgeSep: 18,
      padding: 42, fit, animate: false, nodeDimensionsIncludeLabels: true,
    }).run();
    $("#impactGraphLoading").hidden = true;
  }

  function applyGraphFilters() {
    if (!state.network) return;
    const direction = $("#impactDirectionFilter").value;
    const nodeType = $("#impactNodeTypeFilter").value;
    const visibleNodeIds = new Set();
    state.network.batch(() => {
      state.network.elements().removeClass("filtered-hidden dimmed context");
      state.network.edges().forEach((edge) => {
        const statusAllowed = statusFilterAllows(edge.data("status"));
        const directionAllowed = direction === "all" || edge.data("direction") === direction;
        const sourceType = edge.source().data("type");
        const targetType = edge.target().data("type");
        const typeAllowed = nodeType === "all" || sourceType === nodeType || targetType === nodeType;
        if (!statusAllowed || !directionAllowed || !typeAllowed) edge.addClass("filtered-hidden");
        else {
          visibleNodeIds.add(edge.source().id());
          visibleNodeIds.add(edge.target().id());
        }
      });
      state.network.nodes().forEach((node) => {
        if (!visibleNodeIds.has(node.id())) node.addClass("filtered-hidden");
      });
    });
    clearFocus(false);
    runLayout(true);
    const edgeCount = state.network.edges().not(".filtered-hidden").length;
    const nodeCount = state.network.nodes().not(".filtered-hidden").length;
    $("#impactEdgeCount").textContent = edgeCount;
    $("#impactNodeCount").textContent = nodeCount;
    $("#impactNetworkEmpty").hidden = edgeCount > 0;
  }

  function renderNodeLegend(view) {
    const counts = view.nodes.reduce((result, node) => {
      result[node.type] = (result[node.type] || 0) + 1;
      return result;
    }, {});
    const current = $("#impactNodeTypeFilter").value;
    $("#impactNodeTypeFilter").innerHTML = '<option value="all">全部节点</option>' + Object.keys(counts).sort().map((type) => `<option value="${escapeHtml(type)}">${escapeHtml(typeLabel(type))}</option>`).join("");
    $("#impactNodeTypeFilter").value = counts[current] ? current : "all";
    $("#impactNodeLegend").innerHTML = Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([type, count]) => `
      <button class="impact-node-filter${$("#impactNodeTypeFilter").value === type ? " is-active" : ""}" type="button" data-node-type="${escapeHtml(type)}" style="--node-color:${nodeTypes[type]?.[1] || "#64748b"}"><i></i><span>${escapeHtml(typeLabel(type))}</span><b>${count}</b></button>`).join("");
  }

  function renderNetwork(view) {
    state.network?.destroy();
    state.network = null;
    state.focusNodeId = "";
    defaultInspector();
    renderNodeLegend(view);
    const empty = !view.nodes.length || !view.edges.length;
    $("#impactNetworkEmpty").hidden = !empty;
    if (empty || !window.cytoscape) return;
    if (window.cytoscapeDagre && !window.__impactDagreRegistered) {
      window.cytoscape.use(window.cytoscapeDagre);
      window.__impactDagreRegistered = true;
    }
    state.network = window.cytoscape({
      container: $("#impactNetwork"),
      elements: [
        ...view.nodes.map((node) => ({ group: "nodes", data: { ...node } })),
        ...view.edges.map((edge) => ({ group: "edges", data: { ...edge } })),
      ],
      style: graphStyle(), layout: { name: "preset" }, minZoom: .22, maxZoom: 3,
      wheelSensitivity: .15, boxSelectionEnabled: true, selectionType: "single",
    });
    state.network.on("mouseover", "edge", (event) => event.target.addClass("edge-hover"));
    state.network.on("mouseout", "edge", (event) => event.target.removeClass("edge-hover"));
    state.network.on("tap", "edge", (event) => showEdgeDetail(event.target.data()));
    state.network.on("tap", "node", (event) => {
      const tapped = event.target;
      const now = Date.now();
      if (state.lastTap.id === tapped.id() && now - state.lastTap.at < 320) focusNode(tapped);
      else highlightNeighborhood(tapped);
      state.lastTap = { id: tapped.id(), at: now };
      showNodeDetail(tapped.data());
    });
    state.network.on("tap", (event) => {
      if (event.target !== state.network) return;
      state.network.elements().removeClass("dimmed context");
      defaultInspector();
    });
    applyGraphFilters();
  }

  function highlightNeighborhood(node) {
    const neighborhood = node.closedNeighborhood();
    state.network.batch(() => {
      state.network.elements().removeClass("context dimmed");
      state.network.elements().not(neighborhood).not(".filtered-hidden").addClass("dimmed");
      neighborhood.addClass("context");
    });
  }

  function focusNode(node) {
    state.focusNodeId = node.id();
    const neighborhood = node.closedNeighborhood();
    state.network.batch(() => {
      state.network.elements().removeClass("focus-hidden dimmed context");
      state.network.elements().not(neighborhood).addClass("focus-hidden");
      neighborhood.addClass("context");
    });
    $("#impactFocusTrail").hidden = false;
    $("#impactFocusTrail span").textContent = `聚焦：${node.data("label")}`;
    runLayout(true);
  }

  function clearFocus(layout = true) {
    state.focusNodeId = "";
    $("#impactFocusTrail").hidden = true;
    state.network?.elements().removeClass("focus-hidden dimmed context");
    if (layout) applyGraphFilters();
  }

  function renderApprovals(view) {
    const nodes = new Map(view.nodes.map((node) => [node.id, node]));
    const pending = view.edges.filter((edge) => edge.status === "pending");
    $("#impactApprovalEmpty").hidden = pending.length > 0;
    $("#impactProposalList").innerHTML = pending.map((edge) => {
      const source = nodes.get(edge.source) || {};
      const target = nodes.get(edge.target) || {};
      const actionAttrs = edge.proposal_id
        ? `data-review-proposal="${escapeHtml(edge.proposal_id)}"`
        : `data-review-edge="${escapeHtml(edge.id)}"`;
      return `<article class="impact-proposal-item">
        <div class="impact-proposal-main"><strong>${escapeHtml(source.label)} → ${escapeHtml(target.label)}</strong><span>${escapeHtml(edge.relation)} · ${escapeHtml(edge.evidence || "暂无证据说明")}</span></div>
        <div class="impact-proposal-metric"><span>方向</span><b><span class="impact-direction-badge ${escapeHtml(edge.direction)}">${escapeHtml(directions[edge.direction] || edge.direction)}</span></b></div>
        <div class="impact-proposal-metric"><span>权重</span><b>${Math.round(Number(edge.weight) * 100)}%</b></div>
        <div class="impact-proposal-actions"><button class="text-button" type="button" ${actionAttrs} data-decision="reject">拒绝</button><button class="primary-button" type="button" ${actionAttrs} data-decision="approve">批准</button></div>
      </article>`;
    }).join("");
  }

  function formatDate(value) {
    if (!value) return "-";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }

  async function loadGraph() {
    $("#impactGraphLoading").hidden = false;
    $("#impactGraphNotice").textContent = "";
    try {
      const view = await window.AdminCenter.api(graphUrl());
      state.view = view;
      renderCounts(view);
      renderNetwork(view);
      renderApprovals(view);
      window.lucide?.createIcons();
    } catch (error) {
      $("#impactGraphNotice").textContent = `加载失败：${error.message}`;
      $("#impactNetworkEmpty").hidden = false;
    } finally {
      $("#impactGraphLoading").hidden = true;
    }
  }

  function diagnosticsUrl() {
    const params = new URLSearchParams({ limit: state.diagnostics.limit, offset: state.diagnostics.offset });
    const reason = $("#impactDiagnosticReason").value;
    const query = $("#impactDiagnosticSearch").value.trim();
    if (reason) params.set("reason", reason);
    if (query) params.set("query", query);
    return `/api/news-impact-graph/diagnostics?${params}`;
  }

  function diagnosticPath(payload) {
    if (Array.isArray(payload.path)) return payload.path.join(" › ");
    const source = payload.source_label || payload.source_id || "未知来源";
    const target = payload.target_label || payload.target_id || "未知目标";
    return `${source} › ${payload.relation || "未知关系"} › ${target}`;
  }

  function renderDiagnostics(page) {
    state.diagnostics.total = page.total;
    state.diagnostics.facets = page.facets;
    const selectedReason = $("#impactDiagnosticReason").value;
    $("#impactDiagnosticReason").innerHTML = '<option value="">全部原因</option>' + page.facets.map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)}（${item.count}）</option>`).join("");
    $("#impactDiagnosticReason").value = page.facets.some((item) => item.key === selectedReason) ? selectedReason : "";
    $("#impactDiagnosticFacets").innerHTML = page.facets.map((item) => `<button class="impact-diagnostic-facet${selectedReason === item.key ? " is-active" : ""}" type="button" data-diagnostic-reason="${escapeHtml(item.key)}"><span>${escapeHtml(item.label)}</span><b>${item.count}</b></button>`).join("");
    $("#impactDiagnosticList").innerHTML = page.items.map((item) => `<article class="impact-diagnostic-item">
      <div class="impact-diagnostic-main"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(diagnosticPath(item.payload))}</span><span>运行 ID：${escapeHtml(item.run_id)}</span></div>
      <div class="impact-diagnostic-reasons">${item.validation_errors.map((error) => `<span class="impact-reason-badge">${escapeHtml(error)}</span>`).join("")}</div>
      <time datetime="${escapeHtml(item.created_at)}">${escapeHtml(formatDate(item.created_at))}</time>
    </article>`).join("");
    $("#impactDiagnosticEmpty").hidden = page.items.length > 0;
    const start = page.total ? page.offset + 1 : 0;
    const end = Math.min(page.offset + page.items.length, page.total);
    $("#impactDiagnosticRange").textContent = `${start}–${end} / ${page.total}`;
    $("#impactDiagnosticPrev").disabled = page.offset <= 0;
    $("#impactDiagnosticNext").disabled = page.offset + page.limit >= page.total;
  }

  async function loadDiagnostics() {
    try {
      const page = await window.AdminCenter.api(diagnosticsUrl());
      renderDiagnostics(page);
    } catch (error) {
      $("#impactGraphNotice").textContent = `诊断加载失败：${error.message}`;
    }
  }

  function setTab(tab) {
    if (!["graph", "approval", "diagnostics"].includes(tab)) tab = "graph";
    state.activeTab = tab;
    $$(".impact-tab").forEach((button) => button.classList.toggle("is-active", button.dataset.impactTab === tab));
    $$("[data-impact-panel]").forEach((panel) => {
      const active = panel.dataset.impactPanel === tab;
      panel.hidden = !active;
      panel.classList.toggle("is-active", active);
    });
    history.replaceState(null, "", `#${tab}`);
    if (tab === "graph") {
      setTimeout(() => { state.network?.resize(); state.network?.fit(undefined, 42); }, 0);
    }
    if (tab === "diagnostics") loadDiagnostics();
  }

  async function reviewProposal(id, decision) {
    await window.AdminCenter.api(`/api/agent-proposals/${id}/${decision}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: decision === "approve" ? "从影响关系管理页批准" : "从影响关系管理页拒绝" }),
    });
    await loadGraph();
  }

  async function handleReview(event) {
    const proposal = event.target.closest("[data-review-proposal]");
    if (proposal) return reviewProposal(proposal.dataset.reviewProposal, proposal.dataset.decision);
    const edge = event.target.closest("[data-review-edge]");
    if (edge) {
      await window.AdminCenter.api(`/api/news-impact-graph/edges/${edge.dataset.reviewEdge}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: edge.dataset.decision === "approve" ? "approved" : "rejected" }),
      });
      return loadGraph();
    }
    const detail = event.target.closest("[data-detail-review]");
    if (!detail) return;
    if (detail.dataset.proposalId) return reviewProposal(detail.dataset.proposalId, detail.dataset.detailReview);
    await window.AdminCenter.api(`/api/news-impact-graph/edges/${detail.dataset.edgeId}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: detail.dataset.detailReview === "approve" ? "approved" : "rejected" }),
    });
    await loadGraph();
  }

  document.addEventListener("admin-center-ready", () => {
    loadGraph();
    setTab(state.activeTab);
  });
  document.addEventListener("app-theme-change", () => {
    if (state.view) renderNetwork(state.view);
  });
  $$(".impact-tab").forEach((button) => button.addEventListener("click", () => setTab(button.dataset.impactTab)));
  $("#refreshImpactGraph").addEventListener("click", async () => {
    await loadGraph();
    if (state.activeTab === "diagnostics") await loadDiagnostics();
  });
  $("#impactGraphSearch").addEventListener("input", () => {
    clearTimeout(state.graphTimer);
    state.graphTimer = setTimeout(loadGraph, 250);
  });
  ["#impactGraphStatus", "#impactDirectionFilter", "#impactNodeTypeFilter"].forEach((selector) => $(selector).addEventListener("change", () => {
    if (selector === "#impactNodeTypeFilter") renderNodeLegend(state.view);
    applyGraphFilters();
  }));
  $("#impactNodeLegend").addEventListener("click", (event) => {
    const button = event.target.closest("[data-node-type]");
    if (!button) return;
    $("#impactNodeTypeFilter").value = $("#impactNodeTypeFilter").value === button.dataset.nodeType ? "all" : button.dataset.nodeType;
    renderNodeLegend(state.view);
    applyGraphFilters();
  });
  $("#impactZoomIn").addEventListener("click", () => state.network?.zoom({ level: Math.min(3, state.network.zoom() * 1.2), renderedPosition: { x: state.network.width() / 2, y: state.network.height() / 2 } }));
  $("#impactZoomOut").addEventListener("click", () => state.network?.zoom({ level: Math.max(.22, state.network.zoom() / 1.2), renderedPosition: { x: state.network.width() / 2, y: state.network.height() / 2 } }));
  $("#impactFit").addEventListener("click", () => state.network?.fit(state.network.elements(":visible"), 42));
  $("#impactRelayout").addEventListener("click", () => runLayout(true));
  $("#impactFullscreen").addEventListener("click", () => $("#impactGraphExplorer").requestFullscreen?.());
  $("#impactFocusTrail button").addEventListener("click", () => clearFocus(true));
  $("#impactProposalList").addEventListener("click", (event) => handleReview(event).catch((error) => { $("#impactGraphNotice").textContent = error.message; }));
  $("#impactNetworkDetail").addEventListener("click", (event) => handleReview(event).catch((error) => { $("#impactGraphNotice").textContent = error.message; }));
  $("#impactDiagnosticSearch").addEventListener("input", () => {
    clearTimeout(state.diagnosticTimer);
    state.diagnosticTimer = setTimeout(() => { state.diagnostics.offset = 0; loadDiagnostics(); }, 250);
  });
  $("#impactDiagnosticReason").addEventListener("change", () => { state.diagnostics.offset = 0; loadDiagnostics(); });
  $("#impactDiagnosticFacets").addEventListener("click", (event) => {
    const facet = event.target.closest("[data-diagnostic-reason]");
    if (!facet) return;
    $("#impactDiagnosticReason").value = $("#impactDiagnosticReason").value === facet.dataset.diagnosticReason ? "" : facet.dataset.diagnosticReason;
    state.diagnostics.offset = 0;
    loadDiagnostics();
  });
  $("#impactDiagnosticPrev").addEventListener("click", () => {
    state.diagnostics.offset = Math.max(0, state.diagnostics.offset - state.diagnostics.limit);
    loadDiagnostics();
  });
  $("#impactDiagnosticNext").addEventListener("click", () => {
    if (state.diagnostics.offset + state.diagnostics.limit >= state.diagnostics.total) return;
    state.diagnostics.offset += state.diagnostics.limit;
    loadDiagnostics();
  });
  window.addEventListener("resize", () => {
    clearTimeout(state.resizeTimer);
    state.resizeTimer = setTimeout(() => {
      if (!state.network || state.activeTab !== "graph") return;
      state.network.resize();
      runLayout(true);
    }, 120);
  });
  $("#openImpactEdgeForm").addEventListener("click", () => $("#impactEdgeDialog").showModal());
  $("#backfillImpactGraph").addEventListener("click", async () => {
    const button = $("#backfillImpactGraph");
    button.disabled = true;
    try {
      const result = await window.AdminCenter.api("/api/news-tagging/backfill", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days: 30, batch_size: 8, scope: "impact_priority", engine: "agent" }),
      });
      $("#impactGraphNotice").textContent = `已匹配 ${result.matched} 条重点新闻，${result.queued} 条已进入分析队列。`;
    } catch (error) {
      $("#impactGraphNotice").textContent = `回填失败：${error.message}`;
    } finally {
      button.disabled = false;
    }
  });
  $("#impactEdgeForm").addEventListener("submit", async (event) => {
    if (event.submitter?.value === "cancel") return;
    event.preventDefault();
    const form = event.currentTarget;
    try {
      const body = Object.fromEntries(new FormData(form));
      body.weight = Number(body.weight);
      await window.AdminCenter.api("/api/news-impact-graph/edges", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      form.reset();
      $("#impactEdgeDialog").close();
      await loadGraph();
      setTab("approval");
    } catch (error) {
      $("#impactGraphNotice").textContent = `提交失败：${error.message}`;
    }
  });
  window.lucide?.createIcons();
})();
