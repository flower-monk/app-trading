const assetLoads = new Map();

function loadScriptOnce(src) {
  if (assetLoads.has(src)) return assetLoads.get(src);
  const existing = document.querySelector(`script[src="${src}"]`);
  if (existing) return Promise.resolve();
  const promise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = resolve;
    script.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.body.appendChild(script);
  });
  assetLoads.set(src, promise);
  return promise;
}

async function ensureProfessionalChartAssets() {
  await loadScriptOnce("/static/vendor/lightweight-charts.standalone.production.js");
  await loadScriptOnce("/static/workspace/professional-chart.js?v=20260909.2");
}

function ensureMarketToolsAssets() {
  return loadScriptOnce("/static/vendor/d3-hierarchy.min.js");
}

export { ensureMarketToolsAssets, ensureProfessionalChartAssets, loadScriptOnce };
