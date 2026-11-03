(function () {
  "use strict";

  const validTabs = new Set(["channels", "routes", "invocations", "playground"]);
  const modelState = {
    initialized: false,
    loading: null,
    csrfToken: "",
    role: "",
    presets: [],
    channels: [],
    routes: [],
    invocations: [],
    summary: null,
    activeTab: "channels",
    editingChannel: null,
    editingRoute: null,
    messages: [],
    controller: null,
    running: false,
    noticeTimer: null,
  };

  const select = (selector, root = document) => root.querySelector(selector);
  const selectAll = (selector, root = document) => [...root.querySelectorAll(selector)];
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
  const translate = (source, params = {}) => window.AppI18n?.t(source, params) || Object.entries(params)
    .reduce((value, [key, replacement]) => value.replaceAll(`{${key}}`, String(replacement)), source);

  async function api(url, options = {}) {
    const headers = new Headers(options.headers || {});
    const method = (options.method || "GET").toUpperCase();
    if (!["GET", "HEAD", "OPTIONS"].includes(method) && modelState.csrfToken) {
      headers.set("X-CSRF-Token", modelState.csrfToken);
    }
    const response = await fetch(url, { ...options, headers });
    if (response.status === 401) {
      window.location.href = "/login";
      throw new Error(translate("登录状态已失效"));
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(responseErrorDetail(body.detail));
    return body;
  }

  function refreshIcons() {
    window.lucide?.createIcons();
  }

  function showNotice(message, type = "") {
    const node = select("#modelNotice");
    if (!node) return;
    window.clearTimeout(modelState.noticeTimer);
    node.textContent = message;
    node.className = `model-notice ${type}`;
    if (message) {
      modelState.noticeTimer = window.setTimeout(() => {
        if (node.textContent === message) node.textContent = "";
      }, 5000);
    }
  }

  function formatTime(value) {
    if (!value) return "--";
    return new Date(value).toLocaleString(window.AppI18n?.getLocale?.() || "zh-CN", { hour12: false });
  }

  function renderMetrics() {
    const item = modelState.summary || {};
    select("#modelMetrics").innerHTML = `
      <article><span>${translate("渠道")}</span><strong>${item.healthy_channels || 0}/${item.channels || 0}</strong></article>
      <article><span>${translate("逻辑模型")}</span><strong>${item.routes || 0}</strong></article>
      <article><span>${translate("24h 成功率")}</span><strong>${item.success_rate == null ? "--" : `${item.success_rate}%`}</strong></article>
      <article><span>${translate("平均延迟")}</span><strong>${item.average_latency_ms == null ? "--" : `${item.average_latency_ms}ms`}</strong></article>
      <article><span>${translate("24h Token")}</span><strong>${Number(item.total_tokens || 0).toLocaleString()}</strong></article>
      <article><span>${translate("近期失败")}</span><strong>${item.recent_failures || 0}</strong></article>`;
  }

  function statusText(status) {
    return ({ healthy: "健康", degraded: "降级", unavailable: "不可用", unknown: "未检测", disabled: "已停用", succeeded: "成功", failed: "失败" })[status] || status;
  }

  function renderChannels() {
    const query = select("#modelChannelSearch").value.trim().toLowerCase();
    const provider = select("#modelChannelProvider").value;
    const status = select("#modelChannelStatus").value;
    const rows = modelState.channels.filter((item) => (
      (!query || `${item.name} ${item.provider_type} ${item.models.join(" ")}`.toLowerCase().includes(query))
      && (!provider || item.provider_type === provider)
      && (!status || item.health_status === status)
    ));
    select("#modelChannelRows").innerHTML = rows.length ? rows.map((item) => `
      <tr>
        <td><input type="checkbox" data-model-channel-check value="${escapeHtml(item.id)}" aria-label="${escapeHtml(item.name)}" /></td>
        <td><strong>${escapeHtml(item.name)}</strong><small title="${escapeHtml(item.base_url)}">${escapeHtml(item.base_url)}</small></td>
        <td>${escapeHtml(modelState.presets.find((preset) => preset.id === item.provider_type)?.name || item.provider_type)}<small>${escapeHtml(item.protocol)}</small></td>
        <td><div class="model-chip-list">${item.models.slice(0, 3).map((model) => `<span class="model-chip">${escapeHtml(model)}</span>`).join("")}${item.models.length > 3 ? `<span class="model-chip">+${item.models.length - 3}</span>` : ""}</div></td>
        <td><span class="model-status ${escapeHtml(item.health_status)}">${escapeHtml(translate(statusText(item.health_status)))}</span><small title="${escapeHtml(item.last_error || "")}">${escapeHtml(item.last_error || formatTime(item.last_tested_at))}</small></td>
        <td>${item.response_time_ms == null ? "--" : `${item.response_time_ms}ms`}</td>
        <td><span class="model-status ${item.secret_configured || !item.api_key_env ? "healthy" : "unavailable"}">${escapeHtml(translate(item.secret_configured ? item.credential_origin : item.api_key_env ? "缺失" : "无需密钥"))}</span></td>
        <td><div class="model-row-actions"><button class="text-button" type="button" data-model-test-channel="${escapeHtml(item.id)}">${translate("测试")}</button><button class="text-button" type="button" data-model-sync-channel="${escapeHtml(item.id)}">${translate("同步")}</button><button class="text-button" type="button" data-model-edit-channel="${escapeHtml(item.id)}">${translate("编辑")}</button><button class="danger-button" type="button" data-model-delete-channel="${escapeHtml(item.id)}">${translate("删除")}</button></div></td>
      </tr>`).join("") : `<tr><td colspan="8" class="model-empty">${translate("没有匹配的模型渠道。")}</td></tr>`;
  }

  function renderRoutes() {
    const query = select("#modelRouteSearch").value.trim().toLowerCase();
    const rows = modelState.routes.filter((item) => !query || `${item.name} ${item.alias}`.toLowerCase().includes(query));
    select("#modelRouteRows").innerHTML = rows.length ? rows.map((item) => `
      <tr>
        <td><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.alias)} · ${escapeHtml(item.description)}</small></td>
        <td><div class="model-chip-list">${item.targets.map((target) => `<span class="model-chip">P${target.priority} · ${escapeHtml(target.channel_name)} / ${escapeHtml(target.model)}</span>`).join("")}</div></td>
        <td>${translate("温度")} ${item.temperature}<small>${translate("最大输出")} ${item.max_tokens} Token</small></td>
        <td><span class="model-status ${item.enabled ? "healthy" : "disabled"}">${translate(item.enabled ? "启用" : "停用")}</span></td>
        <td><div class="model-row-actions"><button class="text-button" type="button" data-model-chat-route="${escapeHtml(item.id)}">${translate("聊天测试")}</button><button class="text-button" type="button" data-model-edit-route="${escapeHtml(item.id)}">${translate("编辑")}</button><button class="danger-button" type="button" data-model-delete-route="${escapeHtml(item.id)}">${translate("删除")}</button></div></td>
      </tr>`).join("") : `<tr><td colspan="5" class="model-empty">${translate("尚未配置逻辑模型。")}</td></tr>`;
  }

  function renderInvocations() {
    const status = select("#modelInvocationStatus").value;
    const rows = modelState.invocations.filter((item) => !status || item.status === status);
    select("#modelInvocationRows").innerHTML = rows.length ? rows.map((item) => `
      <tr><td>${formatTime(item.created_at)}<small>${escapeHtml(item.request_id)}</small></td><td>${escapeHtml(item.source)}</td><td>${escapeHtml(item.channel_name || "--")}<small>${escapeHtml(item.model || "--")}</small></td><td><span class="model-status ${escapeHtml(item.status)}">${escapeHtml(translate(statusText(item.status)))}</span></td><td>${item.prompt_tokens} / ${item.completion_tokens}</td><td>${item.latency_ms}ms</td><td><small title="${escapeHtml(item.error || "")}">${escapeHtml(item.error || "--")}</small></td></tr>`).join("") : `<tr><td colspan="7" class="model-empty">${translate("暂无调用记录。")}</td></tr>`;
  }

  function renderAll() {
    renderMetrics();
    renderChannels();
    renderRoutes();
    renderInvocations();
    renderPlaygroundOptions();
    refreshIcons();
  }

  function populatePresetFilters() {
    const options = modelState.presets.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("");
    select("#modelChannelType").innerHTML = options;
    select("#modelChannelProvider").innerHTML = `<option value="">${translate("全部供应商")}</option>${options}`;
  }

  async function reloadAll() {
    const [summary, channels, routes, invocations] = await Promise.all([
      api("/api/model-gateway/summary"),
      api("/api/model-channels"),
      api("/api/model-routes"),
      api("/api/model-invocations?limit=150"),
    ]);
    Object.assign(modelState, { summary, channels, routes, invocations });
    renderAll();
  }

  async function refresh() {
    if (modelState.activeTab === "invocations") {
      [modelState.summary, modelState.invocations] = await Promise.all([api("/api/model-gateway/summary"), api("/api/model-invocations?limit=150")]);
      renderMetrics();
      renderInvocations();
    } else if (modelState.activeTab === "playground") {
      [modelState.channels, modelState.routes] = await Promise.all([api("/api/model-channels"), api("/api/model-routes")]);
      renderPlaygroundOptions();
    } else {
      await reloadAll();
    }
    showNotice(translate("模型中心已刷新。"));
  }

  function updateTabUrl(tab, mode = "push") {
    const params = new URLSearchParams(window.location.search);
    params.set("tab", tab);
    if (tab !== "playground" && tab !== "routes") params.delete("route");
    const url = `/admin/models?${params.toString()}`;
    if (`${window.location.pathname}${window.location.search}` === url) return;
    history[mode === "replace" ? "replaceState" : "pushState"]({ view: "models", tab }, "", url);
  }

  function activateTab(tab, { updateHistory = true, historyMode = "push" } = {}) {
    const nextTab = validTabs.has(tab) ? tab : "channels";
    modelState.activeTab = nextTab;
    selectAll("[data-model-tab]").forEach((button) => {
      const active = button.dataset.modelTab === nextTab;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    selectAll("[data-model-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.modelPanel === nextTab));
    select("#modelMetrics").hidden = nextTab === "playground";
    if (updateHistory) updateTabUrl(nextTab, historyMode);
    refreshIcons();
  }

  function applyChannelPreset() {
    const preset = modelState.presets.find((item) => item.id === select("#modelChannelType").value);
    if (!preset || modelState.editingChannel) return;
    select("#modelChannelName").value = preset.name;
    select("#modelChannelProtocol").value = preset.protocol;
    select("#modelChannelUrl").value = preset.base_url;
    select("#modelChannelEnv").value = preset.api_key_env;
    select("#modelChannelModels").value = preset.models.join("\n");
  }

  function openChannel(item = null) {
    modelState.editingChannel = item;
    select("#modelChannelModalTitle").textContent = translate(item ? "编辑渠道" : "新增渠道");
    select("#modelChannelType").value = item?.provider_type || modelState.presets[0]?.id || "openai";
    select("#modelChannelName").value = item?.name || "";
    select("#modelChannelProtocol").value = item?.protocol || "openai_chat";
    select("#modelChannelUrl").value = item?.base_url || "";
    select("#modelChannelEnv").value = item?.api_key_env || "";
    select("#modelChannelKey").value = "";
    select("#modelChannelTimeout").value = item?.timeout_seconds || 30;
    select("#modelChannelModels").value = (item?.models || []).join("\n");
    select("#modelChannelConfig").value = JSON.stringify(item?.config || {}, null, 2);
    select("#modelChannelEnabled").checked = item?.enabled ?? true;
    showChannelError();
    if (!item) applyChannelPreset();
    select("#modelChannelModal").hidden = false;
  }

  function closeChannel() {
    select("#modelChannelModal").hidden = true;
    modelState.editingChannel = null;
    showChannelError();
    setChannelSaving(false);
  }

  function showChannelError(message = "") {
    const node = select("#modelChannelFormError");
    node.textContent = message;
    node.hidden = !message;
  }

  function setChannelSaving(saving) {
    const button = select("#modelChannelSave");
    button.disabled = saving;
    button.textContent = translate(saving ? "保存中..." : "保存渠道");
  }

  function addTargetRow(target = {}) {
    const row = document.createElement("div");
    row.className = "model-target-row";
    row.innerHTML = `
      <label><span>${translate("渠道")}</span><select data-model-target-channel>${modelState.channels.map((channel) => `<option value="${escapeHtml(channel.id)}" ${channel.id === target.channel_id ? "selected" : ""}>${escapeHtml(channel.name)}</option>`).join("")}</select></label>
      <label><span>${translate("上游模型")}</span><input data-model-target-name value="${escapeHtml(target.model || "")}" required /></label>
      <label><span>${translate("优先级")}</span><input data-model-target-priority type="number" min="-1000" max="1000" value="${target.priority ?? 0}" /></label>
      <label><span>${translate("权重")}</span><input data-model-target-weight type="number" min="0" max="10000" value="${target.weight ?? 100}" /></label>
      <button class="icon-button" type="button" data-model-remove-target title="${translate("删除目标")}"><i data-lucide="trash-2"></i></button>`;
    select("#modelTargetList").append(row);
    refreshIcons();
  }

  function openRoute(item = null) {
    if (!modelState.channels.length) {
      showNotice(translate("请先创建模型渠道。"), "error");
      return;
    }
    modelState.editingRoute = item;
    select("#modelRouteModalTitle").textContent = translate(item ? "编辑逻辑模型" : "新增逻辑模型");
    select("#modelRouteName").value = item?.name || "";
    select("#modelRouteAlias").value = item?.alias || "";
    select("#modelRouteDescription").value = item?.description || "";
    select("#modelRouteTemperature").value = item?.temperature ?? 0.2;
    select("#modelRouteMaxTokens").value = item?.max_tokens ?? 1200;
    select("#modelRouteEnabled").checked = item?.enabled ?? true;
    select("#modelTargetList").innerHTML = "";
    (item?.targets?.length ? item.targets : [{}]).forEach(addTargetRow);
    select("#modelRouteModal").hidden = false;
  }

  function closeRoute() {
    select("#modelRouteModal").hidden = true;
    modelState.editingRoute = null;
  }

  function selectedChannelIds() {
    return selectAll("[data-model-channel-check]:checked").map((item) => item.value);
  }

  async function batchChannels(enabled) {
    const ids = selectedChannelIds();
    if (!ids.length) return showNotice(translate("请先选择渠道。"), "error");
    await api("/api/model-channels/batch-status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids, enabled }),
    });
    await reloadAll();
    showNotice(translate(enabled ? "已启用 {count} 个渠道。" : "已停用 {count} 个渠道。", { count: ids.length }));
  }

  function renderPlaygroundOptions(preferredRouteId = "") {
    const routeSelect = select("#modelPlaygroundRoute");
    const current = preferredRouteId || routeSelect.value;
    routeSelect.innerHTML = modelState.routes.length
      ? modelState.routes.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${escapeHtml(item.alias)}</option>`).join("")
      : `<option value="">${translate("暂无逻辑模型")}</option>`;
    if (current && modelState.routes.some((item) => item.id === current)) routeSelect.value = current;
    updatePlaygroundChannels();
  }

  function updatePlaygroundChannels() {
    const route = modelState.routes.find((item) => item.id === select("#modelPlaygroundRoute").value);
    const channelSelect = select("#modelPlaygroundChannel");
    const current = channelSelect.value;
    const targets = route?.targets || [];
    channelSelect.innerHTML = `<option value="">${translate("自动路由")}</option>${targets.map((target) => `<option value="${escapeHtml(target.channel_id)}">${escapeHtml(target.channel_name)} · ${escapeHtml(target.model)}</option>`).join("")}`;
    if (targets.some((item) => item.channel_id === current)) channelSelect.value = current;
    if (route) {
      select("#modelTemperature").value = route.temperature;
      select("#modelTemperatureValue").textContent = route.temperature;
      select("#modelMaxTokens").value = route.max_tokens;
    }
  }

  function renderMessages() {
    const node = select("#modelChatMessages");
    if (!modelState.messages.length) {
      node.innerHTML = `<div class="model-chat-empty"><div><i data-lucide="message-square"></i><p>${translate("选择逻辑模型并发送消息，验证真实路由与响应。")}</p></div></div>`;
    } else {
      node.innerHTML = modelState.messages.map((item) => `
        <article class="model-message ${item.role} ${item.pending ? "pending" : ""}"><div class="model-message-avatar">${item.role === "assistant" ? "AI" : translate("你")}</div><div class="model-message-body"><strong>${translate(item.role === "assistant" ? "模型" : "你")}</strong><div class="model-message-content">${escapeHtml(item.content)}</div></div></article>`).join("");
      node.scrollTop = node.scrollHeight;
    }
    refreshIcons();
  }

  function setRunning(running) {
    modelState.running = running;
    select("#modelSendChat").disabled = running;
    select("#modelStopChat").hidden = !running;
    select("#modelChatStatus").textContent = translate(running ? "正在生成..." : "就绪");
  }

  function handleStreamEvent(event, assistant) {
    const metadata = select("#modelRunMeta");
    if (event.type === "meta") {
      metadata.innerHTML = `<span>${translate("渠道")} ${escapeHtml(event.channel_name)}</span><span>${translate("模型")} ${escapeHtml(event.model)}</span><span>${translate("请求")} ${escapeHtml(event.request_id)}</span>`;
    } else if (event.type === "delta") {
      assistant.content += event.text || "";
      assistant.pending = false;
      renderMessages();
    } else if (event.type === "done") {
      metadata.innerHTML += `<span>${translate("耗时")} ${event.latency_ms}ms</span><span>Token ${event.prompt_tokens}/${event.completion_tokens}</span>`;
    } else if (event.type === "error") {
      assistant.content += `${assistant.content ? "\n\n" : ""}[${translate("错误")}] ${event.message}`;
      assistant.pending = false;
      renderMessages();
    }
  }

  function chatRequestMessages() {
    return modelState.messages
      .filter((item) => !item.pending && typeof item.content === "string" && item.content.trim())
      .map(({ role, content }) => ({ role, content }));
  }

  function responseErrorDetail(detail) {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((item) => {
      const field = Array.isArray(item?.loc) ? item.loc.filter((part) => part !== "body").join(".") : "";
      return `${field ? `${field}: ` : ""}${item?.msg || JSON.stringify(item)}`;
    }).join("; ");
    return translate("请求失败");
  }

  async function sendChat() {
    const content = select("#modelChatInput").value.trim();
    const routeId = select("#modelPlaygroundRoute").value;
    if (!content || !routeId || modelState.running) return;
    modelState.messages.push({ role: "user", content });
    const assistant = { role: "assistant", content: "", pending: true };
    modelState.messages.push(assistant);
    select("#modelChatInput").value = "";
    select("#modelRunMeta").textContent = "";
    renderMessages();
    setRunning(true);
    modelState.controller = new AbortController();
    try {
      const response = await fetch("/api/model-playground/chat", {
        method: "POST",
        signal: modelState.controller.signal,
        headers: { "Content-Type": "application/json", "X-CSRF-Token": modelState.csrfToken },
        body: JSON.stringify({
          route_id: routeId,
          channel_id: select("#modelPlaygroundChannel").value || null,
          messages: chatRequestMessages(),
          system_prompt: select("#modelSystemPrompt").value,
          temperature: Number(select("#modelTemperature").value),
          max_tokens: Number(select("#modelMaxTokens").value),
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(responseErrorDetail(body.detail));
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop();
        blocks.forEach((block) => {
          const dataLine = block.split("\n").find((line) => line.startsWith("data:"));
          if (dataLine) handleStreamEvent(JSON.parse(dataLine.slice(5).trim()), assistant);
        });
      }
    } catch (error) {
      if (error.name !== "AbortError") assistant.content += `${assistant.content ? "\n\n" : ""}[${translate("错误")}] ${error.message}`;
    } finally {
      assistant.pending = false;
      if (!assistant.content.trim()) {
        const assistantIndex = modelState.messages.indexOf(assistant);
        if (assistantIndex >= 0) modelState.messages.splice(assistantIndex, 1);
      }
      setRunning(false);
      modelState.controller = null;
      renderMessages();
    }
  }

  function clearChat() {
    modelState.controller?.abort();
    modelState.messages = [];
    select("#modelRunMeta").textContent = "";
    renderMessages();
  }

  function bindEvents() {
    const root = select("#modelCenterView");
    root.addEventListener("click", async (event) => {
      const tab = event.target.closest("[data-model-tab]");
      if (tab) return activateTab(tab.dataset.modelTab);
      if (event.target.closest("[data-close-model-channel]")) return closeChannel();
      if (event.target.closest("[data-close-model-route]")) return closeRoute();
      const removeTarget = event.target.closest("[data-model-remove-target]");
      if (removeTarget) return removeTarget.closest(".model-target-row").remove();
      const editChannel = event.target.closest("[data-model-edit-channel]");
      if (editChannel) return openChannel(modelState.channels.find((item) => item.id === editChannel.dataset.modelEditChannel));
      const editRoute = event.target.closest("[data-model-edit-route]");
      if (editRoute) return openRoute(modelState.routes.find((item) => item.id === editRoute.dataset.modelEditRoute));
      const chatRoute = event.target.closest("[data-model-chat-route]");
      if (chatRoute) {
        activateTab("playground");
        renderPlaygroundOptions(chatRoute.dataset.modelChatRoute);
        const params = new URLSearchParams(location.search);
        params.set("route", chatRoute.dataset.modelChatRoute);
        history.replaceState({ view: "models", tab: "playground" }, "", `/admin/models?${params}`);
        return;
      }
      const test = event.target.closest("[data-model-test-channel]");
      if (test) {
        showNotice(translate("正在测试渠道..."));
        try {
          const result = await api(`/api/model-channels/${test.dataset.modelTestChannel}/test`, { method: "POST" });
          await reloadAll();
          showNotice(translate("连接成功，耗时 {latency}ms。", { latency: result.latency_ms }));
        } catch (error) {
          await reloadAll().catch(() => {});
          showNotice(error.message, "error");
        }
        return;
      }
      const sync = event.target.closest("[data-model-sync-channel]");
      if (sync) {
        try {
          const channel = await api(`/api/model-channels/${sync.dataset.modelSyncChannel}/sync-models`, { method: "POST" });
          await reloadAll();
          showNotice(translate("已同步 {count} 个模型。", { count: channel.models.length }));
        } catch (error) { showNotice(error.message, "error"); }
        return;
      }
      const deleteChannel = event.target.closest("[data-model-delete-channel]");
      if (deleteChannel && window.confirm(translate("确认删除该模型渠道？"))) {
        try {
          await api(`/api/model-channels/${deleteChannel.dataset.modelDeleteChannel}`, { method: "DELETE" });
          await reloadAll();
          showNotice(translate("渠道已删除。"));
        } catch (error) { showNotice(error.message, "error"); }
        return;
      }
      const deleteRoute = event.target.closest("[data-model-delete-route]");
      if (deleteRoute && window.confirm(translate("确认删除该逻辑模型？"))) {
        try {
          await api(`/api/model-routes/${deleteRoute.dataset.modelDeleteRoute}`, { method: "DELETE" });
          await reloadAll();
          showNotice(translate("逻辑模型已删除。"));
        } catch (error) { showNotice(error.message, "error"); }
      }
    });

    select("#modelChannelForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      showChannelError();
      setChannelSaving(true);
      try {
        const payload = {
          name: select("#modelChannelName").value.trim(),
          provider_type: select("#modelChannelType").value,
          protocol: select("#modelChannelProtocol").value,
          base_url: select("#modelChannelUrl").value.trim(),
          api_key_env: select("#modelChannelEnv").value.trim(),
          timeout_seconds: Number(select("#modelChannelTimeout").value),
          models: select("#modelChannelModels").value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean),
          config: JSON.parse(select("#modelChannelConfig").value || "{}"),
          enabled: select("#modelChannelEnabled").checked,
        };
        if (select("#modelChannelKey").value) payload.api_key = select("#modelChannelKey").value;
        await api(modelState.editingChannel ? `/api/model-channels/${modelState.editingChannel.id}` : "/api/model-channels", {
          method: modelState.editingChannel ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        closeChannel();
        await reloadAll();
        showNotice(translate("模型渠道已保存。"));
      } catch (error) {
        showChannelError(error instanceof SyntaxError ? translate("高级配置 JSON 格式不正确。") : error.message);
      } finally {
        setChannelSaving(false);
      }
    });

    select("#modelRouteForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const targets = selectAll("#modelTargetList .model-target-row").map((row) => ({
          channel_id: select("[data-model-target-channel]", row).value,
          model: select("[data-model-target-name]", row).value.trim(),
          priority: Number(select("[data-model-target-priority]", row).value),
          weight: Number(select("[data-model-target-weight]", row).value),
          enabled: true,
        }));
        if (!targets.length || targets.some((item) => !item.model)) throw new Error(translate("每个路由目标都必须填写模型。"));
        const payload = {
          name: select("#modelRouteName").value.trim(), alias: select("#modelRouteAlias").value.trim(), description: select("#modelRouteDescription").value.trim(),
          temperature: Number(select("#modelRouteTemperature").value), max_tokens: Number(select("#modelRouteMaxTokens").value), enabled: select("#modelRouteEnabled").checked, targets,
        };
        await api(modelState.editingRoute ? `/api/model-routes/${modelState.editingRoute.id}` : "/api/model-routes", {
          method: modelState.editingRoute ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
        });
        closeRoute();
        await reloadAll();
        showNotice(translate("逻辑模型已保存。"));
      } catch (error) { showNotice(error.message, "error"); }
    });

    select("#modelNewChannel").addEventListener("click", () => openChannel());
    select("#modelNewRoute").addEventListener("click", () => openRoute());
    select("#modelAddTarget").addEventListener("click", () => addTargetRow());
    select("#modelChannelType").addEventListener("change", applyChannelPreset);
    select("#modelChannelSearch").addEventListener("input", renderChannels);
    select("#modelChannelProvider").addEventListener("change", renderChannels);
    select("#modelChannelStatus").addEventListener("change", renderChannels);
    select("#modelRouteSearch").addEventListener("input", renderRoutes);
    select("#modelInvocationStatus").addEventListener("change", renderInvocations);
    select("#modelBatchEnable").addEventListener("click", () => batchChannels(true).catch((error) => showNotice(error.message, "error")));
    select("#modelBatchDisable").addEventListener("click", () => batchChannels(false).catch((error) => showNotice(error.message, "error")));
    select("#modelSelectAllChannels").addEventListener("change", (event) => selectAll("[data-model-channel-check]").forEach((item) => { item.checked = event.target.checked; }));
    select("#modelPlaygroundRoute").addEventListener("change", () => {
      updatePlaygroundChannels();
      const params = new URLSearchParams(location.search);
      if (select("#modelPlaygroundRoute").value) params.set("route", select("#modelPlaygroundRoute").value);
      history.replaceState({ view: "models", tab: "playground" }, "", `/admin/models?${params}`);
    });
    select("#modelTemperature").addEventListener("input", (event) => { select("#modelTemperatureValue").textContent = event.target.value; });
    select("#modelPlaygroundSettingsToggle").addEventListener("click", () => select("#modelPlaygroundSettings").classList.toggle("open"));
    select("#modelClearChat").addEventListener("click", clearChat);
    select("#modelStopChat").addEventListener("click", () => modelState.controller?.abort());
    select("#modelChatForm").addEventListener("submit", (event) => { event.preventDefault(); sendChat(); });
    select("#modelChatInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); sendChat(); }
    });
    document.addEventListener("app-language-change", () => {
      if (!modelState.initialized) return;
      renderAll();
      renderMessages();
    });
  }

  async function init({ csrfToken = "", role = "" } = {}) {
    modelState.csrfToken = csrfToken;
    modelState.role = role;
    if (role && role !== "admin") {
      window.location.replace("/dashboard");
      return;
    }
    const params = new URLSearchParams(location.search);
    let tab = validTabs.has(params.get("tab")) ? params.get("tab") : "channels";
    const requestedRoute = params.get("route") || "";
    if (requestedRoute && tab === "channels") tab = "routes";
    activateTab(tab, { updateHistory: true, historyMode: "replace" });
    if (modelState.initialized) {
      if (requestedRoute && tab === "playground") renderPlaygroundOptions(requestedRoute);
      return;
    }
    if (modelState.loading) return modelState.loading;
    modelState.loading = (async () => {
      modelState.presets = await api("/api/model-provider-presets");
      populatePresetFilters();
      bindEvents();
      await reloadAll();
      modelState.initialized = true;
      if (requestedRoute && tab === "playground") renderPlaygroundOptions(requestedRoute);
      if (requestedRoute && tab === "routes") {
        const route = modelState.routes.find((item) => item.id === requestedRoute);
        if (route) openRoute(route);
      }
      renderMessages();
    })();
    try { await modelState.loading; } finally { modelState.loading = null; }
  }

  window.ModelCenter = { init, refresh, activateTab };
})();
