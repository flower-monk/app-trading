const adminState = {
  me: null,
  csrf: "",
  descriptors: [],
  resourceType: "users",
  page: 1,
  pageSize: 25,
  sort: "",
  direction: "desc",
  pageData: null,
  selected: new Set(),
  modalMode: null,
  modalItem: null,
};

const fieldSchemas = {
  users: [
    { key: "username", label: "用户名", required: true, createOnly: true },
    { key: "display_name", label: "显示名称", required: true },
    { key: "password", label: "密码", type: "password", required: true, createOnly: true },
    { key: "role", label: "角色", type: "select", options: [["admin", "管理员"], ["viewer", "只读"]], required: true },
    { key: "enabled", label: "启用", type: "checkbox" },
  ],
  watchlist: [{ key: "symbol", label: "标的代码", required: true }],
  "screener-presets": [
    { key: "name", label: "模板名称", required: true },
    { key: "filters", label: "筛选条件 JSON", type: "json", required: true },
  ],
  "news-sources": [
    { key: "name", label: "来源名称", required: true },
    { key: "url", label: "RSS URL", type: "url", required: true },
    { key: "symbols", label: "关联标的（逗号分隔）", type: "list" },
    { key: "poll_interval_seconds", label: "轮询秒数", type: "number", required: true },
    { key: "enabled", label: "启用", type: "checkbox" },
  ],
  factors: [
    { key: "name", label: "因子名称", required: true },
    { key: "category", label: "分类", required: true },
    { key: "description", label: "逻辑描述", type: "textarea", required: true },
    { key: "signal", label: "方向", type: "select", options: [["long", "Long"], ["short", "Short"], ["neutral", "Neutral"]] },
    { key: "horizon", label: "周期", required: true },
    { key: "enabled", label: "启用", type: "checkbox" },
  ],
  strategies: [
    { key: "name", label: "策略名称", required: true },
    { key: "category", label: "分类", required: true },
    { key: "description", label: "策略描述", type: "textarea", required: true },
    { key: "factor_ids", label: "绑定因子 JSON", type: "json", required: true },
    { key: "default_filters", label: "默认过滤器 JSON", type: "json", required: true },
    { key: "risk_controls", label: "风控规则 JSON", type: "json", required: true },
    { key: "validation_rules", label: "验证规则 JSON", type: "json", required: true },
    { key: "enabled", label: "启用", type: "checkbox" },
  ],
  alerts: [
    { key: "symbol", label: "标的", required: true, createOnly: true },
    { key: "name", label: "规则名称", required: true },
    { key: "metric", label: "指标", type: "select", options: [["price", "价格"], ["rsi", "RSI"], ["news_impact", "新闻影响"]], createOnly: true },
    { key: "operator", label: "运算符", type: "select", options: [[">=", ">="], ["<=", "<="]] },
    { key: "threshold", label: "阈值", type: "number", required: true },
    { key: "enabled", label: "启用", type: "checkbox" },
  ],
  "notification-channels": [
    { key: "name", label: "渠道名称", required: true },
    { key: "target", label: "Webhook URL", type: "url", required: true },
    { key: "enabled", label: "启用", type: "checkbox" },
  ],
  "portfolio-positions": [
    { key: "symbol", label: "标的", required: true },
    { key: "side", label: "方向", type: "select", options: [["long", "Long"], ["short", "Short"]] },
    { key: "quantity", label: "数量", type: "number", required: true },
    { key: "average_price", label: "成本价", type: "number", required: true },
    { key: "note", label: "备注", type: "textarea" },
  ],
  settings: [{ key: "value", label: "配置值", required: true }],
};

const resourceActions = {
  "auth-sessions": ["revoke"],
  "news-sources": ["refresh"],
  strategies: ["validate"],
  alerts: ["evaluate"],
  "notification-channels": ["test"],
};

