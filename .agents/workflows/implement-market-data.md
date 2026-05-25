---
description: Implement the market data layer for the trading bot.
---

Implement the market data layer for the trading bot.

Tasks:
1. build historical candle fetching for Binance Spot
2. build a websocket client for live market data
3. normalize candle records into a consistent format
4. support configurable symbols and intervals
5. handle reconnects safely
6. create clean interfaces for strategy consumption
7. store candles through the persistence layer where appropriate

Requirements:
- do not hardcode exchange assumptions
- log reconnects and stale data
- keep REST and websocket responsibilities separate
- use clean abstractions

Then run verification or tests for the market data layer.

Output:
- modules implemented
- files changed
- commands run
- test result
- any gaps remaining