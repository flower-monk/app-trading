const state = { me: null, csrf: "", credentials: [], vault: null, editing: null, rotating: null };
const $ = (selector) => document.querySelector(selector);

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

function esc(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function fmt(value) {
  return value ? new Date(value).toLocaleString(window.AppI18n?.getLocale() || "zh-CN", { hour12: false }) : "--";
}

function originLabel(origin) {
  return {
    database: "页面加密存储",
    environment: "环境变量",
    none: "未配置",
    error: "解密异常",
  }[origin] || origin;
}

function testStatusLabel(status) {
  return {
    unknown: "未检测",
    pending: "检测中",
    pass: "通过",
    partial: "部分通过",
    fail: "失败",
  }[status] || status;
}

function notice(message, type = "ok") {
  $("#credentialNotice").textContent = message;
  $("#credentialNotice").className = `resource-notice ${type}`;
}

function renderMetrics() {
  const vault = state.vault;
  $("#credentialMetrics").innerHTML = `
    <article><span>凭据总数</span><strong>${vault.total}</strong></article>
    <article><span>已配置</span><strong>${vault.configured}</strong></article>
    <article><span>页面覆盖</span><strong>${vault.database_overrides}</strong></article>
    <article><span>环境变量回退</span><strong>${vault.environment_fallbacks}</strong></article>
    <article><span>配置异常</span><strong>${vault.errors}</strong></article>
  `;
  $("#vaultBanner").hidden = vault.writable;
  $("#vaultBanner").textContent = vault.writable ? "" : "未配置 APP_CREDENTIAL_MASTER_KEY。当前可继续读取环境变量，但页面无法保存或替换 API Key。";
}

function renderTable() {
  const query = $("#credentialSearch").value.trim().toLowerCase();
  const rows = state.credentials.filter((item) => !query || `${item.name} ${item.env_alias} ${item.bindings.map((binding) => binding.resource_name).join(" ")}`.toLowerCase().includes(query));
  $("#credentialTableBody").innerHTML = rows.length ? rows.map((item) => `
    <tr>
      <td><div class="credential-name"><strong>${esc(item.name)}</strong><small>${esc(item.env_alias)}</small></div></td>
      <td>${esc(originLabel(item.origin))}</td>
      <td><span class="status-pill ${item.configured ? "succeeded" : "failed"}">${item.configured ? esc(item.secret_hint || "已配置") : "未配置"}</span></td>
      <td><div class="credential-bindings">${item.bindings.length ? item.bindings.map((binding) => `<span class="credential-binding" title="${esc(binding.resource_type)}">${esc(binding.resource_name)}</span>`).join("") : '<span class="credential-binding">未绑定</span>'}</div></td>
      <td>${fmt(item.last_rotated_at)}</td>
      <td><span class="status-pill ${item.test_status === "pass" ? "succeeded" : item.test_status === "pending" ? "running" : item.test_status === "unknown" ? "" : "failed"}" title="${esc(item.test_message)}">${esc(testStatusLabel(item.test_status))}</span></td>
      <td><div class="credential-actions">
        <button class="icon-button" type="button" data-edit-credential="${esc(item.id)}" title="编辑元数据"><i data-lucide="pencil"></i></button>
        <button class="icon-button" type="button" data-rotate-credential="${esc(item.id)}" title="替换 API Key" ${state.vault.writable ? "" : "disabled"}><i data-lucide="refresh-cw"></i></button>
        <button class="icon-button" type="button" data-test-credential="${esc(item.id)}" title="检测连接" ${item.configured ? "" : "disabled"}><i data-lucide="plug-zap"></i></button>
        <button class="icon-button" type="button" data-clear-credential="${esc(item.id)}" title="移除页面覆盖" ${item.origin === "database" ? "" : "disabled"}><i data-lucide="undo-2"></i></button>
      </div></td>
    </tr>
  `).join("") : '<tr><td colspan="7" class="empty-cell">没有匹配的凭据。</td></tr>';
  window.lucide?.createIcons();
}

async function reload() {
  [state.credentials, state.vault] = await Promise.all([api("/api/credentials"), api("/api/credentials/status")]);
  renderMetrics();
  renderTable();
}

function openCredentialModal(item = null) {
  state.editing = item;
  $("#credentialFormTitle").textContent = item ? "编辑凭据" : "新增凭据";
  $("#credentialName").value = item?.name || "";
  $("#credentialEnvAlias").value = item?.env_alias || "";
  $("#credentialSecret").value = "";
  $("#credentialSecretField").hidden = Boolean(item);
  $("#credentialFormHint").textContent = item ? "修改环境变量别名会同步更新所有绑定资源。" : "密钥保存后不会再次明文显示。";
  $("#credentialModal").hidden = false;
}

function closeCredentialModal() {
  $("#credentialModal").hidden = true;
  state.editing = null;
}

function openRotateModal(item) {
  state.rotating = item;
  $("#rotateTitle").textContent = `替换 ${item.name} API Key`;
  $("#rotateImpact").innerHTML = `<strong>同步影响</strong><span>${item.binding_count} 个绑定资源将在保存后立即读取新密钥</span><a href="/credentials">${esc(item.secret_hint || "")}</a>`;
  $("#rotateSecret").value = "";
  $("#rotateModal").hidden = false;
}

function closeRotateModal() {
  $("#rotateModal").hidden = true;
  state.rotating = null;
}

async function bootstrap() {
  const session = await api("/api/auth/me");
  state.me = session.user;
  state.csrf = session.csrf_token;
  if (state.me.role !== "admin") throw new Error("只有管理员可以访问凭据管理");
  $("#credentialUserLabel").textContent = `${session.user.display_name} · 管理员`;
  window.AppTheme?.apply(session.preferences?.theme || "system");
  window.AppTheme?.updateControls();
  window.lucide?.createIcons();
  await reload();
}

document.addEventListener("click", (event) => {
  const edit = event.target.closest("[data-edit-credential]");
  if (edit) openCredentialModal(state.credentials.find((item) => item.id === edit.dataset.editCredential));
  const rotate = event.target.closest("[data-rotate-credential]");
  if (rotate) openRotateModal(state.credentials.find((item) => item.id === rotate.dataset.rotateCredential));
  const test = event.target.closest("[data-test-credential]");
  if (test) api(`/api/credentials/${test.dataset.testCredential}/test`, { method: "POST" }).then(reload).then(() => notice("连接检测已进入后台。")).catch((error) => notice(error.message, "error"));
  const clear = event.target.closest("[data-clear-credential]");
  if (clear && confirm("确认移除页面保存的密钥并恢复环境变量回退？")) api(`/api/credentials/${clear.dataset.clearCredential}/secret`, { method: "DELETE" }).then(reload).then(() => notice("页面覆盖已移除。")).catch((error) => notice(error.message, "error"));
});

$("#credentialForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = { name: $("#credentialName").value.trim(), env_alias: $("#credentialEnvAlias").value.trim().toUpperCase() };
  if (!state.editing && $("#credentialSecret").value) payload.secret = $("#credentialSecret").value;
  try {
    await api(state.editing ? `/api/credentials/${state.editing.id}` : "/api/credentials", {
      method: state.editing ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    closeCredentialModal();
    await reload();
    notice("凭据已保存。");
  } catch (error) { notice(error.message, "error"); }
});

$("#rotateForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.rotating) return;
  try {
    await api(`/api/credentials/${state.rotating.id}/secret`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ secret: $("#rotateSecret").value }),
    });
    closeRotateModal();
    await reload();
    notice("新密钥已保存并立即生效，连接检测正在后台执行。");
  } catch (error) { notice(error.message, "error"); }
});

$("#credentialSearch").addEventListener("input", renderTable);
$("#refreshCredentials").addEventListener("click", () => reload().catch((error) => notice(error.message, "error")));
$("#newCredential").addEventListener("click", () => openCredentialModal());
$("#closeCredentialModal").addEventListener("click", closeCredentialModal);
$("#cancelCredentialModal").addEventListener("click", closeCredentialModal);
$("#closeRotateModal").addEventListener("click", closeRotateModal);
$("#cancelRotateModal").addEventListener("click", closeRotateModal);
$("#credentialLogout").addEventListener("click", async () => { await api("/api/auth/logout", { method: "POST" }); window.location.replace("/login"); });
$("#credentialThemeToggle").addEventListener("click", () => window.AppTheme.toggle(state.csrf).catch((error) => notice(error.message, "error")));

bootstrap().catch((error) => notice(error.message, "error"));
