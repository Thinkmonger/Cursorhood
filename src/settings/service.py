from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from src.auth.secrets import get_secret, set_secret
from src.auth.tokens import TokenStore
from src.cursor_models import DEFAULT_API_MODEL
from src.db.migrate import DEFAULT_BOT_ID
from src.paths import CONFIG_DIR
from src.setup.mcp_client import use_cursor_mcp_mode
from src.settings.schema import AppConfig, BotAppConfig, LimitsConfig

ASSET_CLASSES = ("equity", "option", "crypto")
TEMPLATE_DIR = CONFIG_DIR / "templates"

DEFAULT_STRATEGY = """# Equities

US cash equities only. Skip when the cash session is closed. Review `review_equity_order` before any place.

- Each cycle: portfolio, positions, quotes, 1h technicals (open/close, SMA-20, RSI-14), last action. Uncertain → no action + `log_event`.
- Entry (flat in the symbol): 1h close below SMA-20 and RSI(14) < 35, or a ≥2% dip from the 1d high. One new name per cycle.
- Size: at most 20% of portfolio equity in any symbol; `max_order_notional_usd` is the hard cap. Max 5 names.
- DCA once per symbol per cycle if mark is ≥5% below average cost (buy half the current position value).
- Take profit at +4% vs average cost. Stop at −8%. Cancel working orders that survive a cycle.
- Do not guess. If the tape and indicators disagree, skip.
"""