const actionLabels = { refresh: "刷新", validate: "验证", evaluate: "评估", test: "测试", revoke: "注销" };
const $ = (selector) => document.querySelector(selector);

async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("X-CSRF-Token", adminState.csrf);
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    window.location.replace("/login");
    throw new Error("登录已失效");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "请求失败");
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function compactValue(value) {
  if (value === null || value === undefined || value === "") return "--";
  if (typeof value === "boolean") return value ? "启用" : "停用";
  if (typeof value === "object") return JSON.stringify(value).slice(0, 90);
  const text = String(value);
  return text.length > 90 ? `${text.slice(0, 87)}...` : text;
}

function descriptor() {
  return adminState.descriptors.find((item) => item.id === adminState.resourceType);
}

function renderMetrics() {
  const total = adminState.descriptors.reduce((sum, item) => sum + item.total, 0);
  const inactive = adminState.descriptors.reduce((sum, item) => sum + (item.active === null ? 0 : item.total - item.active), 0);
  const histories = adminState.descriptors.filter((item) => ["研究历史", "系统历史"].includes(item.category)).reduce((sum, item) => sum + item.total, 0);
  $("#resourceMetrics").innerHTML = `
    <article><span>资源类型</span><strong>${adminState.descriptors.length}</strong></article>
    <article><span>记录总量</span><strong>${total}</strong></article>
    <article><span>停用/隐藏</span><strong>${inactive}</strong></article>
    <article><span>历史与审计</span><strong>${histories}</strong></article>
  `;
}

function renderNavigation() {
  const groups = [...new Set(adminState.descriptors.map((item) => item.category))];
  $("#resourceNav").innerHTML = groups.map((group) => `
    <section>
      <strong>${escapeHtml(group)}</strong>
      ${adminState.descriptors.filter((item) => item.category === group).map((item) => `
        <button class="resource-nav-item ${item.id === adminState.resourceType ? "active" : ""}" type="button" data-resource="${item.id}">
          <span>${escapeHtml(item.name)}</span><b>${item.total}</b>
        </button>
      `).join("")}
    </section>
  `).join("");
}

function visibleColumns(items) {
  if (!items.length) return [];
  const ignored = new Set(["_resource_id", "equity_curve", "signal_points", "windows", "scenarios", "checks", "actions", "evidence", "request_payload", "filter_validation", "regime"]);
  const preferred = ["username", "name", "title", "symbol", "strategy_name", "factor_name", "key", "status", "pass_fail", "role", "category", "enabled", "created_at", "generated_at", "finished_at"];
  const available = Object.keys(items[0]).filter((key) => !ignored.has(key));
  return [...preferred.filter((key) => available.includes(key)), ...available.filter((key) => !preferred.includes(key))].slice(0, 6);
}

