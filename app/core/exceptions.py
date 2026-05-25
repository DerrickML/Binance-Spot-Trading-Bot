"""Custom exception hierarchy for the trading platform."""


class TradingBotError(Exception):
    """Base exception for all trading bot errors."""


class ConfigError(TradingBotError):
    """Configuration validation or loading error."""


class DataError(TradingBotError):
    """Market data fetching or processing error."""


class StrategyError(TradingBotError):
    """Strategy signal generation or validation error."""


class RiskError(TradingBotError):
    """Risk engine rejection or validation error."""


class ExecutionError(TradingBotError):
    """Order execution or broker error."""


class OrderValidationError(ExecutionError):
    """Order failed validation against exchange filters."""


class InsufficientBalanceError(ExecutionError):
    """Insufficient balance to place order."""


class KillSwitchError(TradingBotError):
    """Kill switch has been triggered — all trading halted."""


class BrokerError(TradingBotError):
    """Broker communication error."""


class TelegramError(TradingBotError):
    """Telegram notification error."""