def normalize_asset_class(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in ASSET_CLASSES else "equity"


def load_class_template(asset_class: str) -> tuple[str, LimitsConfig, BotAppConfig]:
    kind = normalize_asset_class(asset_class)
    folder = TEMPLATE_DIR / kind
    strategy = DEFAULT_STRATEGY
    strategy_path = folder / "strategy.md"
    if strategy_path.exists():
        strategy = strategy_path.read_text(encoding="utf-8")
    limits = LimitsConfig(
        max_order_notional_usd=250,
        max_daily_loss_usd=100,
        max_open_positions=5,
        market_hours_only=kind != "crypto",
        min_seconds_between_orders=60,
        asset_class=kind,  # type: ignore[arg-type]
    )
    limits_path = folder / "limits.yaml"
    if limits_path.exists():
        limits = LimitsConfig.model_validate(yaml.safe_load(limits_path.read_text(encoding="utf-8")) or {})
    app = BotAppConfig(
        cycle_interval_seconds=600 if kind == "option" else 300,
        cursor_model=DEFAULT_API_MODEL,
        auto_start_scheduler=False,
        scheduler_enabled=True,
        simulation_mode=True,
        simulated_cash_starting_value=2500.0,
    )
    app_path = folder / "app.yaml"
    if app_path.exists():
        app = BotAppConfig.model_validate(yaml.safe_load(app_path.read_text(encoding="utf-8")) or {})
    return strategy, limits, app


class SettingsService:
    def __init__(self, bot_id: str = DEFAULT_BOT_ID) -> None:
        self.bot_id = bot_id
        if bot_id == DEFAULT_BOT_ID:
            self.config_dir = CONFIG_DIR
        else:
            self.config_dir = CONFIG_DIR / "bots" / bot_id

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

    def _store(self):
        from src.db.store import Store

        return Store()

    def _row(self) -> dict[str, Any]:
        self._ensure_settings()
        row = self._store().get_bot_settings(self.bot_id)
        if not row:
            raise RuntimeError(f"Missing bot_settings for {self.bot_id}")
        return row

    def _persist(
        self,
        *,
        strategy_md: str,
        limits: LimitsConfig,
        app: BotAppConfig,
    ) -> None:
        self._store().set_bot_settings(
            self.bot_id,
            asset_class=limits.asset_class,
            strategy_md=strategy_md,
            limits_json=json.dumps(limits.model_dump()),
            app_json=json.dumps(app.model_dump()),
        )

    def _yaml_limits(self) -> LimitsConfig | None:
        if not self.limits_path.exists():
            return None
        data = yaml.safe_load(self.limits_path.read_text(encoding="utf-8")) or {}
        return LimitsConfig.model_validate(data)

    def _yaml_app(self) -> BotAppConfig | None:
        if not self.app_path.exists():
            return None
        data = yaml.safe_load(self.app_path.read_text(encoding="utf-8")) or {}
        if self.bot_id == DEFAULT_BOT_ID and "simulation_mode" not in data:
            data["simulation_mode"] = True
        return BotAppConfig.model_validate(data)

    def _ensure_settings(self) -> None:
        store = self._store()
        if store.get_bot_settings(self.bot_id):
            return
        if store.get_bot(self.bot_id) is None:
            return
        strategy = None
        if self.strategy_path.exists():
            strategy = self.strategy_path.read_text(encoding="utf-8")
        limits = self._yaml_limits()
        app = self._yaml_app()
        if strategy is None or limits is None or app is None:
            inferred = limits.asset_class if limits else ("crypto" if self.bot_id == DEFAULT_BOT_ID else "equity")
            t_strategy, t_limits, t_app = load_class_template(inferred)
            strategy = strategy if strategy is not None else t_strategy
            limits = limits or t_limits
            app = app or t_app
        self._persist(strategy_md=strategy, limits=limits, app=app)

    def read_strategy(self) -> str:
        return str(self._row()["strategy_md"])

    def write_strategy(self, content: str) -> None:
        row = self._row()
        limits = LimitsConfig.model_validate(json.loads(row["limits_json"]))
        app = BotAppConfig.model_validate(json.loads(row["app_json"]))
        self._persist(strategy_md=content, limits=limits, app=app)

    def read_limits(self) -> LimitsConfig:
        return LimitsConfig.model_validate(json.loads(self._row()["limits_json"]))

    def write_limits(self, limits: LimitsConfig) -> None:
        row = self._row()
        app = BotAppConfig.model_validate(json.loads(row["app_json"]))
        self._persist(strategy_md=row["strategy_md"], limits=limits, app=app)

    def read_bot_app(self) -> BotAppConfig:
        return BotAppConfig.model_validate(json.loads(self._row()["app_json"]))

    def write_bot_app(self, app: BotAppConfig) -> None:
        row = self._row()
        previous = BotAppConfig.model_validate(json.loads(row["app_json"]))
        limits = LimitsConfig.model_validate(json.loads(row["limits_json"]))
        self._persist(strategy_md=row["strategy_md"], limits=limits, app=app)
        if previous.simulated_cash_starting_value != app.simulated_cash_starting_value:
            from src.simulation.ledger import reset_ledger_for_starting_cash

            reset_ledger_for_starting_cash(self.bot_id)
        if app.max_runs is not None:
            store = self._store()
            if store.count_runs(self.bot_id) < app.max_runs:
                store.set_bot_state(self.bot_id, "scheduler_max_runs_reached", "false")

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

    def init_from_class(self, asset_class: str) -> None:
        strategy, limits, app = load_class_template(asset_class)
        if self._store().get_bot_settings(self.bot_id):
            self._persist(strategy_md=strategy, limits=limits, app=app)
            return
        self._store().set_bot_settings(
            self.bot_id,
            asset_class=limits.asset_class,
            strategy_md=strategy,
            limits_json=json.dumps(limits.model_dump()),
            app_json=json.dumps(app.model_dump()),
        )

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

    def get_cursor_api_key(self) -> str | None:
        return get_secret("CURSOR_API_KEY")

    def save_cursor_api_key(self, api_key: str) -> None:
        set_secret("CURSOR_API_KEY", api_key)

    def get_massive_api_key(self) -> str | None:
        return get_secret("MASSIVE_API_KEY")

    def save_massive_api_key(self, api_key: str) -> None:
        set_secret("MASSIVE_API_KEY", api_key)

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
