# Testing and Running the Trading Bots

This guide outlines the strict, safety-first workflow required before any strategy is permitted to run in the live markets. The Binance Spot Trading Bot is built with a "paper-first" philosophy. No strategy should be traded with real capital until it has passed through this pipeline.

---

## The Pre-Live Pipeline

Before flipping the switch to live trading, a strategy must survive the following gauntlet:

1. **Unit & Integration Testing:** Codebase stability verification.
2. **Data Gathering:** Downloading historical candles for the target datasets.
3. **Research & Optimization:** Matrix-wide parameter search and walk-forward validation.
4. **Parity Auditing:** Ensuring the backtesting engine's results match the real-time orchestrator's behavior.
5. **Offline Simulation:** Replaying historical candles through the exact paper-trading runtime.
6. **Live Paper Trading:** Running against the live Binance WebSocket with simulated balances.
7. **Live Execution:** Turning on real capital.

---

## Phase 1: Unit & Integration Testing

Before running any commands, ensure the codebase is structurally sound. The test suite covers risk engine rules, strategy logic, order derivation, base asset extraction, and orchestrator determinism.

```bash
# Run the complete test suite
python -m pytest tests/ -v

# Run with fail-fast to stop on the first error
python -m pytest tests/ -x -v
```

**What must pass:** All tests (currently 355+ tests). If any tests fail, **do not proceed** to backtesting or paper trading.

---

## Phase 2: Data Gathering

The bot requires historical Kline (candle) data to evaluate strategies.

1. Ensure your `.env` has `BACKTEST_SYMBOLS` and `BACKTEST_INTERVALS` configured.
2. Run the matrix backfill command to download data for all combinations.

```bash
# Fetch data for all configured symbols and intervals
python -m app.cli backfill-matrix

# Alternatively, fetch specific data
python -m app.cli backfill --symbol BTCUSDT --interval 4h --days 180
```

---

## Phase 3: Research & Optimization

The optimizer runs combinations of parameters across all datasets, evaluating them using out-of-sample (walk-forward) validation. It ranks strategies by robustness rather than raw profit.

```bash
# Auto-tune the Hybrid Grid/DCA strategy using the standard parameter grid
# with Walk-Forward (WF) validation enabled
python -m app.cli optimize --strategy hybrid_grid_dca --profile standard --wf

# View the top parameter sets across the matrix
python -m app.cli optimize --top 10
```

*Note: The optimizer saves the best configurations to the `approved_combinations` database table.*

---

## Phase 4: Parity Auditing

Because backtesting engines use assumptions (e.g., executing on the close price), the results might diverge from the real tick-by-tick orchestrator. The parity auditor bridges this gap by running the approved combinations through the actual orchestrator logic.

```bash
# Run the parity auditor on all approved combinations
python -m app.cli audit-approved

# View the final readiness of approved strategies
python -m app.cli paper-readiness
```

If a strategy is statistically approved by the optimizer but fails the parity audit (e.g., risk rules block it, or execution diverges), it will be flagged and should not be paper traded.

---

## Phase 5: Offline Simulation

You can test the real orchestrator offline by feeding it the historical candles you downloaded. This bypasses the WebSocket and plays the market out instantly, tracking paper balances and PnL.

```bash
# Run a dry-run simulation of a specific strategy
python -m app.cli paper-trade --sim --sim-symbol ETHUSDT --strategy hybrid_grid_dca
```

**Checklist:**
- Watch the CLI output to ensure trades are triggering.
- Confirm that Stop-Losses and Take-Profits are executing.
- Review the final equity and drawdown stats in the terminal summary.

---

## Phase 6: Live Paper Trading

Once a strategy passes simulation, run it against live market data. The bot will listen to the Binance WebSocket, calculate indicators in real-time, enforce risk rules, and execute "paper" trades without touching your real Binance wallet.

```bash
# Start paper trading using the best qualified strategy from the database
python -m app.cli paper-trade

# Or, force a specific strategy
python -m app.cli paper-trade --strategy rsi_mean_reversion
```

**Requirements during Paper Trading:**
- Keep this running for a significant duration (days/weeks) depending on your timeframe.
- Monitor Telegram notifications to ensure alerts are firing correctly.
- Review the daily PnL and risk engine rejections in the logs.

---

## Phase 7: Going Live

**DANGER:** Live trading executes real market orders using your Binance API keys. Only proceed if Phase 6 was profitable and behaved exactly as expected over a long period.

### Pre-Flight Checklist

1. **API Keys:** Ensure `BINANCE_API_KEY` and `BINANCE_API_SECRET` are correct in `.env` and have Spot trading permissions.
2. **Environment variables:**
   - `TRADING_MODE=live`
   - `ENABLE_LIVE_TRADING=true`
3. **Risk Configuration:** Double check your `.env` risk parameters:
   - `MAX_RISK_PER_TRADE` (e.g., 0.02 for 2%)
   - `MAX_POSITION_SIZE_PCT` (e.g., 0.40 for 40%)
   - `MAX_DAILY_LOSS_PCT` (e.g., 0.05 for 5%)
   - `STOP_LOSS_PCT` (e.g., 0.05 for 5%)
4. **Kill Switch:** Be aware that you can set `ENABLE_KILL_SWITCH=true` in `.env` and restart the bot to immediately halt trading.

### Execution

```bash
# Start the live trading bot
python -m app.cli live-trade
```

If the environment is not configured correctly, the bot's Fail-Fast checks will instantly abort execution. If it starts, monitor the logs and your Telegram alerts closely.
