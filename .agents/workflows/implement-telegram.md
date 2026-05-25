---
description: Implement the Telegram notification layer.
---

Implement the Telegram notification layer.

Support messages for:
- startup
- shutdown
- trade opened
- trade closed
- stop loss hit
- take profit hit
- daily summary
- weekly summary
- backtest winner
- warnings
- errors
- emergency halt

Requirements:
- use clean message builders
- keep messages concise and readable
- make notifier easy to disable via config
- add tests for message formatting where practical

Output:
- notifier modules implemented
- files changed
- commands run
- tests run
- examples of supported message types