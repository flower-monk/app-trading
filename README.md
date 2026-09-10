# App Trading

App Trading is a FastAPI-based market watch, indicator, news, AI analysis, and factor backtesting dashboard. The application uses server-side real market providers and a TradingView-inspired workstation for U.S. equities, ETFs, major indices, and crypto assets. Production APIs never synthesize missing market, news, factor, or backtest results.

The product is designed for active investors, quant researchers, and semi-systematic traders who need one workspace for price action, indicators, news catalysts, AI risk prompts, and factor research. AI output is treated as research support and risk context, not as direct investment advice or default automated trading.

## Project Architecture

The codebase is organized as a FastAPI modular monolith. Business behavior is
grouped by domain under `app/modules`, HTTP transport stays in `app/api/routes`,
and application startup is assembled in `app/bootstrap`. Frontend pages and
assets live under `app/web` and are grouped by workspace rather than kept in one
global static directory.

```text
app/
├── api/routes/          # Thin FastAPI HTTP and WebSocket adapters
├── bootstrap/           # App factory, middleware, and process lifecycle
├── core/                # Environment-independent application configuration
├── db/                  # SQLite connection primitives
├── modules/             # Market, news, AI, research, trading, and admin domains
├── schemas/             # Shared request/response contracts
├── services/            # Temporary import-compatible migration facades
└── web/
    ├── pages/            # Server-served application shells
    └── static/           # core, workspace, admin, styles, and vendor assets
```

`app/main.py` is intentionally only the ASGI entry point. New business logic
must be added to the owning domain module, not to route files or compatibility
facades. See [`docs/architecture.md`](docs/architecture.md) for module ownership,
dependency rules, and the staged migration policy.

## Current Prototype

Implemented today:

- FastAPI backend serving the page and normalized provider APIs.
- Dark market-watch UI with watchlist, candlestick chart, MA20/MA60 overlays, RSI/MACD summaries, simple signal filters, and symbol switching.
- Real provider symbol catalogs covering U.S. stocks, ETFs, indices, and crypto pairs.
- Professional indicator catalog with 62 series: local trend/momentum/volatility/volume calculations, Binance Futures positioning and order-flow metrics, and FRED macro series. Finnhub symbol search extends the built-in catalog without browser-side provider calls.
- Source-backed indicator resource panel for fundamentals, valuation, earnings, and optional social/news sentiment. Finnhub is primary, Alpha Vantage is the configured fallback, and every card exposes its provider and stale-cache state.
- Market provider adapter interface, active-provider config, provider status API, and Finnhub/Binance/Coinbase implementations used by watchlists, candles, indicators, and backtests.
- SQLite-backed per-provider watchlist customization with add/remove APIs and frontend controls.
- Market breadth API and dashboard panel summarizing advance/decline, average change, RSI/trend score, signal distribution, and market-level heatmap for the active watchlist.
- Server-side screener API and dashboard panel for filtering the active watchlist by change, RSI, trend score, signal, and market, with match reasons.
- SQLite-backed screener presets for saving, loading, validating, and deleting reusable active-watchlist screening templates.
- WebSocket market stream that pushes active watchlist snapshots and market breadth to the frontend for live dashboard refresh.
- CSV export API and panel download actions for watchlist, screener results, portfolio positions, portfolio risk contribution, and backtest summaries.
- Coinbase public REST crypto provider for live BTC-USD and ETH-USD ticker/candles/indicator estimates, selectable from the UI.
- Binance public REST provider skeleton for BTCUSDT and ETHUSDT; access can be region-restricted and may return HTTP 451.
- Persisted source-backed news/event APIs with chart markers, hover summaries, and clickable source links.
- SQLite-backed unified information-source registry for RSS/Atom and REST adapters, with capability metadata, environment-backed authentication, health, polling, article deduplication, symbol mapping, and persisted chart events.
- Dedicated news-source control center at `/admin/news-sources` with per-source refresh intervals, enable/disable controls, single or concurrency-limited bulk refresh, current status, next-run time, 24-hour success metrics, and 30-day run history.
- Independent news-source scheduler with per-source execution locks. Enabled and configured sources run on their own intervals even when platform automation is disabled; overlapping manual, scheduled, and automation refreshes are skipped safely.
- Source-aware news browsing: every persisted story exposes its source ID and name, and the news workspace supports a locally persisted multi-source filter plus one-click filtering from each story badge. The source picker searches source name, provider, and adapter with prefix/substring ranking and bounded typo tolerance. A fast refresh reloads persisted news for every user; administrators can also synchronize all or currently selected upstream sources before reloading the feed.
- News detail endpoint and in-app drawer for timeline/chart-marker clicks, including transparent rule classification, key points, related symbols, impact score, and original source link.
- Dedicated News Tagger Agent that classifies each article independently against the platform instrument catalog. Source-level symbols are candidate hints only; accepted primary tags drive symbol news and K-line markers, while secondary and medium-confidence tags remain visible in the detail/review workflow. Confidence thresholds default to 0.85 for automatic primary acceptance, 0.75 for accepted secondary context, and 0.60 for administrator review.
- Multi-stage news intelligence pipeline with independently managed Event Extractor, Impact Mapper, and Impact Validator Agents. It stores macro/company event scope, taxonomy, topics, geography, macro factors, horizon, significance, novelty, event clusters, multi-target impacts, and explainable transmission paths. Direct mentions auto-apply at 0.85 confidence; derived impacts require an approved graph path, 0.90 confidence, 0.75 relevance, and impact score 70 before entering symbol feeds or K-line markers.
- Administrator-managed news impact graph at `/admin/impact-graph`. Topic, macro-factor, country, and region exposures are proposed first and only become reusable by the Agent pipeline after explicit approval with evidence and provenance.
- High-confidence indirect impacts without an approved path automatically become deduplicated `impact-graph` Agent proposals. The proposal records its originating article, confidence, evidence, and transmission path; approval creates an audited graph edge and immediately remaps the source article, while rejection leaves the graph unchanged.
- Administrator-owned news symbol overrides in the news detail drawer. An override replaces all active automatic tags, supports up to five primary and eight secondary instruments, can explicitly lock an article as unrelated, and can later be released back to automatic detection. Primary tags appear in symbol feeds and K-line markers; secondary tags remain contextual only.
- AI real-time watch panel with market monitor, news analyst, and risk sentinel insights.
- Agent model control plane at `/admin/models` with provider presets, native OpenAI/Azure/Anthropic/Gemini protocols, OpenAI-compatible providers, local Ollama/vLLM channels, model discovery, encrypted credentials, health state, logical-model routing, priority/weight failover, metadata-only invocation logs, and administrator-only streaming chat tests.
- AI global briefing endpoint and right-rail panel summarizing watchlist breadth, top movers, news catalysts, alerts, portfolio exposure, opportunities, risks, and next actions.
- Factor recommendation cards, configurable backtest run form, SQLite-backed backtest persistence, metric calculation, and equity sparklines.
- SQLite-backed factor library with default seeded factors, custom factor creation, enable/disable controls, and dynamic backtest factor selection.
- SQLite-backed data catalog, research health gate, research dashboard summary, data-quality report, data-freshness SLA report, research coverage matrix and backfill, factor coverage matrix, factor lifecycle report, research data-lineage report, research run audit, research action plan, strategy candidate pool, maintainable strategy template library, strategy lifecycle report, strategy risk-control audit, strategy backtest monitor, strategy forward monitor, strategy validation decay report, strategy leaderboard, strategy crowding report, strategy regime-fit ranking, strategy regime admission matrix, strategy promotion gates, market-regime report, history and transition monitor, market-regime scenario risk report, market-regime exposure report, regime risk gate, research-readiness gate, filter validation, filter library scoring, filter-to-strategy coverage, filter admission pack, filter contribution diagnostics, filter stress test, regime-aware filter templates, market-regime playbook, forward-test runs, walk-forward rolling out-of-sample runs, stress-test runs, backtest benchmark comparison, backtest-to-forward consistency report, revalidation queue, trade-readiness gate, parameter sensitivity tests, parameter grid optimization, robustness matrix, strategy drift monitor, strategy allocation recommendations, factor leaderboard, factor correlation/crowding report, regime attribution report, factor admission checklist, factor admission queue, one-click factor validation pipeline, and one-click strategy validation that combines filters, backtests, forward tests, and regime checks.
- Administrator-only management center at `/admin` with one shared navigation and visual shell for operations overview, data/news sources, credentials, Agents, model services, schedules, and platform governance. Dedicated modules own business-resource writes; `/admin/resources` is limited to users, settings, audit, immutable run history, and read-only data governance.
- SQLite-backed alert rules for price, RSI, and news-impact thresholds, with manual evaluation, triggered-state display, enable/disable, threshold update, and deletion.
- SQLite-backed event log plus one-click automation run that refreshes news sources, evaluates alerts, records triggered alerts, and displays system events.
- Persisted automation scheduler settings with an in-process background loop for alert evaluation and Agent event triggering; news-source polling is managed independently per source.
- Unified scheduling center at `/admin/schedules` for platform automation, data/model health checks, ingestible news-source polling, and Agent schedules, with type/status filtering, interval editing, dependency-aware status, immediate runs, and bulk enable/disable.
- Native financial calendar in `/market-tools` with weekly navigation, macro/earnings tabs, country, source and impact filters, actual/forecast/previous comparisons, watchlist earnings, persistent event details, high-impact reminders, and Economic Event Analyst scenarios. Free official BLS and BEA calendars are aggregated by default; every canonical event keeps source snapshots, field-level provenance and conflict flags. Finnhub remains the earnings-calendar source, while Trading Economics is optional and disabled by default.
- SQLite-backed automation run history with status, news source count, inserted news, triggered alerts, summary, and frontend run-history display.
- SQLite-backed webhook notification channels and delivery logs; triggered alerts can be delivered as JSON payloads to enabled webhooks, and channels can be enabled, disabled, updated, or deleted.
- SQLite-backed simulated portfolio positions with mark-to-market valuation, unrealized PnL, long/short exposure, and frontend position management.
- Portfolio risk report API and panel covering gross/net exposure, largest-position concentration, stale prices, warnings, actions, and per-position risk contribution.
- System health endpoint and right-rail status panel summarizing database, active market provider, automation, news sources, alert rules, notifications, portfolio positions, and recent event-log risk/error state.
- Application settings endpoint and right-rail panel for persisted default filters, automation settings, provider summary, watchlist count, news source count, and notification channel count.

