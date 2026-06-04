from __future__ import annotations



from pydantic import BaseModel, Field, field_validator



from src.cursor_models import DEFAULT_API_MODEL





class LimitsConfig(BaseModel):

    max_order_notional_usd: float = Field(gt=0, le=100_000)

    max_daily_loss_usd: float = Field(gt=0, le=1_000_000)

    allowed_symbols: list[str] = Field(default_factory=list)

    max_open_positions: int = Field(ge=1, le=100)

    market_hours_only: bool = True

    min_seconds_between_orders: int = Field(ge=0, le=3600)



    @field_validator("allowed_symbols", mode="before")

    @classmethod

    def normalize_symbols(cls, v: object) -> list[str]:

        if v is None:

            return []

        if isinstance(v, str):

            return [s.strip().upper() for s in v.split(",") if s.strip()]

        return [str(s).strip().upper() for s in v if str(s).strip()]





class BotAppConfig(BaseModel):

    cycle_interval_seconds: int = Field(ge=30, le=3600)

    cursor_model: str = DEFAULT_API_MODEL

    auto_start_scheduler: bool = False

    scheduler_enabled: bool = True

    simulation_mode: bool = False

    simulated_cash_starting_value: float | None = Field(default=None, gt=0, le=10_000_000)

    simulation_include_live_portfolio: bool = False

    max_runs: int | None = Field(default=None, ge=1, le=1_000_000)



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

