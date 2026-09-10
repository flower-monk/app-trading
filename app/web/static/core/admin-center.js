(function () {
  "use strict";

  const groups = [
    { label: "运营", items: [{ id: "overview", label: "运营概览", href: "/admin", icon: "layout-dashboard" }] },
    { label: "数据接入", items: [
      { id: "data-sources", label: "数据源", href: "/admin/data-sources", icon: "database-zap" },
      { id: "news-sources", label: "新闻源", href: "/admin/news-sources", icon: "rss" },
      { id: "credentials", label: "凭据", href: "/admin/credentials", icon: "key-round" },
    ] },
    { label: "智能自动化", items: [
      { id: "agents", label: "Agent", href: "/admin/agents", icon: "workflow" },
      { id: "impact-graph", label: "影响关系", href: "/admin/impact-graph", icon: "git-fork" },
      { id: "models", label: "模型服务", href: "/admin/models", icon: "boxes" },
      { id: "schedules", label: "定时任务", href: "/admin/schedules", icon: "calendar-clock" },
    ] },
    { label: "平台治理", items: [{ id: "resources", label: "资源与审计", href: "/admin/resources", icon: "shield-check" }] },
  ];

  const translate = (text) => window.AppI18n?.t(text) || text;

  function navMarkup(active) {
    return groups.map((group) => `
      <section class="admin-center-nav-group">
        <strong>${translate(group.label)}</strong>
        ${group.items.map((item) => `<a class="admin-center-nav-item ${item.id === active ? "active" : ""}" href="${item.href}" ${item.id === active ? 'aria-current="page"' : ""}>
          <i data-lucide="${item.icon}"></i><span>${translate(item.label)}</span>
        </a>`).join("")}
      </section>`).join("");
  }

  function mountSidebar() {
    const body = document.body;
    const active = body.dataset.adminSection || "overview";
    const sidebar = document.querySelector(".admin-sidebar, .agent-sidebar");
    if (!sidebar) return;
    const contextual = sidebar.querySelector(".agent-nav, .schedule-nav, .source-control-nav, .resource-nav");
    if (contextual && !document.querySelector(".admin-main > .admin-context-tabs, .agent-main > .admin-context-tabs")) {
      contextual.classList.add("admin-context-tabs");
      const main = document.querySelector(".admin-main, .agent-main");
      const topbar = main?.querySelector(".admin-topbar, .agent-topbar");
      if (main) main.insertBefore(contextual, topbar?.nextSibling || main.firstChild);
    }
    sidebar.classList.add("admin-sidebar", "admin-center-sidebar");
    sidebar.classList.remove("agent-sidebar");
    sidebar.innerHTML = `
      <a class="brand admin-brand" href="/admin"><div class="brand-mark">AT</div><div><strong>App Trading</strong><span>${translate("管理中心")}</span></div></a>
      <nav class="admin-center-nav" aria-label="${translate("管理中心导航")}">${navMarkup(active)}</nav>
      <a class="admin-center-return" href="/dashboard"><i data-lucide="arrow-left"></i><span>${translate("返回盯盘工作台")}</span></a>`;
  }

  async function api(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.method && !["GET", "HEAD", "OPTIONS"].includes(options.method.toUpperCase())) {
      headers.set("X-CSRF-Token", window.AdminCenter.session?.csrf_token || "");
    }
    const response = await fetch(url, { ...options, headers });
    if (response.status === 401) window.location.replace("/login");
    if (response.status === 403) window.location.replace("/dashboard");
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || translate("请求失败"));
    return payload;
  }

  async function loadSession() {
    const session = await api("/api/auth/me");
    if (session.user.role !== "admin") {
      window.location.replace("/dashboard");
      throw new Error("Administrator role required");
    }
    window.AdminCenter.session = session;
    document.querySelectorAll("[data-admin-user]").forEach((node) => { node.textContent = `${session.user.display_name} · ${translate("管理员")}`; });
    document.dispatchEvent(new CustomEvent("admin-center-ready", { detail: session }));
    return session;
  }

  function bindChrome() {
    document.querySelectorAll("[data-admin-menu-toggle]").forEach((button) => button.addEventListener("click", () => document.body.classList.toggle("admin-nav-open")));
    document.querySelectorAll("[data-admin-logout]").forEach((button) => button.addEventListener("click", async () => {
      await api("/api/auth/logout", { method: "POST" });
      window.location.replace("/login");
    }));
    document.querySelectorAll("[data-admin-theme]").forEach((button) => button.addEventListener("click", () => {
      window.AppTheme?.toggle(window.AdminCenter.session?.csrf_token || "").catch((error) => console.error(error));
    }));
    document.addEventListener("click", (event) => {
      if (document.body.classList.contains("admin-nav-open") && !event.target.closest(".admin-center-sidebar, [data-admin-menu-toggle]")) document.body.classList.remove("admin-nav-open");
    });
  }

  function normalizeChrome() {
    const shell = document.querySelector(".admin-shell, .agent-shell");
    const main = document.querySelector(".admin-main, .agent-main");
    const topbar = document.querySelector(".admin-topbar, .agent-topbar");
    shell?.classList.add("admin-shell", "admin-center-shell");
    main?.classList.add("admin-main", "admin-center-main");
    topbar?.classList.add("admin-topbar", "admin-center-topbar");
    const title = topbar?.querySelector("h1");
    if (topbar && title && !topbar.querySelector("[data-admin-menu-toggle]")) {
      const button = document.createElement("button");
      button.className = "icon-button admin-menu-toggle";
      button.type = "button";
      button.dataset.adminMenuToggle = "";
      button.title = translate("打开管理导航");
      button.innerHTML = '<i data-lucide="menu"></i>';
      title.parentElement?.prepend(button);
    }
  }

  window.AdminCenter = { api, session: null, ready: null };
  normalizeChrome();
  mountSidebar();
  bindChrome();
  window.AdminCenter.ready = loadSession().catch((error) => {
    if (!/Administrator role required/.test(error.message)) console.error(error);
    throw error;
  });
  window.lucide?.createIcons();
  document.addEventListener("app-language-change", () => { mountSidebar(); window.lucide?.createIcons(); });
})();
