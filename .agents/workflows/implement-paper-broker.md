---
description: implement-paper-broker
---

Implement the paper trading broker and portfolio handling.

Tasks:
1. create a shared broker abstraction
2. implement paper order execution
3. simulate balances, fills, P&L, and position state
4. store paper trades and snapshots
5. keep the interface close to the live broker design
6. connect paper execution to strategy, risk, and notification flows

Requirements:
- include fees and slippage
- avoid unrealistic fill assumptions where possible
- log all simulated orders and fills
- add tests for paper execution behavior

Output:
- files implemented
- commands run
- tests run
- paper trading capabilities now supported