Target design below:

- Pluggable live and historical data providers.
- Additional U.S. equity provider adapters for Polygon/Alpaca beyond the current Finnhub integration.
- Additional production-grade vendor adapters and streaming feeds beyond the current Finnhub/GDELT/CryptoCompare/Alpha Vantage/FRED/RSS normalization layer.
- Additional multimodal, embedding, and rerank adapters beyond the current text-chat model gateway.
- Asynchronous/large-universe backtest jobs beyond the current single-symbol synchronous engine.
- Portfolio, alerting, explainability, and research workflow extensions.

### Indicator Data Architecture

Indicator data is always requested from FastAPI. The browser never calls a market or indicator vendor directly.

| Layer | Coverage | Source |
| --- | --- | --- |
| Local bar calculation | SMA/EMA/WMA/DEMA/TEMA/HMA/ALMA, Ichimoku, Supertrend, MACD, RSI, Stoch RSI, TRIX, PPO, ATR/NATR, Squeeze, OBV/PVT/NVI/PVI/KVO, and related indicators | `pandas-ta-classic` over provider OHLCV |
| Crypto derivatives | Open interest, long/short ratios, taker buy ratio, volume delta, CVD, funding, basis, and annualized variants | Binance Futures public REST |
| Macro chart series | Fed funds, 2Y/10Y yields, 10Y-2Y spread, CPI, unemployment | FRED REST (`FRED_API_KEY`) |
| Fundamentals and valuation | Market cap, growth, P/E, P/B, ROE, dividend yield, beta, latest EPS surprise | Finnhub primary, Alpha Vantage fallback |
| Sentiment snapshots | Reddit, X, news, and Polymarket bullish/activity metrics | Optional Adanos (`ADANOS_API_KEY`) |

The unified data-source registry owns provider priority and availability. Configure it at `/admin/data-sources`; API secrets are edited through `/admin/credentials` and take effect without rebuilding the image. Indicator snapshots use a 10-minute SQLite cache and may serve a marked stale result for up to 24 hours after an upstream failure. Missing credentials or exhausted vendor plans produce an explicit unavailable state; no synthetic business values are generated.

News-source APIs:

```text
GET   /api/news-sources
GET   /api/news-sources/overview
POST  /api/news-sources
PATCH /api/news-sources/{source_id}
POST  /api/news-sources/refresh?force=true&source_id=1&source_id=2
POST  /api/news-sources/{source_id}/refresh
POST  /api/news-sources/{source_id}/test
GET   /api/news-sources/monitoring
GET   /api/news-sources/{source_id}/runs
GET   /api/news?source_id=1&source_id=2&symbol=AAPL&limit=30&offset=0
GET   /api/news/{symbol}?source_id=1&source_id=2
GET   /api/news-tagging/status
POST  /api/news-tagging/backfill
POST  /api/news-events/{news_id}/retag
PUT   /api/news-events/{news_id}/symbols
DELETE /api/news-events/{news_id}/symbols/override
```

Every newly inserted article is queued for the built-in `news_tagger` Agent. Its
structured output stores primary/secondary relation, confidence, direction,
evidence, origin, model, run ID, and tagging version. High-confidence primary
tags are materialized into `news_articles.symbols`; medium-confidence tags
create `news-tags` proposals in `/agents`. Administrators can edit and approve
the proposed primary/secondary symbols. A 30-day backfill is intentionally
manual and becomes available after an enabled LLM/hybrid News Tagger has a
working model provider and credential.
The same control also supports a local-rule backfill of every currently unlinked
article. Rule backfills require no model credential and skip administrator-locked
articles, including articles explicitly confirmed as having no reliable symbol.

The `/news` workspace opens as an all-market, newest-first feed instead of a
single-symbol summary. It supports an all-market/current-symbol scope switch,
multi-source filtering, typo-tolerant source search across name/provider/adapter,
source attribution on every article, and incremental pagination. The workspace
does not duplicate source configuration controls: source creation, refresh
schedules, single/bulk refresh, run history, and health monitoring remain in the
dedicated `/news-sources` control center. Its overview endpoint combines source
configuration and monitor state in one request. The feed refresh control reloads
persisted news without contacting vendors. Its administrator-only sync action
accepts the active source filter, runs the existing ingestion pipeline, reports
fetched/inserted/failure totals, and then reloads the current feed.

Initial-page loading is route aware. The news route requests its feed first and
loads provider/settings metadata in the background; it does not open the market
WebSocket or download chart and market-map libraries. Those assets are loaded on
demand only when their corresponding workspace is opened.

RSS/Atom failures are stored as structured diagnostics on source runs. Each
report identifies the failing stage, assigns a stable error code, records retry
attempts, and provides a remediation suggestion. Response evidence is limited
to HTTP status, final hostname, content type, byte count, content hash, and
parser position; response bodies, URL query parameters, headers, cookies, and
credentials are never persisted. The connection test downloads and parses a
feed without inserting articles or changing its last successful collection
time.

Indicator APIs:

- `GET /api/instruments?query=...` searches built-in instruments and, when configured, Finnhub symbols.
- `GET /api/chart/indicators?instrument_id=...` returns compatibility and provenance metadata for all chart indicators.
- `POST /api/chart/query` returns candles and requested local, derivative, or macro series.
- `GET /api/indicator-snapshots/{instrument_id}` returns grouped fundamental, valuation, and sentiment cards with source metadata.

### Dashboard and Watchlist Workspace

The default route is now `/dashboard`. Its information architecture follows the
operational principles used by OpenStack Horizon: grouped navigation, a compact
resource summary, explicit health states, tabular detail, and direct links from
status to the responsible management surface. It is an application workspace,
not a marketing landing page.

Dashboard provides:

- Six linked KPIs for platform health, market provider, Agents, automation,
  news sources, and notifications.
- Component-level health, recent system events, Agent runs, automation runs,
  and a compact active-Watchlist summary.
- Role-aware quick actions for refreshing news and running monitoring jobs.
- A read-only operational view for viewer accounts; write actions remain
  available only to administrators.

`/watchlist` is a standalone resource-management page rather than a panel inside
the market chart. It supports:

- Active and disabled tabs with soft disable and restore semantics.
- User-defined groups with color markers; deleting a non-default group moves
  its members back to the default group.
- Per-symbol tags and notes, search, group/market/signal filters, sortable
  indicator columns, pagination, row actions, and bulk move/status actions.
- A detail drawer for editing metadata without leaving the list and a direct
  action for opening the selected symbol in the K-line workspace.
- Live snapshot updates from the market WebSocket without reloading metadata.

The complete workspace supports light and dark themes. The first visit follows
the operating-system preference, the selected theme is cached locally for fast
paint, and authenticated preference is persisted per account in SQLite. Chart
canvas colors are resolved from the active theme. Dashboard performs a focused
first-page load; the larger research workstation is loaded on first entry to a
legacy analysis view.

Relevant APIs:

```text
GET   /api/dashboard/overview
GET   /api/watchlist/groups
POST  /api/watchlist/groups
PATCH /api/watchlist/groups/{group_id}
DELETE /api/watchlist/groups/{group_id}
GET   /api/watchlist/items
POST  /api/watchlist/items
PATCH /api/watchlist/items/{symbol}
POST  /api/watchlist/items/bulk-actions
GET   /api/auth/preferences
PATCH /api/auth/preferences
```

SQLite migrations add `watchlist_groups`, Watchlist metadata columns
(`group_id`, `tags`, `note`, `sort_order`, and `updated_at`), and
`user_preferences`. Existing Watchlist rows are retained and assigned to a
provider-specific default group.

### Professional Chart Workstation

The market workspace uses a locally hosted Lightweight Charts runtime and a
backend-normalized chart API. Browsers never call market vendors directly.
Every response includes the active provider, real-time/delayed/cache state,
fetch time, timezone, and any fallback warning.

Supported chart controls:

