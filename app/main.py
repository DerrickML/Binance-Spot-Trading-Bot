"""Application bootstrap and startup."""

from __future__ import annotations

from app.core.logging import get_logger, setup_logging

logger = get_logger(__name__)


def startup() -> None:
    """Initialize the application."""
    from app.config.settings import get_settings

    setup_logging()
    settings = get_settings()

    logger.info(
        "application_startup",
        mode=settings.trading_mode,
        env=settings.app_env,
        live_enabled=settings.enable_live_trading,
        kill_switch=settings.enable_kill_switch,
        symbols=settings.trade_symbols,
    )

    # Initialize database
    from app.persistence.db import init_db
    init_db(settings.database_url)

    # Import strategies to trigger registration
    import app.strategies.ema_atr  # noqa: F401
    import app.strategies.rsi_mean_reversion  # noqa: F401
    import app.strategies.bollinger_mean_reversion  # noqa: F401
    import app.strategies.breakout  # noqa: F401
    import app.strategies.regime_strategy  # noqa: F401
    import app.strategies.momentum_continuation  # noqa: F401
    import app.strategies.pullback_uptrend  # noqa: F401
    import app.strategies.volatility_breakout  # noqa: F401

    from app.strategies.registry import list_strategies
    logger.info("strategies_loaded", strategies=list_strategies())

    return settings


if __name__ == "__main__":
    startup()
