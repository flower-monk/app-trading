(function () {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  const escapeHtml = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const t = (text) => window.AppI18n?.t(text) || text;
  const formatTime = (value) => new Date(value).toLocaleString(window.AppI18n?.getLocale?.() || "zh-CN", { hour12: false });

  function render(data) {
    $("#adminOverviewTime").textContent = `${t("更新时间")} ${formatTime(data.generated_at)}`;
    $("#adminOverviewMetrics").innerHTML = data.metrics.map((item) => `<a class="admin-metric-link ${item.status}" href="${item.href}"><span>${escapeHtml(t(item.label))}</span><strong>${escapeHtml(item.value)}</strong><small>${escapeHtml(t(item.detail))}</small></a>`).join("");
    $("#adminAttentionList").innerHTML = data.attention.length ? data.attention.map((item) => `<a class="admin-overview-item ${item.severity}" href="${item.href}"><i></i><span><strong>${escapeHtml(t(item.title))}</strong><small>${escapeHtml(t(item.detail))}</small></span><i data-lucide="chevron-right"></i></a>`).join("") : `<p class="admin-overview-empty">${t("当前没有待处理事项。")}</p>`;
    $("#adminActivityList").innerHTML = data.activity.length ? data.activity.map((item) => `<div class="admin-overview-item"><i></i><span><strong>${escapeHtml(item.actor)} · ${escapeHtml(item.action)}</strong><small>${escapeHtml(item.resource)} · ${formatTime(item.created_at)}</small></span></div>`).join("") : `<p class="admin-overview-empty">${t("暂无管理活动。")}</p>`;
    window.lucide?.createIcons();
  }

  async function load() {
    const notice = $("#adminOverviewNotice");
    notice.textContent = "";
    try { render(await window.AdminCenter.api("/api/admin/overview")); }
    catch (error) { notice.textContent = error.message; notice.className = "resource-notice error"; }
  }

  $("#refreshAdminOverview").addEventListener("click", load);
  document.addEventListener("app-language-change", load);
  window.AdminCenter.ready.then(load).catch(() => {});
})();
