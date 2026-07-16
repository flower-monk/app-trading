# App Trading

App Trading is a FastAPI-based market watch, indicator, news, AI analysis, and factor backtesting dashboard. The current codebase is a runnable prototype with mock market data and a TradingView-inspired dark workstation. This document defines the target product and technical design for evolving it into a quant-oriented monitoring platform for U.S. equities, ETFs, and major crypto assets.

The product is designed for active investors, quant researchers, and semi-systematic traders who need one workspace for price action, indicators, news catalysts, AI risk prompts, and factor research. AI output is treated as research support and risk context, not as direct investment advice or default automated trading.

## Current Prototype

Implemented today:

- FastAPI backend serving the page and mock APIs.
- Dark market-watch UI with watchlist, candlestick chart, MA20/MA60 overlays, RSI/MACD summaries, simple signal filters, and symbol switching.
- Mock symbols covering U.S. stocks, ETFs, and crypto pairs.
- Market provider adapter interface, active-provider config, provider status API, and mock provider implementation used by watchlist, candles, indicators, and backtests.
- SQLite-backed per-provider watchlist customization with add/remove APIs and frontend controls.
- Market breadth API and dashboard panel summarizing advance/decline, average change, RSI/trend score, signal distribution, and market-level heatmap for the active watchlist.
- Server-side screener API and dashboard panel for filtering the active watchlist by change, RSI, trend score, signal, and market, with match reasons.
- SQLite-backed screener presets for saving, loading, validating, and deleting reusable active-watchlist screening templates.
- WebSocket market stream that pushes active watchlist snapshots and market breadth to the frontend for live dashboard refresh.
- CSV export API and panel download actions for watchlist, screener results, portfolio positions, portfolio risk contribution, and backtest summaries.
- Coinbase public REST crypto provider for live BTC-USD and ETH-USD ticker/candles/indicator estimates, selectable from the UI.
- Binance public REST provider skeleton for BTCUSDT and ETHUSDT; access can be region-restricted and may return HTTP 451.
- Mock news/event APIs with chart markers, hover summaries, and clickable source links.
- SQLite-backed RSS news source management, refresh, article deduplication, symbol mapping, and persisted chart events.
- News detail endpoint and in-app drawer for timeline/chart-marker clicks, including AI-style summary, key points, related symbols, impact score, and original source link.
- AI real-time watch panel with market monitor, news analyst, and risk sentinel insights.
- AI global briefing endpoint and right-rail panel summarizing watchlist breadth, top movers, news catalysts, alerts, portfolio exposure, opportunities, risks, and next actions.
- Factor recommendation cards, configurable backtest run form, SQLite-backed backtest persistence, metric calculation, and equity sparklines.
- SQLite-backed factor library with default seeded factors, custom factor creation, enable/disable controls, and dynamic backtest factor selection.
- SQLite-backed data catalog, research health gate, research dashboard summary, data-quality report, data-freshness SLA report, research coverage matrix and backfill, factor coverage matrix, factor lifecycle report, research data-lineage report, research run audit, research action plan, strategy candidate pool, maintainable strategy template library, strategy lifecycle report, strategy risk-control audit, strategy backtest monitor, strategy forward monitor, strategy validation decay report, strategy leaderboard, strategy crowding report, strategy regime-fit ranking, strategy regime admission matrix, strategy promotion gates, market-regime report, history and transition monitor, market-regime scenario risk report, market-regime exposure report, regime risk gate, research-readiness gate, filter validation, filter library scoring, filter-to-strategy coverage, filter admission pack, filter contribution diagnostics, filter stress test, regime-aware filter templates, market-regime playbook, forward-test runs, walk-forward rolling out-of-sample runs, stress-test runs, backtest benchmark comparison, backtest-to-forward consistency report, revalidation queue, trade-readiness gate, parameter sensitivity tests, parameter grid optimization, robustness matrix, strategy drift monitor, strategy allocation recommendations, factor leaderboard, factor correlation/crowding report, regime attribution report, factor admission checklist, factor admission queue, one-click factor validation pipeline, and one-click strategy validation that combines filters, backtests, forward tests, and regime checks.
- Authenticated resource management center with administrator/viewer roles, first-run administrator setup, CSRF-protected writes, persisted sessions, full resource inventory, search, pagination, details, CSV export, soft disable/restore, bulk status changes, immutable run history, reruns, and administrator audit logs.
- SQLite-backed alert rules for price, RSI, and news-impact thresholds, with manual evaluation, triggered-state display, enable/disable, threshold update, and deletion.
- SQLite-backed event log plus one-click automation run that refreshes news sources, evaluates alerts, records triggered alerts, and displays system events.
- Persisted automation scheduler settings with an in-process background loop for interval-based news refresh and alert evaluation.
- SQLite-backed automation run history with status, news source count, inserted news, triggered alerts, summary, and frontend run-history display.
- SQLite-backed webhook notification channels and delivery logs; triggered alerts can be delivered as JSON payloads to enabled webhooks, and channels can be enabled, disabled, updated, or deleted.
- SQLite-backed simulated portfolio positions with mark-to-market valuation, unrealized PnL, long/short exposure, and frontend position management.
- Portfolio risk report API and panel covering gross/net exposure, largest-position concentration, stale prices, warnings, actions, and per-position risk contribution.
- System health endpoint and right-rail status panel summarizing database, active market provider, automation, news sources, alert rules, notifications, portfolio positions, and recent event-log risk/error state.
- Application settings endpoint and right-rail panel for persisted default filters, automation settings, provider summary, watchlist count, news source count, and notification channel count.