- Intervals: `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `1d`,
  `1w`, and `1mo`.
- Ranges: `1D`, `5D`, `1M`, `3M`, `6M`, `YTD`, `1Y`, `3Y`, `5Y`, and
  `Max`.
- Interval and range controls enforce a meaningful chart window: `2h/4h`
  require at least `5D`, daily candles require `1M`, weekly candles require
  `6M`, and monthly candles require `3Y`. Incompatible buttons are disabled in
  both directions; the UI never changes the other selection automatically.
  The backend applies the same validation to chart queries and saved layouts.
  Existing invalid layouts remain stored but are rejected when selected.
- Main-chart studies: SMA, EMA, Bollinger Bands, VWAP, Ichimoku,
  Supertrend, Donchian Channel, Keltner Channel, and Parabolic SAR.
- Indicator panes: Volume, MACD, RSI, Stochastic, CCI, ADX/DMI, ATR, OBV,
  MFI, ROC, Williams %R, CMF, A/D, and Force Index.
- Crypto-perpetual metrics: open interest and change, global and top-trader
  long/short ratios, taker buy/sell ratio, funding rate, and basis.
- Every chart pane has a persistent TradingView-style legend. The main pane
  shows OHLC plus main-chart studies, while each indicator pane shows its own
  name, parameters, component colors, and value. Crosshair movement updates
  each pane at the selected candle and restores the latest value on exit.

The instrument registry covers U.S. equities and ETFs, BTC/ETH perpetuals,
and major global indices including the Shanghai Composite, Shenzhen
Component, CSI 300, Hang Seng, S&P 500, Nasdaq 100, Dow Jones, Russell 2000,
VIX, FTSE 100, DAX, and Nikkei 225. Provider capability determines which
intervals and derivative studies are enabled.

Data adapters use Finnhub for configured U.S. assets, Binance USD-M Futures
for perpetual candles and derivative statistics, and delayed public index
data for the global index catalog. When a provider is unavailable, the
backend can serve a recent cache or an explicitly labeled public/spot
fallback. Derivative metrics are never synthesized from spot data.

Indicator layouts are persisted per account and asset class. A layout stores
the interval, range, pane order, visibility, colors, and parameters, and can
be selected as the default for that asset class. Alert rules accept the same
canonical instrument IDs, interval, indicator ID, and parameter object used
by the chart.

News events are normalized and snapped on the backend to the nearest prior
candle. Lightweight Charts renders them as non-autoscaling native markers so
they remain attached to time/price while avoiding price-scale distortion.
Clicking a marker opens the existing news detail workflow.

Relevant APIs:

```text
GET    /api/instruments
GET    /api/chart/indicators
POST   /api/chart/query
WS     /api/stream/chart
GET    /api/chart/layouts
POST   /api/chart/layouts
PATCH  /api/chart/layouts/{layout_id}
DELETE /api/chart/layouts/{layout_id}
```

### Unified Information Sources

The original RSS-only resource has been expanded into a unified information
source registry while retaining the `/api/news-sources` compatibility API.
The design follows OpenStock's composition model: use specialized providers for
their strongest capabilities instead of forcing market data, visualization,
news, filings, macro releases, and sentiment through one vendor.

Every source records:

- Category: `market`, `news`, `filing`, `macro`, `sentiment`, `social`, or
  `alternative`.
- Transport and adapter: RSS/Atom, REST API, WebSocket, widget, or reference.
- Provider, endpoint, region, language, symbol scope, and capabilities.
- Authentication mode and the environment-variable name containing the key;
  secret values are never persisted or returned by the API.
- Poll interval, priority, reliability weight, enabled state, last successful
  fetch, and last error.

Fresh databases receive disabled catalog entries for:

| Source | Category | Adapter | Key |
| --- | --- | --- | --- |
| Finnhub Market News | News/company/quotes | REST | `FINNHUB_API_KEY` |
| Trading Economics | Economic calendar/consensus forecasts | REST | `TRADING_ECONOMICS_API_KEY` |
| U.S. Bureau of Labor Statistics | Official U.S. release calendar | iCalendar | None |
| U.S. Bureau of Economic Analysis | Official GDP, PCE and trade release calendar | JSON | None |
| Federal Reserve | Official FOMC meeting and decision calendar | HTML | None |
| National Bureau of Statistics of China | Official China statistical release calendar | HTML table | None |
| European Central Bank | Official euro-area statistical release calendar | HTML | None |
| TradingView Market Widgets | Charts/heatmap/timeline | Widget | None |
| Adanos | Reddit/X/news/Polymarket sentiment | REST | `ADANOS_API_KEY` |
| SEC EDGAR Latest Filings | Regulatory filings | Atom | None |
| Federal Reserve Press Releases | Macro/rates | RSS | None |
| GDELT Global Financial News | Global multilingual news | REST | None |
| CryptoCompare Digital Asset News | Crypto news | REST | Optional `CRYPTOCOMPARE_API_KEY` |
| Alpha Vantage News Sentiment | News and ticker sentiment | REST | `ALPHAVANTAGE_API_KEY` |
| FRED Economic Releases | Macro releases | REST | `FRED_API_KEY` |

The ingest pipeline currently normalizes RSS/Atom, Finnhub, Adanos, GDELT,
CryptoCompare, Alpha Vantage, and FRED responses into the existing
`news_articles` store. It deduplicates URLs, extracts known symbols, classifies
sentiment/event type, and feeds chart markers and Agent context. Macro releases
fall back to `SPY/QQQ`; crypto-wide stories fall back to `BTCUSD/ETHUSD`.
Adanos uses its documented Reddit, X, News, and Polymarket stock comparison
endpoints, then maps buzz score, bullish percentage, trend, and activity into
the platform's normalized sentiment events. Widget/reference entries remain
catalog metadata and are not fetched as articles.

Automatic monitoring refreshes only enabled sources whose individual polling
interval has elapsed. Manual refresh uses `force=true`. Administrators can
create, edit, enable, disable, refresh, inspect, export, and bulk-update sources
from the resource management page.

The unified data-source control plane is available at `/data-sources`. It maps
the existing content feeds and the built-in market/index adapters into managed
profiles, then routes requests by market and capability. Each fallback route
has an ordered primary/secondary chain; news routes use aggregate mode.
Configuration changes validate every affected source before an atomic publish,
retain revision history, and can be rolled back. Runtime failures update a
persistent circuit-breaker state: three consecutive failures mark a source
offline and three successful probes restore it. Market-tool responses expose
the actual source, health state, cache state, and fallback reason.

```text
GET   /api/information-sources
POST  /api/information-sources
GET   /api/information-sources/summary
GET   /api/information-sources/health
PATCH /api/information-sources/{source_id}
POST  /api/information-sources/{source_id}/refresh
POST  /api/information-sources/refresh?force=true
GET   /api/data-sources
POST  /api/data-sources/{source_id}/test
GET   /api/data-source-routes
PUT   /api/data-source-routes/{market}/{capability}
GET   /api/data-source-config/revisions
```

Provider behavior and limits must be reviewed before enabling production
feeds. See the [OpenStock integration inventory](https://github.com/Open-Dev-Society/OpenStock#-data--integrations),
[Finnhub authentication and rate limits](https://api.finnhub.io/docs/api/rate-limit),
[SEC EDGAR RSS documentation](https://www.sec.gov/about/rss-feeds), and
[GDELT DOC API documentation](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/).

### Finnhub Market Provider

Finnhub is available as a selectable server-side market provider alongside the
Binance and Coinbase adapters. The browser calls only the FastAPI
endpoints; `FINNHUB_API_KEY` is appended to upstream quote and candle requests
inside the backend and is never returned to the frontend. The initial supported
universe is `AAPL`, `NVDA`, `TSLA`, `MSFT`, `SPY`, and `QQQ`. Quotes are
normalized into Watchlist snapshots, while candles feed the same MA/RSI,
indicator, and backtest contracts used by the other providers.

```text
FINNHUB_API_KEY=...
FINNHUB_BASE_URL=https://finnhub.io/api/v1
TRADING_ECONOMICS_API_KEY=...
ADANOS_API_KEY=...
ADANOS_API_BASE_URL=https://api.adanos.org
```

When a Finnhub key is absent, the provider remains visible as degraded and
cannot serve live requests. The API returns an explicit upstream error unless
a still-valid cache or another configured real provider route can serve the request.

### Native Market Tools

`/market-tools` uses an OpenStock-inspired four-panel information architecture:
market index trends, a volume-weighted stock heatmap, quote rankings, and top
stories. The implementation is independent and uses the platform's FastAPI
contracts (`/api/chart/query`, `/api/screener`, `/api/market-breadth`, and
`/api/market-news`) instead of browser-side TradingView requests. Heatmap tiles
are rendered with the locally vendored BSD-licensed `d3-hierarchy` package;
selecting a tile or quote opens the native market workspace, and selecting a
story opens the existing news detail drawer.

### Encrypted Credential Management

Administrators can manage API keys from `/credentials`, from an information
source edit form, or from the Agent model-provider editor. Credentials are
encrypted with Fernet before they are written to SQLite. API responses expose
only configuration state, origin, a masked hint, version, bound resources, and
connection-test status; plaintext and ciphertext are never returned.

Configure the master key once before using page-based secret writes:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Store the result in the deployment environment:

```text
APP_CREDENTIAL_MASTER_KEY=...
APP_CREDENTIAL_ASYNC_TESTS=true
```

The runtime resolution order is encrypted SQLite override first, then the
resource's existing environment-variable alias. Removing a page override
restores environment fallback. Existing environment-only deployments remain
compatible and are not automatically copied into SQLite.

Finnhub market data and Finnhub news share the same `FINNHUB_API_KEY`
credential binding. Rotating that credential invalidates the Finnhub quote
cache and affects both resources on their next outbound request. Information
sources, Agent providers, and future providers use the same binding mechanism.
Saving a key takes effect immediately; connection checks run afterward and do
not roll back the new value if an upstream service is unavailable.

```text
GET    /api/credentials
POST   /api/credentials
PATCH  /api/credentials/{credential_id}
PUT    /api/credentials/{credential_id}/secret
DELETE /api/credentials/{credential_id}/secret
POST   /api/credentials/{credential_id}/test
PUT    /api/market-providers/{provider_id}/credential
```

## Product Design

### Core Workflows

1. **Real-time monitoring**
   - Watch selected symbols across stocks, ETFs, and crypto.
   - Track price, volume, volatility, spread, trend score, factor score, news sentiment, and risk level.
   - Surface unusual activity such as volume spikes, gap moves, trend breaks, liquidation-like crypto moves, and news-driven jumps.

2. **News-aware chart analysis**
   - Subscribe to RSS feeds, mainstream financial media APIs, market data vendor news, and hot-topic feeds.
   - Map relevant news to symbols and draw event markers directly on the K-line chart.
   - Hover markers to see summary, source, sentiment, affected symbols, and impact score.
   - Click markers to open a detail drawer with AI summary, original link, related events, and post-news price reaction.

3. **AI real-time watch agent**
   - Continuously analyze price, indicators, news, and risk context.
   - Generate concise risk prompts, catalyst explanations, and market briefings.
   - Recommend factors worth testing based on current market regime and recent news.

4. **Factor research and backtesting**
   - Build single-factor and multi-factor strategies.
   - Test factor performance on selected universes and time windows.
   - Compare return, drawdown, Sharpe, Sortino, win rate, turnover, IC, and Rank IC.

5. **Quant workstation expansion**
   - Maintain simulated portfolios, watchlists, alerts, factor dashboards, market breadth, sector heatmaps, and research notebooks.

#### Unified Scheduling

The scheduling center provides one operational view over the platform's existing
schedulers without duplicating their configuration:

- `automation:market-monitor` maps to the platform automation loop that refreshes
  due information sources and evaluates alerts.
- `news_source:{id}` maps to each ingestible source's enabled state and polling
  interval. A source is shown as blocked when the platform automation loop is
  disabled.
- `agent:{id}` maps to each Agent's independent schedule, interval,
  `last_run_at`, and `next_run_at`. A schedule is shown as blocked when its Agent
  instance is disabled.

Administrators can edit intervals, enable or pause schedules, run a task
immediately, and apply bulk status changes. Viewer accounts receive the same
read-only operational view. Mutations use the existing CSRF and administrator
middleware and are recorded in the administrator audit log.

```text
GET   /api/schedules
GET   /api/schedules/{task_id}
PATCH /api/schedules/{task_id}
POST  /api/schedules/{task_id}/run
POST  /api/schedules/bulk-status
```

### Navigation

The first screen should remain the actual trading workstation, not a marketing page.

- Left nav: `行情`, `新闻`, `AI盯盘`, `因子`, `回测`, `组合`, `预警`, `设置`.
- Main chart area: K-line chart, technical indicators, volume, news/event markers, and drawing tools.
- Right rail: AI Agent cards, risk radar, key news, factor recommendations, and symbol context.
- Bottom panel: watchlist, news timeline, backtest results, simulated positions, and alert logs.

## System Architecture

### Current Project Layout

The application is a modular FastAPI monolith organized first by business
capability, with transport and browser delivery kept at the outer boundary:

```text
app/
├── main.py                  # Minimal Uvicorn/ASGI entry point
├── bootstrap/               # Explicit router composition and process lifecycle
├── api/
│   └── routes/              # Thin HTTP/WebSocket transport adapters
├── core/                    # Application factory and environment configuration
├── db/                      # SQLite connection infrastructure
├── modules/                 # Business capabilities and public service APIs
│   ├── ai/                  # Agents and model gateway
│   ├── calendar/            # Economic and earnings calendars
│   ├── integrations/        # External data-source control plane
│   ├── market/              # Instruments, charts, watchlists, and indicators
│   ├── news/                # Sources, ingestion, tagging, and impact graph
│   ├── operations/          # Schedules, health, and background execution
│   ├── platform/            # Auth, settings, credentials, and audit
│   ├── research/            # Factors, experiments, strategies, and backtests
│   └── trading/             # Alerts, portfolio, and paper trading
├── schemas/                 # Pydantic transport and domain contracts
├── services/
│   └── runtime.py           # Static legacy import compatibility facade
└── web/
    ├── pages/               # FastAPI-served HTML pages
    ├── router.py            # Browser page routes
    └── static/              # Core, workspace, admin, styles, and vendor assets
