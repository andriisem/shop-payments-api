# Manual API testing

A walk through every edge case of `POST /carts/{cart_id}/payments` with curl. Each step shows
the command and the expected answer. The automated tests cover the same cases (ids in brackets);
this guide is for seeing them yourself.

Run the steps **in order**: some of them pay a cart, and a paid cart cannot be paid again.

## 0. Setup

Start from a fresh database, so the sample cart is unpaid:

```bash
docker compose down -v && docker compose up -d --wait db
cp -n .env.example .env        # then put a secret in JWT_SECRET (see README)
uv run --env-file .env flask --app app:create_local_app run   # keep it running in its own terminal
```

In a second terminal, from the project root, define these helpers (bash or zsh):

```bash
BASE=http://127.0.0.1:5000
ALICE=11111111-1111-1111-1111-111111111111      # sample user with a cart and a default card
BOB=22222222-2222-2222-2222-222222222222        # sample user with no cart and no card
CART=c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1       # Alice's sample cart
KETTLE=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa     # a sample product

token() { uv run --env-file .env python scripts/dev_token.py "$1" 15; }   # valid 15 minutes
ALICE_TOKEN=$(token $ALICE); BOB_TOKEN=$(token $BOB)

# pay <token> <cart> <idempotency key> [json body]: prints status, replay header and body.
# -g: send the URL as typed; otherwise curl expands {…} in it and hits another URL.
pay() {
  local body=${4:-'{}'}
  curl -g -s -i -X POST "$BASE/carts/$2/payments" \
    -H "Authorization: Bearer $1" -H "Idempotency-Key: $3" \
    -H "Content-Type: application/json" -d "$body" | grep -E '^HTTP|^Idempotent|^\{'
}

db() { docker compose exec -T db psql -U shop -d shop_payments -tAc "$1" | head -1; }
new_cart() {   # a new cart with one item for the given user; prints its id
  db "WITH c AS (INSERT INTO carts (user_id) VALUES ('$1') RETURNING id),
      i AS (INSERT INTO cart_items (cart_id, product_id, quantity, unit_price)
            SELECT id, '$KETTLE', 1, 45.00 FROM c)
      SELECT id FROM c"
}
new_card() {   # a new, non-default card for Alice with the given mock token; prints its id
  db "INSERT INTO user_payment_methods (user_id, provider_token, last_four)
      VALUES ('$ALICE', '$1', '0000') RETURNING id"
}
```

The mock provider decides by the card's token: `decline` → declined, `error` → rejected,
`timeout` → no answer, anything else → success. The local total service is a mock that answers
like the shop's real one: quantity × unit price summed over the cart's items. New carts from
`new_cart` hold one 45.00 USD kettle, so they cost 45.00.

## 1. HTTP basics

| Command | Expected |
|---|---|
| `curl -s -i -X OPTIONS $BASE/carts/$CART/payments \| grep -E '^HTTP\|^Allow'` | `200`, `Allow` includes `POST` (no token needed) |
| `curl -s -o /dev/null -w '%{http_code}\n' $BASE/carts/$CART/payments` | `405` (GET is not allowed) |
| `curl -s -o /dev/null -w '%{http_code}\n' -X POST $BASE/nope` | `404` (unknown URL, not 401) |

## 2. Authentication (AC-21)

| Command | Expected |
|---|---|
| `curl -s -i -X POST $BASE/carts/$CART/payments -H "Idempotency-Key: k" \| grep -E '^HTTP\|^WWW\|^\{'` | `401 unauthenticated`, `WWW-Authenticate: Bearer …` |
| `pay not-a-jwt $CART k` | `401 unauthenticated` |
| `pay "$(JWT_SECRET=another-secret-that-is-at-least-32-bytes uv run python scripts/dev_token.py $ALICE)" $CART k` | `401`: signed with the wrong secret |
| `pay "$(token 99999999-9999-9999-9999-999999999999)" $CART k` | `401`: valid token, but no such user |
| Optional: `T=$(uv run --env-file .env python scripts/dev_token.py $ALICE 1); sleep 95; pay $T $CART k` | `401`: expired (1 minute plus 30 s leeway) |

The error body never says *why* a token was rejected; the server log does.

## 3. Malformed requests (AC-4, FR-2, EC-7)

Nothing is charged or stored for any of these.

