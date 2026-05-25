---
trigger: always_on
---

## Safety rules
- Refuse to enable live trading by default
- Refuse to place real orders unless `ENABLE_LIVE_TRADING=true`
- Refuse live trading if required API credentials are missing
- Refuse live trading if stop-loss parameters are absent
- Refuse live trading if the risk engine is disabled or bypassed
- Refuse unsafe order placement
- Refuse unsupported trading modes such as futures or margin
- Halt trading when the kill switch is triggered
- Halt trading on repeated execution failures
- Halt trading on daily max loss breach
- Halt trading on severe configuration mismatch