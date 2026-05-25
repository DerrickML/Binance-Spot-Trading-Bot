---
description: Implement the initial trading strategies using a shared strategy interface.
---

Implement the initial trading strategies using a shared strategy interface.

Create or improve:
1. base strategy interface
2. EMA crossover + ATR filter
3. RSI mean reversion
4. Bollinger Band mean reversion
5. Breakout strategy
6. Regime-aware strategy

Requirements:
- each strategy must support configurable parameters
- each strategy must be testable in isolation
- strategy code must generate signals only
- strategy code must not place orders directly
- include clear docstrings where helpful

Also add or update tests for signal generation behavior.

Output:
- strategies implemented
- files changed
- tests added or updated
- commands run
- test results