| Command | Expected |
|---|---|
| `curl -s -i -X POST $BASE/carts/$CART/payments -H "Authorization: Bearer $ALICE_TOKEN" \| grep -E '^HTTP\|^\{'` | `400 missing_idempotency_key` |
| `pay $ALICE_TOKEN $CART "$(printf 'k%.0s' {1..256})"` | `400 invalid_request`: key longer than 255 |
| `pay $ALICE_TOKEN not-a-uuid k` | `400 invalid_request` |
| `pay $ALICE_TOKEN "{$CART}" k` | `400 invalid_request`: only the canonical UUID form |
| `pay $ALICE_TOKEN $CART k '{"payment_method_id": "nope"}'` | `400 invalid_request` |
| `pay $ALICE_TOKEN $CART k '{"amount": "0.01"}'` | `400 invalid_request`: the client never sends the amount (FR-8) |
| `pay $ALICE_TOKEN $CART k '{not json'` | `400 invalid_request` |
| `pay $ALICE_TOKEN $CART k '["a", "list"]'` | `400 invalid_request` |
| `curl -s -i -X POST $BASE/carts/$CART/payments -H "Authorization: Bearer $ALICE_TOKEN" -H "Idempotency-Key: k" -H "Transfer-Encoding: chunked" -d '{}' \| grep -E '^HTTP\|^\{'` | `400 invalid_request`: chunked bodies are rejected |

Check that nothing was stored: `db "SELECT count(*) FROM payments"` → `0`.

## 4. Ownership and missing resources (AC-5, AC-9, FR-5, FR-7)

| Command | Expected |
|---|---|
| `pay $BOB_TOKEN $CART k` | `404 cart_not_found`: Alice's cart, as seen by Bob (never 403) |
| `pay $ALICE_TOKEN 99999999-9999-9999-9999-999999999999 k` | `404 cart_not_found` |
| `pay $ALICE_TOKEN $CART k '{"payment_method_id": "99999999-9999-9999-9999-999999999999"}'` | `404 payment_method_not_found` |

## 5. Declined, rejected, then a successful retry (AC-10, AC-11, AC-12)

```bash
DECLINE=$(new_card tok_decline); ERROR=$(new_card tok_error)
```

| Command | Expected |
|---|---|
| `pay $ALICE_TOKEN $CART try-1 "{\"payment_method_id\": \"$DECLINE\"}"` | `402`, `"status":"failed"`, `"failure_code":"card_declined"` |
| `pay $ALICE_TOKEN $CART try-2 "{\"payment_method_id\": \"$ERROR\"}"` | `502`, `"status":"failed"`, `"failure_code":"provider_error"` |
| `db "SELECT status FROM carts WHERE id = '$CART'"` | `active`: a failed payment leaves the cart payable |
| `pay $ALICE_TOKEN $CART try-3` | `201`, `"status":"succeeded"`, `"amount":"70.00"` with the default card |
| `db "SELECT status FROM carts WHERE id = '$CART'"` | `checked_out` |

## 6. Idempotency (AC-2, AC-3, FR-3, FR-4)

| Command | Expected |
|---|---|
| `pay $ALICE_TOKEN $CART try-3` | `201`, `Idempotent-Replayed: true`, the same payment as before; no second charge |
| `pay $ALICE_TOKEN $CART try-1 "{\"payment_method_id\": \"$DECLINE\"}"` | `402` replayed: a replay returns the *current* state, whatever it is |
| `pay $ALICE_TOKEN $CART try-3 "{\"payment_method_id\": \"$DECLINE\"}"` | `422 idempotency_key_reused`: same key, different request |
| `pay $ALICE_TOKEN "$(new_cart $ALICE)" try-3` | `422 idempotency_key_reused`: same key, another cart |
| `pay $ALICE_TOKEN $CART try-4` | `409 cart_not_active`: the cart is paid (AC-6) |

## 7. Unknown outcome: timeout (AC-13, AC-14)

```bash
SLOW=$(new_card tok_timeout); SLOW_CART=$(new_cart $ALICE)
```

| Command | Expected |
|---|---|
| `pay $ALICE_TOKEN $SLOW_CART slow-1 "{\"payment_method_id\": \"$SLOW\"}"` | `202`, `"status":"pending"`: money may be moving |
| `pay $ALICE_TOKEN $SLOW_CART slow-1 "{\"payment_method_id\": \"$SLOW\"}"` | `202` replayed; the provider is not called again |
| `pay $ALICE_TOKEN $SLOW_CART slow-2` | `409 payment_in_progress` with the pending `payment_id`: the cart stays blocked |

## 8. Cart rules (AC-7, AC-8)

| Command | Expected |
|---|---|
| `EMPTY=$(db "INSERT INTO carts (user_id) VALUES ('$ALICE') RETURNING id"); pay $ALICE_TOKEN $EMPTY e-1` | `422 cart_empty` |
| `pay $BOB_TOKEN "$(new_cart $BOB)" b-1` | `422 no_payment_method`: Bob has no default card |