```

Dependencies point inward: route modules use schemas and domain services;
domain modules may call another domain's public service but never import API
routers; schemas do not depend on HTTP handlers. Router registration is explicit
in `app/bootstrap/application.py`, and the exact 316-route surface is protected
by `tests/contracts/routes.json`.

New endpoints belong in the matching `app/api/routes` module, new Pydantic models
belong in the matching `app/schemas` module, and environment/path configuration
belongs in `app/core/config.py`. New domain logic belongs in the owning
`app/modules` capability rather than `services/runtime.py`; that module contains
only explicit legacy re-exports for existing scripts and tests. The browser
workspace uses native ES Modules and ordered CSS imports, so deployment requires
no frontend build toolchain.

### High-level Components

- **FastAPI API server**
  - Serves REST APIs, static frontend assets, provider configuration, and job orchestration endpoints.
  - Exposes normalized market, news, factor, AI, and backtest APIs.

- **Provider adapter layer**
  - Wraps third-party market and news providers behind stable internal interfaces.
  - Allows switching between REST polling, WebSocket streaming, and batch historical ingestion.

- **Market data service**
  - Handles symbols, snapshots, candles, trades, quotes, order book, and technical indicator calculations.
  - Normalizes provider-specific schemas into internal models.

- **News and event service**
  - Pulls RSS/API/social feeds, deduplicates stories, extracts symbols, scores relevance, and stores chart-ready events.

- **AI Agent service**
  - Runs monitoring, analysis, risk, briefing, and factor ideation tasks.
  - Produces auditable outputs with source links, confidence, assumptions, and timestamps.

- **Factor and backtest service**
  - Computes factors, builds signals, simulates portfolios, and stores backtest runs.

- **Storage**
  - PostgreSQL for metadata, watchlists, news, AI outputs, alerts, and backtest definitions.
  - TimescaleDB or ClickHouse for candles, ticks, and factor time series when data volume grows.
  - Redis for cache, pub/sub, job state, WebSocket fanout, and rate-limit coordination.

### Data Flow

1. Provider adapters ingest raw market/news data.
2. Normalizers convert raw payloads into canonical internal models.
3. Enrichment jobs calculate indicators, extract symbols, score events, and classify sentiment.
4. FastAPI exposes data to the frontend through REST and WebSocket endpoints.
5. AI Agents subscribe to enriched streams and produce prompts, summaries, and risk notes.
6. Chart UI overlays candles, indicators, alerts, factor signals, and news markers on the same timeline.

## Market Data Source Design

### Supported Access Modes

- **REST polling**
  - Suitable for snapshots, historical candles, reference data, and lower-frequency watchlists.
  - Uses provider rate-limit aware polling intervals.

- **WebSocket streaming**
  - Suitable for real-time trades, quotes, aggregate bars, crypto prices, and alert triggers.
  - Requires reconnect logic, heartbeat checks, backoff, and stream resubscription.

- **Batch historical ingestion**
  - Suitable for backtesting, factor research, and nightly data repair.
  - Should persist immutable historical bars and provider lineage.

### Candidate Providers

U.S. stocks and ETFs:

- Polygon/Massive Stocks API: REST, WebSocket, tickers, snapshots, aggregate bars, trades, quotes, technical indicators, and news-style related data.
- Alpaca Market Data API: stock and crypto market data with Trading API compatibility for future paper trading.
- IEX Cloud or Finnhub: optional alternatives for quotes, fundamentals, news, and company data.

Crypto:

- Binance Spot WebSocket streams for trades, klines, tickers, and depth updates.
- Coinbase Advanced Trade WebSocket for market data and exchange-grade crypto streams.
- CCXT as an optional adapter abstraction for multi-exchange REST access.

### Provider Adapter Interface

Each market provider adapter should implement:

- `list_symbols(asset_class, market) -> list[Symbol]`
- `get_snapshot(symbols) -> list[QuoteSnapshot]`
- `get_candles(symbol, interval, start, end, limit) -> list[Candle]`
- `stream_trades(symbols) -> AsyncIterator[TradeTick]`
- `stream_quotes(symbols) -> AsyncIterator[QuoteTick]`
- `get_corporate_actions(symbol, start, end) -> list[CorporateAction]`
- `healthcheck() -> ProviderHealth`

Provider results must include:

- `provider`
- `provider_symbol`
- `received_at`
- `event_time`
- `latency_ms` when available
- `is_delayed`
- `source_plan` or subscription tier when known

### Canonical Market Models

```text
Symbol
- id
- symbol
- display_symbol
- name
- asset_class: stock | etf | crypto
- market
- exchange
- currency
- timezone
- active

QuoteSnapshot
- symbol
- price
- change
- change_percent
- bid
- ask
- bid_size
- ask_size
- volume
- vwap
- open
- high
- low
- previous_close
- event_time
- provider

Candle
- symbol
- interval
- time
- open
- high
- low
- close
- volume
- vwap
- trades
- provider

TradeTick
- symbol
- price
- size
- exchange
- conditions
- event_time
- provider

