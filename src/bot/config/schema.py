from pydantic import BaseModel, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..core.enums import MarginType, Mode


class ExchangeCfg(BaseModel):
    symbol: str = "BTCUSDT"
    max_leverage: int = 2
    margin_mode: MarginType = MarginType.ISOLATED
    fee_maker_bps: float = 2.0
    fee_taker_bps: float = 5.0


class DataCfg(BaseModel):
    ob_depth_levels: int = 20
    ob_interval_ms: int = 100
    oi_poll_sec: int = 60
    sentiment_poll_sec: int = 3600
    bar_intervals: list[str] = ["5m", "15m"]


class StrategyCfg(BaseModel):
    ob_imbalance_long_threshold: float = 0.30
    ob_imbalance_short_threshold: float = -0.30
    oi_delta_long_threshold_pct: float = 0.003    # +0.3%
    oi_delta_short_threshold_pct: float = -0.003  # -0.3%
    funding_long_bias_threshold: float = -0.0001  # funding < -0.01% favors long
    funding_short_trigger: float = 0.0001         # funding > +0.01% required for short
    fear_greed_short_min: int = 75                # short only if F&G >= 75
    fear_greed_long_max: int = 50                 # long preferred when F&G <= 50
    cvd_lookback_bars: int = 3
    spoof_cancel_window_snapshots: int = 2
    spoof_size_multiplier: float = 3.0            # flag level if > N× neighbor avg


class RiskCfg(BaseModel):
    risk_per_trade: float = 0.01        # 1% of equity per trade
    max_positions: int = 3
    long_sl_pct: float = -0.018         # -1.8% (midpoint of -1.5% to -2%)
    short_sl_pct: float = -0.0075       # -0.75% (midpoint of -0.5% to -1%)
    trail_atr_multiplier: float = 1.5
    trail_lookback_bars: int = 5
    # breakeven: once price moves +trigger% in favor, ratchet the stop to entry so a
    # winner can never round-trip to a loss (winner-asymmetry / exit-alpha, trading §3).
    breakeven_trigger_pct: float = 0.01   # +1% favorable excursion arms breakeven
    breakeven_offset_pct: float = 0.0     # extra buffer beyond entry for the BE stop (e.g. fees); 0 = exact entry
    # time stop (winner-asymmetry "cut losers short" — counterpart to breakeven's "let winners run").
    # An UNPROVEN position (breakeven never armed = never reached +trigger%) is market-closed after
    # time_stop_bars 5m bars. Proven winners (be_armed) are exempt and ride the trail. 0 disables.
    time_stop_bars: int = 0             # e.g. 48 = 4h of 5m bars; 0 = off
    max_hold_bars: int = 0              # hard cap closing ANY position regardless of proof; 0 = off
    daily_loss_limit: float = -0.03     # circuit breaker at -3%
    circuit_reset_hour_kst: int = 9
    long_bias_window: int = 20          # rolling window for 80% long check
    long_bias_min: float = 0.80
    short_max_daily: int = 1

    @field_validator("long_sl_pct", "short_sl_pct")
    @classmethod
    def must_be_negative(cls, v: float) -> float:
        if v >= 0:
            raise ValueError("SL percentages must be negative")
        return v

    @field_validator("breakeven_trigger_pct", "breakeven_offset_pct")
    @classmethod
    def must_be_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("breakeven percentages must be >= 0 (0 disables breakeven)")
        return v

    @model_validator(mode="after")
    def breakeven_offset_below_trigger(self) -> "RiskCfg":
        # If the offset met/exceeded the trigger, the breakeven stop would sit at or
        # above the price that armed it → an SL above market → the amendment is
        # rejected by the exchange and breakeven silently fails to engage.
        if self.breakeven_trigger_pct > 0 and self.breakeven_offset_pct >= self.breakeven_trigger_pct:
            raise ValueError(
                "breakeven_offset_pct must be < breakeven_trigger_pct "
                f"(got offset={self.breakeven_offset_pct}, trigger={self.breakeven_trigger_pct})"
            )
        return self

    @field_validator("time_stop_bars", "max_hold_bars")
    @classmethod
    def must_be_non_negative_int(cls, v: int) -> int:
        if v < 0:
            raise ValueError("time_stop_bars / max_hold_bars must be >= 0 (0 disables)")
        return v

    @model_validator(mode="after")
    def max_hold_above_time_stop(self) -> "RiskCfg":
        # If a hard cap sits below the conditional cut, the conditional time-stop is
        # unreachable (max_hold fires first for everyone) → almost certainly a misconfig.
        if 0 < self.max_hold_bars < self.time_stop_bars:
            raise ValueError(
                "max_hold_bars must be >= time_stop_bars when both are set "
                f"(got max_hold={self.max_hold_bars}, time_stop={self.time_stop_bars})"
            )
        return self


class ExecutionCfg(BaseModel):
    retry_max: int = 3
    retry_backoff_sec: float = 0.5
    sl_place_timeout_sec: float = 2.0   # SL must be placed within 2s of entry


class NotifyCfg(BaseModel):
    rate_limit_sec: float = 1.0
    queue_max: int = 100


class LoggingCfg(BaseModel):
    level: str = "INFO"
    json_format: bool = True
    log_dir: str = "logs"


class AppConfig(BaseModel):
    mode: Mode = Mode.PAPER
    dry_run: bool = True
    exchange: ExchangeCfg = ExchangeCfg()
    data: DataCfg = DataCfg()
    strategy: StrategyCfg = StrategyCfg()
    risk: RiskCfg = RiskCfg()
    execution: ExecutionCfg = ExecutionCfg()
    notifications: NotifyCfg = NotifyCfg()
    logging: LoggingCfg = LoggingCfg()


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    binance_api_key: str = ""
    binance_secret_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    coinglass_api_key: str = ""
