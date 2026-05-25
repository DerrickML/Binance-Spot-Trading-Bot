---
description: Implement the Binance Spot live broker safely.
---

Implement the Binance Spot live broker safely.

Tasks:
1. create or improve Binance Spot authenticated execution
2. fetch exchange metadata and symbol filters dynamically
3. validate quantity and price precision
4. validate balances before order submission
5. support market and limit orders
6. support safe dry-run or test validation where appropriate
7. log all requests and responses
8. ensure live trading is impossible unless explicitly enabled

Requirements:
- Spot only
- no futures, margin, or leverage support
- refuse execution when config is unsafe
- require risk engine approval before order placement
- require stop loss for live trades
- add tests for filter parsing and order rounding

Output:
- files implemented
- safety checks enforced
- commands run
- test results
- any limitations still remaining