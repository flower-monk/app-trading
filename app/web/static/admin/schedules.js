const scheduleState = {
  me: null,
  csrf: "",
  tasks: [],
  type: "",
  selected: new Set(),
  editing: null,
};

const $ = (selector) => document.querySelector(selector);
const taskTypeLabels = {
  automation: "平台自动化",
  news_source: "新闻源",
  data_source: "数据源",
  agent: "Agent",
};
const taskTypeIcons = {
  automation: "activity",
  news_source: "rss",
  data_source: "database-zap",
  agent: "bot",
};
const statusLabels = {
  scheduled: "已计划",
  paused: "已暂停",
  blocked: "已阻塞",
  running: "运行中",
  warning: "需关注",
  failed: "异常",
};

async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("X-CSRF-Token", scheduleState.csrf);
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

function formatInterval(seconds) {
  if (seconds % 3600 === 0) return `${seconds / 3600} 小时`;
  if (seconds % 60 === 0) return `${seconds / 60} 分钟`;
  return `${seconds} 秒`;
}

function formatTime(value) {
  if (!value) return "--";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "--";
  return new Intl.DateTimeFormat(document.documentElement.lang === "en-US" ? "en-US" : "zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function relativeTime(value) {
  if (!value) return "";
  const delta = new Date(value).getTime() - Date.now();
  const absolute = Math.abs(delta);
  const suffix = delta >= 0 ? "后" : "前";
  if (absolute < 60000) return delta >= 0 ? "即将执行" : "刚刚";
  if (absolute < 3600000) return `${Math.round(absolute / 60000)} 分钟${suffix}`;
  if (absolute < 86400000) return `${Math.round(absolute / 3600000)} 小时${suffix}`;
  return `${Math.round(absolute / 86400000)} 天${suffix}`;
}

function showNotice(message, status = "") {
  $("#scheduleNotice").className = `resource-notice ${status}`;
  $("#scheduleNotice").textContent = message;
}

function visibleTasks() {
  const query = $("#scheduleSearch").value.trim().toLowerCase();
  const status = $("#scheduleStatus").value;
  return scheduleState.tasks.filter((task) => {
    if (scheduleState.type && task.task_type !== scheduleState.type) return false;
    if (query && !`${task.name} ${task.owner_name} ${task.description}`.toLowerCase().includes(query)) return false;
    if (status === "enabled" && !task.enabled) return false;
    if (status === "paused" && task.status !== "paused") return false;
    if (status === "attention" && !["blocked", "warning", "failed"].includes(task.status)) return false;
    return true;
  });
}

function renderMetrics() {
  const enabled = scheduleState.tasks.filter((task) => task.enabled).length;
  const effective = scheduleState.tasks.filter((task) => task.effective_enabled).length;
  const attention = scheduleState.tasks.filter((task) => ["blocked", "warning", "failed"].includes(task.status)).length;
  const dueSoon = scheduleState.tasks.filter((task) => {
    if (!task.next_run_at || !task.effective_enabled) return false;
    const delta = new Date(task.next_run_at).getTime() - Date.now();
    return delta <= 3600000;
  }).length;
  $("#scheduleMetrics").innerHTML = `
    <article><span>任务总数</span><strong>${scheduleState.tasks.length}</strong></article>
    <article><span>已启用</span><strong>${enabled}</strong></article>
    <article><span>有效运行</span><strong>${effective}</strong></article>
    <article><span>一小时内到期 / 需关注</span><strong>${dueSoon} / ${attention}</strong></article>
  `;
  $("#allTaskCount").textContent = scheduleState.tasks.length;
  $("#automationTaskCount").textContent = scheduleState.tasks.filter((task) => task.task_type === "automation").length;
  $("#newsTaskCount").textContent = scheduleState.tasks.filter((task) => task.task_type === "news_source").length;
  $("#dataSourceTaskCount").textContent = scheduleState.tasks.filter((task) => task.task_type === "data_source").length;
  $("#agentTaskCount").textContent = scheduleState.tasks.filter((task) => task.task_type === "agent").length;
}

function renderSelection() {
  const count = scheduleState.selected.size;
  $("#selectedTaskCount").textContent = count ? `已选择 ${count} 个任务` : "未选择任务";
  $("#bulkEnableSchedules").disabled = count === 0;
  $("#bulkDisableSchedules").disabled = count === 0;
}

function renderTable() {
  const tasks = visibleTasks();
  const canWrite = scheduleState.me?.role === "admin";
  $("#scheduleTableBody").innerHTML = tasks.length ? tasks.map((task) => {
    const nextTime = task.next_run_at ? new Date(task.next_run_at).getTime() : null;
    const overdue = nextTime && nextTime < Date.now() && task.effective_enabled;
    const dependency = task.dependency ? `<span>${escapeHtml(task.dependency)}</span>` : "";
    return `
      <tr data-task-row="${escapeHtml(task.id)}">
        <td><input type="checkbox" data-select-task="${escapeHtml(task.id)}" ${scheduleState.selected.has(task.id) ? "checked" : ""} ${canWrite ? "" : "disabled"} /></td>
        <td><div class="schedule-task-name"><strong>${escapeHtml(task.name)}</strong><span>${escapeHtml(task.description)}</span></div></td>
        <td><span class="schedule-type"><i data-lucide="${taskTypeIcons[task.task_type]}"></i>${taskTypeLabels[task.task_type]}</span></td>
        <td><span class="schedule-status ${task.status}">${statusLabels[task.status]}</span>${dependency}</td>
        <td><div class="schedule-time"><strong>${formatInterval(task.interval_seconds)}</strong><span>${task.interval_seconds} 秒</span></div></td>
        <td><div class="schedule-time"><strong>${formatTime(task.last_run_at)}</strong><span>${task.last_status || "--"}</span></div></td>
        <td><div class="schedule-time ${overdue ? "overdue" : ""}"><strong>${formatTime(task.next_run_at)}</strong><span>${relativeTime(task.next_run_at)}</span></div></td>
        <td><div class="schedule-row-actions">
          <button class="icon-button admin-write" type="button" data-run-task="${escapeHtml(task.id)}" title="立即运行"><i data-lucide="play"></i></button>
          <button class="icon-button admin-write" type="button" data-edit-task="${escapeHtml(task.id)}" title="编辑"><i data-lucide="pencil"></i></button>
          <button class="icon-button admin-write ${task.enabled ? "danger-action" : ""}" type="button" data-toggle-task="${escapeHtml(task.id)}" data-enable="${task.enabled ? "false" : "true"}" title="${task.enabled ? "停用" : "启用"}"><i data-lucide="${task.enabled ? "pause" : "power"}"></i></button>
        </div></td>
      </tr>
    `;
  }).join("") : `<tr><td colspan="8" class="empty-cell">当前条件下没有定时任务。</td></tr>`;
  $("#visibleTaskCount").textContent = `${tasks.length} 个任务`;
  $("#selectAllSchedules").checked = tasks.length > 0 && tasks.every((task) => scheduleState.selected.has(task.id));
  $("#selectAllSchedules").disabled = !tasks.length || !canWrite;
  renderSelection();
  window.lucide?.createIcons();
}

function renderAll() {
  renderMetrics();
  renderTable();
  $("#scheduleUpdatedAt").textContent = `更新于 ${formatTime(new Date().toISOString())}`;
}

async function loadSchedules() {
  scheduleState.tasks = await api("/api/schedules");
  scheduleState.selected = new Set([...scheduleState.selected].filter((id) => scheduleState.tasks.some((task) => task.id === id)));
  renderAll();
}

function bestIntervalUnit(seconds) {
  if (seconds % 3600 === 0) return 3600;
  if (seconds % 60 === 0) return 60;
  return 1;
}

function openScheduleModal(task) {
  scheduleState.editing = task;
  const unit = bestIntervalUnit(task.interval_seconds);
  $("#scheduleFormTitle").textContent = task.name;
  $("#scheduleFormContext").innerHTML = `
    <strong>${taskTypeLabels[task.task_type]} · ${escapeHtml(task.owner_name)}</strong>
    <span>${escapeHtml(task.description)}</span>
    ${task.dependency ? `<small>${escapeHtml(task.dependency)}</small>` : ""}
  `;
  $("#scheduleEnabled").checked = task.enabled;
  $("#scheduleIntervalUnit").value = String(unit);
  $("#scheduleIntervalValue").value = task.interval_seconds / unit;
  $("#scheduleIntervalValue").min = Math.ceil(task.minimum_interval_seconds / unit);
  $("#scheduleIntervalValue").max = Math.floor(task.maximum_interval_seconds / unit);
  $("#scheduleIntervalHint").textContent = `允许范围 ${task.minimum_interval_seconds}-${task.maximum_interval_seconds} 秒`;
  $("#scheduleModal").hidden = false;
  window.lucide?.createIcons();
}

function closeScheduleModal() {
  $("#scheduleModal").hidden = true;
  scheduleState.editing = null;
}

function syncIntervalBounds() {
  if (!scheduleState.editing) return;
  const unit = Number($("#scheduleIntervalUnit").value);
  $("#scheduleIntervalValue").min = Math.ceil(scheduleState.editing.minimum_interval_seconds / unit);
  $("#scheduleIntervalValue").max = Math.floor(scheduleState.editing.maximum_interval_seconds / unit);
}

async function saveSchedule(event) {
  event.preventDefault();
  const task = scheduleState.editing;
  if (!task) return;
  const interval = Math.round(Number($("#scheduleIntervalValue").value) * Number($("#scheduleIntervalUnit").value));
  await api(`/api/schedules/${encodeURIComponent(task.id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: $("#scheduleEnabled").checked, interval_seconds: interval }),
  });
  closeScheduleModal();
  await loadSchedules();
  showNotice("定时任务已保存。", "ok");
}

async function toggleTask(taskId, enabled) {
  await api(`/api/schedules/${encodeURIComponent(taskId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  await loadSchedules();
  showNotice(enabled ? "定时任务已启用。" : "定时任务已暂停。", "ok");
}

async function runTask(taskId) {
  showNotice("任务正在提交...", "watch");
  const result = await api(`/api/schedules/${encodeURIComponent(taskId)}/run`, { method: "POST" });
  await loadSchedules();
  showNotice(result.message, result.status === "failed" ? "error" : result.status === "warning" ? "watch" : "ok");
}

async function bulkStatus(enabled) {
  if (!scheduleState.selected.size) return;
  const label = enabled ? "启用" : "停用";
  if (!window.confirm(`确认${label} ${scheduleState.selected.size} 个定时任务？`)) return;
  await api("/api/schedules/bulk-status", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: [...scheduleState.selected], enabled }),
  });
  scheduleState.selected.clear();
  await loadSchedules();
  showNotice(`批量${label}完成。`, "ok");
}

async function bootstrapSchedules() {
  const session = await api("/api/auth/me");
  scheduleState.me = session.user;
  scheduleState.csrf = session.csrf_token;
  window.AppTheme?.apply(session.preferences?.theme || "system");
  window.AppTheme?.updateControls();
  $("#scheduleUserLabel").textContent = `${session.user.display_name} · ${session.user.role === "admin" ? "管理员" : "只读"}`;
  document.body.classList.toggle("viewer-mode", session.user.role !== "admin");
  await loadSchedules();
}

document.addEventListener("click", (event) => {
  const type = event.target.closest("[data-schedule-type]");
  if (type) {
    scheduleState.type = type.dataset.scheduleType;
    document.querySelectorAll("[data-schedule-type]").forEach((item) => item.classList.toggle("active", item === type));
    scheduleState.selected.clear();
    renderTable();
    return;
  }
  const edit = event.target.closest("[data-edit-task]");
  if (edit) {
    const task = scheduleState.tasks.find((item) => item.id === edit.dataset.editTask);
    if (task) openScheduleModal(task);
    return;
  }
  const run = event.target.closest("[data-run-task]");
  if (run) {
    runTask(run.dataset.runTask).catch((error) => showNotice(error.message, "error"));
    return;
  }
  const toggle = event.target.closest("[data-toggle-task]");
  if (toggle && window.confirm(`确认${toggle.dataset.enable === "true" ? "启用" : "停用"}该定时任务？`)) {
    toggleTask(toggle.dataset.toggleTask, toggle.dataset.enable === "true").catch((error) => showNotice(error.message, "error"));
  }
});

$("#scheduleTableBody").addEventListener("change", (event) => {
  const checkbox = event.target.closest("[data-select-task]");
  if (!checkbox) return;
  if (checkbox.checked) scheduleState.selected.add(checkbox.dataset.selectTask);
  else scheduleState.selected.delete(checkbox.dataset.selectTask);
  renderSelection();
});
$("#selectAllSchedules").addEventListener("change", (event) => {
  visibleTasks().forEach((task) => {
    if (event.target.checked) scheduleState.selected.add(task.id);
    else scheduleState.selected.delete(task.id);
  });
  renderTable();
});
$("#scheduleSearch").addEventListener("input", renderTable);
$("#scheduleStatus").addEventListener("change", renderTable);
$("#refreshSchedules").addEventListener("click", () => loadSchedules().catch((error) => showNotice(error.message, "error")));
$("#bulkEnableSchedules").addEventListener("click", () => bulkStatus(true).catch((error) => showNotice(error.message, "error")));
$("#bulkDisableSchedules").addEventListener("click", () => bulkStatus(false).catch((error) => showNotice(error.message, "error")));
$("#scheduleForm").addEventListener("submit", (event) => saveSchedule(event).catch((error) => showNotice(error.message, "error")));
$("#scheduleIntervalUnit").addEventListener("change", syncIntervalBounds);
$("#closeScheduleModal").addEventListener("click", closeScheduleModal);
$("#cancelScheduleModal").addEventListener("click", closeScheduleModal);
$("#scheduleModal").addEventListener("click", (event) => { if (event.target.id === "scheduleModal") closeScheduleModal(); });
$("#scheduleThemeToggle").addEventListener("click", () => window.AppTheme.toggle(scheduleState.csrf).catch((error) => showNotice(error.message, "error")));
$("#scheduleLogout").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  window.location.replace("/login");
});

bootstrapSchedules().catch((error) => showNotice(error.message, "error"));