Target design below:

- Pluggable live and historical data providers.
- Real U.S. equity provider adapters for Polygon/Alpaca beyond the current mock and crypto providers.
- Additional vendor/API news ingestion beyond the current RSS implementation.
- Real AI provider integration beyond current deterministic mock Agent outputs.
- Asynchronous/large-universe backtest jobs beyond the current single-symbol synchronous engine.
- Portfolio, alerting, explainability, and research workflow extensions.

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

### Navigation

The first screen should remain the actual trading workstation, not a marketing page.

- Left nav: `行情`, `新闻`, `AI盯盘`, `因子`, `回测`, `组合`, `预警`, `设置`.
- Main chart area: K-line chart, technical indicators, volume, news/event markers, and drawing tools.
- Right rail: AI Agent cards, risk radar, key news, factor recommendations, and symbol context.
- Bottom panel: watchlist, news timeline, backtest results, simulated positions, and alert logs.

## System Architecture

### High-level Components

- **FastAPI API server**
  - Serves REST APIs, static frontend assets, provider configuration, and job orchestration endpoints.
  - Exposes normalized market, news, factor, AI, and backtest APIs.

- **Provider adapter layer**
  - Wraps third-party market and news providers behind stable internal interfaces.
  - Allows switching between mock, REST polling, WebSocket streaming, and batch historical ingestion.

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

- **Mock mode**
  - Current default for development and UI prototyping.
  - Deterministic generated candles and snapshots.

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
- type: rss | api | vendor | social | manual
- url
- enabled
- poll_interval_seconds
- credibility_score
- language

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
- Stores prompts, source IDs, model name, response, latency, and token usage for audit.
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
- system/task prompts, model provider, notification channel IDs, and rule fallback.

Viewers can inspect all configuration, runs, model status, and proposals but cannot mutate or execute resources.

#### Model Providers and Secrets

Model providers use the OpenAI-compatible `POST {base_url}/chat/completions` contract. Provider records store only the base URL, model, limits, and the name of an environment variable. API keys are never stored in SQLite, returned by the API, or included in run/audit output.

For the default Compose setup, export the key before starting the service:

```bash
export OPENAI_API_KEY="..."
docker compose up -d --build
```

In `/agents`, create a provider whose key environment variable is `OPENAI_API_KEY`. Other gateways can use any uppercase environment variable passed into the container. If a model call fails and fallback is enabled, the run succeeds with a rule-based result and records the model error as fallback context.

#### Scheduling, Runs, and Approval

Agent work is persisted in `agent_runs`. The in-process worker checks schedules, consumes queued work, prevents duplicate claiming, and records output, errors, latency, model, and token usage. Supported triggers are `manual`, `schedule`, `high_impact_news`, `alert_triggered`, and `backtest_completed`.

Test runs are isolated: they do not create proposals, update the last production run, or send notifications. Production Agent outputs may create proposals for factors, alerts, or watchlist resources. A proposal does not mutate any resource until an administrator explicitly approves it; approval, rejection, execution, and failure are retained in both proposal history and the administrator audit log.

Primary APIs:

- `GET/POST/PATCH /api/agents`
- `POST /api/agents/{agent_id}/clone`
- `POST /api/agents/{agent_id}/run` and `/test`
- `GET /api/agents/{agent_id}/runs` and `GET /api/agents/runs`
- `GET/POST/PATCH /api/agent-providers`
- `POST /api/agent-providers/{provider_id}/test`
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
  - Filter by symbol, source, sentiment, event type, impact score, and time range.

- `GET /api/news/{id}`
  - Return article details, AI summary, source metadata, and related events.

- `GET /api/chart-events/{symbol}`
  - Return chart-ready news/event markers for the K-line view.

- `GET /api/news-events/{id}`
  - Return local news detail for a chart or timeline event, including summary, key points, related symbols, and original URL.

- `POST /api/news/sources`
  - Add RSS/API/manual source configuration.

- `PATCH /api/news/sources/{id}`
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
- Keep mock provider as default.
- Add canonical market and news models.
- Add RSS ingestion and manual source configuration.
- Add chart event API and render mock news markers on the K-line chart.

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
