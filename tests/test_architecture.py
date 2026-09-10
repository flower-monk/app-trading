from __future__ import annotations

import ast
import json
from pathlib import Path

from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Mount, Route

from app.bootstrap.application import app


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def referenced_internal_modules(path: Path) -> set[str]:
    """Include lazy module references passed as string arguments."""

    modules = imported_modules(path)
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("app."):
                modules.add(node.value)
    return modules


def test_main_is_only_the_asgi_entry_point() -> None:
    tree = ast.parse((APP / "main.py").read_text())
    assert not [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert imported_modules(APP / "main.py") == {"app.bootstrap.application"}


def test_production_code_does_not_import_legacy_runtime() -> None:
    production_paths = [
        *APP.joinpath("api").rglob("*.py"),
        *APP.joinpath("bootstrap").rglob("*.py"),
        *APP.joinpath("core").rglob("*.py"),
        *APP.joinpath("modules").rglob("*.py"),
        *APP.joinpath("web").rglob("*.py"),
    ]
    offenders = [
        str(path.relative_to(ROOT))
        for path in production_paths
        if "app.services.runtime" in imported_modules(path)
    ]
    assert offenders == []


def test_application_composition_and_legacy_facade_are_static() -> None:
    bootstrap_source = (APP / "bootstrap" / "application.py").read_text()
    runtime_source = (APP / "services" / "runtime.py").read_text()

    assert "import_module(" not in bootstrap_source
    assert "ROUTER_MODULES" not in bootstrap_source
    assert "import_module(" not in runtime_source
    assert "for _router in" not in runtime_source


def test_domain_modules_do_not_directly_import_api_routers() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in APP.joinpath("modules").rglob("*.py")
        if any(module.startswith("app.api") for module in referenced_internal_modules(path))
    ]
    assert offenders == []


def test_api_routers_do_not_import_each_other() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in APP.joinpath("api", "routes").glob("*.py")
        if any(module.startswith("app.api.routes") for module in imported_modules(path))
    ]
    assert offenders == []


def test_backend_modules_stay_within_reviewable_size() -> None:
    oversized = {
        str(path.relative_to(ROOT)): len(path.read_text().splitlines())
        for path in APP.joinpath("modules").rglob("*.py")
        if len(path.read_text().splitlines()) > 4_000
    }
    assert oversized == {}


def test_workspace_modules_stay_within_reviewable_size() -> None:
    workspace = APP / "web" / "static" / "workspace"
    oversized_javascript = {
        str(path.relative_to(ROOT)): len(path.read_text().splitlines())
        for path in workspace.rglob("*.js")
        if len(path.read_text().splitlines()) > 4_000
    }
    oversized_styles = {
        str(path.relative_to(ROOT)): len(path.read_text().splitlines())
        for path in APP.joinpath("web", "static", "styles", "workspace").glob("*.css")
        if len(path.read_text().splitlines()) > 2_000
    }
    assert oversized_javascript == {}
    assert oversized_styles == {}


def test_web_assets_use_owned_directories() -> None:
    static = APP / "web" / "static"
    assert not (APP / "static").exists()
    assert (APP / "web" / "pages" / "index.html").is_file()
    for directory in ("core", "workspace", "admin", "styles", "vendor"):
        assert (static / directory).is_dir()


def test_core_public_routes_are_preserved() -> None:
    paths = {route.path for route in app.routes}
    assert {
        "/dashboard",
        "/market",
        "/watchlist",
        "/news",
        "/admin",
        "/api/health",
        "/api/watchlist",
        "/api/news",
        "/api/chart/query",
        "/api/backtest-runs",
        "/api/paper/orders",
    } <= paths


def test_complete_route_contract_is_preserved() -> None:
    def model_name(value: object) -> str | None:
        if value is None:
            return None
        return getattr(value, "__name__", str(value).replace("typing.", ""))

    actual: list[dict[str, object]] = []
    for route in app.routes:
        item: dict[str, object] = {
            "path": route.path,
            "name": route.name,
            "kind": route.__class__.__name__,
        }
        if isinstance(route, APIRoute):
            item.update(
                methods=sorted(route.methods or []),
                response_model=model_name(route.response_model),
                deprecated=bool(route.deprecated),
            )
        elif isinstance(route, Route):
            item["methods"] = sorted(route.methods or [])
        elif isinstance(route, APIWebSocketRoute):
            item["methods"] = ["WEBSOCKET"]
        elif isinstance(route, Mount):
            item["methods"] = ["MOUNT"]
        actual.append(item)

    expected = json.loads((ROOT / "tests" / "contracts" / "routes.json").read_text())
    assert actual == expected
