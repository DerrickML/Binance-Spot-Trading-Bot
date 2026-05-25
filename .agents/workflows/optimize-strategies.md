---
description: Implement or improve the strategy optimization and ranking workflow.
---

Implement or improve the strategy optimization and ranking workflow.

Tasks:
1. allow multiple parameter sets per strategy
2. run batch backtests
3. rank strategies using multiple metrics
4. favor robustness and consistency, not raw profit alone
5. save optimization results for later review
6. produce a readable summary of the best candidate and why it was selected

Requirements:
- include stability across time windows where feasible
- include trade count sanity checks
- reject obviously weak overfit candidates when possible
- produce structured outputs

Then run a verification pass or related tests.

Output:
- ranking logic implemented
- files changed
- commands run
- verification result
- selected strategy summary format