# Binance Spot Trading Bot

A production-structured, modular Binance Spot automated trading platform built with Python 3.12+.

## Features

- **9 trading strategies** — trend, mean-reversion, breakout, regime-adaptive, volatility, and paper-only Hybrid Grid/DCA
- **Backtesting engine** — candle-by-candle simulation with fees, slippage, stop-loss, and take-profit
- **Multi-metric strategy ranking** — Sharpe, Sortino, drawdown, profit factor, win rate (never selects by profit alone)
- **Walk-forward validation** — train/test window splits with out-of-sample metrics
- **Buy-and-hold benchmark** — automatic comparison vs benchmark for honest evaluation
- **Winner qualification** — configurable thresholds via `.env`; unqualified winners are not auto-selected
- **Multi-dataset evaluation** — strategies evaluated across symbol × interval matrix for cross-dataset consistency
- **Matrix parameter optimization** — grid search across all datasets ranked by robustness, not profit alone
- **v2 strategies** — cooldown, trend confirmation, RSI/volume/volatility filters, ATR-scaled stops
- **Regime gating** — skip trading when market conditions are hostile (extreme volatility, dead markets)
- **Walk-forward optimization** — train/test validation during optimization, degradation penalty
- **Pass-rate qualification** — configurable % of datasets that must qualify, not all-or-nothing
- **Risk engine** — 9 enforceable rules including kill switch, position limits, daily loss, symbol cooldown, and mandatory stop-loss
- **Paper-first runtime** — paper trading and non-polluting replay are wired; live order execution remains intentionally disabled
- **Telegram notifications** — startup/shutdown, trades, stop-loss, errors, daily summaries, emergency halts
- **Persistence** — SQLAlchemy + SQLite for candles, trades, positions, incidents, and more
- **CLI interface** — Typer-based commands for backtest, trading, config, and health checks
- **Web dashboard** — Secure FastAPI + vanilla JS management UI with Telegram OTP login, real-time CLI command runner, and PM2 deployment
- **Structured logging** — structlog with console and JSON output modes

## Safety

- **Binance Spot only** — no futures, margin, or leverage
- **Live trading disabled by default** — requires explicit `ENABLE_LIVE_TRADING=true`
- **Paper trading is the default mode**
- **Kill switch** — halts all trading immediately
- **Mandatory stop-loss** in live mode
- **Risk engine is the final gate** — can reject any signal
- **Fail-fast config** — invalid or dangerous configuration raises errors at startup

## Quick Start

```bash
# Clone and install
cd "Trading Bot"
pip install -e ".[dev]"

# Copy and edit .env
cp .env.example .env
# Edit .env with your Binance API keys and Telegram token

# Show configuration
python -m app.cli show-config
```

For the complete, safe workflow from testing to live trading, please read the **[Testing and Running Guide](docs/testing_and_running.md)**.

## CLI Commands

| Command | Description |
|---------|-------------|
| `show-config` | Display current configuration |
| `health-check` | Run system health checks |
| `list-strategies` | List registered strategies |
| `backfill` | Download and persist historical candles |
| `run-backtest` | Matrix evaluation across all configured symbols/intervals |
| `run-backtest --symbols X,Y` | Override symbols for one run |
| `run-backtest --intervals 1h,4h` | Override intervals for one run |
| `run-backtest --days N` | Override lookback days |
| `run-backtest --validate` | Enable walk-forward validation (default: on) |
| `backfill-matrix` | Download candles for all configured symbols × intervals |
| `optimize` | Matrix-wide parameter optimization across all datasets |
| `optimize --strategy X` | Optimize a single strategy |
| `optimize --profile fast\|standard\|deep` | Select parameter grid size for auto-tuning |
| `optimize --workers N` | Evaluate parameter sets concurrently |
| `optimize --max-combinations N` | Cap large optimizer profiles |
| `optimize --wf` | Enable walk-forward validation during optimization |
| `optimize --wf --wf-windows 3` | Walk-forward with 3 windows |
| `optimize --top 15` | Show top 15 parameter sets |
| `show-approved` | Show which datasets are approved for paper trading |
| `paper-readiness` | Show approval and runtime-readiness diagnostics |
| `research-cycle` | Backfill, optimize, parity-audit, readiness, and paper-sim smoke |
| `paper-trade` | Paper trading: live WebSocket or `--sim` replay |
| `paper-trade --strategy <name>` | Manual strategy selection |
| `paper-trade --sim` | Replay persisted candles offline |
| `show-winner` | Display latest backtest winner with qualification status |
| `show-winner --qualified` | Show only qualified winners |
| `export-report` | Export paper trade reports (JSON/CSV) |
| `live-trade` | Confirms live trading remains intentionally disabled |
| `send-test-telegram` | Send a test Telegram message |

