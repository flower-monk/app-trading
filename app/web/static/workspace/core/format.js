export function formatPrice(value) {
  return value > 1000 ? value.toLocaleString("en-US", { maximumFractionDigits: 0 }) : value.toFixed(2);
}

export function formatTime(value) {
  return new Intl.DateTimeFormat(window.AppI18n?.getLocale() || "zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
