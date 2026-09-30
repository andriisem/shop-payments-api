# shop-payments-api

The payment part of an online shop: one endpoint, `POST /carts/{cart_id}/payments`, that charges
a user's cart with their saved card, plus the SQL schema behind it.

Built with Python 3.12+, Flask, SQLAlchemy 2 and PostgreSQL 16. The full design, with every
requirement id used below (FR, NFR, AC, EC), is in [docs/tech-spec.md](docs/tech-spec.md).

## Run it

You need [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync                                  # install dependencies into .venv
docker compose up -d --wait db           # PostgreSQL 16 on localhost:5434
```

On first start, Postgres creates the database from `migrations/*.sql`: the provided base
schema with its sample data (Alice, her cart and card), then the payments table. This happens
only for an **empty** volume; to start over, run `docker compose down -v` first.

Create your local settings. `.env` is gitignored; set `JWT_SECRET` in it:

```bash
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'   # paste as JWT_SECRET
uv run --env-file .env flask --app app:create_local_app run       # http://127.0.0.1:5000
```

Pay Alice's sample cart. Tokens normally come from the identity provider; for local testing
`scripts/dev_token.py` signs one with the same `JWT_SECRET`:

```bash
TOKEN=$(uv run --env-file .env python scripts/dev_token.py 11111111-1111-1111-1111-111111111111)

curl -i -X POST http://127.0.0.1:5000/carts/c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1/payments \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: checkout-1" \
  -H "Content-Type: application/json" \
  -d '{}'
```

The first call returns `201` with the payment. Sending it again returns the same payment with
`Idempotent-Replayed: true`, and the card is not charged again.

## Test it

The tests run against a real PostgreSQL: the partial unique index, `FOR UPDATE` and the race
tests cannot be checked on SQLite. Each run recreates the `shop_payments_test` database in the
compose container.

```bash
docker compose up -d --wait db
uv run pytest                                    # all tests
uv run ruff format --check . && uv run ruff check .
uv run mypy                                      # strict, app/ only
```

| Test file | What it proves |
|---|---|
| `test_create_payment.py` | Outcomes: success, decline, provider error, timeout, the card token never leaks |
| `test_payment_errors.py` | Every rejected request: 400, 404, 409, 422. None of them charges or writes |
| `test_idempotency.py` | Replays, key reuse, keys scoped per user |
| `test_concurrency.py` | Real threads racing on one cart: at most one charge |
| `test_transactions.py` | No transaction or lock during the provider call; a failed TX2 stays `pending` |
| `test_auth.py` | Every way an access token can be invalid |
| `test_mock_total_service.py` | The local total-service mock, and that the app charges what it answers |
| `test_payments_schema.py` | The table's constraints, which are the last line of defence |

## The API

```
POST /carts/{cart_id}/payments
Authorization: Bearer <access token>
Idempotency-Key: <1–255 printable ASCII characters, one per payment attempt>

{"payment_method_id": "<uuid>"}      ← optional; the user's default card when omitted
```

The client never sends the amount. It comes from the shop's total service.

| Status | Meaning | What the client should do |
|---|---|---|
| `201` | Charged; the cart is `checked_out` | Show the confirmation |
| `202` | Outcome unknown (for example a provider timeout); the payment is `pending` | Resend the **same** key and body until it changes |
| `402` | Card declined (`card_declined`), no charge | Let the user pick another card; use a **new** key |
| `502` | Provider rejected the request (`provider_error`), no charge | Retry later with a **new** key |
| `400` / `401` / `404` / `409` / `422` | The request was rejected before anything was charged | See `error.code` |

Errors look like `{"error": {"code": "cart_not_active", "message": "..."}}`. A `409
payment_in_progress` also carries the `payment_id` that blocks the cart. The full contract is
in the spec.

**The one rule for clients:** the same key and the same body always give the same payment,
in its current state. Only a `failed` payment lets you start a new attempt with a new key.

### Mock provider

There is no real payment provider. The mock decides by the card's token:

| Token contains | Result |
|---|---|
| `decline` | Declined: `402`, `failed` |
| `error` | Provider rejects the request: `502`, `failed` |
| `timeout` | Outcome unknown: `202`, stays `pending` |
| anything else | Success: `201`, `succeeded` |

Alice's sample card (`tok_test_alice_visa`) succeeds. To try a decline, add a card and pass its
id as `payment_method_id`. Do this before paying the sample cart successfully: a paid cart is
`checked_out`, so any further payment gets `409 cart_not_active` (run `docker compose down -v`
to start over).

```bash
docker compose exec db psql -U shop -d shop_payments -c "INSERT INTO user_payment_methods
  (user_id, provider_token, last_four) VALUES ('11111111-1111-1111-1111-111111111111',
  'tok_decline', '0002') RETURNING id;"
```

## Assumptions

- **Scope.** Users, carts, the total calculation service and saved cards already exist. This
  service builds only the payment part. It **never calculates** the amount: it asks the
  existing total service through a one-method interface (`TotalService` in `app/external.py`).
  Locally a mock stands in for it and answers the way that service would: the sum of
  quantity × unit price over the cart's items, in the products' currency. A cart with items in
  more than one currency cannot be totalled and fails with a 500.
- **Identity.** The caller is identified by a JWT access token from the shop's identity
  provider, which the service verifies itself: signature, expiry, issuer, audience, token type
  `at+jwt`, at most 15 minutes of lifetime, and that the user exists. Issuing and refreshing
  tokens is the identity provider's job. Locally a shared HS256 secret stands in for the
  provider's keys.
- **Money.** Amounts are `Decimal` / `NUMERIC(12,2)`, sent as strings in JSON (`"70.00"`), and
  given to the provider in minor units (`7000`). Only currencies with exactly 2 decimal places
  are accepted; JPY or KWD would otherwise be charged the wrong amount.
- **Synchronous provider.** The endpoint waits for the provider. If the provider times out, the
  payment stays `pending` and the client polls with the same key. With a real provider, its
  webhooks (plus a reconciliation job for missed ones) would resolve `pending` payments through
  the same guarded status update. Both are out of scope here.
- **Default card.** Without `payment_method_id`, the user's default card is used. If there are
  several defaults, the newest wins.
- **Other services' contracts.** While a cart has a live payment, the cart service must not
  change it. A saved card that a payment used must be soft-deleted, not deleted.
- **Not included:** refunds, webhooks, 3-D Secure, stock reservation, a job that reconciles stuck
  `pending` payments, rate limiting. See "Out of Scope" in the spec.

## Design decisions

The service must never charge twice, never lose a charge, and never charge a wrong amount.
Each decision below serves one of these.

- **Two short transactions around the provider call.** TX1 locks the cart, checks it and commits
  a `pending` payment. Then the provider is called with **no** transaction or lock open. TX2
  records the answer. A lock held during a slow network call would block other requests and
  use up the connection pool.
- **`pending` is written before the provider call.** If the process crashes after the card was
  charged, the payment still exists and can be reconciled. Nothing is lost.
- **An unknown outcome stays `pending`, never `failed`.** `failed` would let the user retry with
  a new key, and if the first charge had actually gone through, the card would be charged
  twice. Only an answer that confirms no charge happened becomes `failed`.
- **Idempotency keys, twice.** The client's key makes retries return the original payment. The
  provider is called with the payment's id as its own idempotency key, so a retried provider
  call never charges twice either.
- **A cart row lock plus a partial unique index.** `SELECT … FOR UPDATE` on the cart serializes
  parallel requests. The index `uq_payments_cart_live` allows at most one `pending` or
  `succeeded` payment per cart, so the database enforces the rule even if the code were wrong.
- **Guarded status updates.** Every change is `… WHERE status = 'pending'`, so a final payment
  can never change again.
- **READ COMMITTED, pinned.** Two requests with the same key queue on the cart lock. The second
  re-checks the key after the lock and replays the first payment. That re-check depends on
  seeing rows committed while it waited.
- **Ownership in every query.** Carts, cards and payments are always filtered by the caller's
  user id, and another user's resources return `404`, so their existence is not revealed.

## Operating notes

- **Alert on `Provider result … was not recorded`.** It means the provider answered but the
  database could not record it. The payment stays `pending` and the cart stays blocked until
  someone reconciles it with the provider, looking it up by the payment id. The index
  `idx_payments_pending_created_at` supports finding stuck payments.
- **A real total service client** must use a short timeout, because it is called while the cart
  is locked. A real provider client must use an explicit timeout and raise
  `ProviderTimeoutError` when it expires.
- **In production**, `JWT_SECRET` comes from a secret manager, not a file. Better still, verify
  RS256 tokens with the identity provider's public keys, so there is no shared secret at all.
- **The request body has no size limit** in the app. Put one in the gateway or web server.
