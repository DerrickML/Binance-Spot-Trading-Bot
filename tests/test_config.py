"""Tests for configuration validation and safety rules."""

from __future__ import annotations

import pytest

from app.config.settings import Settings


class TestSettingsValidation:
    """Test configuration validation and fail-fast behavior."""

    def test_default_settings_are_safe(self, settings_overrides):
        """Default settings must have live trading disabled."""
        s = Settings(**settings_overrides)
        assert s.enable_live_trading is False
        assert s.trading_mode.value == "paper"
        assert s.enable_kill_switch is False

    def test_live_trading_requires_api_key(self, settings_overrides):
        """Live trading must fail fast without API credentials."""
        overrides = {**settings_overrides, "enable_live_trading": True, "trading_mode": "live"}
        with pytest.raises(ValueError, match="BINANCE_API_KEY"):
            Settings(**overrides)

    def test_live_trading_requires_api_secret(self, settings_overrides):
        """Live trading needs both key and secret."""
        overrides = {
            **settings_overrides,
            "enable_live_trading": True,
            "trading_mode": "live",
            "binance_api_key": "real_key",
        }
        with pytest.raises(ValueError, match="BINANCE_API_SECRET"):
            Settings(**overrides)

    def test_live_mode_mismatch_rejected(self, settings_overrides):
        """ENABLE_LIVE_TRADING=true with TRADING_MODE=paper must fail."""
        overrides = {
            **settings_overrides,
            "enable_live_trading": True,
            "trading_mode": "paper",
        }
        with pytest.raises(ValueError, match="TRADING_MODE=live"):
            Settings(**overrides)

    def test_telegram_requires_token(self, settings_overrides):
        """Telegram enabled without token must fail."""
        overrides = {**settings_overrides, "enable_telegram": True}
        with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
            Settings(**overrides)

    def test_empty_symbols_rejected(self, settings_overrides):
        """Empty trade symbols must fail."""
        overrides = {**settings_overrides, "trade_symbols": []}
        with pytest.raises(ValueError, match="TRADE_SYMBOLS"):
            Settings(**overrides)

    def test_symbols_parsed_from_json_string(self, settings_overrides):
        """Symbol list should parse from JSON string."""
        overrides = {**settings_overrides, "trade_symbols": '["BTCUSDT","ETHUSDT"]'}
        s = Settings(**overrides)
        assert s.trade_symbols == ["BTCUSDT", "ETHUSDT"]

    def test_symbols_parsed_from_csv(self, settings_overrides):
        """Symbol list should parse from comma-separated string."""
        overrides = {**settings_overrides, "trade_symbols": "BTCUSDT, ETHUSDT"}
        s = Settings(**overrides)
        assert s.trade_symbols == ["BTCUSDT", "ETHUSDT"]

    def test_fee_conversion(self, settings_overrides):
        """BPS to percentage conversion should be correct."""
        s = Settings(**settings_overrides)
        assert s.taker_fee_pct == 0.001  # 10 bps = 0.1%
        assert s.slippage_pct == 0.001

    def test_is_live_property(self, settings_overrides):
        """is_live should be False in paper mode."""
        s = Settings(**settings_overrides)
        assert s.is_live is False
