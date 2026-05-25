---
trigger: always_on
---

## Data and exchange handling rules
- Never hardcode Binance precision or symbol filters
- Always fetch exchange metadata and validate against it
- Always round quantities and prices using exchange rules
- Always validate balances before live order submission
- Always account for fees and slippage in backtesting and paper trading
- Always normalize incoming market data before downstream use
- Always handle websocket reconnects safely
- Always log stale data and stream issues