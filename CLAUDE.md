# shop-payments-api

Flask + SQLAlchemy 2 + PostgreSQL service with one job: charge a user's cart via `POST /carts/{cart_id}/payments`.

**The spec is the source of truth:** @docs/tech-spec.md. Every behaviour traces to an FR/NFR/AC/EC id there. If the code needs something the spec doesn't cover, update the spec first.

## Commands

```bash
uv sync                          # install deps (.venv)
docker compose up -d db          # PostgreSQL 16 for app and tests
uv run pytest                    # all tests (need the db container)
uv run pytest -k <name>          # single test
uv run ruff format . && uv run ruff check --fix .
uv run mypy                      # strict, app/ only
```

## Architecture

- `app/payments/routes.py`: HTTP only (parse, validate, serialize). No business logic.
- `app/payments/service.py`: `PaymentService.pay_cart()` implements the spec's payment flow (TX1 → provider → TX2).
- `app/external.py`: the systems outside the payment part (payment provider, the shop's existing total service) and their stand-ins. Injected; tests swap them via `create_app()`. The payment part never calculates the amount.
- `app/payments/errors.py`: domain errors carry `code` + HTTP status. A single error handler maps them.
- `migrations/`: plain SQL. `001_base_schema.sql` is the provided schema and must never be edited. Add new numbered files.

## Rules that tools can't enforce

- Money is `Decimal` / `NUMERIC(12,2)`, never `float`. JSON amounts are strings. The provider gets integer minor units.
- Never hold a DB transaction or row lock while calling the provider.
- Unknown provider outcome (timeout) → payment stays `pending`. Never mark it `failed` unless the provider confirmed there was no charge.
- Status updates are guarded: `WHERE id = :id AND status = 'pending'`.
- Every cart / payment-method / payment query filters by `user_id`. Another user's resource → 404.
- Never log or return `provider_token`. Return only a generic `card_declined`, never detailed decline reasons.
- Tests run against real PostgreSQL, not SQLite: partial indexes and `FOR UPDATE` must be exercised for real.

## Workflow

- Test-first: write the failing test for an AC/EC, then implement.
- Commit directly to `main`. Use Conventional Commits (commitlint enforced, body lines ≤ 100 chars). Make small commits, one per logical change.
- Before committing, run ruff, mypy and pytest; they must be green. For payment-flow changes, run the `payment-reviewer` agent.
