---
trigger: always_on
---

## Testing rules
- Write or update tests whenever logic changes materially
- Prefer `pytest`
- Include fixtures for candle data and strategy scenarios
- Test config validation, order validation, risk rules, strategy logic, metrics, and broker behavior
- After implementing meaningful code, run the relevant tests
- Do not claim a feature works unless it has at least basic verification