OrderBookLevel
- symbol
- side
- price
- size
- level
- event_time
- provider
```

## News Subscription and Event Design

### News Source Types

- **RSS feeds**
  - Support RSS 2.0 feeds from financial media, company investor relations pages, exchange notices, SEC-like announcement pages, and crypto project blogs.

- **News API aggregators**
  - Support APIs such as NewsAPI-style `everything` search with query, source, domain, date range, language, sort, and pagination.

- **Market-data vendor news**
  - Support vendor-provided market news and symbol-linked articles when available.

- **Mainstream media and financial platforms**
  - Support Bloomberg-like, Reuters-like, CNBC-like, Yahoo Finance-like, and CoinDesk-like feeds where licensing allows.

- **Hot-topic and social feeds**
  - Support Reddit, X/Twitter-like streams, Google Trends-like sources, and crypto community feeds as optional sentiment inputs.
  - These sources should be tagged as noisy and never treated as verified news without corroboration.

### Subscription Modes

- By symbol: `AAPL`, `NVDA`, `BTCUSD`.
- By watchlist: all current user-selected symbols.
- By sector/theme: semiconductors, AI, crypto miners, mega-cap tech, ETF flows.
- By keyword: earnings, guidance, SEC, lawsuit, ETF approval, hack, liquidation.
- By source: RSS URL, media domain, vendor feed, social community.
- By importance: breaking only, high-impact only, all news.
- By session: pre-market, regular hours, after-hours, 24/7 crypto.

### News Processing Pipeline

1. Fetch article/feed items.
2. Normalize title, description, body snippet, source, URL, author, and published time.
3. Deduplicate by canonical URL, title similarity, source cluster, and published time.
4. Extract symbols and entities from title/body.
5. Classify language and translate/normalize when needed.
6. Classify event type: earnings, macro, regulatory, M&A, product, funding, security incident, ETF flow, analyst rating, social trend.
7. Score sentiment: bullish, bearish, neutral, mixed.
8. Score impact: 0-100 based on source credibility, novelty, entity match, market reaction, and topic severity.
9. Persist as `NewsArticle` and `ChartEvent`.
10. Notify AI Agent and frontend subscribers.

### Canonical News Models

```text
NewsSource
- id
- name
- type: rss | rest_api | websocket | widget | reference
- category: market | news | filing | macro | sentiment | social | alternative
- adapter
- provider
- url
- enabled
- symbols
- poll_interval_seconds
- priority
- reliability_weight
- auth_type
- api_key_env
- capabilities
- config
- region
- language
- last_fetched_at
- last_error

NewsArticle
- id
- source_id
- title
- summary
- url
- image_url
- author
- published_at
- fetched_at
- symbols
- entities
- language
- sentiment
- impact_score
- event_type
- dedupe_key

ChartEvent
- id
- symbol
- article_id
- event_time
- event_type
- sentiment
- impact_score
- marker_type
- title
- summary
- source_name
```

## K-line News Marker Design

News events should render directly on the K-line chart timeline.

### Marker Placement

- Map `ChartEvent.event_time` to the closest candle bucket.
- Use vertical stacking when multiple events occur on the same candle.
- Keep marker collision rules deterministic so chart layout does not jump during refresh.
- For daily candles, group intraday events into a compact stack with count badges.

### Marker Visual Types

- Green upward marker: bullish event.
- Red downward marker: bearish event.
- Gray dot: neutral article.
- Yellow diamond: earnings, guidance, or macro event.
- Blue dot: product, partnership, or analyst event.
- Purple marker: crypto-specific event such as ETF flow, exchange listing, exploit, bridge incident, or liquidation cluster.

### Hover Tooltip

Tooltip fields:

- headline
- source
- published time
- AI summary
- sentiment
- impact score
- affected symbols
- immediate price reaction when available

### Click Detail Drawer

The detail drawer should show:

- original article link
- source metadata
- full AI summary
- event classification
- affected symbols and confidence
- related previous events
- price reaction after 5m, 30m, 1h, 1d
- user actions: pin, hide source, create alert, send to AI analysis, add to backtest event set

### Filters

- Current symbol only.
- Watchlist only.
- High-impact only.
- Positive, negative, neutral, or mixed.
- Event type.
- Source type.
- Trading session.

## AI Agent Module

AI should operate as a set of specialized agents with explicit permissions and auditable outputs.

### Agents

#### Market Monitor Agent

Purpose:

- Watch real-time price, volume, spread, volatility, technical indicators, factor scores, and news events.
- Detect abnormal moves and explain likely causes.

Outputs:

- "NVDA volume is 2.3x its 20-day intraday baseline while price is testing yesterday's high."
- "BTCUSD has increased volatility after a negative regulatory headline; avoid treating the move as purely technical."

#### News Analyst Agent

Purpose:

- Summarize articles.
- Extract affected symbols.
- Classify event type and sentiment.
- Compare with historical similar events.

Outputs:

- short summary
- bullish/bearish/mixed score
- affected symbols
- uncertainty notes
- source links

#### Risk Sentinel Agent

Purpose:

- Identify portfolio and watchlist risks.
- Detect earnings proximity, gap risk, sharp drawdown, liquidity deterioration, crowded factor exposure, and crypto weekend/event risk.

Outputs:

- risk card
- severity
- affected symbols
- reason
- suggested user review action

#### Factor Research Agent

Purpose:

- Suggest factors worth testing based on market regime, recent news, and historical behavior.
- Convert natural-language factor ideas into backtest configuration.

Examples:

- "Test a 20-day momentum factor gated by positive news sentiment."
- "Test post-earnings drift for mega-cap tech with above-average volume."
- "Test BTC breakout after volatility compression and positive ETF-flow news."

#### Briefing Agent

Purpose:

- Generate pre-market, intraday, post-market, and crypto 24/7 briefings.

Briefing sections:

- market overview
- top movers
- high-impact news
- watchlist risks
- factor regime
- upcoming events

### AI Output Contract

Every AI output must include:

- `agent_name`
- `created_at`
- `symbols`
- `summary`
- `evidence`
- `confidence`
- `risk_level`
- `source_ids`
- `suggested_next_actions`
- `disclaimer`

AI outputs must avoid pretending to know private or unavailable information. The UI should show "insufficient evidence" when source coverage is weak.

### AI Runtime Design

- Triggered by WebSocket market events, new high-impact news, user request, scheduled briefing, or backtest completion.
- Uses retrieval over local market/news/factor data before generating output.
- Stores selected route/channel/model, status, latency, token usage, and redacted errors; prompt and response bodies are not persisted.
- Supports user feedback: useful, not useful, wrong symbol, wrong sentiment, hide similar.

### Agent Management Center

The running application exposes a dedicated Agent control plane at `/agents`. It is served by FastAPI with the rest of the static frontend and therefore does not require a separate frontend deployment.

Five built-in Agent templates are initialized idempotently:

- `market_monitor`: price, indicator, trend, and unusual-move monitoring.
- `news_analyst`: news impact, sentiment, symbol, and catalyst analysis.
- `risk_sentinel`: portfolio, drawdown, indicator-break, and news-risk monitoring.
- `factor_research`: factor research proposals that must be approved before entering the factor library.
- `briefing`: watchlist-wide market, news, opportunity, risk, and action summaries.

Administrators can clone templates into custom Agent instances and configure:

- `rule`, `llm`, or `hybrid` runtime mode.
- watchlist, full-universe, or explicit symbol scope.
- independent interval scheduling and event triggers.
- template-specific thresholds stored as validated JSON.
- system/task prompts, logical model, notification channel IDs, and rule fallback.

Viewers can inspect Agent configuration, runs, and proposals but cannot mutate or execute resources. Model channels, credentials, health tests, invocation logs, and the test chat are administrator-only.

#### Model Channels, Routes, and Secrets

The model gateway separates upstream channels from logical models. An Agent binds to a logical model; its enabled targets select an upstream channel/model by priority, weight, and current health. Network, rate-limit, authentication, and server failures can fail over before output begins. Three consecutive failures open the channel circuit for five minutes.

Built-in presets cover OpenAI, Azure OpenAI, Anthropic, Gemini, DeepSeek, DashScope/Qwen, Zhipu GLM, VolcEngine/Doubao, Moonshot/Kimi, SiliconFlow, OpenRouter, xAI, Mistral, Ollama, vLLM, and generic OpenAI-compatible endpoints. Model names can be synchronized from supporting upstreams or entered manually.

API keys use the shared encrypted credential vault and environment fallback. Secrets are never returned by APIs or included in model invocation/audit output. Invocation logs retain only route, channel, model, source, status, latency, token counts, and a redacted error.

For the default Compose setup, export the key before starting the service:

```bash
export OPENAI_API_KEY="..."
docker compose up -d --build
```

Create channels and logical models at `/model-management`, then select the logical model in `/agents`. Use `/model-playground` for multi-turn streaming validation. If all route targets fail and Agent fallback is enabled, the run succeeds with its rule-based result and records the model failure as fallback context.

#### Scheduling, Runs, and Approval

Agent work is persisted in `agent_runs`. The in-process worker checks schedules, consumes queued work, prevents duplicate claiming, and records output, errors, latency, model, and token usage. Supported triggers are `manual`, `schedule`, `high_impact_news`, `alert_triggered`, and `backtest_completed`.

Test runs are isolated: they do not create proposals, update the last production run, or send notifications. Production Agent outputs may create proposals for factors, alerts, or watchlist resources. A proposal does not mutate any resource until an administrator explicitly approves it; approval, rejection, execution, and failure are retained in both proposal history and the administrator audit log.

Primary APIs:

- `GET/POST/PATCH /api/agents`
- `POST /api/agents/{agent_id}/clone`
- `POST /api/agents/{agent_id}/run` and `/test`
- `GET /api/agents/{agent_id}/runs` and `GET /api/agents/runs`
- `GET/POST/PATCH/DELETE /api/model-channels`
- `POST /api/model-channels/{channel_id}/test` and `/sync-models`
- `GET/POST/PATCH/DELETE /api/model-routes`
- `GET /api/model-invocations`
- `POST /api/model-playground/chat` (SSE)
- Legacy `/api/agent-providers` endpoints remain available for compatibility.
- `GET /api/agent-proposals`
- `POST /api/agent-proposals/{proposal_id}/approve` or `/reject`

## Factor Library and Backtesting

### Factor Categories

- **Price momentum**
  - 5/20/60-day return
  - moving-average slope
  - breakout above range

- **Mean reversion**
  - RSI extremes
  - z-score distance from moving average
  - gap fade

- **Volatility**
  - realized volatility
  - ATR expansion/compression
  - volatility breakout

- **Volume and liquidity**
  - volume surge
  - dollar volume
  - turnover
  - spread proxy

- **Trend quality**
  - higher-high/higher-low score
  - MA alignment
  - trend persistence

- **News sentiment**
  - event impact score
  - sentiment score
  - source credibility-weighted news count
  - post-news drift

- **Event-driven**
  - earnings drift
  - analyst rating changes
  - regulatory events
  - product launch events
  - ETF approval/fund-flow events

- **Crypto-specific**
  - funding-rate proxy where available
  - volatility compression
  - exchange listing events
  - liquidity shock
  - token unlock or security incident news

### Backtest Workflow

1. Select universe: watchlist, S&P 500-like set, top-volume crypto, custom symbols.
2. Select date range and candle interval.
3. Choose one or more factors.
4. Configure factor transforms: rank, z-score, winsorize, smoothing, lag.
5. Generate signals: long-only, long/short simulation, threshold-based, top-N, weighted score.
6. Configure portfolio rules: rebalance frequency, max position weight, cash handling.
7. Configure costs: commission, spread, slippage, borrow cost placeholder.
8. Run backtest job.
9. Review equity curve, drawdown, trades, exposure, factor IC, and attribution.
10. Save strategy template or ask AI Agent for interpretation.

### Backtest Metrics

- total return
- annualized return
- volatility
- max drawdown
- Sharpe ratio
- Sortino ratio
- Calmar ratio
- win rate
- profit factor
- average holding period
- turnover
- exposure
- alpha/beta versus benchmark
- IC and Rank IC
- hit rate by market regime

### Backtest Models

```text
FactorDefinition
- id
- name
- category
- expression
- required_inputs
- default_params
- description

