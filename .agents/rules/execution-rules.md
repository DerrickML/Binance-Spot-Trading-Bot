---
trigger: always_on
---

## Execution rules
- Implement a shared broker abstraction
- Paper mode and live mode should behave similarly at the interface level
- Live broker requests must be validated before submission
- All order requests and responses must be logged
- Avoid duplicate orders during retries
- Use dry-run or test validation where appropriate before real execution
- If execution is uncertain, prefer refusal over unsafe action