function renderTable() {
  const data = adminState.pageData;
  const items = data?.items || [];
  const columns = visibleColumns(items);
  const canStatus = Boolean(data?.resource.status_field);
  $("#resourceTableHead").innerHTML = `<tr><th><input id="selectAll" type="checkbox" aria-label="选择当前页" /></th>${columns.map((key) => `<th><button class="table-sort" type="button" data-sort="${escapeHtml(key)}">${escapeHtml(key)}${adminState.sort === key ? (adminState.direction === "asc" ? " ↑" : " ↓") : ""}</button></th>`).join("")}<th>操作</th></tr>`;
  $("#resourceTableBody").innerHTML = items.length ? items.map((item) => {
    const id = String(item._resource_id);
    const enabled = canStatus ? Boolean(item[data.resource.status_field]) : null;
    return `<tr class="${enabled === false ? "inactive" : ""}">
      <td><input type="checkbox" data-select-id="${escapeHtml(id)}" ${adminState.selected.has(id) ? "checked" : ""} /></td>
      ${columns.map((key) => `<td title="${escapeHtml(compactValue(item[key]))}">${escapeHtml(compactValue(item[key]))}</td>`).join("")}
      <td><div class="row-actions">
        <button class="icon-button" type="button" data-detail-id="${escapeHtml(id)}" title="查看详情">⌕</button>
        ${adminState.me.role === "admin" && fieldSchemas[adminState.resourceType] && adminState.resourceType !== "watchlist" ? `<button class="icon-button admin-write" type="button" data-edit-id="${escapeHtml(id)}" title="编辑">✎</button>` : ""}
        ${adminState.me.role === "admin" && canStatus && adminState.resourceType !== "users" ? `<button class="icon-button admin-write" type="button" data-status-id="${escapeHtml(id)}" data-enabled="${enabled ? "false" : "true"}" title="${enabled ? "停用" : "启用"}">${enabled ? "Ⅱ" : "▶"}</button>` : ""}
        ${data.resource.actions.includes("rerun") && adminState.me.role === "admin" ? `<button class="icon-button admin-write" type="button" data-rerun-id="${escapeHtml(id)}" title="重新运行">↻</button>` : ""}
      </div></td>
    </tr>`;
  }).join("") : `<tr><td colspan="${columns.length + 2}" class="empty-cell">当前条件下没有资源。</td></tr>`;
  $("#resourceCount").textContent = `${data?.total || 0} 条`;
  const pages = Math.max(1, Math.ceil((data?.total || 0) / adminState.pageSize));
  $("#pageLabel").textContent = `${adminState.page} / ${pages}`;
  $("#previousPage").disabled = adminState.page <= 1;
  $("#nextPage").disabled = adminState.page >= pages;
  $("#bulkEnable").hidden = !canStatus || adminState.me.role !== "admin" || adminState.resourceType === "users";
  $("#bulkDisable").hidden = !canStatus || adminState.me.role !== "admin" || adminState.resourceType === "users";
  $("#resourceStatus").hidden = !canStatus;
  const createSupported = Boolean(fieldSchemas[adminState.resourceType]) && !["settings"].includes(adminState.resourceType);
  $("#createResource").hidden = adminState.me.role !== "admin" || !createSupported;
}

async function loadDescriptors() {
  adminState.descriptors = await api("/api/admin/resources");
  if (!adminState.descriptors.some((item) => item.id === adminState.resourceType)) adminState.resourceType = adminState.descriptors[0]?.id || "users";
  renderMetrics();
  renderNavigation();
}

async function loadResource(resetPage = false) {
  if (resetPage) adminState.page = 1;
  adminState.selected.clear();
  const params = new URLSearchParams({ page: adminState.page, page_size: adminState.pageSize });
  const query = $("#resourceSearch").value.trim();
  const status = $("#resourceStatus").value;
  if (query) params.set("query", query);
  if (status) params.set("enabled", status);
  if (adminState.sort) {
    params.set("sort", adminState.sort);
    params.set("direction", adminState.direction);
  }
  adminState.pageData = await api(`/api/admin/resources/${adminState.resourceType}?${params}`);
  $("#resourceTitle").textContent = adminState.pageData.resource.name;
  renderTable();
}

function fieldValue(field, item) {
  const value = item?.[field.key];
  if (field.type === "json") return JSON.stringify(value ?? (field.key === "default_filters" ? {} : []), null, 2);
  if (field.type === "list") return Array.isArray(value) ? value.join(",") : value || "";
  if (field.type === "checkbox") return value === undefined ? true : Boolean(value);
  if (value !== undefined && value !== null) return value;
  if (field.key === "poll_interval_seconds") return 900;
  if (field.key === "role") return "viewer";
  if (field.key === "signal") return "long";
  if (field.key === "side") return "long";
  if (field.key === "operator") return ">=";
  return "";
}

