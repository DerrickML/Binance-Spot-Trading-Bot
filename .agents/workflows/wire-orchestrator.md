---
description: Wire the system together through an orchestrator service.
---

Wire the system together through an orchestrator service.

Connect:
- config
- logging
- persistence
- market data
- strategy selection
- risk engine
- broker
- notifications
- reporting

Requirements:
- startup must clearly log mode
- unsafe live startup must fail fast
- paper mode should be the default runnable path
- module boundaries should remain clean
- no hidden side effects during imports

Run a verification command after wiring.

Output:
- orchestration flow summary
- files changed
- commands run
- startup verification result
- next integration gaps