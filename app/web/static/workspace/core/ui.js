export const signalText = {
  strong_buy: "强买入",
  buy: "买入",
  neutral: "中性",
  sell: "卖出",
  strong_sell: "强卖出",
};

export const signalClass = {
  strong_buy: "up",
  buy: "up",
  neutral: "neutral",
  sell: "down",
  strong_sell: "down",
};

export const $ = (selector) => document.querySelector(selector);
export const translateText = (source, params = {}) => window.AppI18n?.t(source, params) || source;

export const sentimentClass = {
  bullish: "up",
  bearish: "down",
  neutral: "flat",
  mixed: "watch",
};

export const severityClass = {
  info: "neutral",
  watch: "watch",
  risk: "down",
  opportunity: "up",
};

export const riskClass = {
  risk: "down",
  watch: "watch",
  neutral: "neutral",
  constructive: "up",
};

export const riskText = {
  risk: "Risk",
  watch: "Watch",
  neutral: "Neutral",
  constructive: "Constructive",
};

export const viewConfig = {
  dashboard: { title: "Dashboard", modules: ["dashboard"], focus: "dashboardView", wide: true, path: "/dashboard" },
  market: { title: "行情", modules: ["market"], focus: "marketPanel", wide: false },
  "market-tools": { title: "市场工具", modules: ["market-tools"], focus: "marketToolsView", wide: true, path: "/market-tools" },
  watchlist: { title: "Watchlist", modules: ["watchlist"], focus: "watchlistResourceView", wide: true, path: "/watchlist" },
  news: { title: "新闻", modules: ["news"], focus: "newsPanel", wide: true },
  ai: { title: "AI盯盘", modules: ["ai"], focus: "aiPanel", wide: true },
  factors: { title: "因子", modules: ["factors"], focus: "factorSignalPanel", wide: true },
  backtest: { title: "回测", modules: ["backtest"], focus: "backtestPanel", wide: true },
  alerts: { title: "预警", modules: ["alerts"], focus: "alertPanel", wide: true },
};

Object.entries(viewConfig).forEach(([key, config]) => {
  if (!config.path) config.path = `/${key}`;
});
