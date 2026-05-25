# Architecture Documentation

## System Overview

The Binance Spot Trading Bot is a modular, production-structured platform with strict separation of concerns.

```
Market Data → Strategies → Risk Engine → Broker → Notifications
                ↑                           ↓
           Backtesting                  Persistence
                ↓
         Ranking/Selection
```

## Module Architecture

### Config (`app/config/`)
- Pydantic `BaseSettings` with environment variable binding
- Fail-fast validation: unsafe configurations raise errors at startup
- Safety rules: live trading requires keys, stop-loss, and explicit enablement

### Core (`app/core/`)
- **enums.py** — `TradingMode`, `OrderSide`, `OrderType`, `SignalType`, `Interval`, etc.
- **exceptions.py** — Custom hierarchy: `ConfigError`, `RiskError`, `ExecutionError`, `KillSwitchError`
- **logging.py** — `structlog` with console/JSON rendering
- **utils.py** — Exchange-compliant rounding, timestamp conversion, safe math

### Data (`app/data/`)
- **MarketDataService** — Binance REST via `httpx`: klines, tickers, exchange info
- **HistoricalLoader** — Bulk download with pagination and deduplication
- **BinanceWebSocketClient** — Live kline streams with auto-reconnect

### Strategies (`app/strategies/`)
- **BaseStrategy** (ABC) — `generate_signals(candles) → list[StrategySignal]`
- Strategies generate signals only — they never place orders
- Registry pattern for discovery and instantiation
- 5 implementations: EMA+ATR, RSI, Bollinger, Breakout, Regime

### Backtesting (`app/backtesting/`)
- **BacktestEngine** — Candle-by-candle simulation with fees, slippage, SL/TP
- **Metrics** — Sharpe, Sortino, drawdown, profit factor, win rate
- **Optimizer** — Grid search over parameter space
- **Ranking** — Weighted normalized scoring across multiple dimensions
- **Validation** — Walk-forward train/test splits, buy-and-hold benchmark, configurable qualification thresholds
- **Matrix Evaluation** — Cross-symbol × cross-interval strategy evaluation with consistency scoring

### Services (`app/services/`)
- **Orchestrator** — End-to-end pipeline: WebSocket → candle buffer → strategy → risk → broker → persistence → Telegram
  - Processes CLOSED candles only, deduplicates, respects kill switch
  - Deterministic `process_candle()` method for testability
  - SL/TP monitoring on open positions
  - `replay_candles()` for offline simulation using persisted data
  - 3-tier strategy resolution: `--strategy` flag → qualified winner → fallback
- **StrategySelectionService** — Backtest all → rank → validate → qualify → persist winner
- **HealthService** — Config, database, and strategy health checks

### Risk (`app/risk/`)
- **RiskEngine** — Final gate before execution; evaluates all rules
- 9 default rules: kill switch, max positions, daily loss, position size, stop-loss, consecutive loss cooldown, disabled symbols, error halt, symbol cooldown
- All decisions are logged and explainable

### Execution (`app/execution/`)
- **BaseBroker** (ABC) — Shared interface for `submit_order`, `cancel_order`, `get_balance`
- **PaperBroker** — Virtual balances, simulated fills with fees/slippage
- **BinanceBroker** — Live execution with HMAC signing, exchange filter validation, safety guards
- **OrderValidator** — Validates lot size, price filter, min notional

### Notifications (`app/notifications/`)
- **TelegramNotifier** — Bot API client for all notification types
- **MessageBuilder** — HTML templates for trades, errors, summaries, alerts

### Persistence (`app/persistence/`)
- SQLAlchemy 2.0 ORM with SQLite (migration-friendly for PostgreSQL)
- Models: Candle, StrategyDefinition, BacktestRun, BacktestTrade, Signal, Trade, Position, AccountSnapshot, Notification, Incident, SelectedStrategy



## Data Flow

### Backtest Flow
1. Load historical candles (REST API or DB)
2. Strategy generates signals across all candles
3. Engine simulates trades with fees/slippage/SL/TP
4. Metrics calculated from results
5. Ranking scores strategies across multiple dimensions
6. Winner selected (never by profit alone)

### Live/Paper Trading Flow
1. WebSocket receives live candle updates
2. Strategy generates signal on closed candle
3. Risk engine evaluates signal against all rules
4. If approved: broker submits order
5. Telegram notification sent
6. Trade and signal persisted to database

## Safety Architecture

```
Signal → Risk Engine → [APPROVE/REJECT] → Broker
                ↓
            Kill Switch ──── Emergency Halt
            Max Positions
            Daily Loss Limit
            Stop Loss Required (live)
            Consecutive Loss Cooldown
            Symbol Cooldown
            Error Halt
```

No module may bypass the risk engine in live mode. The kill switch can halt all trading immediately.
