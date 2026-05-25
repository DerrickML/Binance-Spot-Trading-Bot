"""Configuration management with pydantic validation."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

from app.core.enums import AppEnv, Interval, TradingMode


class Settings(BaseSettings):
    """Application settings with validation and fail-fast behavior.

    All settings are loaded from environment variables or a .env file.
    Unsafe configurations are rejected at startup.
    """

    # ---------- Application ----------
    app_env: AppEnv = AppEnv.DEVELOPMENT
    trading_mode: TradingMode = TradingMode.PAPER

    # ---------- Binance API ----------
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_base_url: str = "https://api.binance.com"
    binance_ws_url: str = "wss://stream.binance.com:9443/ws"

    # ---------- Telegram ----------
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    enable_telegram: bool = False

    # ---------- Trading ----------
    default_quote_asset: str = "USDT"
    trade_symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    trade_interval: Interval = Interval.HOUR_1

    # ---------- Risk ----------
    max_risk_per_trade: float = Field(default=0.02, ge=0.001, le=0.1)
    max_daily_loss_pct: float = Field(default=0.05, ge=0.01, le=0.2)
    max_open_positions: int = Field(default=3, ge=1, le=20)
    max_position_size_pct: float = Field(default=0.25, ge=0.05, le=0.5)
    stop_loss_pct: float = Field(default=0.03, ge=0.005, le=0.15)

    # ---------- Safety ----------
    enable_live_trading: bool = False
    enable_kill_switch: bool = False

    # ---------- Fees ----------
    slippage_bps: int = Field(default=10, ge=0, le=100)
    taker_fee_bps: int = Field(default=10, ge=0, le=100)
    maker_fee_bps: int = Field(default=10, ge=0, le=100)

    # ---------- Research / Backtest ----------
    backtest_symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "BNBUSDT"])
    backtest_intervals: list[str] = Field(default_factory=lambda: ["15m", "1h", "4h"])
    backtest_lookback_days: int = Field(default=180, ge=7, le=1000)

    # ---------- Qualification Thresholds ----------
    qual_min_return_pct: float = Field(default=0.0, ge=-1.0, le=10.0)
    qual_min_sharpe: float = Field(default=0.0, ge=-5.0, le=10.0)
    qual_min_trades: int = Field(default=5, ge=1, le=1000)
    qual_max_drawdown_pct: float = Field(default=0.30, ge=0.01, le=1.0)
    qual_min_profit_factor: float = Field(default=0.8, ge=0.0, le=100.0)
    qual_min_oos_consistency: float = Field(default=0.0, ge=0.0, le=1.0)
    qual_min_benchmark_alpha_pct: float = Field(default=0.0, ge=-1.0, le=10.0)
    qual_min_dataset_pass_rate: float = Field(default=0.5, ge=0.0, le=1.0)  # fraction of datasets that must qualify

    # ---------- Regime Gating ----------
    regime_min_volatility_pct: float = Field(default=0.2, ge=0.0, le=10.0)
    regime_max_volatility_pct: float = Field(default=8.0, ge=0.5, le=50.0)
    enable_regime_gating: bool = True

    # ---------- Database ----------
    database_url: str = "sqlite:///data/trading_bot.db"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }

    @field_validator("trade_symbols", "backtest_symbols", mode="before")
    @classmethod
    def parse_symbol_list(cls, v: Any) -> list[str]:
        """Parse symbol lists from JSON string, CSV, or list."""
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [s.upper() for s in parsed]
            except (json.JSONDecodeError, TypeError):
                pass
            return [s.strip().upper() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return [str(s).strip().upper() for s in v]
        return v

    @field_validator("backtest_intervals", mode="before")
    @classmethod
    def parse_interval_list(cls, v: Any) -> list[str]:
        """Parse interval list from JSON string, CSV, or list."""
        valid = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    items = [str(s).strip() for s in parsed]
                else:
                    items = [s.strip() for s in v.split(",") if s.strip()]
            except (json.JSONDecodeError, TypeError):
                items = [s.strip() for s in v.split(",") if s.strip()]
        elif isinstance(v, list):
            items = [str(s).strip() for s in v]
        else:
            return v
        for item in items:
            if item not in valid:
                raise ValueError(f"Invalid interval '{item}'. Valid: {sorted(valid)}")
        return items

    @model_validator(mode="after")
    def validate_safety(self) -> Settings:
        """Enforce safety rules at startup. Fail fast on dangerous config."""
        if self.enable_live_trading:
            if self.trading_mode != TradingMode.LIVE:
                raise ValueError(
                    "ENABLE_LIVE_TRADING=true requires TRADING_MODE=live"
                )
            if not self.binance_api_key or self.binance_api_key == "your_binance_api_key_here":
                raise ValueError(
                    "Live trading requires a valid BINANCE_API_KEY"
                )
            if not self.binance_api_secret or self.binance_api_secret == "your_binance_api_secret_here":
                raise ValueError(
                    "Live trading requires a valid BINANCE_API_SECRET"
                )
            if self.stop_loss_pct <= 0:
                raise ValueError(
                    "Live trading requires a positive STOP_LOSS_PCT"
                )
        if self.enable_telegram:
            if not self.telegram_bot_token or self.telegram_bot_token == "your_telegram_bot_token_here":
                raise ValueError(
                    "Telegram enabled but TELEGRAM_BOT_TOKEN is not set"
                )
            if not self.telegram_chat_id or self.telegram_chat_id == "your_telegram_chat_id_here":
                raise ValueError(
                    "Telegram enabled but TELEGRAM_CHAT_ID is not set"
                )
        if not self.trade_symbols:
            raise ValueError("TRADE_SYMBOLS must not be empty")
        return self

    @property
    def is_live(self) -> bool:
        return self.trading_mode == TradingMode.LIVE and self.enable_live_trading

    @property
    def slippage_pct(self) -> float:
        return self.slippage_bps / 10_000

    @property
    def taker_fee_pct(self) -> float:
        return self.taker_fee_bps / 10_000

    @property
    def maker_fee_pct(self) -> float:
        return self.maker_fee_bps / 10_000


def get_settings(**overrides: Any) -> Settings:
    """Create and return validated settings instance."""
    return Settings(**overrides)