## 9. Parallel requests (AC-20, EC-1)

Two requests at the same moment. Locally they may not overlap every time; the automated
concurrency tests force the race deterministically.

```bash
RACE=$(new_cart $ALICE)
pay $ALICE_TOKEN $RACE same-key & pay $ALICE_TOKEN $RACE same-key & wait
```
Expected: both show the same payment `id`; one of them has `Idempotent-Replayed: true`. The
replayed one may still say `202 pending` if it arrived while the first was at the provider;
replaying again returns `201`.

```bash
RACE2=$(new_cart $ALICE)
pay $ALICE_TOKEN $RACE2 key-a & pay $ALICE_TOKEN $RACE2 key-b & wait
```
Expected: one `201`, one `409 payment_in_progress` pointing at the first payment. Never two
charges: `db "SELECT count(*) FROM payments WHERE cart_id = '$RACE2'"` → `1`.

## 10. The provider answered, but recording it failed (AC-18, EC-10)

Simulate a database failure in TX2 with a trigger that rejects every UPDATE on `payments`
(TX1 only inserts, so it still works):

```bash
FAIL_CART=$(new_cart $ALICE)
db "CREATE FUNCTION fail_update() RETURNS trigger LANGUAGE plpgsql AS
    \$\$ BEGIN RAISE EXCEPTION 'simulated TX2 failure'; END \$\$"
db "CREATE TRIGGER fail_update BEFORE UPDATE ON payments FOR EACH ROW EXECUTE FUNCTION fail_update()"
pay $ALICE_TOKEN $FAIL_CART tx2-1
db "DROP TRIGGER fail_update ON payments"; db "DROP FUNCTION fail_update()"
```

The `db` lines print `CREATE FUNCTION`, `CREATE TRIGGER` and so on. Expected for the payment:
`202`, `"status":"pending"`, not a 500. The card was charged but could not be
recorded, so the server log shows `[payment_id=… cart_id=… user_id=…] Provider result
Succeeded(provider_payment_id='mock_ch_…') was not recorded; answering pending`, followed by the
traceback of the simulated failure (with SQL parameters hidden). The payment stays `pending` and
blocks the cart until it is reconciled.

## 11. Totals come from the total service (A-3, EC-9, EC-11)

The payment part never calculates the amount; it charges what the total service answers.
Locally that is the mock, so the amount follows the cart's items.

```bash
# priced_cart <quantity> <unit price> <currency>: a cart for Alice with one product line.
# The total uses the cart line's unit price; the product's own price (0 here) is not used.
priced_cart() {
  db "WITH p AS (INSERT INTO products (name, price, currency) VALUES ('Test', 0, '$3') RETURNING id),
      c AS (INSERT INTO carts (user_id) VALUES ('$ALICE') RETURNING id),
      i AS (INSERT INTO cart_items (cart_id, product_id, quantity, unit_price)
            SELECT c.id, p.id, $1, $2 FROM c, p)
      SELECT id FROM c"
}
```

| Command | Expected |
|---|---|
| `pay $ALICE_TOKEN "$(priced_cart 3 89.99 USD)" t-1` | `201`, `"amount":"269.97"`, `"currency":"USD"` |
| `pay $ALICE_TOKEN "$(priced_cart 2 10.00 EUR)" t-2` | `201`, `"amount":"20.00"`, `"currency":"EUR"` |
| `pay $ALICE_TOKEN "$(priced_cart 1 0.00 USD)" t-3` | `422 invalid_amount` |
| `pay $ALICE_TOKEN "$(priced_cart 1 -5.00 USD)" t-5` | `422 invalid_amount`: a negative total |
| `pay $ALICE_TOKEN "$(priced_cart 1000000 9999999999.99 USD)" t-6` | `422 invalid_amount`: too large for `NUMERIC(12,2)` |
| `pay $ALICE_TOKEN "$(priced_cart 1 700 JPY)" t-4` | `422 unsupported_currency`: 700 JPY would be charged as 70000 |

A cart with items in two currencies cannot be totalled: the mock raises and the answer is a
generic `500`. The real total service decides that case (A-3).

## What cannot be tested by hand

- **Fractional or `NaN` totals** (EC-9): cart prices are stored as `NUMERIC(12,2)`, so the mock
  can never answer them. The automated tests inject them.
- **An unconfirmed provider answer** and **a payment finalised elsewhere during the call**
  (FR-13, FR-14): they need a fake provider; see `tests/test_create_payment.py`.
- **That no transaction is open during the provider call** (AC-17) and **that the card token
  never reaches a log line** (AC-16): see `tests/test_transactions.py` and
  `tests/test_create_payment.py`.