BacktestConfig
- id
- name
- universe
- start
- end
- interval
- factors
- signal_rule
- portfolio_rule
- cost_model
- benchmark

BacktestRun
- id
- config_id
- status
- started_at
- finished_at
- metrics
- equity_curve
- drawdown_curve
- trades
- logs
```

## Public API Design

The API should remain simple REST for user actions and use WebSocket for live streams.

### Market APIs

- `GET /api/symbols`
  - Query supported symbols by asset class, market, exchange, keyword, and active status.

- `GET /api/watchlist`
  - Return watchlist snapshots with price, change, volume, indicators, factor score, and risk level.

- `POST /api/watchlist`
  - Add a symbol supported by the active market provider to the persisted watchlist.

- `DELETE /api/watchlist/{symbol}`
  - Remove a symbol from the persisted watchlist for the active market provider.

- `GET /api/market-breadth`
  - Return active-watchlist breadth statistics, signal distribution, and market-level heatmap groups.

- `GET /api/screener`
  - Filter the active watchlist by technical and market conditions and return ranked matches with reasons.

- `GET /api/screener/presets`, `POST /api/screener/presets`, and `DELETE /api/screener/presets/{id}`
  - Manage reusable screener templates for active-watchlist research workflows.

- `GET /api/screener/preset-validations`
  - Validate saved screener templates against the current active watchlist and return pass/watch/fail status, match count, warnings, and suggestions.

- `WS /api/stream/market`
  - Push active provider status, watchlist snapshots, and market breadth at a configurable interval.

- `GET /api/candles/{symbol}`
  - Return normalized candles with optional indicator overlays.

- `GET /api/indicators/{symbol}`
  - Return current technical indicator summaries.

- `WS /api/stream/market`
  - Stream snapshots, trades, quotes, candles, alerts, and chart events.

### News APIs

- `GET /api/news`
  - Return the persisted all-market news feed with optional repeated
    `source_id`, `symbol`, `limit`, and `offset` parameters. The response also
    includes source facets for the active market/symbol scope.

- `GET /api/news/{id}`
  - Return article details, AI summary, source metadata, and related events.

- `GET /api/chart-events/{symbol}`
  - Return chart-ready news/event markers for the K-line view.

- `GET /api/news-events/{id}`
  - Return local news detail for a chart or timeline event, including summary, key points, related symbols, and original URL.

- `GET /api/information-sources`, `POST /api/information-sources`
  - List or add RSS/API/widget/reference source configuration.

- `PATCH /api/information-sources/{id}`
  - Enable, disable, or update polling settings.

### AI APIs

- `GET /api/ai/briefing`
  - Generate a watchlist-wide market briefing with opportunities, risks, suggested actions, and evidence.

- `POST /api/ai/briefing`
  - Generate market/watchlist briefing.

- `POST /api/ai/analyze-news`
  - Analyze one article or a cluster of articles.

- `POST /api/ai/risk-check`
  - Analyze risk for a symbol, watchlist, or simulated portfolio.

- `POST /api/ai/recommend-factors`
  - Recommend factor ideas based on market regime and recent events.

- `GET /api/ai/outputs`
  - List previous AI outputs with filters.

### Factor and Backtest APIs

- `GET /api/factors`
  - List available factors.

- `GET /api/factor-definitions`
  - List persisted factor definitions, optionally filtering to enabled factors only.

- `POST /api/factor-definitions`
  - Create a custom factor definition for recommendation and backtest selection.

- `PATCH /api/factor-definitions/{factor_id}`
  - Enable or disable a factor definition.

- `GET /api/data-catalog`
  - List current market, news, factor, strategy, and research-run datasets.

- `GET /api/data-quality`
  - Summarize provider health, watchlist coverage, K-line completeness, news/factor/strategy coverage, and research-run readiness.

- `GET /api/data-freshness`
  - Score market, news, research-run, factor, strategy, and regime datasets against freshness SLA windows.

- `GET /api/strategy-templates`
  - List persisted strategy templates with default filters, factor bindings, risk controls, and validation rules.

- `GET /api/strategy-leaderboard`
  - Rank strategy templates by enabled state, factor/filter coverage, latest validation result, and market-state fit.

- `GET /api/strategy-regime-fit`
  - Rank strategy templates for the current market regime by default filter validity, bound factor regime fit, enabled state, and latest validation status.

- `GET /api/strategy-regime-matrix`
  - Compare each strategy's open/reduce/pause/block decision across risk-on, risk-off, trend, range, and volatile regimes.

- `GET /api/strategy-crowding`
  - Compare enabled strategies for shared factors, filter overlap, and concurrent current-regime decisions to flag strategy-library crowding.

- `GET /api/strategy-lifecycle`
  - Summarize each strategy's stage from draft/validation to candidate, active, or paused.

- `GET /api/strategy-promotion-gates`
  - List promotion gate reports for all strategies, combining data quality, research readiness, latest validation, and strategy rank.

- `GET /api/strategy-promotion-gates/{strategy_id}`
  - Return the promotion gate report for one strategy.

- `POST /api/strategy-templates`
  - Create a strategy template from selected factors and filter settings.

- `PATCH /api/strategy-templates/{strategy_id}`
  - Update, enable, or disable a strategy template.

- `DELETE /api/strategy-templates/{strategy_id}`
  - Remove a custom strategy template.

- `POST /api/strategy-templates/{strategy_id}/validate`
  - Validate a strategy template by running filter validation, backtests, forward tests, and market-regime checks.

- `GET /api/strategy-validations`
  - List persisted strategy validation reports.

- `GET /api/strategy-validation-decay`
  - Rank latest strategy validations by backtest-to-forward decay, degradation ratio, sample counts, and current regime context.

- `GET /api/strategy-risk-controls`
  - Audit strategy risk controls, validation rules, promotion gates, lifecycle stage, crowding, and validation-decay status.

- `GET /api/market-state-controls`
  - Combine local data quality, research readiness, strategy regime matrix, crowding, validation decay, and risk-control audit into one market-state operating decision.

- `GET /api/strategy-operating-plan`
  - Produce per-strategy open/observe/reduce/block decisions and suggested simulation weight caps from market-state controls, promotion gates, lifecycle, regime matrix, backtest/forward monitors, and risk-control audit.

- `GET /api/strategy-evidence-packs`
  - Summarize each strategy's validation evidence chain, including filters, latest validation, backtest/forward returns, decay, gates, risk controls, gaps, and next actions.

- `GET /api/strategy-sensitivity`
  - Aggregate existing backtest and forward-test runs into per-strategy robustness sensitivity scores across factors, symbols, intervals, forward windows, return dispersion, degradation dispersion, and drawdown.

- `GET /api/strategy-revalidation-plan`
  - Rank strategies that need revalidation under the current market regime, using the strategy regime matrix, sensitivity report, evidence packs, and lifecycle stage.

- `GET /api/strategy-validation-coverage`
  - Show a per-strategy validation coverage matrix across backtests, forward tests, walk-forward tests, stress tests, bound factors, and symbols, with explicit missing tasks.

- `GET /api/market-regime`
  - Return current breadth, volatility, trend, liquidity, and risk-state classification.

- `GET /api/market-regime/history`
  - List persisted market-regime snapshots for reviewing recent state changes.

- `GET /api/market-regime/transition`
  - Compare the current market regime with the latest previous snapshot and flag transition risk, score deltas, and risk-control actions.

- `GET /api/regime-scenario-risk`
  - Preview strategy availability, open/reduce/pause/block counts, scenario score, and risk budget across risk-on, risk-off, trend, range, and volatile regimes.

- `GET /api/regime-exposure`
  - Aggregate watchlist-level allocation, factor crowding, and risk budgets into a current market-regime exposure report.

- `GET /api/research-readiness`
  - Return a research readiness gate combining system health, data coverage, market regime, strategy validation coverage, and latest validation results.

- `GET /api/research-coverage`
  - Summarize per-symbol research coverage across backtests, forward tests, walk-forward runs, and stress tests.

- `POST /api/research-coverage/backfill`
  - Run a bounded set of missing research tasks to improve per-symbol coverage.

- `GET /api/factor-coverage`
  - Summarize research coverage per enabled factor across watchlist symbols, including backtest, forward, walk-forward, and stress coverage.

- `GET /api/factor-lifecycle`
  - Summarize each factor's stage from idea/research/validation to candidate, active, or paused.

- `GET /api/data-lineage`
  - Trace research dependencies from datasets to factors, strategies, and validation coverage gaps.

- `POST /api/factor-coverage/backfill`
  - Run bounded missing research tasks for one factor across watchlist symbols to close factor-level coverage gaps.

- `GET /api/research-action-plan`
  - Aggregate data-quality, readiness, coverage, and strategy-gate findings into prioritized research actions.

- `GET /api/research-health`
  - Combine readiness, factor lifecycle, run audit, market exposure, and filter admission into a go/caution/stop research gate.

- `GET /api/strategy-candidates`
  - Combine screener matches, enabled factors, and trade-readiness checks into ranked symbol/factor candidates.

- `GET /api/research-dashboard`
  - Return KPI-style research dashboard summary across data quality, readiness, coverage, strategy gates, and market regime.

- `POST /api/filters/validate`
  - Validate screener/filter settings against current candidates and market state.

- `GET /api/filters/library-score`
  - Score saved filter presets using validation, contribution concentration, stress stability, and market-regime admission checks.

- `GET /api/filters/strategy-coverage`
  - Compare saved filter presets with strategy default filters to identify reused templates, orphan presets, and unregistered strategy filters.

- `POST /api/filters/contribution`
  - Diagnose how much each active filter constrains the candidate universe and suggest which conditions to relax first.

- `POST /api/filters/admission`
  - Combine filter validation, contribution diagnostics, stress testing, and market regime into an accept/revise/reject admission pack.

- `POST /api/filters/stress-test`
  - Stress-test screener filters by relaxing and tightening RSI/trend thresholds, scoring match-count stability and actions.

- `GET /api/filters/regime-templates`
  - Return market-regime-aware screener templates with match counts, validation warnings, and rationale.

- `GET /api/regime-playbook`
  - Return a market-regime playbook with preferred factors, filter templates, strategy budget actions, and risk controls.

- `GET /api/backtest-benchmark/{symbol}`
  - Compare the latest factor backtest with a buy-and-hold benchmark using alpha, drawdown delta, and Sharpe delta.

- `GET /api/backtest-forward-consistency/{symbol}`
  - Score whether the latest backtest is supported by forward, walk-forward, and stress-test evidence for one symbol/factor pair.

- `POST /api/forward-tests`
  - Run a forward test that compares a training segment with an out-of-sample segment.

- `GET /api/forward-tests/{symbol}`
  - List forward-test runs for a symbol.

- `GET /api/forward-leaderboard`
  - Rank the latest forward-test results by symbol/factor with out-of-sample return, degradation, drawdown, and actions.

- `GET /api/strategy-backtest-monitor`
  - Aggregate latest backtest evidence by strategy, including factor coverage, run count, positive-return count, return, Sharpe, win rate, drawdown, freshness, and actions.

- `GET /api/strategy-forward-monitor`
  - Aggregate latest forward-test evidence by strategy, including factor coverage, pass/watch/fail counts, freshness, degradation, and actions.

- `POST /api/walk-forward-tests`
  - Run rolling out-of-sample validation across multiple train/forward windows with pass rate and stability scoring.

- `GET /api/walk-forward-tests/{symbol}`
  - List persisted walk-forward validation runs for a symbol.

- `GET /api/walk-forward-leaderboard`
  - Rank latest rolling out-of-sample results by stability score, pass rate, average/worst forward return, and drawdown.

- `POST /api/stress-tests`
  - Run scenario stress tests for a factor and symbol under risk-off, volatility, and liquidity shocks.

- `GET /api/stress-tests/{symbol}`
  - List stress-test runs for a symbol.

- `GET /api/stress-leaderboard`
  - Rank latest stress-test results by worst return, worst drawdown, scenario pass count, and resilience actions.

- `GET /api/robustness/{symbol}`
  - Summarize enabled factors against latest backtests, forward tests, walk-forward runs, stress tests, and the current market regime.

- `GET /api/drift/{symbol}`
  - Compare latest backtest baselines with forward, walk-forward, stress, and market-regime alignment to flag strategy drift.

- `GET /api/allocation/{symbol}`
  - Convert factor rankings, drift, robustness, market regime, and portfolio risk into recommended strategy weights and controls.

- `GET /api/trade-readiness/{symbol}`
  - Return a trade gate for one symbol/factor using filters, market regime, robustness, drift, allocation, revalidation gaps, news risk, and portfolio risk.

- `GET /api/regime-risk-gate/{symbol}`
  - Combine market regime, regime transition risk, trade readiness, and allocation budget into an execution-state gate.

- `POST /api/research-pipelines`
  - Run filter validation, backtest, forward test, stress test, and robustness scoring for one symbol/factor pair.

- `POST /api/factor-admission`
  - Build a pre-simulation admission checklist across filters, robustness, drift, allocation, correlation, regime attribution, and parameter optimization.

- `GET /api/factor-admission-queue`
  - Rank current watchlist symbol/factor candidates for the next full admission check.

- `GET /api/revalidation-queue`
  - List missing, stale, or failed backtest/forward/walk-forward/stress tasks that should be rerun.

- `GET /api/research-run-audit`
  - Return a unified latest-run audit trail across backtests, forward tests, walk-forward tests, and stress tests.

- `POST /api/sensitivity-tests`
  - Run parameter sensitivity checks across intervals and fee assumptions for one symbol/factor pair.

- `POST /api/parameter-optimization`
  - Rank RSI/trend threshold combinations for one symbol/factor pair without persisting every grid run as a backtest.

- `GET /api/factor-leaderboard/{symbol}`
  - Rank enabled factors by robustness, latest backtest performance, forward-test result, and stress-test resilience.

- `GET /api/factor-correlation/{symbol}`
  - Compare enabled factor signal series for correlation, overlap, exposure rate, and crowding risk.

- `GET /api/regime-attribution/{symbol}`
  - Attribute one factor's simulated performance across risk-on, risk-off, trend, range, and volatile market slices.

- `GET /api/regime-factor-fit/{symbol}`
  - Rank enabled factors by current market-regime fit, using factor leaderboard scores and regime attribution.

- `POST /api/factors/evaluate`
  - Compute factor values for a universe and time range.

- `POST /api/backtests`
  - Start a backtest job.

- `GET /api/backtests/{id}`
  - Fetch status, logs, metrics, and result artifacts.

- `GET /api/backtests/{id}/trades`
  - Fetch simulated trades.

- `GET /api/backtests/{id}/equity`
  - Fetch equity and drawdown series.

### Authentication and Resource Management APIs

- `GET /api/auth/setup-status`, `POST /api/auth/setup`
  - Detect an empty user database and transactionally create the first administrator. Public registration is not available after setup.

- `POST /api/auth/login`, `GET /api/auth/me`, `POST /api/auth/logout`
  - Manage eight-hour database-backed sessions. State-changing requests require the session CSRF token in `X-CSRF-Token`.

- `GET/POST /api/admin/users`, `PATCH /api/admin/users/{id}`
  - Create administrators or viewers, update roles and status, while preventing self-disable and removal of the final active administrator.

- `POST /api/admin/users/{id}/reset-password`, `POST /api/admin/users/{id}/revoke-sessions`
  - Reset credentials or revoke active sessions without deleting the account.

- `GET /api/admin/resources`
  - List every managed local resource with category, record count, active count, mutability, and supported actions.

- `GET/POST /api/admin/resources/{type}`, `GET/PATCH /api/admin/resources/{type}/{id}`
  - Search and paginate resources, create supported business resources, inspect details, and edit allowlisted fields.

- `PATCH /api/admin/resources/{type}/{id}/status`, `POST /api/admin/resources/{type}/bulk-status`
  - Enable, disable, hide, or restore resources. The application does not permanently delete business or historical data.

- `GET /api/admin/resources/{type}/export`, `POST /api/admin/resources/{type}/{id}/rerun`
  - Export managed resources as CSV or create a new research run from a persisted historical request.

The full application requires login. Administrators can mutate resources and run jobs; viewers can browse, filter, inspect, and export. On a new database, open `/setup`; after setup use `/login`, and open `/admin` for resource management.

### Current Prototype Operations APIs

- `GET /api/export/{dataset}`
  - Export core and research datasets as CSV. Supported datasets include `watchlist`, `screener`, `portfolio`, `portfolio-risk`, `backtests`, `data-quality`, `data-freshness`, `research-coverage`, `research-health`, `factor-lifecycle`, `strategy-lifecycle`, `strategy-risk-controls`, `market-state-controls`, `strategy-operating-plan`, `strategy-evidence-packs`, `strategy-sensitivity`, `strategy-revalidation-plan`, `strategy-validation-coverage`, `strategy-backtest-monitor`, `strategy-forward-monitor`, `strategy-validation-decay`, `filter-library`, `filter-strategy-coverage`, `research-run-audit`, `data-lineage`, `regime-exposure`, `regime-scenario-risk`, `strategy-regime-matrix`, `strategy-crowding`, `research-action-plan`, `strategy-leaderboard`, and `strategy-promotion-gates`.

- `GET /api/settings` and `PATCH /api/settings`
  - Read or update persisted application settings, including default filters, automation settings, and active provider preference.

- `GET /api/health`
  - Return aggregated system health for database readiness, active market provider, automation settings, news source errors, enabled alert rules, webhook delivery health, simulated portfolio coverage, and recent event-log risk/error counts.

- `GET /api/events`
  - Return recent system, news refresh, automation, and alert-trigger events.

- `GET /api/alerts`, `POST /api/alerts`, `PATCH /api/alerts/{id}`, `DELETE /api/alerts/{id}`, and `POST /api/alerts/evaluate`
  - Manage alert rules, enable or disable them, delete obsolete rules, and evaluate active alerts.

- `GET /api/automation/settings` and `POST /api/automation/settings`
  - Read or update the in-process scheduler configuration.

- `POST /api/automation/run`
  - Manually run one monitoring cycle: refresh enabled news sources, evaluate enabled alerts, record events, and dispatch webhook notifications for triggered alerts.

- `GET /api/automation/runs`
  - Return recent persisted automation run records for operations review.

- `GET /api/notifications/channels`, `POST /api/notifications/channels`, `PATCH /api/notifications/channels/{id}`, `DELETE /api/notifications/channels/{id}`, and `GET /api/notifications/deliveries`
  - Manage webhook channels, enable or disable delivery, delete obsolete channels, and inspect delivery results.

- `GET /api/portfolio`, `POST /api/portfolio/positions`, and `DELETE /api/portfolio/positions/{id}`
  - Manage simulated positions and mark them to market with the active provider.

- `GET /api/portfolio/risk`
  - Return portfolio risk analytics including concentration, net exposure, warnings, actions, and per-position contribution.

## Frontend Design

### Chart Workstation

- Candlestick canvas or charting library area.
- Indicator overlays: MA, EMA, VWAP, Bollinger Bands, MACD, RSI, volume.
- Event overlays: news markers, AI risk markers, factor entry/exit markers, earnings markers.
- Interval selector: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`, `1w`.
- Symbol search with asset class badges.
- Layout presets: monitor, news, research, backtest.

