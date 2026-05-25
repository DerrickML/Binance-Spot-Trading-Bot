---
description: Perform a safety and hardening review of the trading bot codebase.
---

Perform a safety and hardening review of the trading bot codebase.

Review for:
- dangerous defaults
- missing config validation
- live trading safety gaps
- risk engine bypass paths
- missing stop-loss enforcement
- missing logging on critical actions
- poor exception handling
- missing tests for dangerous logic
- exchange precision/filter handling gaps
- duplicated or fragile code

Then apply the highest-value safe fixes and run verification.

Output:
- issues found
- fixes applied
- files changed
- commands run
- test or verification result
- remaining hardening TODOs