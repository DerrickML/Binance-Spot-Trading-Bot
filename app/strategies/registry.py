"""Strategy registry for discovery and instantiation."""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.strategies.base import BaseStrategy

logger = get_logger(__name__)

_REGISTRY: dict[str, type[BaseStrategy]] = {}


def register_strategy(strategy_cls: type[BaseStrategy]) -> type[BaseStrategy]:
    """Register a strategy class in the global registry.

    Use as a decorator:
        @register_strategy
        class MyStrategy(BaseStrategy):
            ...
    """
    name = strategy_cls.name
    if name in _REGISTRY:
        logger.warning("strategy_overwrite", name=name, cls=strategy_cls.__name__)
    _REGISTRY[name] = strategy_cls
    logger.info("strategy_registered", name=name, cls=strategy_cls.__name__)
    return strategy_cls


def get_strategy(name: str, params: dict[str, Any] | None = None) -> BaseStrategy:
    """Instantiate a registered strategy by name."""
    if name not in _REGISTRY:
        available = list(_REGISTRY.keys())
        raise KeyError(f"Strategy '{name}' not found. Available: {available}")
    return _REGISTRY[name](params=params)


def get_all_strategies(params_map: dict[str, dict[str, Any]] | None = None) -> list[BaseStrategy]:
    """Instantiate all registered strategies."""
    params_map = params_map or {}
    return [cls(params=params_map.get(name)) for name, cls in _REGISTRY.items()]


def list_strategies() -> list[str]:
    """Return names of all registered strategies."""
    return list(_REGISTRY.keys())


def clear_registry() -> None:
    """Clear the registry (for testing)."""
    _REGISTRY.clear()
