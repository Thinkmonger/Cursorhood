from __future__ import annotations



from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator



from src.cursor_models import DEFAULT_API_MODEL





class LimitsConfig(BaseModel):

    max_order_notional_usd: float = Field(gt=0, le=100_000)

    max_daily_loss_usd: float = Field(gt=0, le=1_000_000)

    allowed_symbols: list[str] = Field(default_factory=list)

    max_open_positions: int = Field(ge=1, le=100)

    market_hours_only: bool = True

    min_seconds_between_orders: int = Field(ge=0, le=3600)

    symbol_source: Literal["static", "watchlist", "popular_watchlist", "scan"] = "static"

    symbol_source_ref: str | None = None

    symbol_source_limit: int = Field(default=20, ge=1, le=100)

    asset_class: Literal["equity", "option", "crypto"] = "equity"
    """Exclusive class this bot may trade. Options and crypto flags stay in sync."""

    options_enabled: bool = False

    max_option_contracts: int = Field(default=1, ge=1, le=1000)

    max_option_notional_usd: float = Field(default=100.0, gt=0, le=100_000)

    min_days_to_expiry: int = Field(default=7, ge=0, le=1000)

    max_days_to_expiry: int = Field(default=60, ge=0, le=2000)

    allowed_option_types: list[str] = Field(default_factory=lambda: ["call", "put"])

    allow_option_selling: bool = False

    crypto_enabled: bool = False

    allowed_crypto_pairs: list[str] = Field(default_factory=list)

    max_crypto_notional_usd: float = Field(default=25.0, gt=0, le=100_000)

    @model_validator(mode="before")
    @classmethod
    def infer_asset_class(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        raw = str(data.get("asset_class") or "").strip().lower()
        if raw in ("equity", "option", "crypto"):
            data["asset_class"] = raw
            return data
        if data.get("crypto_enabled"):
            data["asset_class"] = "crypto"
        elif data.get("options_enabled"):
            data["asset_class"] = "option"
        else:
            data["asset_class"] = "equity"
        return data

    @model_validator(mode="after")
    def sync_exclusive_asset_flags(self) -> "LimitsConfig":
        self.crypto_enabled = self.asset_class == "crypto"
        self.options_enabled = self.asset_class == "option"
        return self

    @field_validator("allowed_symbols", "allowed_crypto_pairs", mode="before")

    @classmethod

    def normalize_symbols(cls, v: object) -> list[str]:

        if v is None:

            return []

        if isinstance(v, str):

            return [s.strip().upper() for s in v.split(",") if s.strip()]

        return [str(s).strip().upper() for s in v if str(s).strip()]



    @field_validator("allowed_option_types", mode="before")

    @classmethod

    def normalize_option_types(cls, v: object) -> list[str]:

        if v is None or v == "":

            return ["call", "put"]

        raw = v.split(",") if isinstance(v, str) else list(v)

        out = [str(s).strip().lower() for s in raw if str(s).strip()]

        return [s for s in out if s in ("call", "put")] or ["call", "put"]



    @field_validator("symbol_source_ref", mode="before")

    @classmethod

    def empty_ref_to_none(cls, v: object) -> object | None:

        if v is None or (isinstance(v, str) and not v.strip()):

            return None

        return str(v).strip()





class BotAppConfig(BaseModel):

    cycle_interval_seconds: int = Field(ge=30, le=3600)

    cursor_model: str = DEFAULT_API_MODEL

    auto_start_scheduler: bool = False

    scheduler_enabled: bool = True

    simulation_mode: bool = False

    simulated_cash_starting_value: float | None = Field(default=None, gt=0, le=10_000_000)

    simulation_include_live_portfolio: bool = False

    max_runs: int | None = Field(default=None, ge=1, le=1_000_000)

    context_profile: Literal["minimal", "standard", "research"] = "minimal"

    scanners_enabled: bool = True

    simulation_slippage_bps: float = Field(default=0.0, ge=0, le=1000)

    simulation_commission_per_order: float = Field(default=0.0, ge=0, le=100)

    simulation_settlement_days: int = Field(default=0, ge=0, le=5)



    @field_validator("simulated_cash_starting_value", "max_runs", mode="before")

    @classmethod

    def empty_optional_to_none(cls, v: object) -> object | None:

        if v == "" or v is None:

            return None

        return v



    @field_validator("cursor_model")

    @classmethod

    def strip_cursor_model(cls, v: str) -> str:

        return str(v).strip()





class AppConfig(BaseModel):

    cycle_interval_seconds: int = Field(ge=30, le=3600)

    cursor_model: str = DEFAULT_API_MODEL

    auto_start_scheduler: bool = False

    scheduler_enabled: bool = True

    dashboard_host: str = "127.0.0.1"

    dashboard_port: int = Field(default=8765, ge=1024, le=65535)

    open_browser_on_start: bool = True



    @field_validator("cursor_model")

    @classmethod

    def strip_cursor_model(cls, v: str) -> str:

        return str(v).strip()





class StrategyUpdate(BaseModel):

    content: str





class BotUpdate(BaseModel):

    name: str | None = Field(default=None, min_length=1, max_length=80)

    id: str | None = Field(default=None, min_length=1, max_length=64)





class CreateBotBody(BaseModel):

    name: str = Field(min_length=1, max_length=80)





class SlugFromNameBody(BaseModel):

    name: str | None = Field(default=None, min_length=1, max_length=80)





class ResetSimulationBody(BaseModel):

    simulated_cash_starting_value: float | None = Field(default=None, gt=0, le=10_000_000)



    @field_validator("simulated_cash_starting_value", mode="before")

    @classmethod

    def empty_optional_to_none(cls, v: object) -> object | None:

        if v == "" or v is None:

            return None

        return v

