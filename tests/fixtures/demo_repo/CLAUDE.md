# Acme web

@docs/architecture.md
@docs/runbook.md

## Commands

- Dev server: `npm run dev`
- Lint and fix: `npm run lint:fix`
- Tests: `make test`
- Database migrations: `make migrate`
- Ship it: `just deploy`
- Background worker: `python -m app.worker`

```bash
python scripts/seed.py
python scripts/backfill.py --since 2024-01-01
```

## Layout

- HTTP routes are in `src/app/routes.py`
- Config is read from env: `DATABASE_URL`, `STRIPE_SECRET_KEY`, `CACHE_URL`
