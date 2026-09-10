from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "App Trading"
    app_version: str = "0.1.0"
    session_cookie: str = "app_trading_session"
    session_hours: int = 8
    password_iterations: int = 600_000

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    @property
    def static_dir(self) -> Path:
        return self.project_root / "app" / "web" / "static"

    @property
    def page_dir(self) -> Path:
        return self.project_root / "app" / "web" / "pages"

    @property
    def database_path(self) -> Path:
        configured = os.getenv("APP_DB_PATH")
        return Path(configured) if configured else self.project_root / "app_data.db"


settings = Settings()
