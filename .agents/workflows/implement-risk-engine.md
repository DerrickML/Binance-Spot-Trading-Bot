---
description: Implement the risk engine as the final gate before execution.
---

Implement the risk engine as the final gate before execution.

It must enforce:
- max risk per trade
- max open positions
- max capital exposure
- max daily loss
- consecutive-loss cooldown
- per-symbol cooldown
- disabled symbols support
- mandatory stop loss for live mode
- emergency halt on repeated errors

Requirements:
- the risk engine must be able to reject any strategy signal
- risk decisions must be explicit and logged
- no live order path may bypass the risk engine
- add tests for rejection and approval cases

Output:
- files implemented
- rules enforced
- commands run
- test results
- remaining risk gaps if any