---
description: Implement the backtesting engine and metrics pipeline.
---

Implement the backtesting engine and metrics pipeline.

Tasks:
1. build candle-by-candle trade simulation
2. support entries and exits
3. include fees and slippage
4. support stop loss and take profit
5. record trades and equity curve
6. compute metrics such as return, drawdown, Sharpe, profit factor, win rate, and trade count
7. export structured outputs to JSON, CSV, or markdown where useful

Requirements:
- backtests must be deterministic when inputs are fixed
- metrics must be validated with tests
- strategy comparison must consider risk-adjusted performance
- do not rank by profit alone

Then run the related tests.

Output:
- files implemented
- commands run
- test results
- sample reports produced if any