# Architecture

Open Trading is a modular FastAPI monolith. It keeps one deployable process and
one SQLite database while separating code by business capability. This gives
the project clear ownership boundaries without adding distributed-system
operational cost.

## Runtime Flow

```text
app.main:app
  -> bootstrap.application.create_app
     -> core.application.create_application
     -> bootstrap.middleware
     -> web.router
     -> api.routes.*
  -> bootstrap.lifecycle.lifespan
     -> database initialization
     -> independently named background jobs
```

`app/main.py` must remain a small ASGI entry point. Router registration belongs
to `bootstrap/application.py`; startup and shutdown behavior belongs to
`bootstrap/lifecycle.py`.

## Business Modules

| Module | Owns |
| --- | --- |
| `infrastructure` | Schema bootstrap, seeds, and database compatibility |
| `platform` | Authentication, users, settings, credentials, and audit |
| `integrations` | Unified external data-source registry and routing |
| `market` | Instruments, providers, watchlists, screeners, charts, indicators |
| `news` | Source ingestion, articles, tagging, impact graph, provenance |
| `ai` | Model gateway, channels, Agents, runs, proposals |
| `research` | Data governance, factors, experiments, strategies, backtests |
| `trading` | Alerts, notifications, portfolio, automation, paper trading |
| `calendar` | Economic and earnings calendar aggregation |
| `operations` | Health, dashboard summaries, schedules, background execution |

## Agent Workflow Control Plane

`app.modules.ai.workflow` owns versioned workflow definitions, the controlled
tool registry, LangGraph compilation, durable checkpoints, execution traces,
approval interrupts, and investment artifacts. Capability Agents remain in
`app.modules.ai.service`; a workflow invokes them through an explicit Agent
node and records the child run against the workflow trace and parent span.

Workflow dependencies are one-way: triggers and read tools gather domain data,
Agent nodes produce structured analysis, conditions route on explicit fields,
and output nodes create typed investment artifacts. No workflow node may call
an arbitrary URL, SQL statement, credential API, or trading operation.

The research domain is further split by responsibility:

- `catalog.py`: quality, freshness, lineage, coverage, and exports.
- `factors.py`: factor definitions and signal simulation.
- `experiments.py`: optimization, forward/walk-forward/stress validation.
- `strategy.py`: admission, allocation, regime, and readiness decisions.
- `backtesting.py`: backtest engine and benchmark calculations.
- `service.py`: stable public facade for existing callers.

## Dependency Rules

1. `api/routes` may depend on schemas and domain service APIs. Routes should
   validate transport concerns and delegate business work.
2. Domain modules may use `core`, `db`, schemas, and another domain's public
   service API. They must not directly import FastAPI routers.
3. `web` owns HTML delivery and browser assets; it must not contain business
   persistence logic.
4. `bootstrap` composes the process. Domain modules must never import it.
5. New code must not import `app.services.runtime` or add behavior to it.
6. External provider calls stay server-side and return provenance and explicit
   unavailable states. Missing data must not be replaced with mock business data.

Cross-domain calls that can form an import cycle use
`app.modules._dependencies.service_call`, which resolves the target only when it
is invoked. Every target must be another domain service; targeting
`app.api.routes` is prohibited by the architecture tests. Prefer an ordinary
import when it does not introduce a cycle, and move multi-domain orchestration
into an owning application service rather than an HTTP handler.

## Frontend Ownership

```text
app/web/static/
├── core/                 # Theme, localization, and shared admin shell
├── workspace/
│   ├── core/             # State, HTTP, formatting, and shared workspace UI
│   ├── features/         # Dashboard, market tools, research, and backtesting
│   ├── bootstrap.js      # Workspace composition and remaining feature wiring
│   └── professional-chart.js
├── admin/                # Resource-specific administration screens
├── styles/
│   └── workspace/        # Ordered workspace CSS modules
└── vendor/               # Vendored browser dependencies
```

Pages are outside `/static`, so protected HTML is served only through FastAPI
routes and authentication middleware. URL paths and API contracts remain
unchanged by this physical reorganization. The workspace uses native browser ES
Modules; no Node build step or generated bundle is required.

## Compatibility Migration

The staged migration preserves `app/services/*` as thin import facades for old
extensions and the existing test harness. Production startup, routers, and
background jobs use `app/modules` directly. Compatibility facades must contain
no new business behavior and can be removed after downstream imports have moved.
`app/services/runtime.py` uses explicit static re-exports only; it must not
discover or traverse routers dynamically.

The exact FastAPI route surface is snapshotted in `tests/contracts/routes.json`.
Any intentional endpoint change must update that contract in the same change;
directory-only refactors must leave it untouched.

For a new feature:

1. Put request/response contracts in the appropriate schema file.
2. Implement business behavior in the owning domain module.
3. Add a thin endpoint in the matching router.
4. Place browser code in `core`, `workspace`, or `admin` according to ownership.
5. Add focused behavior tests and keep `tests/test_architecture.py` passing.
