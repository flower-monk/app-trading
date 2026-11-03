(function () {
  const rangeOrder = ["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "Max"];
  const minimumRangeByInterval = {
    "1m": "1D",
    "3m": "1D",
    "5m": "1D",
    "15m": "1D",
    "30m": "1D",
    "1h": "1D",
    "2h": "5D",
    "4h": "5D",
    "1d": "1M",
    "1w": "6M",
    "1mo": "3Y",
  };
  const intervalLabels = { "1d": "1日", "1w": "1周", "1mo": "1月" };
  const newsModeStorageKey = "open-trading.chart.news-mode";
  const storedNewsMode = localStorage.getItem(newsModeStorageKey);
  const chartState = {
    initialized: false,
    csrf: "",
    instruments: [],
    instrument: null,
    interval: "15m",
    range: "1M",
    indicators: [
      { id: "ema", instance_id: "default-ema-20", pane: "main", parameters: { length: 20 }, color: "#f3ba2f", visible: true },
      { id: "ema", instance_id: "default-ema-60", pane: "main", parameters: { length: 60 }, color: "#4aa3ff", visible: true },
      { id: "volume", instance_id: "default-volume", pane: "sub", parameters: {}, color: "#6b7280", visible: true },
      { id: "macd", instance_id: "default-macd", pane: "sub", parameters: {}, visible: true },
      { id: "rsi", instance_id: "default-rsi-14", pane: "sub", parameters: { length: 14 }, visible: true },
    ],
    catalog: [],
    layouts: [],
    activeLayoutId: "",
    bundle: null,
    chart: null,
    candleSeries: null,
    renderedSeries: new Map(),
    paneLegends: new Map(),
    markerPlugin: null,
    eventSeries: null,
    eventPaneIndex: null,
    selectedMarkerId: "",
    hoveredMarkerId: "",
    newsMode: ["key", "all", "off"].includes(storedNewsMode) ? storedNewsMode : "key",
    socket: null,
    resizeObserver: null,
    altWheelHandler: null,
    portfolioPositions: [],
    paperOrders: [],
    paperFills: [],
    positionPriceLines: [],
    positionZoneElements: [],
    executionMarkerPlugin: null,
    category: "all",
    instrumentSearchTimer: null,
    loadingOlder: false,
    applyTimer: null,
    pendingSelectionWarning: "",
    chartAbortController: null,
    seriesAbortController: null,
    requestGeneration: 0,
    seriesGeneration: 0,
  };
  const $ = (selector) => document.querySelector(selector);
  const t = (source, params = {}) => window.AppI18n?.t(source, params) || source;
  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  function indicatorInstanceId(identifier) {
    return `${identifier}-${crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
  }

  function normalizeIndicatorInstances(configs, prefix = "indicator") {
    return configs.map((config, index) => ({
      ...config,
      instance_id: config.instance_id || `${prefix}-${index}-${config.id}`,
    }));
  }

  async function api(url, options = {}) {
    const headers = new Headers(options.headers || {});
    const method = (options.method || "GET").toUpperCase();
    if (!["GET", "HEAD", "OPTIONS"].includes(method) && chartState.csrf) {
      headers.set("X-CSRF-Token", chartState.csrf);
    }
    const response = await fetch(url, { ...options, headers });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "图表请求失败");
    return payload;
  }

  function chartColors() {
    const style = getComputedStyle(document.documentElement);
    const color = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
    return {
      background: color("--app-chart", "#131820"),
      text: color("--app-text-muted", "#8b95a7"),
      line: color("--app-border", "#252b35"),
      up: color("--app-green", "#22ab94"),
      down: color("--app-red", "#f23645"),
    };
  }

  function destroyChart() {
    clearPositionOverlays();
    chartState.markerPlugin?.detach?.();
    chartState.markerPlugin = null;
    chartState.resizeObserver?.disconnect();
    chartState.resizeObserver = null;
    if (chartState.altWheelHandler) $("#priceChart")?.removeEventListener("wheel", chartState.altWheelHandler);
    chartState.altWheelHandler = null;
    if (chartState.chart) chartState.chart.remove();
    chartState.chart = null;
    chartState.candleSeries = null;
    chartState.eventSeries = null;
    chartState.eventPaneIndex = null;
    chartState.hoveredMarkerId = "";
    chartState.renderedSeries.clear();
    chartState.paneLegends.clear();
  }

  function clearPositionOverlays() {
    if (chartState.candleSeries) {
      chartState.positionPriceLines.forEach((line) => {
        try { chartState.candleSeries.removePriceLine(line); } catch (_) { /* chart is being replaced */ }
      });
    }
    chartState.positionPriceLines = [];
    chartState.executionMarkerPlugin?.detach?.();
    chartState.executionMarkerPlugin = null;
    chartState.positionZoneElements.forEach((element) => element.remove());
    chartState.positionZoneElements = [];
  }

  function aggregatePortfolioPositions() {
    if (!chartState.instrument) return [];
    const matching = chartState.portfolioPositions.filter((item) => (
      item.instrument_id === chartState.instrument.id
      || (!item.instrument_id && item.symbol === chartState.instrument.symbol)
    ));
    return ["long", "short"].map((side) => {
      const rows = matching.filter((item) => item.side === side);
      const quantity = rows.reduce((sum, item) => sum + Number(item.base_quantity || item.quantity || 0), 0);
      if (!rows.length || quantity <= 0) return null;
      const averagePrice = rows.reduce((sum, item) => (
        sum + Number(item.base_quantity || item.quantity || 0) * Number(item.average_price)
      ), 0) / quantity;
      const sameUnit = rows.every((item) => item.quantity_unit === rows[0].quantity_unit);
      const displayQuantity = sameUnit
        ? rows.reduce((sum, item) => sum + Number(item.quantity), 0)
        : quantity;
      return { side, rows, quantity, displayQuantity, unitLabel: sameUnit ? rows[0].unit_label : "基础单位", averagePrice };
    }).filter(Boolean);
  }

  function positionNumber(value) {
    return Number(value).toLocaleString(window.AppI18n?.getLocale() || "zh-CN", { maximumFractionDigits: 4 });
  }

  function positionOverlayGroups() {
    const currentPrice = Number(chartState.bundle?.candles?.at(-1)?.close);
    if (!Number.isFinite(currentPrice) || !chartState.candleSeries) return [];
    return aggregatePortfolioPositions().map((group) => {
      const pnlPercent = (group.side === "long" ? currentPrice - group.averagePrice : group.averagePrice - currentPrice)
        / group.averagePrice * 100;
      return { ...group, currentPrice, pnlPercent, profitable: pnlPercent >= 0 };
    });
  }

  function updatePositionZones() {
    const target = $("#priceChart");
    const groups = positionOverlayGroups();
    chartState.positionZoneElements.forEach((element, index) => {
      const group = groups[index];
      if (!group) {
        element.hidden = true;
        return;
      }
      const costY = chartState.candleSeries.priceToCoordinate(group.averagePrice);
      const currentY = chartState.candleSeries.priceToCoordinate(group.currentPrice);
      if (!Number.isFinite(costY) || !Number.isFinite(currentY)) {
        element.hidden = true;
        return;
      }
      element.hidden = false;
      element.style.top = `${Math.min(costY, currentY)}px`;
      element.style.height = `${Math.max(2, Math.abs(costY - currentY))}px`;
      element.style.left = `${Math.max(0, target.clientWidth * 0.72)}px`;
      element.style.right = "64px";
    });
  }

  function renderPositionOverlays() {
    clearPositionOverlays();
    positionOverlayGroups().forEach((group) => {
      const direction = group.side === "long" ? t("多") : t("空");
      const sign = group.pnlPercent >= 0 ? "+" : "";
      const line = chartState.candleSeries.createPriceLine({
        price: group.averagePrice,
        color: group.side === "long" ? "#4aa3ff" : "#f3ba2f",
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: `${direction} ${positionNumber(group.displayQuantity)} ${group.unitLabel} · ${sign}${group.pnlPercent.toFixed(2)}%`,
      });
      chartState.positionPriceLines.push(line);
      const representative = group.rows[0];
      [[representative.take_profit, t("止盈"), "#22ab94"], [representative.stop_loss, t("止损"), "#f23645"]].forEach(([price, label, color]) => {
        if (!Number(price)) return;
        chartState.positionPriceLines.push(chartState.candleSeries.createPriceLine({
          price: Number(price), color, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted,
          axisLabelVisible: true, title: `${label} ${positionNumber(price)}`,
        }));
      });
    });
    chartState.paperOrders.filter((item) => ["pending", "triggered"].includes(item.status) && item.instrument_id === chartState.instrument?.id).forEach((order) => {
      const price = Number(order.limit_price || order.stop_price);
      if (!price) return;
      chartState.positionPriceLines.push(chartState.candleSeries.createPriceLine({
        price, color: "#f3ba2f", lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true, title: `${t("委托")} ${order.quantity} ${order.unit_label}`,
      }));
    });
    const candleTimes = chartState.bundle?.candles?.map((item) => item.time) || [];
    const markers = chartState.paperFills.filter((item) => item.instrument_id === chartState.instrument?.id).map((fill) => {
      const executed = Math.floor(new Date(fill.executed_at).getTime() / 1000);
      const time = candleTimes.reduce((best, candidate) => Math.abs(candidate - executed) < Math.abs(best - executed) ? candidate : best, candleTimes[0]);
      const buy = (fill.position_side === "long" && fill.effect === "open") || (fill.position_side === "short" && fill.effect === "close");
      const action = t(buy
        ? (fill.effect === "open" ? "买入开仓" : "买入平仓")
        : (fill.effect === "open" ? "卖出开仓" : "卖出平仓"));
      const quantity = `${positionNumber(fill.quantity)}${fill.unit_label || ""}`;
      const price = positionNumber(fill.price);
      return { id: `fill:${fill.id}`, time, position: buy ? "belowBar" : "aboveBar", shape: buy ? "arrowUp" : "arrowDown",
        color: buy ? "#2962ff" : "#f23645", text: `${action} · ${quantity} @ ${price}`, size: 1 };
    }).filter((item) => item.time);
    if (markers.length) chartState.executionMarkerPlugin = LightweightCharts.createSeriesMarkers(chartState.candleSeries, markers, { autoScale: false });
  }

  function setPortfolioPositions(positions = []) {
    chartState.portfolioPositions = Array.isArray(positions) ? positions : [];
    if (chartState.chart && chartState.candleSeries) renderPositionOverlays();
  }

  function setPaperTradingData(positions = [], orders = [], fills = []) {
    chartState.portfolioPositions = Array.isArray(positions) ? positions : [];
    chartState.paperOrders = Array.isArray(orders) ? orders : [];
    chartState.paperFills = Array.isArray(fills) ? fills : [];
    if (chartState.chart && chartState.candleSeries) renderPositionOverlays();
  }

  function lineOptions(series, fallbackColor) {
    return {
      color: accessibleChartColor(series.color || fallbackColor),
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: false,
    };
  }

  function accessibleChartColor(value) {
    if (!/^#[0-9a-f]{6}$/i.test(value || "")) return value;
    let channels = [1, 3, 5].map((index) => Number.parseInt(value.slice(index, index + 2), 16));
    const luminance = (items) => items.map((channel) => {
      const normalized = channel / 255;
      return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
    }).reduce((total, channel, index) => total + channel * [0.2126, 0.7152, 0.0722][index], 0);
    const lightTheme = document.documentElement.dataset.theme === "light";
    const background = lightTheme ? 1 : 0.007;
    const contrast = () => (Math.max(luminance(channels), background) + 0.05) / (Math.min(luminance(channels), background) + 0.05);
    let attempts = 0;
    while (contrast() < 4.5 && attempts < 12) {
      channels = channels.map((channel) => Math.round(lightTheme ? channel * 0.82 : channel + (255 - channel) * 0.18));
      attempts += 1;
    }
    return `#${channels.map((channel) => channel.toString(16).padStart(2, "0")).join("")}`;
  }

  function registerRenderedSeries(series, api, paneIndex, label, color) {
    const key = `${paneIndex}:${series.id}:${label}:${chartState.renderedSeries.size}`;
    chartState.renderedSeries.set(key, { api, paneIndex, label, color, ownerId: series.owner_id || series.id });
  }

  function addLine(chart, series, paneIndex, data, label, color) {
    const api = chart.addSeries(LightweightCharts.LineSeries, lineOptions(series, color), paneIndex);
    api.setData(data);
    registerRenderedSeries(series, api, paneIndex, label, accessibleChartColor(series.color || color));
    return api;
  }

  function renderIndicatorSeries(chart, series, paneIndex) {
    if (series.series_type === "band") {
      const definitions = [
        ["Upper", "upper", series.color],
        ["Middle", "middle", "#94a3b8"],
        ["Lower", "lower", series.color],
      ];
      definitions.forEach(([label, key, color]) => addLine(
        chart,
        series,
        paneIndex,
        series.points.filter((point) => point[key] !== null && point[key] !== undefined)
          .map((point) => ({ time: point.time, value: point[key] })),
        `${series.label} ${label}`,
        color,
      ));
      return;
    }
    if (series.series_type === "histogram") {
      const api = chart.addSeries(LightweightCharts.HistogramSeries, {
        color: accessibleChartColor(series.color),
        priceLineVisible: false,
        lastValueVisible: true,
        priceFormat: { type: "volume" },
      }, paneIndex);
      api.setData(series.points.filter((point) => point.value !== null).map((point) => ({
        time: point.time,
        value: point.value,
        color: accessibleChartColor(point.color || series.color),
      })));
      registerRenderedSeries(series, api, paneIndex, series.label, accessibleChartColor(series.color));
      return;
    }
    addLine(
      chart,
      series,
      paneIndex,
      series.points.filter((point) => point.value !== null).map((point) => ({ time: point.time, value: point.value })),
      series.label,
      series.color,
    );
  }

  function markerColor(sentiment) {
    return {
      bullish: "#22ab94",
      bearish: "#f23645",
      mixed: "#f3ba2f",
      neutral: "#8b95a7",
    }[sentiment] || "#8b95a7";
  }

  function mountPaneLegends(panes) {
    $("#priceChart").querySelectorAll(".indicator-pane-legend").forEach((item) => item.remove());
    chartState.paneLegends.clear();
    panes.forEach((pane, paneIndex) => {
      const host = pane.getHTMLElement?.();
      if (!host) return;
      host.classList.add("chart-pane-host");
      const legend = document.createElement("div");
      legend.className = `indicator-pane-legend${paneIndex === 0 ? " main" : " sub"}`;
      if (paneIndex === chartState.eventPaneIndex) {
        legend.className = "indicator-pane-legend news-event-pane-legend";
        legend.innerHTML = `
          <strong>${esc(t("新闻事件"))}</strong>
          <span class="news-event-legend-key"><i style="background:#22ab94"></i>${esc(t("偏多"))}</span>
          <span class="news-event-legend-key"><i style="background:#f23645"></i>${esc(t("偏空"))}</span>
          <span class="news-event-legend-key"><i style="background:#f3ba2f"></i>${esc(t("多空混合"))}</span>
        `;
      }
      legend.dataset.paneIndex = String(paneIndex);
      host.appendChild(legend);
      chartState.paneLegends.set(paneIndex, legend);
    });
  }

  function latestSeriesPoint(api) {
    const data = api.data?.() || [];
    return data[data.length - 1] || null;
  }

  function formatLegendValue(api, point) {
    if (!point || point.value === null || point.value === undefined || !Number.isFinite(Number(point.value))) return "--";
    try {
      return api.priceFormatter().format(Number(point.value));
    } catch (_error) {
      return Number(point.value).toFixed(4);
    }
  }

  function renderPaneLegends(param, bundle) {
    const last = bundle.candles[bundle.candles.length - 1];
    if (!last) return;
    const hasCrosshair = param?.time !== undefined && param?.seriesData;
    let candle = last;
    if (hasCrosshair && param.seriesData.has(chartState.candleSeries)) candle = param.seriesData.get(chartState.candleSeries);
    chartState.paneLegends.forEach((legend, paneIndex) => {
      if (paneIndex === chartState.eventPaneIndex) return;
      const items = [];
      if (paneIndex === 0) {
        items.push(
          '<strong class="pane-legend-title">K线</strong>',
          `<span>O <b>${Number(candle.open).toFixed(2)}</b></span>`,
          `<span>H <b>${Number(candle.high).toFixed(2)}</b></span>`,
          `<span>L <b>${Number(candle.low).toFixed(2)}</b></span>`,
          `<span>C <b>${Number(candle.close).toFixed(2)}</b></span>`,
        );
      }
      chartState.renderedSeries.forEach(({ api, paneIndex: seriesPane, label, color }) => {
        if (seriesPane !== paneIndex) return;
        const point = hasCrosshair ? param.seriesData.get(api) : latestSeriesPoint(api);
        items.push(
          `<span class="pane-legend-series" style="color:${esc(color)}">`
          + `<i style="background:${esc(color)}"></i>${esc(label)} <b>${esc(formatLegendValue(api, point))}</b></span>`,
        );
      });
      legend.innerHTML = items.join("");
    });
  }

  function markerById(identifier) {
    return chartState.bundle?.markers?.find((item) => item.id === identifier) || null;
  }

  function markerForNews(newsId) {
    return chartState.bundle?.markers?.find((marker) => marker.events?.some((item) => item.id === newsId)) || null;
  }

  function timeValueToDate(value) {
    if (typeof value === "number") return new Date(value * 1000);
    if (value && typeof value === "object" && "year" in value) {
      return new Date(Date.UTC(value.year, value.month - 1, value.day));
    }
    return new Date(value);
  }

  function formatChartTime(value, options, { tradingDate = false } = {}) {
    const date = timeValueToDate(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat(window.AppI18n?.getLocale() || "zh-CN", {
      ...options,
      timeZone: tradingDate ? "UTC" : (chartState.instrument?.timezone || "UTC"),
      hour12: false,
    }).format(date);
  }

  function usesTradingDate() {
    return ["1d", "1w", "1mo"].includes(chartState.bundle?.interval || chartState.interval);
  }

  function formatNewsTime(value) {
    return formatChartTime(value, {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", timeZoneName: "short",
    });
  }

  function formatCandleTime(value) {
    if (usesTradingDate()) {
      return formatChartTime(value, { year: "numeric", month: "2-digit", day: "2-digit" }, { tradingDate: true });
    }
    return formatChartTime(value, {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZoneName: "short",
    });
  }

  function formatTimeScaleLabel(value) {
    if (usesTradingDate()) {
      return formatChartTime(value, { year: "numeric", month: "2-digit", day: "2-digit" }, { tradingDate: true });
    }
    return formatChartTime(value, {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZoneName: "short",
    });
  }

  function formatTickMark(value, tickMarkType) {
    const tickTypes = LightweightCharts.TickMarkType || {};
    let options = { hour: "2-digit", minute: "2-digit" };
    if (tickMarkType === tickTypes.Year) options = { year: "numeric" };
    else if (tickMarkType === tickTypes.Month) options = { month: "short" };
    else if (tickMarkType === tickTypes.DayOfMonth) options = { month: "2-digit", day: "2-digit" };
    else if (tickMarkType === tickTypes.TimeWithSeconds) options = { hour: "2-digit", minute: "2-digit", second: "2-digit" };
    return formatChartTime(value, options, { tradingDate: usesTradingDate() });
  }

  function eventMappingLabel(reason, candleTime) {
    const time = formatCandleTime(candleTime);
    if (reason === "next_trading_candle") {
      return t("盘后/非交易时段发布 · 映射到下一交易K线 {time}", { time });
    }
    return t("关联K线 {time}", { time });
  }

  function sentimentLabel(sentiment) {
    return { bullish: t("偏多"), bearish: t("偏空"), mixed: t("多空混合"), neutral: t("中性") }[sentiment] || t("中性");
  }

  function hideNewsTooltip() {
    const tooltip = $("#eventTooltip");
    tooltip.hidden = true;
    tooltip.classList.remove("professional-event-tooltip");
    chartState.hoveredMarkerId = "";
  }

  function showNewsTooltip(marker, point = null) {
    const tooltip = $("#eventTooltip");
    const previews = (marker.events || []).slice(0, 3);
    tooltip.classList.add("professional-event-tooltip");
    tooltip.hidden = false;
    tooltip.innerHTML = `
      <div class="event-tooltip-head">
        <strong>${esc(marker.count > 1 ? t("{count} 条新闻事件", { count: marker.count }) : t("新闻事件"))}</strong>
        <span>${esc(sentimentLabel(marker.aggregate_sentiment || marker.sentiment))}</span>
      </div>
      <div class="event-tooltip-list">
        ${previews.map((item) => `
          <div class="event-tooltip-item">
            <strong>${esc(item.title)}</strong>
            <span>${esc(item.source)} · ${esc(t("影响 {score}", { score: item.impact_score }))}</span>
            <span>${esc(t("发布时间 {time}", { time: formatNewsTime(item.published_at) }))}</span>
            <span>${esc(eventMappingLabel(item.mapping_reason, marker.candle_time))}</span>
            <p>${esc(item.summary)}</p>
          </div>
        `).join("")}
      </div>
      ${marker.count > previews.length ? `<div class="event-tooltip-count">${esc(t("另有 {count} 条新闻", { count: marker.count - previews.length }))}</div>` : ""}
    `;
    const chartRect = $("#priceChart").getBoundingClientRect();
    const anchorX = point?.x ?? chartState.chart?.timeScale().timeToCoordinate(marker.time) ?? chartRect.width / 2;
    const anchorY = point?.y ?? chartRect.height - 58;
    const tooltipRect = tooltip.getBoundingClientRect();
    const left = anchorX + tooltipRect.width + 24 <= chartRect.width
      ? anchorX + 12
      : anchorX - tooltipRect.width - 12;
    tooltip.style.left = `${Math.max(8, Math.min(left, chartRect.width - tooltipRect.width - 8))}px`;
    tooltip.style.top = `${Math.max(8, Math.min(anchorY - tooltipRect.height - 12, chartRect.height - tooltipRect.height - 8))}px`;
    chartState.hoveredMarkerId = marker.id;
  }

  function renderNewsSummary(marker) {
    const summary = $("#chartEventSummary");
    if (!marker) {
      summary.hidden = true;
      summary.innerHTML = "";
      return;
    }
    const primary = marker.events?.[0] || marker;
    summary.hidden = false;
    summary.innerHTML = `
      <span class="chart-event-swatch" style="background:${esc(markerColor(marker.aggregate_sentiment || marker.sentiment))}"></span>
      <div class="chart-event-summary-copy">
        <span>${esc(t("已固定"))} · ${esc(t("发布时间 {time}", { time: formatNewsTime(primary.published_at) }))}</span>
        <strong data-i18n-skip>${esc(primary.title)}</strong>
      </div>
      <div class="chart-event-summary-meta">
        <span>${esc(primary.source)}</span>
        <span>${esc(t("影响 {score}", { score: primary.impact_score }))}</span>
        <span>${esc(sentimentLabel(marker.aggregate_sentiment || marker.sentiment))}</span>
        <span>${esc(eventMappingLabel(primary.mapping_reason || marker.mapping_reason, marker.candle_time))}</span>
        ${marker.count > 1 ? `<span>${esc(t("共 {count} 条", { count: marker.count }))}</span>` : ""}
      </div>
      <button class="text-button" type="button" data-chart-news-details="${esc(marker.id)}">
        ${esc(marker.count > 1 ? t("查看全部 {count} 条", { count: marker.count }) : t("查看详情"))}
      </button>
      ${primary.trace_id ? `<a class="text-button" href="/admin/agent-studio?run=${esc(primary.run_id)}">${esc(t("查看 Agent 调用链"))}</a>` : ""}
    `;
  }

  function focusMarker(marker, { selected = false, tooltip = false } = {}) {
    if (!marker || !chartState.chart || !chartState.candleSeries) return;
    const candle = chartState.bundle?.candles?.find((item) => item.time === marker.time);
    if (candle) chartState.chart.setCrosshairPosition(candle.close, marker.time, chartState.candleSeries);
    if (selected) {
      chartState.selectedMarkerId = marker.id;
      renderNewsSummary(marker);
    }
    if (tooltip) showNewsTooltip(marker);
  }

  function clearMarkerSelection() {
    chartState.selectedMarkerId = "";
    chartState.chart?.clearCrosshairPosition?.();
    renderNewsSummary(null);
    hideNewsTooltip();
  }

  function openMarkerDetails(marker) {
    if (!marker?.events?.length) return;
    if (marker.events.length === 1) {
      window.dispatchEvent(new CustomEvent("chart-news-marker-click", { detail: { id: marker.events[0].id } }));
      return;
    }
    window.dispatchEvent(new CustomEvent("chart-news-group-click", { detail: marker }));
  }

  function renderNewsModeControls() {
    document.querySelectorAll("[data-chart-news-mode]").forEach((button) => {
      const active = button.dataset.chartNewsMode === chartState.newsMode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-checked", String(active));
    });
    $("#newsModeMenu")?.querySelector("summary")?.classList.toggle("active", chartState.newsMode !== "off");
  }

  function markerOptions(markers) {
    return markers.map((marker) => ({
      id: marker.id,
      time: marker.time,
      position: "inBar",
      shape: "circle",
      color: markerColor(marker.aggregate_sentiment || marker.sentiment),
      text: marker.count > 1 ? `×${marker.count}` : undefined,
      size: marker.impact_score >= 75 ? 1 : 0.8,
    }));
  }

  function refreshPaneLayout() {
    if (!chartState.chart) return;
    const panes = chartState.chart.panes();
    panes[0]?.setStretchFactor(4);
    panes.forEach((pane, paneIndex) => {
      if (paneIndex > 0 && paneIndex !== chartState.eventPaneIndex) pane.setStretchFactor(1.15);
    });
    panes[chartState.eventPaneIndex]?.setHeight?.(window.innerWidth <= 720 ? 44 : 52);
    mountPaneLegends(panes);
    if (chartState.bundle) renderPaneLegends(null, chartState.bundle);
  }

  function applyIndicatorSeries(series) {
    if (!chartState.chart) return;
    chartState.renderedSeries.forEach(({ api }) => {
      try { chartState.chart.removeSeries(api); } catch (_error) { /* Series may already be detached with its pane. */ }
    });
    chartState.renderedSeries.clear();

    const paneByOwner = new Map();
    let nextPane = 1;
    series.forEach((item) => {
      let paneIndex = 0;
      if (item.pane === "sub") {
        const owner = item.owner_id || item.id.split(":")[0];
        if (!paneByOwner.has(owner)) paneByOwner.set(owner, nextPane++);
        paneIndex = paneByOwner.get(owner);
      }
      renderIndicatorSeries(chartState.chart, item, paneIndex);
    });
    if (chartState.eventSeries) {
      chartState.eventSeries.moveToPane?.(nextPane);
      chartState.eventPaneIndex = nextPane;
    }
    if (chartState.bundle) chartState.bundle.series = series;
    requestAnimationFrame(refreshPaneLayout);
    performance.mark("chart:indicators-visible");
    try {
      performance.measure("chart:indicator-stage", "chart:candles-visible", "chart:indicators-visible");
    } catch (_error) { /* The candle mark may belong to an aborted request. */ }
  }

  function indicatorPane(config) {
    return config.pane || selectedDefinition(config)?.pane || "main";
  }

  function removeIndicatorOwner(ownerId) {
    if (!chartState.chart) return;
    [...chartState.renderedSeries.entries()].forEach(([key, item]) => {
      if (item.ownerId !== ownerId) return;
      try { chartState.chart.removeSeries(item.api); } catch (_error) { /* Already detached. */ }
      chartState.renderedSeries.delete(key);
    });
  }

  function reflowIndicatorPanes() {
    if (!chartState.chart) return;
    const subOwners = chartState.indicators
      .filter((config) => config.visible && indicatorPane(config) === "sub")
      .map((config) => config.instance_id);
    subOwners.forEach((ownerId, index) => {
      chartState.renderedSeries.forEach((item) => {
        if (item.ownerId !== ownerId) return;
        item.api.moveToPane?.(index + 1);
        item.paneIndex = index + 1;
      });
    });
    if (chartState.eventSeries) {
      chartState.eventPaneIndex = subOwners.length + 1;
      chartState.eventSeries.moveToPane?.(chartState.eventPaneIndex);
    }
    requestAnimationFrame(refreshPaneLayout);
  }

  function applyOwnerSeries(ownerId, series) {
    removeIndicatorOwner(ownerId);
    const config = chartState.indicators.find((item) => item.instance_id === ownerId);
    if (!config) {
      reflowIndicatorPanes();
      return;
    }
    const subOwners = chartState.indicators
      .filter((item) => item.visible && indicatorPane(item) === "sub")
      .map((item) => item.instance_id);
    const paneIndex = indicatorPane(config) === "sub" ? subOwners.indexOf(ownerId) + 1 : 0;
    series.forEach((item) => renderIndicatorSeries(chartState.chart, item, paneIndex));
    reflowIndicatorPanes();
  }

  async function loadIndicatorSeries({ retry = true, configs = chartState.indicators, replaceAll = true } = {}) {
    if (!chartState.instrument || !chartState.bundle) return;
    chartState.seriesAbortController?.abort();
    const controller = new AbortController();
    const generation = ++chartState.seriesGeneration;
    chartState.seriesAbortController = controller;
    const payload = {
      instrument_id: chartState.instrument.id,
      interval: chartState.interval,
      range: chartState.range,
      indicators: configs,
      expected_revision: chartState.bundle.status.revision,
      limit: 500,
    };
    try {
      const result = await api("/api/chart/series/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      if (generation !== chartState.seriesGeneration) return;
      if (result.revision && result.revision !== chartState.bundle.status.revision && retry) {
        await loadIndicatorSeries({ retry: false, configs, replaceAll });
        return;
      }
      if (replaceAll) applyIndicatorSeries(result.series || []);
      else {
        configs.forEach((config) => applyOwnerSeries(
          config.instance_id,
          (result.series || []).filter((item) => item.owner_id === config.instance_id),
        ));
      }
      if (!replaceAll) performance.mark("chart:indicator-incremental-visible");
      const warnings = [...(chartState.bundle.warnings || []), ...(result.warnings || [])];
      $("#chartWarnings").hidden = warnings.length === 0;
      $("#chartWarnings").textContent = [...new Set(warnings)].join(" · ");
    } catch (error) {
      if (error.name !== "AbortError") showError(error);
    }
  }

  function renderChart(bundle, preserveRange = null) {
    destroyChart();
    const target = $("#priceChart");
    const colors = chartColors();
    const chart = LightweightCharts.createChart(target, {
      autoSize: true,
      layout: {
        background: { type: LightweightCharts.ColorType.Solid, color: colors.background },
        textColor: colors.text,
        panes: { separatorColor: colors.line, separatorHoverColor: "#4aa3ff", enableResize: true },
      },
      localization: {
        locale: window.AppI18n?.getLocale() || "zh-CN",
        timeFormatter: formatTimeScaleLabel,
      },
      grid: {
        vertLines: { color: colors.line },
        horzLines: { color: colors.line },
      },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: { borderColor: colors.line },
      timeScale: {
        borderColor: colors.line,
        tickMarkFormatter: formatTickMark,
        timeVisible: !["1d", "1w", "1mo"].includes(bundle.interval),
        secondsVisible: false,
        rightOffset: 6,
        barSpacing: 7,
        minBarSpacing: 2,
      },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: true, mouseWheel: false, pinch: true },
    });
    chartState.chart = chart;
    chartState.resizeObserver = new ResizeObserver(() => requestAnimationFrame(updatePositionZones));
    chartState.resizeObserver.observe(target);
    chartState.altWheelHandler = (event) => {
      if (!event.altKey) return;
      const range = chart.timeScale().getVisibleLogicalRange();
      if (!range || !Number.isFinite(range.from) || !Number.isFinite(range.to)) return;
      event.preventDefault();
      event.stopPropagation();
      const bounds = target.getBoundingClientRect();
      const anchorRatio = bounds.width > 0 ? Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)) : 0.5;
      const currentSpan = Math.max(1, range.to - range.from);
      const maximumSpan = Math.max(30, bundle.candles.length + 20);
      const nextSpan = Math.max(8, Math.min(maximumSpan, currentSpan * (event.deltaY > 0 ? 1.12 : 0.88)));
      const anchor = range.from + currentSpan * anchorRatio;
      chart.timeScale().setVisibleLogicalRange({
        from: anchor - nextSpan * anchorRatio,
        to: anchor + nextSpan * (1 - anchorRatio),
      });
    };
    target.addEventListener("wheel", chartState.altWheelHandler, { passive: false });
    const candleSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {
      upColor: colors.up,
      downColor: colors.down,
      wickUpColor: colors.up,
      wickDownColor: colors.down,
      borderVisible: false,
      priceLineVisible: true,
    }, 0);
    candleSeries.setData(bundle.candles.map((item) => ({
      time: item.time,
      open: item.open,
      high: item.high,
      low: item.low,
      close: item.close,
    })));
    chartState.candleSeries = candleSeries;

    const paneByIndicator = new Map();
    let nextPane = 1;
    bundle.series.forEach((series) => {
      let paneIndex = 0;
      if (series.pane === "sub") {
        const rootId = series.id.split(":")[0];
        if (!paneByIndicator.has(rootId)) paneByIndicator.set(rootId, nextPane++);
        paneIndex = paneByIndicator.get(rootId);
      }
      renderIndicatorSeries(chart, series, paneIndex);
    });

    if (chartState.newsMode !== "off") {
      chartState.eventPaneIndex = nextPane;
      chartState.eventSeries = chart.addSeries(LightweightCharts.LineSeries, {
        color: "transparent",
        lineVisible: false,
        crosshairMarkerVisible: false,
        priceLineVisible: false,
        lastValueVisible: false,
        priceFormat: { type: "custom", formatter: () => "" },
      }, chartState.eventPaneIndex);
      chartState.eventSeries.setData(bundle.candles.map((item) => ({ time: item.time, value: 0 })));
      chartState.eventSeries.priceScale().applyOptions({ borderVisible: false, minimumWidth: 0 });
    }

    const panes = chart.panes();
    panes[0]?.setStretchFactor(4);
    panes.forEach((pane, paneIndex) => {
      if (paneIndex > 0 && paneIndex !== chartState.eventPaneIndex) pane.setStretchFactor(1.15);
    });

    if (bundle.markers.length && chartState.eventSeries) {
      chartState.markerPlugin = LightweightCharts.createSeriesMarkers(
        chartState.eventSeries,
        markerOptions(bundle.markers),
        { autoScale: false },
      );
    }

    chart.subscribeCrosshairMove((param) => {
      renderPaneLegends(param, bundle);
      const marker = param.hoveredObjectId ? markerById(param.hoveredObjectId) : null;
      if (marker) showNewsTooltip(marker, param.point);
      else if (!chartState.selectedMarkerId) hideNewsTooltip();
    });
    chart.subscribeClick((param) => {
      const marker = param.hoveredObjectId ? markerById(param.hoveredObjectId) : null;
      if (marker) {
        focusMarker(marker, { selected: true });
        hideNewsTooltip();
      } else {
        clearMarkerSelection();
      }
    });
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (range && range.from < 20) loadOlder().catch(showError);
      requestAnimationFrame(updatePositionZones);
    });
    if (preserveRange) chart.timeScale().setVisibleLogicalRange(preserveRange);
    else chart.timeScale().fitContent();
    requestAnimationFrame(() => {
      if (chartState.chart !== chart) return;
      chart.panes()[chartState.eventPaneIndex]?.setHeight?.(window.innerWidth <= 720 ? 44 : 52);
      mountPaneLegends(chart.panes());
      renderPaneLegends(null, bundle);
      renderPositionOverlays();
      const selected = markerById(chartState.selectedMarkerId);
      if (selected) focusMarker(selected, { selected: true });
      else if (chartState.selectedMarkerId) clearMarkerSelection();
    });
  }

  function renderStatus(bundle) {
    const modeLabel = {
      realtime: "实时",
      delayed: "延迟",
      historical: "历史",
      cached: bundle.status.stale ? "缓存降级" : "缓存",
    }[bundle.status.mode] || bundle.status.mode;
    $("#activeSymbol").textContent = `${bundle.instrument.symbol} · ${bundle.instrument.name}`;
    $("#chartSourceStatus").innerHTML = `
      <span class="status-dot ${esc(bundle.status.mode)}"></span>
      <span>${esc(bundle.status.provider_label)} · ${esc(modeLabel)}</span>
      <span>${new Date(bundle.status.fetched_at).toLocaleString(window.AppI18n?.getLocale() || "zh-CN", { hour12: false })}</span>
      <span>${esc(bundle.instrument.timezone)}</span>
    `;
    const warnings = [...(bundle.warnings || [])];
    if (chartState.pendingSelectionWarning) {
      warnings.push(chartState.pendingSelectionWarning);
    }
    $("#chartWarnings").hidden = warnings.length === 0;
    $("#chartWarnings").textContent = warnings.join(" · ");
    const last = bundle.candles[bundle.candles.length - 1];
    const first = bundle.candles[0];
    if (last) {
      $("#lastPrice").textContent = last.close.toFixed(2);
      const change = first?.close ? (last.close / first.close - 1) * 100 : 0;
      $("#lastChange").textContent = `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
      $("#lastChange").className = change >= 0 ? "up" : "down";
    }
    const trend = bundle.series.find((item) => ["rsi", "macd:hist", "global_long_short_ratio"].includes(item.id));
    $("#trendScore").textContent = trend?.current_value === null || trend?.current_value === undefined
      ? "--"
      : Number(trend.current_value).toFixed(2);
  }

  function queryPayload(before = null, { includeSeries = false, cachePolicy = "prefer_cache" } = {}) {
    return {
      instrument_id: chartState.instrument.id,
      interval: chartState.interval,
      range: chartState.range,
      indicators: chartState.indicators,
      news_mode: chartState.newsMode,
      include_series: includeSeries,
      cache_policy: cachePolicy,
      before,
      limit: 500,
    };
  }

  function applyBaseBundle(bundle) {
    if (!chartState.chart || !chartState.candleSeries) return;
    const existingSeries = chartState.bundle?.series || [];
    const previousRevision = chartState.bundle?.status?.revision;
    chartState.bundle = { ...bundle, series: existingSeries };
    chartState.candleSeries.setData(bundle.candles.map((item) => ({
      time: item.time, open: item.open, high: item.high, low: item.low, close: item.close,
    })));
    if (chartState.eventSeries) {
      chartState.eventSeries.setData(bundle.candles.map((item) => ({ time: item.time, value: 0 })));
      if (chartState.markerPlugin) chartState.markerPlugin.setMarkers(markerOptions(bundle.markers || []));
      else if (bundle.markers?.length) {
        chartState.markerPlugin = LightweightCharts.createSeriesMarkers(
          chartState.eventSeries, markerOptions(bundle.markers), { autoScale: false },
        );
      }
    }
    renderStatus(chartState.bundle);
    renderPaneLegends(null, chartState.bundle);
    renderPositionOverlays();
    if (previousRevision !== bundle.status.revision) loadIndicatorSeries().catch(showError);
  }

  async function refreshBaseBundle(generation) {
    try {
      const bundle = await api("/api/chart/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(queryPayload(null, { includeSeries: false, cachePolicy: "refresh" })),
      });
      if (generation === chartState.requestGeneration) applyBaseBundle(bundle);
    } catch (error) {
      if (generation === chartState.requestGeneration) showError(error);
    }
  }

  async function loadChart({ fit = true } = {}) {
    if (!chartState.instrument) return;
    chartState.chartAbortController?.abort();
    chartState.seriesAbortController?.abort();
    const controller = new AbortController();
    const generation = ++chartState.requestGeneration;
    chartState.chartAbortController = controller;
    performance.mark("chart:load-start");
    $("#chartLoading").hidden = false;
    try {
      const bundle = await api("/api/chart/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(queryPayload(null, { includeSeries: false })),
        signal: controller.signal,
      });
      if (generation !== chartState.requestGeneration) return;
      chartState.bundle = bundle;
      renderChart(bundle, fit ? null : chartState.chart?.timeScale().getVisibleLogicalRange());
      performance.mark("chart:candles-visible");
      performance.measure("chart:candle-stage", "chart:load-start", "chart:candles-visible");
      renderStatus(bundle);
      connectStream();
      loadIndicatorSeries().catch(showError);
      if (bundle.status.refresh_required) refreshBaseBundle(generation);
    } catch (error) {
      if (error.name !== "AbortError") throw error;
    } finally {
      if (generation === chartState.requestGeneration) $("#chartLoading").hidden = true;
    }
  }

  function mergeByTime(left, right) {
    const values = new Map();
    [...left, ...right].forEach((item) => values.set(item.time, item));
    return [...values.values()].sort((a, b) => a.time - b.time);
  }

  function mergeMarkers(left, right) {
    const values = new Map();
    [...left, ...right].forEach((marker) => {
      const existing = values.get(marker.id);
      if (!existing) {
        values.set(marker.id, marker);
        return;
      }
      const events = new Map([...(existing.events || []), ...(marker.events || [])].map((item) => [item.id, item]));
      const mergedEvents = [...events.values()].sort((a, b) => b.impact_score - a.impact_score);
      values.set(marker.id, {
        ...existing,
        ...marker,
        events: mergedEvents,
        count: mergedEvents.length,
        title: mergedEvents[0]?.title || marker.title,
        impact_score: mergedEvents[0]?.impact_score || marker.impact_score,
      });
    });
    return [...values.values()].sort((a, b) => a.time - b.time);
  }

  async function loadOlder() {
    if (chartState.loadingOlder || !chartState.bundle?.next_before) return;
    chartState.loadingOlder = true;
    const previousCount = chartState.bundle.candles.length;
    try {
      const older = await api("/api/chart/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(queryPayload(chartState.bundle.next_before, { includeSeries: true })),
      });
      chartState.bundle.candles = mergeByTime(older.candles, chartState.bundle.candles);
      older.series.forEach((series) => {
        const current = chartState.bundle.series.find((item) => item.id === series.id);
        if (current) current.points = mergeByTime(series.points, current.points);
        else chartState.bundle.series.push(series);
      });
      chartState.bundle.markers = mergeMarkers(older.markers, chartState.bundle.markers);
      chartState.bundle.next_before = older.next_before;
      renderChart(chartState.bundle, { from: older.candles.length, to: previousCount + older.candles.length });
    } finally {
      chartState.loadingOlder = false;
    }
  }

  function connectStream() {
    chartState.socket?.close();
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${protocol}://${window.location.host}/api/stream/chart?instrument_id=${encodeURIComponent(chartState.instrument.id)}&interval=${encodeURIComponent(chartState.interval)}`;
    const socket = new WebSocket(url);
    chartState.socket = socket;
    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.type !== "chart_update" || payload.instrument_id !== chartState.instrument.id) return;
      const candle = payload.candle;
      chartState.candleSeries?.update({
        time: candle.time,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      });
      if (chartState.bundle) {
        const last = chartState.bundle.candles[chartState.bundle.candles.length - 1];
        if (last?.time === candle.time) chartState.bundle.candles[chartState.bundle.candles.length - 1] = candle;
        else chartState.bundle.candles.push(candle);
        renderStatus({ ...chartState.bundle, status: payload.status });
        renderPaneLegends(null, chartState.bundle);
        renderPositionOverlays();
      }
    };
    socket.onerror = () => socket.close();
  }

  function selectedDefinition(config) {
    return chartState.catalog.find((item) => item.id === config.id);
  }

  function renderIndicatorDrawer() {
    const categories = [
      ["all", "全部"], ["trend", "趋势"], ["momentum", "动量"],
      ["volatility", "波动"], ["volume", "成交量"], ["derivatives", "衍生品"], ["macro", "宏观"],
    ];
    $("#indicatorCategoryTabs").innerHTML = categories.map(([key, label]) => {
      const count = key === "all" ? chartState.catalog.length : chartState.catalog.filter((item) => item.category === key).length;
      return `<button class="${chartState.category === key ? "active" : ""}" type="button" data-indicator-category="${key}">${t(label)} ${count}</button>`;
    }).join("");
    $("#chartIndicatorCatalogCount").textContent = chartState.catalog.filter((item) => item.available).length;
    $("#activeIndicatorList").innerHTML = chartState.indicators.length
      ? chartState.indicators.map((config, index) => {
        const definition = selectedDefinition(config);
        const parameter = definition?.parameters?.[0];
        return `<div class="active-indicator-row">
          <strong title="${esc(definition?.description || config.id)}">${esc(definition?.name || config.id)}</strong>
          ${parameter ? `<input type="number" data-indicator-length="${index}" min="${parameter.minimum || 2}" max="${parameter.maximum || 300}" step="${parameter.step || 1}" value="${esc(config.parameters?.[parameter.key] ?? parameter.default)}" title="${esc(parameter.label)}" />` : "<span></span>"}
          <input type="color" data-indicator-color="${index}" value="${esc(config.color || "#4aa3ff")}" title="颜色" />
          <button class="icon-button" type="button" data-indicator-up="${index}" title="上移" ${index === 0 ? "disabled" : ""}><i data-lucide="arrow-up"></i></button>
          <button class="icon-button" type="button" data-indicator-down="${index}" title="下移" ${index === chartState.indicators.length - 1 ? "disabled" : ""}><i data-lucide="arrow-down"></i></button>
          <button class="icon-button" type="button" data-indicator-remove="${index}" title="移除"><i data-lucide="x"></i></button>
        </div>`;
      }).join("")
      : '<p class="empty-cell">尚未添加指标。</p>';
    const query = $("#indicatorSearch").value.trim().toLowerCase();
    const rows = chartState.catalog.filter((item) => (
      (chartState.category === "all" || item.category === chartState.category)
      && (!query || `${item.name} ${item.description}`.toLowerCase().includes(query))
    ));
    $("#indicatorCatalog").innerHTML = rows.map((item) => `
      <article class="indicator-catalog-item" aria-disabled="${!item.available}">
        <div><strong>${esc(item.name)}</strong><span>${esc(item.description)}</span>${!item.available ? `<small>${esc(item.unavailable_reason)}</small>` : ""}</div>
        <button class="icon-button" type="button" data-indicator-add="${esc(item.id)}" title="添加指标" ${item.available ? "" : "disabled"}><i data-lucide="plus"></i></button>
      </article>
    `).join("");
    window.lucide?.createIcons();
  }

  function scheduleIndicatorApply(instanceId) {
    clearTimeout(chartState.applyTimer);
    chartState.applyTimer = setTimeout(() => {
      const config = chartState.indicators.find((item) => item.instance_id === instanceId);
      if (config) loadIndicatorSeries({ configs: [config], replaceAll: false }).catch(showError);
    }, 100);
  }

  function addIndicator(identifier) {
    const definition = chartState.catalog.find((item) => item.id === identifier);
    if (!definition?.available) return;
    const subPaneIds = new Set(chartState.indicators.filter((item) => item.pane === "sub").map((item) => item.id));
    if (definition.pane === "sub" && !subPaneIds.has(identifier) && subPaneIds.size >= 6) {
      showError(new Error("最多同时展示 6 个副图指标"));
      return;
    }
    const parameters = {};
    definition.parameters.forEach((item) => { parameters[item.key] = item.default; });
    const config = {
      id: identifier,
      instance_id: indicatorInstanceId(identifier),
      pane: definition.pane,
      parameters,
      color: ["#f3ba2f", "#4aa3ff", "#22ab94", "#a78bfa"][chartState.indicators.length % 4],
      visible: true,
    };
    chartState.indicators.push(config);
    renderIndicatorDrawer();
    populateAlertMetrics();
    loadIndicatorSeries({ configs: [config], replaceAll: false }).catch(showError);
  }

  function renderInstrumentOptions() {
    $("#chartInstrumentOptions").innerHTML = chartState.instruments.map((item) => (
      `<option value="${esc(item.id)}">${esc(item.symbol)} · ${esc(item.name)} · ${esc(item.market)}</option>`
    )).join("");
  }

  function renderInstrumentResults(query = "") {
    const target = $("#chartInstrumentResults");
    const needle = query.trim().toLowerCase();
    const assetLabels = { stock: "股票", etf: "ETF", crypto: "现货", crypto_perpetual: "永续", index: "指数" };
    const rows = chartState.instruments
      .filter((item) => !needle || `${item.id} ${item.symbol} ${item.name} ${item.market}`.toLowerCase().includes(needle))
      .sort((left, right) => (left.asset_class === "index" ? -1 : 0) - (right.asset_class === "index" ? -1 : 0))
      .slice(0, 30);
    target.innerHTML = rows.map((item) => `
      <button class="instrument-search-result" type="button" role="option" data-chart-instrument-id="${esc(item.id)}">
        <b>${esc(item.symbol)}</b><span>${esc(item.name)} · ${esc(item.market)}</span><small>${esc(assetLabels[item.asset_class] || item.asset_class)}</small>
      </button>
    `).join("") || `<div class="empty-cell">未找到匹配标的</div>`;
    target.hidden = false;
  }

  function hideInstrumentResults() {
    $("#chartInstrumentResults").hidden = true;
  }

  async function searchInstruments(query) {
    const needle = query.trim();
    if (needle.length < 2) {
      renderInstrumentResults(needle);
      return;
    }
    const rows = await api(`/api/instruments?query=${encodeURIComponent(needle)}`);
    const merged = new Map(chartState.instruments.map((item) => [item.id, item]));
    rows.forEach((item) => merged.set(item.id, item));
    chartState.instruments = [...merged.values()];
    renderInstrumentOptions();
    renderInstrumentResults(needle);
  }

  function intervalRangeCompatible(interval, range) {
    return rangeOrder.indexOf(range) >= rangeOrder.indexOf(minimumRangeByInterval[interval]);
  }

  function intervalRangeReason(interval) {
    return `${intervalLabels[interval] || interval} K 至少需要 ${minimumRangeByInterval[interval]} 范围`;
  }

  function updateIntervalControls() {
    document.querySelectorAll("[data-chart-interval]").forEach((button) => {
      const interval = button.dataset.chartInterval;
      const supported = chartState.instrument.intervals.includes(interval);
      const compatible = intervalRangeCompatible(interval, chartState.range);
      button.disabled = !supported || !compatible;
      button.title = !supported ? "当前标的不支持该周期" : compatible ? "" : intervalRangeReason(interval);
      button.setAttribute("aria-disabled", String(button.disabled));
      button.classList.toggle("active", interval === chartState.interval);
    });
    document.querySelectorAll("[data-chart-range]").forEach((button) => {
      const compatible = intervalRangeCompatible(chartState.interval, button.dataset.chartRange);
      button.disabled = !compatible;
      button.title = compatible ? "" : intervalRangeReason(chartState.interval);
      button.setAttribute("aria-disabled", String(button.disabled));
      button.classList.toggle("active", button.dataset.chartRange === chartState.range);
    });
  }

  async function selectInstrument(instrument, { loadDefault = true } = {}) {
    if (!instrument) return;
    const assetChanged = chartState.instrument?.asset_class !== instrument.asset_class;
    chartState.instrument = instrument;
    $("#chartInstrumentSearch").value = instrument.id;
    hideInstrumentResults();
    window.dispatchEvent(new CustomEvent("professional-instrument-change", { detail: instrument }));
    updateIntervalControls();
    const initialInterval = chartState.interval;
    const initialRange = chartState.range;
    const initialIndicators = JSON.stringify(chartState.indicators);
    const chartPromise = loadChart();
    const catalogPromise = api(`/api/chart/indicators?instrument_id=${encodeURIComponent(instrument.id)}`);
    const layoutsPromise = assetChanged && loadDefault ? loadLayouts() : Promise.resolve();
    [chartState.catalog] = await Promise.all([catalogPromise, layoutsPromise]);
    let baseSelectionChanged = false;
    if (assetChanged && loadDefault) {
      const defaultLayout = chartState.layouts.find((item) => item.is_default && item.asset_class === instrument.asset_class);
      const defaultApplied = defaultLayout ? applyLayout(defaultLayout, false) : false;
      baseSelectionChanged = defaultApplied
        && (chartState.interval !== initialInterval || chartState.range !== initialRange);
      if (!defaultApplied) chartState.indicators = normalizeIndicatorInstances(
        chartState.indicators.filter((config) => chartState.catalog.find((item) => item.id === config.id)?.available),
      );
    }
    updateIntervalControls();
    renderIndicatorDrawer();
    populateAlertMetrics();
    await chartPromise;
    if (baseSelectionChanged) await loadChart();
    else if (JSON.stringify(chartState.indicators) !== initialIndicators) loadIndicatorSeries().catch(showError);
  }

  async function selectLegacySymbol(symbol) {
    if (!chartState.initialized) return;
    const normalized = String(symbol).toUpperCase();
    let instrument = chartState.instruments.find((item) => (
      item.symbol.toUpperCase() === normalized
      || item.id.toUpperCase() === normalized
      || (normalized === "BTCUSD" && item.symbol === "BTCUSDT")
      || (normalized === "ETHUSD" && item.symbol === "ETHUSDT")
    ));
    if (!instrument) {
      const rows = await api(`/api/instruments?query=${encodeURIComponent(normalized)}`);
      const merged = new Map(chartState.instruments.map((item) => [item.id, item]));
      rows.forEach((item) => merged.set(item.id, item));
      chartState.instruments = [...merged.values()];
      renderInstrumentOptions();
      instrument = chartState.instruments.find((item) => (
        item.symbol.toUpperCase() === normalized || item.id.toUpperCase() === normalized
      ));
    }
    if (!instrument) throw new Error(`未找到标的 ${normalized}`);
    if (instrument && instrument.id !== chartState.instrument?.id) {
      performance.mark("chart:symbol-switch-start");
      await selectInstrument(instrument);
      performance.mark("chart:symbol-switch-visible");
      performance.measure("chart:symbol-switch", "chart:symbol-switch-start", "chart:symbol-switch-visible");
    }
  }

  async function loadLayouts() {
    chartState.layouts = await api("/api/chart/layouts");
    renderLayoutOptions();
  }

  function renderLayoutOptions() {
    $("#chartLayoutSelect").innerHTML = '<option value="">当前临时布局</option>' + chartState.layouts
      .filter((item) => !chartState.instrument || item.asset_class === chartState.instrument.asset_class)
      .map((item) => `<option value="${esc(item.id)}">${esc(item.name)}${item.is_default ? " · 默认" : ""}</option>`).join("");
    $("#chartLayoutSelect").value = chartState.activeLayoutId;
  }

  function applyLayout(layout, reload = true) {
    const supported = chartState.instrument?.intervals.includes(layout.interval);
    if (!supported || !intervalRangeCompatible(layout.interval, layout.range)) {
      const reason = supported ? intervalRangeReason(layout.interval) : "当前标的不支持该布局周期";
      const message = `布局“${layout.name}”未应用：${reason}`;
      chartState.pendingSelectionWarning = message;
      $("#chartLayoutStatus").textContent = message;
      $("#chartWarnings").hidden = false;
      $("#chartWarnings").textContent = message;
      renderLayoutOptions();
      return false;
    }
    chartState.pendingSelectionWarning = "";
    $("#chartLayoutStatus").textContent = "";
    chartState.activeLayoutId = layout.id;
    chartState.interval = layout.interval;
    chartState.range = layout.range;
    chartState.indicators = normalizeIndicatorInstances(layout.indicators, `layout-${layout.id}`);
    $("#chartLayoutName").value = layout.name;
    $("#chartLayoutDefault").checked = layout.is_default;
    updateIntervalControls();
    renderIndicatorDrawer();
    if (reload) loadChart().catch(showError);
    return true;
  }

  async function saveLayout(event) {
    event.preventDefault();
    const name = $("#chartLayoutName").value.trim();
    if (!name) {
      $("#chartLayoutStatus").textContent = "请输入布局名称。";
      return;
    }
    const payload = {
      name,
      asset_class: chartState.instrument.asset_class,
      is_default: $("#chartLayoutDefault").checked,
      interval: chartState.interval,
      range: chartState.range,
      indicators: chartState.indicators,
    };
    const editing = chartState.layouts.find((item) => item.id === chartState.activeLayoutId);
    const layout = await api(editing ? `/api/chart/layouts/${editing.id}` : "/api/chart/layouts", {
      method: editing ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    chartState.activeLayoutId = layout.id;
    await loadLayouts();
    $("#chartLayoutStatus").textContent = "布局已保存并同步到账户。";
  }

  function populateAlertMetrics() {
    const select = $("#alertMetric");
    if (!select) return;
    const existing = select.value;
    select.innerHTML = [
      '<option value="price">价格</option>',
      '<option value="news_impact">新闻影响分</option>',
      ...chartState.catalog.filter((item) => item.available).map((item) => (
        `<option value="${esc(item.id)}">${esc(item.name)}</option>`
      )),
    ].join("");
    if ([...select.options].some((item) => item.value === existing)) select.value = existing;
  }

  function showError(error) {
    $("#chartWarnings").hidden = false;
    $("#chartWarnings").textContent = error.message || String(error);
    $("#chartLoading").hidden = true;
  }

  function bindEvents() {
    $("#chartInstrumentSearch").addEventListener("focus", (event) => renderInstrumentResults(event.target.value));
    $("#chartInstrumentSearch").addEventListener("input", (event) => {
      clearTimeout(chartState.instrumentSearchTimer);
      chartState.instrumentSearchTimer = setTimeout(() => {
        searchInstruments(event.target.value).catch(showError);
      }, 220);
    });
    $("#chartInstrumentSearch").addEventListener("change", async (event) => {
      const value = event.target.value.trim().toLowerCase();
      let instrument = chartState.instruments.find((item) => (
        item.id.toLowerCase() === value || item.symbol.toLowerCase() === value || item.name.toLowerCase() === value
      ));
      if (!instrument && value.length >= 2) {
        await searchInstruments(value);
        instrument = chartState.instruments.find((item) => (
          item.id.toLowerCase() === value || item.symbol.toLowerCase() === value || item.name.toLowerCase() === value
        ));
      }
      if (instrument) selectInstrument(instrument).catch(showError);
      else showError(new Error("未找到该标的，请从候选列表选择。"));
    });
    $("#chartInstrumentResults").addEventListener("click", (event) => {
      const option = event.target.closest("[data-chart-instrument-id]");
      if (!option) return;
      const instrument = chartState.instruments.find((item) => item.id === option.dataset.chartInstrumentId);
      if (instrument) selectInstrument(instrument).catch(showError);
    });
    document.addEventListener("click", (event) => {
      if (!event.target.closest(".instrument-picker-shell")) hideInstrumentResults();
    });
    document.querySelectorAll("[data-chart-interval]").forEach((button) => button.addEventListener("click", () => {
      chartState.interval = button.dataset.chartInterval;
      updateIntervalControls();
      loadChart().catch(showError);
    }));
    document.querySelectorAll("[data-chart-range]").forEach((button) => button.addEventListener("click", () => {
      chartState.range = button.dataset.chartRange;
      updateIntervalControls();
      loadChart().catch(showError);
    }));
    $("#refreshProfessionalChart").addEventListener("click", () => loadChart().catch(showError));
    $("#newsModeMenu").addEventListener("click", (event) => {
      const option = event.target.closest("[data-chart-news-mode]");
      if (!option) return;
      chartState.newsMode = option.dataset.chartNewsMode;
      localStorage.setItem(newsModeStorageKey, chartState.newsMode);
      $("#newsModeMenu").open = false;
      clearMarkerSelection();
      renderNewsModeControls();
      loadChart().catch(showError);
    });
    $("#chartEventSummary").addEventListener("click", (event) => {
      const button = event.target.closest("[data-chart-news-details]");
      if (button) openMarkerDetails(markerById(button.dataset.chartNewsDetails));
    });
    $("#openIndicatorDrawer").addEventListener("click", () => {
      renderIndicatorDrawer();
      $("#indicatorDrawer").hidden = false;
    });
    document.querySelectorAll("[data-open-indicator-drawer]").forEach((button) => button.addEventListener("click", () => {
      renderIndicatorDrawer();
      $("#indicatorDrawer").hidden = false;
    }));
    $("#closeIndicatorDrawer").addEventListener("click", () => { $("#indicatorDrawer").hidden = true; });
    $("#indicatorSearch").addEventListener("input", renderIndicatorDrawer);
    $("#indicatorDrawer").addEventListener("click", (event) => {
      const category = event.target.closest("[data-indicator-category]");
      if (category) { chartState.category = category.dataset.indicatorCategory; renderIndicatorDrawer(); return; }
      const add = event.target.closest("[data-indicator-add]");
      if (add) { addIndicator(add.dataset.indicatorAdd); return; }
      const remove = event.target.closest("[data-indicator-remove]");
      if (remove) {
        const [removed] = chartState.indicators.splice(Number(remove.dataset.indicatorRemove), 1);
        if (removed) removeIndicatorOwner(removed.instance_id);
        renderIndicatorDrawer();
        populateAlertMetrics();
        reflowIndicatorPanes();
        return;
      }
      const moveUp = event.target.closest("[data-indicator-up]");
      const moveDown = event.target.closest("[data-indicator-down]");
      if (moveUp || moveDown) {
        const from = Number((moveUp || moveDown).dataset[moveUp ? "indicatorUp" : "indicatorDown"]);
        const to = from + (moveUp ? -1 : 1);
        const [item] = chartState.indicators.splice(from, 1);
        chartState.indicators.splice(to, 0, item);
        renderIndicatorDrawer();
        reflowIndicatorPanes();
      }
    });
    $("#indicatorDrawer").addEventListener("input", (event) => {
      if (event.target.matches("[data-indicator-length]")) {
        const index = Number(event.target.dataset.indicatorLength);
        const definition = selectedDefinition(chartState.indicators[index]);
        const key = definition.parameters[0].key;
        chartState.indicators[index].parameters[key] = Number(event.target.value);
        scheduleIndicatorApply(chartState.indicators[index].instance_id);
      }
      if (event.target.matches("[data-indicator-color]")) {
        const config = chartState.indicators[Number(event.target.dataset.indicatorColor)];
        config.color = event.target.value;
        scheduleIndicatorApply(config.instance_id);
      }
    });
    $("#openLayoutDialog").addEventListener("click", () => {
      renderLayoutOptions();
      $("#chartLayoutModal").hidden = false;
    });
    $("#closeLayoutDialog").addEventListener("click", () => { $("#chartLayoutModal").hidden = true; });
    $("#chartLayoutSelect").addEventListener("change", (event) => {
      const layout = chartState.layouts.find((item) => item.id === event.target.value);
      if (layout) applyLayout(layout);
      else {
        chartState.activeLayoutId = "";
        $("#chartLayoutName").value = "";
        $("#chartLayoutDefault").checked = false;
      }
    });
    $("#chartLayoutForm").addEventListener("submit", (event) => saveLayout(event).catch(showError));
    $("#deleteChartLayout").addEventListener("click", async () => {
      if (!chartState.activeLayoutId) return;
      await api(`/api/chart/layouts/${chartState.activeLayoutId}`, { method: "DELETE" });
      chartState.activeLayoutId = "";
      $("#chartLayoutName").value = "";
      await loadLayouts();
      $("#chartLayoutStatus").textContent = "布局已删除。";
    });
    document.addEventListener("mouseover", (event) => {
      const newsItem = event.target.closest?.("#newsTimeline .news-item[data-news-id]");
      if (!newsItem || chartState.selectedMarkerId) return;
      const marker = markerForNews(newsItem.dataset.newsId);
      if (marker) focusMarker(marker, { tooltip: true });
    });
    document.addEventListener("mouseout", (event) => {
      const newsItem = event.target.closest?.("#newsTimeline .news-item[data-news-id]");
      if (!newsItem || newsItem.contains(event.relatedTarget) || chartState.selectedMarkerId) return;
      chartState.chart?.clearCrosshairPosition?.();
      hideNewsTooltip();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && chartState.selectedMarkerId) clearMarkerSelection();
    });
    document.addEventListener("app-theme-change", () => {
      if (chartState.bundle) renderChart(chartState.bundle, chartState.chart?.timeScale().getVisibleLogicalRange());
    });
    document.addEventListener("app-language-change", () => {
      renderNewsModeControls();
      if (chartState.bundle) renderChart(chartState.bundle, chartState.chart?.timeScale().getVisibleLogicalRange());
    });
  }

  async function init({ csrfToken = "", defaultSymbol = "AAPL" } = {}) {
    if (chartState.initialized || !$("#priceChart") || !window.LightweightCharts) return;
    chartState.csrf = csrfToken;
    chartState.instruments = await api("/api/instruments");
    renderInstrumentOptions();
    bindEvents();
    renderNewsModeControls();
    chartState.initialized = true;
    const initial = chartState.instruments.find((item) => item.symbol === defaultSymbol)
      || chartState.instruments.find((item) => item.id === defaultSymbol)
      || chartState.instruments[0];
    await selectInstrument(initial);
    window.dispatchEvent(new CustomEvent("professional-chart-ready"));
    window.lucide?.createIcons();
  }

  window.ProfessionalChart = {
    init,
    selectLegacySymbol,
    refreshNewsMarkers: () => refreshBaseBundle(chartState.requestGeneration),
    resize: () => chartState.chart?.resize($("#priceChart").clientWidth, $("#priceChart").clientHeight),
    currentInstrumentId: () => chartState.instrument?.id || "",
    currentQuote: () => {
      const candle = chartState.bundle?.candles?.at(-1);
      return candle ? {
        instrument_id: chartState.instrument?.id || "",
        price: candle.close,
        currency: chartState.instrument?.currency || "",
        as_of: new Date(candle.time * 1000).toISOString(),
        source: chartState.bundle?.status?.provider_label || chartState.bundle?.status?.provider || "K线",
        stale: Boolean(chartState.bundle?.status?.stale),
      } : null;
    },
    setPortfolioPositions,
    setPaperTradingData,
    selectInstrument: (instrumentId) => {
      const instrument = chartState.instruments.find((item) => item.id === instrumentId);
      return instrument ? selectInstrument(instrument) : Promise.resolve();
    },
    currentInterval: () => chartState.interval,
    visibleLogicalRange: () => chartState.chart?.timeScale().getVisibleLogicalRange() || null,
    currentIndicatorParameters: (identifier) => (
      chartState.indicators.find((item) => item.id === identifier)?.parameters || {}
    ),
  };
})();
