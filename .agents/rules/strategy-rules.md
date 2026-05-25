---
trigger: always_on
---

## Strategy rules
- Every strategy must inherit from a common base interface
- Every strategy must support configurable parameters
- Every strategy must be testable in isolation
- Strategy code may generate signals, but must not place orders directly
- Strategy outputs must flow through risk validation before execution
- Prefer understandable strategies over opaque logic
- Do not select winning strategies using raw profit alone
- Favor robustness, consistency, and drawdown control