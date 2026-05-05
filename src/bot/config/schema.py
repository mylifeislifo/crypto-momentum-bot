"""Pydantic config schema. YAML files map directly into AppConfig."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, NonNegativeFloat, PositiveFloat, PositiveInt

from bot.core.enums import Interval, Mode


class UniverseCfg(BaseModel):
    market: str = "KRW"
    top_n: PositiveInt = 10
    by: str = "quote_volume_24h"
    refresh_hours: PositiveFloat = 12.0


class DataCfg(BaseModel):
    interval: Interval = Interval.M5
    lookback_days: PositiveInt = 180
    cache_dir: str = "data/upbit"


class StrategyParams(BaseModel):
    ema_fast: PositiveInt = 20
    ema_slow: PositiveInt = 60
    atr_period: PositiveInt = 14
    adx_period: PositiveInt = 14
    adx_min: NonNegativeFloat = 20.0
    donchian_period: PositiveInt = 20
    vol_ratio_min: NonNegativeFloat = 1.2
    momentum_lookback: PositiveInt = 12
    reentry_cooldown_hours: NonNegativeFloat = 24.0


class StrategyCfg(BaseModel):
    name: str = "momentum_trend"
    params: StrategyParams = Field(default_factory=StrategyParams)


class RegimeCfg(BaseModel):
    source_symbol: str = "KRW-BTC"
    source_interval: Interval = Interval.M60
    ema_fast: PositiveInt = 50
    ema_slow: PositiveInt = 200


class RiskCfg(BaseModel):
    risk_per_trade: PositiveFloat = 0.015
    atr_stop_mult: PositiveFloat = 2.0
    trail_atr_mult: PositiveFloat = 3.0
    time_stop_hours: PositiveFloat = 48.0
    max_concurrent: PositiveInt = 4
    max_concentration: PositiveFloat = 0.25
    daily_loss_limit: float = -0.03  # negative number
    weekly_loss_limit: float = -0.08
    mdd_killswitch: float = -0.25


class ExecutionCfg(BaseModel):
    fee_per_side: NonNegativeFloat = 0.0005
    slippage_bps: NonNegativeFloat = 5.0
    retry_max: PositiveInt = 3
    retry_backoff_sec: PositiveFloat = 1.0


class BacktestCfg(BaseModel):
    start: str = "2024-01-01"
    end: str = "2025-12-31"
    initial_equity: PositiveFloat = 10_000_000.0


class WalkForwardCfg(BaseModel):
    is_months: PositiveInt = 6
    oos_months: PositiveInt = 1
    step_months: PositiveInt = 1


class LoggingCfg(BaseModel):
    level: str = "INFO"
    json_logs: bool = Field(default=True, alias="json")
    dir: str = "logs"

    model_config = {"populate_by_name": True}


class AppConfig(BaseModel):
    mode: Mode = Mode.BACKTEST
    exchange: str = "upbit"
    universe: UniverseCfg = Field(default_factory=UniverseCfg)
    data: DataCfg = Field(default_factory=DataCfg)
    strategy: StrategyCfg = Field(default_factory=StrategyCfg)
    regime: RegimeCfg = Field(default_factory=RegimeCfg)
    risk: RiskCfg = Field(default_factory=RiskCfg)
    execution: ExecutionCfg = Field(default_factory=ExecutionCfg)
    backtest: BacktestCfg = Field(default_factory=BacktestCfg)
    walkforward: WalkForwardCfg = Field(default_factory=WalkForwardCfg)
    logging: LoggingCfg = Field(default_factory=LoggingCfg)
    dry_run: bool = True

    # Secrets are loaded separately from .env, never from YAML
    upbit_access_key: Optional[str] = None
    upbit_secret_key: Optional[str] = None

    def cache_path(self) -> Path:
        return Path(self.data.cache_dir)