function renderField(field, item) {
  const value = fieldValue(field, item);
  if (field.type === "checkbox") return `<label class="resource-check"><input name="${field.key}" type="checkbox" ${value ? "checked" : ""} /><span>${field.label}</span></label>`;
  if (field.type === "select") return `<label><span>${field.label}</span><select name="${field.key}">${field.options.map(([key, label]) => `<option value="${escapeHtml(key)}" ${value === key ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}</select></label>`;
  if (["textarea", "json", "list"].includes(field.type)) return `<label><span>${field.label}</span><textarea name="${field.key}" rows="${field.type === "json" ? 5 : 3}" ${field.required ? "required" : ""}>${escapeHtml(value)}</textarea></label>`;
  return `<label><span>${field.label}</span><input name="${field.key}" type="${field.type || "text"}" value="${escapeHtml(value)}" ${field.type === "number" ? 'step="any"' : ""} ${field.required ? "required" : ""} /></label>`;
}

function openResourceModal(mode, item = null) {
  adminState.modalMode = mode;
  adminState.modalItem = item;
  const schema = fieldSchemas[adminState.resourceType] || [];
  const fields = schema.filter((field) => mode === "create" || !field.createOnly);
  $("#resourceFormTitle").textContent = `${mode === "create" ? "新建" : "编辑"}${descriptor().name}`;
  $("#resourceFields").innerHTML = fields.map((field) => renderField(field, item)).join("");
  $("#resourceModal").hidden = false;
}

function closeResourceModal() {
  $("#resourceModal").hidden = true;
  adminState.modalItem = null;
}

function openPasswordReset(userId) {
  adminState.modalMode = "reset-password";
  adminState.modalItem = { _resource_id: userId };
  $("#resourceFormTitle").textContent = "重置用户密码";
  $("#resourceFields").innerHTML = renderField({ key: "password", label: "新密码", type: "password", required: true }, null);
  $("#resourceModal").hidden = false;
}

function parseFormPayload(form) {
  const schema = fieldSchemas[adminState.resourceType] || [];
  const payload = {};
  for (const field of schema) {
    if (adminState.modalMode === "edit" && field.createOnly) continue;
    const control = form.elements.namedItem(field.key);
    if (!control) continue;
    if (field.type === "checkbox") payload[field.key] = control.checked;
    else if (field.type === "number") payload[field.key] = Number(control.value);
    else if (field.type === "json") payload[field.key] = JSON.parse(control.value || "null");
    else if (field.type === "list") payload[field.key] = control.value.split(/[,，\s]+/).filter(Boolean).map((item) => item.toUpperCase());
    else payload[field.key] = control.value.trim();
  }
  return payload;
}

async function saveResource(event) {
  event.preventDefault();
  const payload = parseFormPayload(event.currentTarget);
  const item = adminState.modalItem;
  let url = `/api/admin/resources/${adminState.resourceType}`;
  let method = "POST";
  if (adminState.modalMode === "reset-password") {
    url = `/api/admin/users/${item._resource_id}/reset-password`;
  } else if (adminState.modalMode === "edit") {
    const id = item._resource_id;
    url = adminState.resourceType === "users" ? `/api/admin/users/${id}` : `/api/admin/resources/${adminState.resourceType}/${encodeURIComponent(id)}`;
    method = "PATCH";
  }
  await api(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  closeResourceModal();
  await Promise.all([loadDescriptors(), loadResource()]);
  showNotice("资源已保存。", "ok");
}

function showNotice(message, status = "") {
  $("#resourceNotice").className = `resource-notice ${status}`;
  $("#resourceNotice").textContent = message;
}

async function openDetail(id) {
  const item = await api(`/api/admin/resources/${adminState.resourceType}/${encodeURIComponent(id)}`);
  $("#drawerTitle").textContent = `${descriptor().name}详情`;
  const actions = resourceActions[adminState.resourceType] || [];
  $("#drawerBody").innerHTML = `
    <dl class="resource-detail">${Object.entries(item).filter(([key]) => key !== "_resource_id").map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd><pre>${escapeHtml(typeof value === "object" ? JSON.stringify(value, null, 2) : value)}</pre></dd></div>`).join("")}</dl>
    ${adminState.me.role === "admin" && actions.length ? `<div class="drawer-actions">${actions.map((action) => `<button class="text-button" type="button" data-resource-action="${action}" data-action-id="${escapeHtml(id)}">${actionLabels[action]}</button>`).join("")}</div>` : ""}
    ${adminState.me.role === "admin" && adminState.resourceType === "users" ? `<div class="drawer-actions"><button class="text-button" type="button" data-reset-user="${escapeHtml(id)}">重置密码</button><button class="text-button" type="button" data-revoke-user="${escapeHtml(id)}">注销会话</button></div>` : ""}
  `;
  $("#resourceDrawer").hidden = false;
}

async function setResourceStatus(id, enabled) {
  await api(`/api/admin/resources/${adminState.resourceType}/${encodeURIComponent(id)}/status`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled }),
  });
  await Promise.all([loadDescriptors(), loadResource()]);
  showNotice(enabled ? "资源已启用。" : "资源已停用并保留数据。", "ok");
}