### AI Panel

- Live feed of AI observations.
- Risk cards with severity levels.
- One-click actions:
  - explain current move
  - summarize latest news
  - identify risks
  - suggest factors
  - create alert
  - send event to backtest

### News Panel

- Timeline with source, time, sentiment, impact, and affected symbols.
- Filters by source, event type, impact, sentiment, and symbol.
- Hot news mode for market-wide catalysts.
- Detail drawer synchronized with chart marker selection.

### Factor and Backtest Panel

- Factor catalog with category filters.
- Natural-language factor idea input.
- Backtest configuration builder.
- Result dashboard with equity curve, drawdown, metrics, trades, and regime breakdown.
- Compare multiple runs side by side.

### Alerts and Risk

- Price alerts.
- Indicator alerts.
- News keyword alerts.
- AI risk alerts.
- Factor signal alerts.
- Portfolio exposure alerts.

Delivery channels can include in-app notifications first, then email, webhook, Slack/Discord/Telegram-style integrations later.

## Quant Extensions

Recommended future modules:

- Market breadth dashboard: advance/decline, new highs/lows, sector strength.
- Sector and theme heatmaps.
- Correlation matrix and rolling beta.
- Regime detection: trend, volatility, risk-on/risk-off, crypto liquidity regime.
- Portfolio simulator with positions, PnL, exposure, and drawdown.
- Event study engine for earnings/news/regulatory events.
- Strategy notebook for saving hypotheses, charts, and AI notes.
- Screener with technical, fundamental, news, and factor filters.
- Data quality monitor for gaps, stale quotes, duplicate candles, and provider outages.
- Audit log for AI prompts, alerts, source data, and user decisions.
- Paper trading integration after the risk engine is stable.

