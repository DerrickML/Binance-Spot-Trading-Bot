---
trigger: always_on
---

## Persistence rules
- Use SQLAlchemy
- Use SQLite by default
- Keep the schema migration-friendly for future PostgreSQL support
- Persist trades, candles, incidents, account states, strategy runs, and notifications
- Make trade decisions auditable from stored records