(function () {
  const storageKey = "app-trading-theme";
  const validThemes = new Set(["system", "light", "dark"]);

  function effectiveTheme(preference) {
    if (preference === "light" || preference === "dark") return preference;
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }

  function apply(preference, persist = true) {
    const resolvedPreference = validThemes.has(preference) ? preference : "system";
    const resolvedTheme = effectiveTheme(resolvedPreference);
    document.documentElement.dataset.theme = resolvedTheme;
    document.documentElement.dataset.themePreference = resolvedPreference;
    document.documentElement.style.colorScheme = resolvedTheme;
    if (persist) localStorage.setItem(storageKey, resolvedPreference);
    document.dispatchEvent(new CustomEvent("app-theme-change", {
      detail: { preference: resolvedPreference, theme: resolvedTheme },
    }));
    updateControls();
    return resolvedPreference;
  }

  function updateControls() {
    const preference = document.documentElement.dataset.themePreference || "system";
    const theme = document.documentElement.dataset.theme || "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.dataset.themeValue = preference;
      button.title = theme === "dark" ? "切换到浅色主题" : "切换到深色主题";
      button.setAttribute("aria-label", button.title);
      const label = button.querySelector("[data-theme-label]");
      if (label) label.textContent = theme === "dark" ? "浅色" : "深色";
      const icon = button.querySelector("[data-lucide]");
      if (icon) icon.dataset.lucide = theme === "dark" ? "sun" : "moon";
    });
    if (window.lucide) window.lucide.createIcons();
  }

  async function save(preference, csrfToken) {
    apply(preference);
    if (!csrfToken) return preference;
    const response = await fetch("/api/auth/preferences", {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ theme: preference }),
    });
    if (!response.ok) throw new Error("主题偏好保存失败");
    const saved = await response.json();
    apply(saved.theme);
    return saved.theme;
  }

  function toggle(csrfToken) {
    const current = document.documentElement.dataset.theme || effectiveTheme("system");
    return save(current === "dark" ? "light" : "dark", csrfToken);
  }

  const cached = localStorage.getItem(storageKey) || "system";
  apply(cached, false);
  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
    if ((document.documentElement.dataset.themePreference || "system") === "system") apply("system", false);
  });

  window.AppTheme = { apply, save, toggle, updateControls };
})();