## Implementation Roadmap

### Phase 1: Data and News Foundation

- Introduce provider adapter interfaces.
- Use Coinbase as the credential-free default provider and Finnhub for U.S. equities.
- Add canonical market and news models.
- Add RSS ingestion and manual source configuration.
- Add chart event API and render persisted source-backed news markers on the K-line chart.

### Phase 2: AI Agent Workspace

- Add AI output storage.
- Build Market Monitor, News Analyst, Risk Sentinel, Briefing, and Factor Research Agent contracts.
- Add AI side panel and event-triggered risk cards.
- Add source-backed summaries with confidence and evidence.

### Phase 3: Factor and Backtesting

- Add factor catalog and factor evaluation service.
- Add backtest config and async job model.
- Build result dashboard with metrics, equity curve, drawdown, and simulated trades.
- Connect AI factor recommendations to backtest templates.

### Phase 4: Live Providers and Streaming

- Add real provider adapters for selected stock and crypto sources.
- Add WebSocket market stream.
- Add Redis-backed pub/sub and cache.
- Add provider health monitoring and stale data warnings.

### Phase 5: Portfolio and Alerts

- Add simulated portfolios.
- Add alert rules and notification channels.
- Add event study and multi-run comparison.
- Add paper trading only after permission, audit, and risk controls are implemented.

## Engineering Notes

- Default development mode must continue to run without paid API keys.
- All real provider credentials should come from environment variables, not source code.
- Provider adapters must expose whether data is real-time or delayed.
- News and AI outputs must preserve source links for auditability.
- Backtests must use lagged data to avoid look-ahead bias.
- Corporate actions and splits must be handled before using historical stock data for backtests.
- Crypto data is 24/7; stocks require exchange calendars, market sessions, pre-market, and after-hours handling.
- AI-generated content must include disclaimer, confidence, and evidence.

## References

- Polygon/Massive Stocks API: https://polygon.io/docs/stocks/getting-started
- Alpaca Market Data API: https://docs.alpaca.markets/docs/about-market-data-api
- Binance WebSocket Streams: https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams
- Coinbase Advanced Trade WebSocket: https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-overview
- NewsAPI Everything endpoint: https://newsapi.org/docs/endpoints/everything
- RSS 2.0 specification: https://www.rssboard.org/rss-specification

## Paper Trading

The market workspace includes a user-isolated paper-trading terminal rather than editable position cards.

- USD, CNY, and USDT subaccounts are created automatically with independent cash balances.
- The order ticket supports market, limit, stop, and stop-limit orders, DAY/GTC duration, long/short books, and optional OCO take-profit/stop-loss brackets.
- Market orders use the latest non-stale quote with 1 bp adverse slippage. Pending orders are checked every 60 seconds against candles created after submission; the schedule can be managed from the unified task center.
- Fills, cash movements, fees, realized P&L, and open positions are persisted as separate auditable records. Position cost uses moving weighted average; full and partial closes are supported.
- The chart displays average-cost, TP/SL, and pending-order lines plus explicit blue/red paper-fill arrows. News remains on its own event track.
- The first release assumes full fills because the configured feeds do not provide a reliable order book. Shorts reserve 100% collateral; leverage, funding rates, liquidation, and perpetual-contract trading are intentionally unavailable.
- Legacy `portfolio_positions` records are migrated once to synthetic opening fills owned by the earliest administrator. Legacy portfolio APIs remain available for compatibility but the workspace uses `/api/paper/*`.

Paper trading is an approximation for research and interface testing. It is not broker execution and must not be presented as actual transaction evidence.

## Run

### Local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`. A new database redirects to `/setup` to create the first administrator; subsequent visits use `/login`.

Optional environment variables:

- `APP_DB_PATH`: SQLite path, default `app_data.db`.
- `APP_COOKIE_SECURE`: set to `true` behind production HTTPS so the session cookie is HTTPS-only.

### Docker

Build the image:

```bash
docker build -t app-trading .
```

Run the container:

```bash
docker run --rm -p 8000:8000 app-trading
```

Open `http://localhost:8000`.

Persist the SQLite database on the host:

```bash
docker run --rm -p 8000:8000 -v "$(pwd)/app_data.db:/app/app_data.db" app-trading
```

### Docker Compose

Start the service:

```bash
docker compose up -d --build
```

Open `http://localhost:8002`.

If port `8002` is already in use, override the host port:

```bash
APP_PORT=8003 docker compose up -d --build
```

Then open `http://localhost:8003`.

View logs:

```bash
docker compose logs -f app-trading
```

Stop the service:

```bash
docker compose down
```

The Compose setup mounts `./app_data.db` to `/app/app_data.db` so local SQLite data is preserved across container restarts.

## Test

```bash
source .venv/bin/activate
pytest
```