async function bulkStatus(enabled) {
  if (!adminState.selected.size) return showNotice("请先选择资源。", "watch");
  if (!window.confirm(`确认${enabled ? "启用" : "停用"} ${adminState.selected.size} 条资源？`)) return;
  await api(`/api/admin/resources/${adminState.resourceType}/bulk-status`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: [...adminState.selected], enabled }),
  });
  await Promise.all([loadDescriptors(), loadResource()]);
  showNotice("批量状态更新完成。", "ok");
}

async function rerunResource(id) {
  showNotice("正在重新运行，请保持页面打开...", "watch");
  const result = await api(`/api/admin/resources/${adminState.resourceType}/${encodeURIComponent(id)}/rerun`, { method: "POST" });
  await Promise.all([loadDescriptors(), loadResource()]);
  showNotice(`已生成新运行 ${result.new_id || "记录"}。`, "ok");
}

async function executeAction(action, id) {
  showNotice(`正在执行${actionLabels[action]}...`, "watch");
  await api(`/api/admin/resources/${adminState.resourceType}/${encodeURIComponent(id)}/action/${action}`, { method: "POST" });
  $("#resourceDrawer").hidden = true;
  await Promise.all([loadDescriptors(), loadResource()]);
  showNotice(`${actionLabels[action]}完成。`, "ok");
}

async function bootstrapAdmin() {
  const session = await api("/api/auth/me");
  adminState.me = session.user;
  adminState.csrf = session.csrf_token;
  $("#adminUserLabel").textContent = `${session.user.display_name} · ${session.user.role === "admin" ? "管理员" : "只读"}`;
  document.body.classList.toggle("viewer-mode", session.user.role !== "admin");
  await loadDescriptors();
  await loadResource();
}

