---
trigger: always_on
---

## Risk engine rules
- The risk engine is the final gate before execution
- It must be able to reject any strategy signal
- It must enforce position sizing, loss limits, cooldowns, and exposure limits
- No module may bypass the risk engine in live mode
- Risk decisions must be logged and explainable