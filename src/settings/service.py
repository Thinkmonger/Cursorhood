from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from src.auth.tokens import TokenStore
from src.db.migrate import DEFAULT_BOT_ID
from src.paths import CONFIG_DIR, ENV_PATH
from src.setup.mcp_client import use_cursor_mcp_mode
from src.cursor_models import DEFAULT_API_MODEL
from src.settings.schema import AppConfig, BotAppConfig, LimitsConfig

DEFAULT_STRATEGY = """# Strategy

Monitor the symbols listed in your risk limits each cycle.

- Fetch portfolio, positions, and quotes before making any decision.
- If no holdings, purchase $5 market value in **one** symbol per cycle (Robinhood blocks a second trade until investor profile is completed in the mobile app).
- Buy and sell in reasonable increments, always aim for profit!
- Only buy if RSI is low. Only sell if RSI is high.
- If a watched symbol drops more than 2% from prior close, buy $5 notional (market).
- Always dollar cost average where possible.
- Do not add to a symbol if it already exceeds 25% of portfolio equity.
- Rebalance toward equal weight when allocation drift exceeds 10%.
- If no rule triggers, report "no action" and log the cycle summary.
"""


class SettingsService:
    def __init__(self, bot_id: str = DEFAULT_BOT_ID) -> None:
        self.bot_id = bot_id
        if bot_id == DEFAULT_BOT_ID:
            self.config_dir = CONFIG_DIR
        else:
            self.config_dir = CONFIG_DIR / "bots" / bot_id
        self.config_dir.mkdir(parents=True, exist_ok=True)

    @property
    def strategy_path(self) -> Path:
        return self.config_dir / "strategy.md"

    @property
    def limits_path(self) -> Path:
        return self.config_dir / "limits.yaml"

    @property
    def app_path(self) -> Path:
        return self.config_dir / "app.yaml"

    _GLOBAL_APP_KEYS = ("dashboard_host", "dashboard_port", "open_browser_on_start")
    _GLOBAL_APP_DEFAULTS: dict[str, Any] = {
        "dashboard_host": "127.0.0.1",
        "dashboard_port": 8765,
        "open_browser_on_start": True,
    }

    @staticmethod
    def global_app_path() -> Path:
        return CONFIG_DIR / "global.yaml"

    def read_strategy(self) -> str:
        if not self.strategy_path.exists():
            if self.bot_id != DEFAULT_BOT_ID:
                default = SettingsService(DEFAULT_BOT_ID)
                if default.strategy_path.exists():
                    self.write_strategy(default.read_strategy())
                else:
                    self.write_strategy(DEFAULT_STRATEGY)
            else:
                self.write_strategy(DEFAULT_STRATEGY)
        return self.strategy_path.read_text(encoding="utf-8")

    def write_strategy(self, content: str) -> None:
        self.strategy_path.write_text(content, encoding="utf-8")

    def read_limits(self) -> LimitsConfig:
        if not self.limits_path.exists():
            if self.bot_id != DEFAULT_BOT_ID:
                default = SettingsService(DEFAULT_BOT_ID)
                self.write_limits(default.read_limits())
            else:
                self.write_limits(
                    LimitsConfig(
                        max_order_notional_usd=100,
                        max_daily_loss_usd=250,
                        allowed_symbols=["AAPL", "MSFT"],
                        max_open_positions=10,
                        market_hours_only=True,
                        min_seconds_between_orders=60,
                    )
                )
        data = yaml.safe_load(self.limits_path.read_text(encoding="utf-8")) or {}
        return LimitsConfig.model_validate(data)

    def write_limits(self, limits: LimitsConfig) -> None:
        payload = limits.model_dump()
        self.limits_path.write_text(
            yaml.safe_dump(payload, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    def read_bot_app(self) -> BotAppConfig:
        if not self.app_path.exists():
            if self.bot_id != DEFAULT_BOT_ID:
                default = SettingsService(DEFAULT_BOT_ID)
                da = default.read_bot_app()
                self.write_bot_app(da)
            else:
                self.write_bot_app(
                    BotAppConfig(
                        cycle_interval_seconds=120,
                        cursor_model=DEFAULT_API_MODEL,
                        auto_start_scheduler=False,
                        scheduler_enabled=True,
                        simulation_mode=True,
                    )
                )
        data = yaml.safe_load(self.app_path.read_text(encoding="utf-8")) or {}
        if self.bot_id == DEFAULT_BOT_ID and "simulation_mode" not in data:
            data["simulation_mode"] = True
        return BotAppConfig.model_validate(data)

    def write_bot_app(self, app: BotAppConfig) -> None:
        previous = self.read_bot_app() if self.app_path.exists() else None
        payload = app.model_dump()
        self.app_path.write_text(
            yaml.safe_dump(payload, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        if previous and previous.simulated_cash_starting_value != app.simulated_cash_starting_value:
            from src.simulation.ledger import reset_ledger_for_starting_cash

            reset_ledger_for_starting_cash(self.bot_id)
        if app.max_runs is not None:
            from src.db.store import Store

            if Store().count_runs(self.bot_id) < app.max_runs:
                Store().set_bot_state(self.bot_id, "scheduler_max_runs_reached", "false")

    def read_global_dashboard(self) -> dict[str, Any]:
        global_path = self.global_app_path()
        data: dict[str, Any] = {}
        if global_path.exists():
            data = yaml.safe_load(global_path.read_text(encoding="utf-8")) or {}
        else:
            legacy_path = CONFIG_DIR / "app.yaml"
            if legacy_path.exists():
                raw = yaml.safe_load(legacy_path.read_text(encoding="utf-8")) or {}
                extracted = {k: raw[k] for k in self._GLOBAL_APP_KEYS if k in raw}
                if extracted:
                    data = extracted
                    self.write_global_dashboard({**self._GLOBAL_APP_DEFAULTS, **data})
        merged = {**self._GLOBAL_APP_DEFAULTS, **{k: data[k] for k in self._GLOBAL_APP_KEYS if k in data}}
        return merged

    def write_global_dashboard(self, data: dict[str, Any]) -> None:
        global_path = self.global_app_path()
        global_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: data[k] for k in self._GLOBAL_APP_KEYS if k in data}
        global_path.write_text(
            yaml.safe_dump(payload, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    def read_app(self) -> AppConfig:
        bot_app = self.read_bot_app()
        gdata = self.read_global_dashboard()
        return AppConfig(**{**bot_app.model_dump(), **gdata})

    def init_from_template(self, template_bot_id: str = DEFAULT_BOT_ID) -> None:
        src = SettingsService(template_bot_id)
        self.write_strategy(src.read_strategy())
        self.write_limits(src.read_limits())
        self.write_bot_app(src.read_bot_app())

    @staticmethod
    def remove_bot_config(bot_id: str) -> None:
        if bot_id == DEFAULT_BOT_ID:
            return
        path = CONFIG_DIR / "bots" / bot_id
        if path.exists():
            shutil.rmtree(path)

    @staticmethod
    def rename_bot_config(old_id: str, new_id: str) -> None:
        if old_id == DEFAULT_BOT_ID:
            return
        old = CONFIG_DIR / "bots" / old_id
        new = CONFIG_DIR / "bots" / new_id
        if not old.exists():
            return
        new.parent.mkdir(parents=True, exist_ok=True)
        if new.exists():
            shutil.rmtree(new)
        shutil.move(str(old), str(new))

    def _read_env_key(self, name: str) -> str | None:
        value = os.environ.get(name)
        if value:
            return value.strip() or None
        if ENV_PATH.exists():
            prefix = f"{name}="
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                if line.startswith(prefix):
                    stored = line.split("=", 1)[1].strip()
                    return stored or None
        return None

    def _write_env_key(self, name: str, value: str) -> None:
        lines: list[str] = []
        if ENV_PATH.exists():
            lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
        prefix = f"{name}="
        updated = False
        for i, line in enumerate(lines):
            if line.startswith(prefix):
                lines[i] = f"{prefix}{value}"
                updated = True
                break
        if not updated:
            lines.append(f"{prefix}{value}")
        ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.environ[name] = value

    def get_cursor_api_key(self) -> str | None:
        return self._read_env_key("CURSOR_API_KEY")

    def save_cursor_api_key(self, api_key: str) -> None:
        self._write_env_key("CURSOR_API_KEY", api_key.strip())

    def get_massive_api_key(self) -> str | None:
        return self._read_env_key("MASSIVE_API_KEY")

    def save_massive_api_key(self, api_key: str) -> None:
        self._write_env_key("MASSIVE_API_KEY", api_key)

    def mask_key(self, key: str | None) -> str | None:
        if not key:
            return None
        if len(key) <= 8:
            return "***"
        return f"{key[:4]}...{key[-4:]}"

    def to_connections_dict(self) -> dict[str, Any]:
        via_cursor = use_cursor_mcp_mode()
        has_token = TokenStore().is_connected()
        return {
            "connections": {
                "cursor_api_key_set": bool(self.get_cursor_api_key()),
                "cursor_api_key_masked": self.mask_key(self.get_cursor_api_key()),
                "massive_api_key_set": bool(self.get_massive_api_key()),
                "massive_api_key_masked": self.mask_key(self.get_massive_api_key()),
                "robinhood_via_cursor": via_cursor,
                "robinhood_has_token": has_token,
                "robinhood_connected": has_token,
            },
        }

    def to_bot_settings_dict(self) -> dict[str, Any]:
        limits = self.read_limits()
        app = self.read_bot_app()
        return {
            "bot_id": self.bot_id,
            "strategy": self.read_strategy(),
            "limits": limits.model_dump(),
            "app": app.model_dump(),
        }
