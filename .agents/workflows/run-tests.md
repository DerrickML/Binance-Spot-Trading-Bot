---
description: Run the relevant tests for the current repository state and fix obvious failures.
---

Run the relevant tests for the current repository state and fix obvious failures.

Tasks:
1. inspect available tests
2. run the most relevant test subset first
3. run the broader test suite if reasonable
4. fix clear import, syntax, typing, or logic issues discovered
5. report what passed and what remains

Requirements:
- do not claim success without actual test execution
- keep fixes targeted and safe
- do not rewrite unrelated code just to silence failures

Output:
- commands run
- tests passed
- tests failed
- fixes applied
- remaining blockers