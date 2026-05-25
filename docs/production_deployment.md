# Production Deployment Guide

Complete step-by-step guide to deploy, populate data, run paper trading, and eventually enable live trading on your VPS.

---

## Table of Contents

1. [Server Setup](#1-server-setup)
2. [Configuration](#2-configuration)
3. [Database & Data Population](#3-database--data-population)
4. [Strategy Optimization](#4-strategy-optimization)
5. [Validation & Approval](#5-validation--approval)
6. [Paper Trading (Simulation)](#6-paper-trading-simulation)
7. [Paper Trading (Live WebSocket)](#7-paper-trading-live-websocket)
8. [Web Dashboard](#8-web-dashboard)
9. [Monitoring & Maintenance](#9-monitoring--maintenance)
10. [Going Live](#10-going-live)
11. [Emergency Procedures](#11-emergency-procedures)
12. [Command Reference](#12-command-reference)

---

## 1. Server Setup

### Prerequisites

- Linux VPS (Ubuntu 22.04+ recommended)
- Python 3.12+
- Node.js 18+ and PM2 (for process management)
- Git

### Installation

```bash
# Clone the repository
cd /home/admin/apps/derrick
git clone <your-repo-url> Binance-Spot-Trading-Bot
cd Binance-Spot-Trading-Bot

# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install all dependencies
pip install -e ".[dev,web]"

# Create required directories
mkdir -p data logs

# Install PM2 if not present
npm install -g pm2
```

### Verify Installation

```bash
source .venv/bin/activate

# Check Python sees the package
python -m app.cli show-config

# Run the test suite (must all pass before proceeding)
python -m pytest tests/ -v
```

> ⚠️ **Do NOT proceed if any tests fail.** Fix failures first.

---

## 2. Configuration

### Create `.env`

```bash
cp .env.example .env
nano .env   # or vim .env
```

### Required Settings

```env
# ── Application ──
APP_ENV=production
TRADING_MODE=paper                     # Start with paper, always

# ── Binance API ──
BINANCE_API_KEY=your_actual_key
BINANCE_API_SECRET=your_actual_secret
BINANCE_BASE_URL=https://api.binance.com
BINANCE_WS_URL=wss://stream.binance.com:9443/ws

# ── Telegram ──
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
ENABLE_TELEGRAM=true

# ── Trading Parameters ──
DEFAULT_QUOTE_ASSET=USDT
TRADE_SYMBOLS=["BTCUSDT","ETHUSDT","BNBUSDT"]
TRADE_INTERVAL=1h

# ── Risk Management ──
MAX_RISK_PER_TRADE=0.02
MAX_DAILY_LOSS_PCT=0.05
MAX_OPEN_POSITIONS=5
MAX_POSITION_SIZE_PCT=0.40
STOP_LOSS_PCT=0.05

# ── Safety (DO NOT CHANGE until Phase 10) ──
ENABLE_LIVE_TRADING=false
ENABLE_KILL_SWITCH=false

# ── Fees ──
SLIPPAGE_BPS=10
TAKER_FEE_BPS=10
MAKER_FEE_BPS=10

# ── Database ──
DATABASE_URL=sqlite:///data/trading_bot.db

# ── Research / Backtest ──
BACKTEST_SYMBOLS=["BTCUSDT","ETHUSDT","BNBUSDT"]
BACKTEST_INTERVALS=["15m","1h","4h"]
BACKTEST_LOOKBACK_DAYS=180

# ── Qualification Thresholds ──
QUAL_MIN_RETURN_PCT=0
QUAL_MIN_SHARPE=0
QUAL_MIN_TRADES=5
QUAL_MAX_DRAWDOWN_PCT=0.30
QUAL_MIN_PROFIT_FACTOR=0.8
QUAL_MIN_OOS_CONSISTENCY=0
QUAL_MIN_BENCHMARK_ALPHA_PCT=0
QUAL_MIN_DATASET_PASS_RATE=0.5

# ── Regime Gating ──
REGIME_MIN_VOLATILITY_PCT=0.2
REGIME_MAX_VOLATILITY_PCT=8.0
ENABLE_REGIME_GATING=true

# ── Web Dashboard ──
WEB_PORT=8880
SESSION_TTL_HOURS=24
```

### Binance API Key Permissions

Enable ONLY these on your Binance API key:

| Permission | Enable |
|------------|--------|
| Enable Reading | ✅ Yes |
| Enable Spot & Margin Trading | ✅ Yes |
| Enable Symbol Whitelist | ✅ Yes → add BTCUSDT, ETHUSDT, BNBUSDT |
| Everything else | ❌ No |

### Verify Configuration

```bash
source .venv/bin/activate
python -m app.cli show-config
python -m app.cli health-check
```

Confirm:
- Mode shows `paper`
- `ENABLE_LIVE_TRADING` shows `false`
- Telegram test passes: `python -m app.cli send-test-telegram`

---

## 3. Database & Data Population

The database is created automatically on first run. You need to populate it with historical candle data for backtesting and optimization.

### Step 3.1: Backfill Historical Data

```bash
# Download candles for ALL configured symbols × intervals
# This fetches 180 days of history (configurable via BACKTEST_LOOKBACK_DAYS)
python -m app.cli backfill-matrix
```

**Expected output:** Progress bars for each symbol/interval pair. Takes 5-15 minutes depending on the number of combinations.

### Step 3.2: Verify Data

```bash
# Check the database has candle data
python -m app.cli health-check
```

The health check will report candle counts per symbol/interval. Ensure all configured pairs have data.

---

## 4. Strategy Optimization

The optimizer searches parameter combinations across all datasets (symbol × interval) and ranks them by robustness — not raw profit.

### Step 4.1: Run Full Optimization

```bash
# Optimize ALL strategies with walk-forward validation
# This is the recommended production command
python -m app.cli optimize --wf --profile standard

# For a specific strategy only:
python -m app.cli optimize --strategy hybrid_grid_dca --wf --profile standard
```

**Duration:** 30-90 minutes depending on your VPS specs and number of strategies.

### Step 4.2: Review Results

```bash
# View top 10 parameter sets
python -m app.cli optimize --top 10

# View which strategies are approved for paper trading
python -m app.cli show-approved
```

### Understanding Approval

A strategy+symbol+interval combination is "approved" when it passes ALL qualification thresholds:
- Minimum return, Sharpe ratio, profit factor
- Maximum drawdown
- Minimum trade count
- Walk-forward consistency
- Dataset pass rate (≥50% of datasets must qualify)

---

## 5. Validation & Approval

### Step 5.1: Parity Audit

Verify that the backtesting results match the real-time orchestrator behavior:

```bash
python -m app.cli audit-approved
```

This replays approved combinations through the exact runtime logic, catching any divergence between the backtest engine and the live orchestrator.

### Step 5.2: Paper Readiness Check

```bash
python -m app.cli paper-readiness
```

This shows a comprehensive diagnostics report:
- Which combinations are approved
- Whether the risk engine would accept them
- Whether regime conditions are favorable
- Any blockers that would prevent paper trading

### Step 5.3: Offline Simulation (Dry Run)

Test the runtime with historical data before going live:

```bash
# Simulate using historical candles (no WebSocket, instant replay)
python -m app.cli paper-trade --sim

# Simulate a specific strategy on a specific symbol
python -m app.cli paper-trade --sim --sim-symbol ETHUSDT --strategy hybrid_grid_dca
```

**Checklist:**
- ✅ Trades are triggering in the output
- ✅ Stop-losses and take-profits fire correctly
- ✅ Final equity is reported
- ✅ No errors or risk engine panics

### Step 5.4: Full Research Cycle (Optional Shortcut)

This single command runs backfill → optimize → audit → readiness in sequence:

```bash
python -m app.cli research-cycle
```

> ⚠️ This takes a long time (1-3 hours). You can run it from the web dashboard so it continues even if you disconnect.

---

## 6. Paper Trading (Simulation)

Before connecting to live market data, do a final offline simulation:

```bash
# Run paper trading with historical candle replay
python -m app.cli paper-trade --sim
```

Review the output for:
- Number of trades executed
- Win rate and PnL
- Risk engine decisions (accepted/rejected signals)
- Any errors or warnings

---

## 7. Paper Trading (Live WebSocket)

This connects to the real Binance WebSocket and trades with simulated balances.

### Start Paper Trading

```bash
# Use the best approved strategy from the database
python -m app.cli paper-trade

# Or force a specific strategy
python -m app.cli paper-trade --strategy hybrid_grid_dca
```

### Running with PM2

For production paper trading, use PM2 so it survives SSH disconnects:

```bash
# Start via PM2 directly
pm2 start /home/admin/apps/derrick/Binance-Spot-Trading-Bot/.venv/bin/python \
  --name "trading-bot-paper" \
  --interpreter none \
  -- -m app.cli paper-trade

# Or add it to ecosystem.config.js (see below)
```

### Monitor Paper Trading

```bash
# View PM2 process status
pm2 status

# View live logs
pm2 logs trading-bot-paper

# Stop paper trading
pm2 stop trading-bot-paper
```

### How Long to Paper Trade

- **Minimum:** 1-2 weeks for hourly strategies
- **Recommended:** 4+ weeks across different market conditions
- **What to watch:**
  - Telegram notifications firing correctly
  - Trades matching expected strategy behavior
  - Drawdown staying within configured limits
  - No repeated errors or risk engine halts

---

## 8. Web Dashboard

The web dashboard lets you manage everything from your browser — run commands, monitor trades, and view system status.

### Start the Dashboard

```bash
# Start with PM2 (recommended for production)
pm2 start ecosystem.config.js

# Or manually for testing
source .venv/bin/activate
python -m uvicorn app.web.server:app --host 0.0.0.0 --port 8880
```

### Login

1. Open `http://<your-vps-ip>:8880`
2. Click **"Request Login Code"**
3. Check your Telegram — you'll receive a 6-digit OTP
4. Enter the code → you're authenticated (session lasts 24h)

### Dashboard Features

| Tab | What You Can Do |
|-----|----------------|
| **Dashboard** | View system status, equity curve, recent trades, approved strategies, current winner |
| **Commands** | Run any CLI command with live terminal output. Commands continue even if you disconnect. |
| **History** | View past command runs: exit code, duration, output tail |

### Running Commands via Dashboard

1. Go to the **Commands** tab
2. Select a command from the dropdown (e.g., `optimize`)
3. Enter arguments (e.g., `--wf --profile standard`)
4. Click **▶ Run**
5. Watch live output in the terminal

> 💡 **Disconnect resilience:** If you close your browser or lose internet, the command keeps running on the server. When you reconnect, you'll see the full output.

> ⚠️ **Mutex lock:** Only one command runs at a time. The UI locks until the current command finishes or you click **⏹ Force Stop**.

### Securing with HTTPS (Recommended)

Put the dashboard behind a reverse proxy:

```bash
# Install Caddy (simplest option)
sudo apt install -y caddy

# Edit Caddyfile
sudo nano /etc/caddy/Caddyfile
```

```
yourdomain.com {
    reverse_proxy localhost:8880
}
```

```bash
sudo systemctl restart caddy
```

Caddy auto-provisions SSL certificates via Let's Encrypt.

---

## 9. Monitoring & Maintenance

### Daily Checks

```bash
# Check PM2 processes are running
pm2 status

# View recent logs
pm2 logs --lines 50

# Check system health
source .venv/bin/activate
python -m app.cli health-check
```

### Telegram Alerts

With `ENABLE_TELEGRAM=true`, you'll receive:
- Trade entries and exits
- Stop-loss triggers
- Risk engine rejections
- Daily PnL summaries
- Error and halt notifications
- Web dashboard login codes

### Re-Optimization Schedule

Market conditions change. Re-run optimization periodically:

```bash
# Weekly or bi-weekly
python -m app.cli backfill-matrix
python -m app.cli optimize --wf --profile standard
python -m app.cli audit-approved
python -m app.cli paper-readiness
```

Or use the single shortcut:

```bash
python -m app.cli research-cycle
```

### Log Rotation

PM2 log files grow over time:

```bash
# Install pm2-logrotate
pm2 install pm2-logrotate
pm2 set pm2-logrotate:max_size 50M
pm2 set pm2-logrotate:retain 7
```

---

## 10. Going Live

> 🚨 **DANGER:** Live trading executes REAL market orders with REAL money. Only proceed after weeks of successful paper trading.

### Pre-Flight Checklist

| Check | Status |
|-------|--------|
| Paper trading ran for 2+ weeks | ☐ |
| Paper PnL was positive or acceptable | ☐ |
| No unexpected risk engine halts | ☐ |
| Telegram alerts working correctly | ☐ |
| All tests pass (`pytest tests/ -v`) | ☐ |
| API key has Spot trading + Reading enabled | ☐ |
| Symbol whitelist matches `TRADE_SYMBOLS` | ☐ |
| Risk parameters reviewed and confirmed | ☐ |
| Stop-loss configured (`STOP_LOSS_PCT`) | ☐ |
| Kill switch accessible (`ENABLE_KILL_SWITCH`) | ☐ |
| You understand you can lose money | ☐ |

### Update `.env` for Live

```env
TRADING_MODE=live
ENABLE_LIVE_TRADING=true
```

### Start Live Trading

```bash
python -m app.cli live-trade
```

### Monitor Closely

- Watch Telegram alerts for every trade
- Check the web dashboard frequently
- Keep the kill switch ready:

```env
# In .env, set:
ENABLE_KILL_SWITCH=true
# Then restart the bot
```

---

## 11. Emergency Procedures

### Kill Switch

The fastest way to halt all trading:

```bash
# Option 1: Kill via PM2
pm2 stop all

# Option 2: Set kill switch in .env
# Edit .env → ENABLE_KILL_SWITCH=true
# Restart the bot

# Option 3: Force kill
pm2 kill
```

### Restart After Incident

```bash
# 1. Check what happened
pm2 logs --lines 200

# 2. Review incidents in the database
source .venv/bin/activate
python -m app.cli health-check

# 3. Fix the issue

# 4. Restart
pm2 restart all
```

### Rollback to Paper Mode

```bash
# Edit .env
TRADING_MODE=paper
ENABLE_LIVE_TRADING=false

# Restart
pm2 restart all
```

---

## 12. Command Reference

All commands are run with `python -m app.cli <command>` (or via the web dashboard).

### System

| Command | Description |
|---------|-------------|
| `show-config` | Display current configuration |
| `health-check` | Run system health checks |
| `list-strategies` | List all registered strategies |
| `send-test-telegram` | Send a test message to verify Telegram |

### Data

| Command | Description |
|---------|-------------|
| `backfill --symbol X --interval Y --days N` | Download candles for one pair |
| `backfill-matrix` | Download candles for ALL configured pairs |

### Research & Optimization

| Command | Description |
|---------|-------------|
| `optimize --wf --profile standard` | Full optimization with walk-forward |
| `optimize --strategy X --wf` | Optimize one strategy |
| `optimize --top N` | Show top N parameter sets |
| `show-approved` | Show approved strategy combinations |
| `show-winner` | Show the current best strategy |
| `audit-approved` | Run parity audit on approved combos |
| `paper-readiness` | Full readiness diagnostics |
| `research-cycle` | Backfill → optimize → audit → readiness |

### Paper Trading

| Command | Description |
|---------|-------------|
| `paper-trade --sim` | Offline simulation (replay historical candles) |
| `paper-trade` | Live paper trading (WebSocket, simulated orders) |
| `paper-trade --strategy X` | Force a specific strategy |
| `export-report` | Export paper trade reports (JSON/CSV) |

### Live Trading

| Command | Description |
|---------|-------------|
| `live-trade` | Start live trading (requires `ENABLE_LIVE_TRADING=true`) |

---

## Typical Production Workflow

```
Step 1: backfill-matrix          ← Download 180 days of candles
         │
Step 2: optimize --wf            ← Find best parameters
         │
Step 3: show-approved            ← Check what was approved
         │
Step 4: audit-approved           ← Verify parity
         │
Step 5: paper-readiness          ← Final diagnostics
         │
Step 6: paper-trade --sim        ← Offline dry run
         │
Step 7: paper-trade              ← Live paper trading (run for weeks)
         │
Step 8: [review results]         ← Check Telegram, dashboard, PnL
         │
Step 9: live-trade               ← Only after weeks of paper success
```

> 💡 Steps 1-6 can be run from the web dashboard. Step 7 should be run via PM2 for production stability.
