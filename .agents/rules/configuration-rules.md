---
trigger: always_on
---

## Configuration rules
- All configuration must come from validated settings
- Use environment variables and pydantic validation
- Never hardcode API keys, secrets, chat IDs, or credentials
- Keep a complete `.env.example`
- Fail fast on missing or dangerous config
- Startup must clearly log the current runtime mode