## Project Structure

```
app/
├── config/        # Pydantic-validated settings
├── core/          # Enums, exceptions, logging, utilities
├── data/          # Binance REST + WebSocket clients
├── strategies/    # 9 trading strategies + base + registry
├── backtesting/   # Engine, metrics, optimizer, ranking
├── risk/          # Risk engine, rules, position sizing
├── execution/     # Paper + Binance live brokers
├── notifications/ # Telegram notifier + message builder
├── persistence/   # SQLAlchemy models + repositories
├── reporting/     # Report generation + export (JSON/CSV/MD)
├── services/      # Orchestrator, strategy selection, health
├── web/           # FastAPI web dashboard + static frontend
│   ├── routes/    # Auth, dashboard, commands API
│   └── static/    # HTML, CSS, JS (SPA)
├── cli.py         # CLI entrypoints
└── main.py        # Application bootstrap
tests/             # pytest regression suite
ecosystem.config.js # PM2 process config
```

## Configuration

All settings are managed through environment variables. See `.env.example` for the complete list with documentation.

Key variables:
- `TRADING_MODE` — `paper` (default) or `live`
- `ENABLE_LIVE_TRADING` — must be `true` for live trading
- `ENABLE_KILL_SWITCH` — emergency halt
- `TRADE_SYMBOLS` — JSON array of trading pairs
- `MAX_RISK_PER_TRADE` — max risk as fraction of equity

## Strategies

| Strategy | Type | Description |
|----------|------|-------------|
| EMA+ATR Crossover | Trend | Dual EMA crossover with ATR volatility filter |
| RSI Mean Reversion | Mean Reversion | RSI overbought/oversold with reversal signals |
| Bollinger Band | Mean Reversion | Band touch reversals with width filter |
| Breakout | Momentum | Price breakout with volume confirmation |
| Regime Adaptive | Hybrid | ADX-based regime detection, switches trend/range logic |
| Momentum Continuation | Trend | SMA/ADX/ROC continuation filter |
| Pullback Uptrend | Trend | EMA trend with pullback entry filter |
| Volatility Breakout | Momentum | Keltner/ATR volatility expansion |
| Hybrid Grid/DCA | Spot Basket | Paper-only long Grid/DCA basket with capped scale-ins and full-basket exits |

Hybrid Grid/DCA uses adaptive spacing, trend-slope and anchor-deviation filters, fee-buffered exits, stop-exit cooldowns, and optimizer diagnostics for open/scale-in/take-profit/stop counts, allocation usage, hold bars, and PnL by exit path.

## Testing

It is mandatory to run the test suite before any live execution.

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=app --cov-report=term-missing
```

See the [Testing and Running Guide](docs/testing_and_running.md) for the full 7-step safety pipeline.

## Web Dashboard

A secure web UI for remote management and monitoring.

```bash
# Install web dependencies
pip install -e ".[web]"

# Start locally
python -m uvicorn app.web.server:app --host 0.0.0.0 --port 8880

# Deploy with PM2 on VPS
pm2 start ecosystem.config.js
```

**Login:** Uses Telegram OTP — a 6-digit code is sent to your configured chat. No passwords.

**Features:**
- Dashboard: system status, equity chart, trades, approved combinations, winner
- Commands: run any CLI command with live terminal output streaming via WebSocket
- History: view past command runs with exit codes, duration, and output
- Lock: only one command runs at a time; UI locks until it completes or is force-stopped
- Disconnect resilience: commands keep running on the server if your browser disconnects; output is buffered and replayed on reconnect

## Documentation

- [Testing and Running Guide](docs/testing_and_running.md)
- [Architecture Details](docs/architecture.md)

## License

MIT