document.addEventListener("click", (event) => {
  const nav = event.target.closest("[data-resource]");
  if (nav) {
    adminState.resourceType = nav.dataset.resource;
    adminState.page = 1;
    adminState.sort = "";
    adminState.direction = "desc";
    $("#resourceSearch").value = "";
    $("#resourceStatus").value = "";
    renderNavigation();
    loadResource().catch((error) => showNotice(error.message, "error"));
    return;
  }
  const detail = event.target.closest("[data-detail-id]");
  if (detail) openDetail(detail.dataset.detailId).catch((error) => showNotice(error.message, "error"));
  const edit = event.target.closest("[data-edit-id]");
  if (edit) {
    const item = adminState.pageData.items.find((row) => String(row._resource_id) === edit.dataset.editId);
    openResourceModal("edit", item);
  }
  const status = event.target.closest("[data-status-id]");
  if (status && window.confirm(`确认${status.dataset.enabled === "true" ? "启用" : "停用"}该资源？`)) setResourceStatus(status.dataset.statusId, status.dataset.enabled === "true").catch((error) => showNotice(error.message, "error"));
  const rerun = event.target.closest("[data-rerun-id]");
  if (rerun) rerunResource(rerun.dataset.rerunId).catch((error) => showNotice(error.message, "error"));
  const action = event.target.closest("[data-resource-action]");
  if (action) executeAction(action.dataset.resourceAction, action.dataset.actionId).catch((error) => showNotice(error.message, "error"));
  const reset = event.target.closest("[data-reset-user]");
  if (reset) openPasswordReset(reset.dataset.resetUser);
  const revoke = event.target.closest("[data-revoke-user]");
  if (revoke) api(`/api/admin/users/${revoke.dataset.revokeUser}/revoke-sessions`, { method: "POST" }).then(() => showNotice("账号会话已注销。", "ok")).catch((error) => showNotice(error.message, "error"));
  const selected = event.target.closest("[data-select-id]");
  if (selected) selected.checked ? adminState.selected.add(selected.dataset.selectId) : adminState.selected.delete(selected.dataset.selectId);
  const sort = event.target.closest("[data-sort]");
  if (sort) {
    adminState.direction = adminState.sort === sort.dataset.sort && adminState.direction === "desc" ? "asc" : "desc";
    adminState.sort = sort.dataset.sort;
    loadResource(true).catch((error) => showNotice(error.message, "error"));
  }
});

$("#resourceTableHead").addEventListener("change", (event) => {
  if (event.target.id !== "selectAll") return;
  adminState.selected.clear();
  document.querySelectorAll("[data-select-id]").forEach((input) => {
    input.checked = event.target.checked;
    if (input.checked) adminState.selected.add(input.dataset.selectId);
  });
});
$("#resourceForm").addEventListener("submit", (event) => saveResource(event).catch((error) => showNotice(error.message, "error")));
$("#createResource").addEventListener("click", () => openResourceModal("create"));
$("#closeResourceModal").addEventListener("click", closeResourceModal);
$("#cancelResourceModal").addEventListener("click", closeResourceModal);
$("#closeDrawer").addEventListener("click", () => { $("#resourceDrawer").hidden = true; });
$("#resourceRefresh").addEventListener("click", () => Promise.all([loadDescriptors(), loadResource()]).catch((error) => showNotice(error.message, "error")));
$("#resourceSearch").addEventListener("search", () => loadResource(true).catch((error) => showNotice(error.message, "error")));
$("#resourceSearch").addEventListener("keydown", (event) => { if (event.key === "Enter") loadResource(true).catch((error) => showNotice(error.message, "error")); });
$("#resourceStatus").addEventListener("change", () => loadResource(true).catch((error) => showNotice(error.message, "error")));
$("#bulkEnable").addEventListener("click", () => bulkStatus(true).catch((error) => showNotice(error.message, "error")));
$("#bulkDisable").addEventListener("click", () => bulkStatus(false).catch((error) => showNotice(error.message, "error")));
$("#exportResource").addEventListener("click", () => {
  const params = new URLSearchParams();
  const query = $("#resourceSearch").value.trim();
  const status = $("#resourceStatus").value;
  if (query) params.set("query", query);
  if (status) params.set("enabled", status);
  if (adminState.sort) {
    params.set("sort", adminState.sort);
    params.set("direction", adminState.direction);
  }
  window.location.href = `/api/admin/resources/${adminState.resourceType}/export${params.toString() ? `?${params}` : ""}`;
});
$("#previousPage").addEventListener("click", () => { adminState.page -= 1; loadResource().catch((error) => showNotice(error.message, "error")); });
$("#nextPage").addEventListener("click", () => { adminState.page += 1; loadResource().catch((error) => showNotice(error.message, "error")); });
$("#adminLogout").addEventListener("click", async () => { await api("/api/auth/logout", { method: "POST" }); window.location.replace("/login"); });

bootstrapAdmin().catch((error) => showNotice(error.message, "error"));
