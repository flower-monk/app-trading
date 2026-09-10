const authState = { setup: false };

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "请求失败");
  return payload;
}

function setMode(setup) {
  authState.setup = setup;
  document.querySelector("#displayNameField").hidden = !setup;
  document.querySelector("#authDisplayName").required = setup;
  document.querySelector("#authPassword").autocomplete = setup ? "new-password" : "current-password";
  document.querySelector("#authEyebrow").textContent = setup ? "First-time Setup" : "Secure Workspace";
  document.querySelector("#authTitle").textContent = setup ? "初始化管理员" : "登录工作台";
  document.querySelector("#authDescription").textContent = setup
    ? "创建首个管理员账号。初始化完成后不开放公开注册。"
    : "使用系统账号进入行情和资源管理中心。";
  document.querySelector("#authSubmit").textContent = setup ? "创建并进入" : "登录";
}

async function bootstrapAuth() {
  const status = await requestJson("/api/auth/setup-status");
  if (status.required && window.location.pathname !== "/setup") {
    window.location.replace("/setup");
    return;
  }
  if (!status.required && window.location.pathname === "/setup") {
    window.location.replace("/login");
    return;
  }
  setMode(status.required);
}

document.querySelector("#authForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = document.querySelector("#authStatus");
  const button = document.querySelector("#authSubmit");
  status.textContent = authState.setup ? "正在创建管理员..." : "正在验证账号...";
  button.disabled = true;
  const body = {
    username: document.querySelector("#authUsername").value.trim(),
    password: document.querySelector("#authPassword").value,
  };
  if (authState.setup) body.display_name = document.querySelector("#authDisplayName").value.trim();
  try {
    const session = await requestJson(authState.setup ? "/api/auth/setup" : "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    window.AppTheme?.apply(session.preferences?.theme || "system");
    window.location.replace("/dashboard");
  } catch (error) {
    status.textContent = error.message;
    button.disabled = false;
  }
});

document.querySelector("#authThemeToggle").addEventListener("click", () => window.AppTheme.toggle().catch(() => {}));
window.AppTheme?.updateControls();
window.lucide?.createIcons();

bootstrapAuth().catch((error) => {
  document.querySelector("#authStatus").textContent = error.message;
});
