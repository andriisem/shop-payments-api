# Manual API test report

**Result: 48 of 48 checks passed, 0 failed.** Every step of [manual-testing.md](manual-testing.md), sections 1 to 11, was run against the local app, and the API behaved as the guide expects.

## Run

| | |
|---|---|
| Date | 2026-09-30, 23:00 EEST |
| Commit | `7c4ec36` chore: update run command in .env.example |
| App | `flask --app app:create_local_app run` on http://127.0.0.1:5000, local `.env`; mock provider and mock total service |
| Database | PostgreSQL 16.15 in docker compose, reset with `docker compose down -v` before the run (step 0) |
| Stack | Python 3.14.7, Flask 3.1.3, SQLAlchemy 2.1.1, psycopg 3.3.6, PyJWT 2.15.1 |
| Client | macOS 26.6.2, zsh 5.9, curl 8.7.1 |

## How it was run

The helper blocks of the guide (section 0 and section 11) were sourced unchanged, then each command was run exactly as written, in the guide's order. Each response was reduced to its status line, `Idempotent-Replayed` header and key body fields, and compared with the guide's expected result. The two parallel requests of section 9 were checked response by response. The **Actual** column is what the server returned; ids are shortened to 8 characters.

## Results

### 1. HTTP basics

| Check | Expected | Actual | Result |
|---|---|---|---|
| OPTIONS needs no token | 200, Allow includes POST | HTTP/1.1 200 Allow: POST, OPTIONS | ✅ PASS |
| GET is not allowed | 405 | 405 | ✅ PASS |
| Unknown URL is 404, not 401 | 404 | 404 | ✅ PASS |

### 2. Authentication (AC-21)

| Check | Expected | Actual | Result |
|---|---|---|---|
| No token | 401 unauthenticated, WWW-Authenticate: Bearer | HTTP/1.1 401 WWW-Authenticate: Bearer code:unauthenticated | ✅ PASS |
| Garbage token | 401 unauthenticated | HTTP/1.1 401 code:unauthenticated | ✅ PASS |
| Token signed with the wrong secret | 401 unauthenticated | HTTP/1.1 401 code:unauthenticated | ✅ PASS |
| Valid token, unknown user | 401 unauthenticated | HTTP/1.1 401 code:unauthenticated | ✅ PASS |
| Expired token (1 min + 30 s leeway) | 401 unauthenticated | HTTP/1.1 401 code:unauthenticated | ✅ PASS |

### 3. Malformed requests (AC-4, FR-2, EC-7)

| Check | Expected | Actual | Result |
|---|---|---|---|
| Missing Idempotency-Key | 400 missing_idempotency_key | HTTP/1.1 400 code:missing_idempotency_key | ✅ PASS |
| Key longer than 255 | 400 invalid_request | HTTP/1.1 400 code:invalid_request | ✅ PASS |
| Cart id not a UUID | 400 invalid_request | HTTP/1.1 400 code:invalid_request | ✅ PASS |
| Braced cart UUID | 400 invalid_request | HTTP/1.1 400 code:invalid_request | ✅ PASS |
| payment_method_id not a UUID | 400 invalid_request | HTTP/1.1 400 code:invalid_request | ✅ PASS |
| Client sends an amount | 400 invalid_request | HTTP/1.1 400 code:invalid_request | ✅ PASS |
| Malformed JSON | 400 invalid_request | HTTP/1.1 400 code:invalid_request | ✅ PASS |
| Body is a list | 400 invalid_request | HTTP/1.1 400 code:invalid_request | ✅ PASS |
| Chunked body | 400 invalid_request | HTTP/1.1 400 code:invalid_request | ✅ PASS |
| Nothing was stored | 0 payments | 0 | ✅ PASS |

### 4. Ownership and missing resources (AC-5, AC-9)

| Check | Expected | Actual | Result |
|---|---|---|---|
| Bob pays Alice's cart | 404 cart_not_found | HTTP/1.1 404 code:cart_not_found | ✅ PASS |
| Cart does not exist | 404 cart_not_found | HTTP/1.1 404 code:cart_not_found | ✅ PASS |
| Card does not exist | 404 payment_method_not_found | HTTP/1.1 404 code:payment_method_not_found | ✅ PASS |

### 5. Declined, rejected, successful retry (AC-10, AC-11, AC-12)

| Check | Expected | Actual | Result |
|---|---|---|---|
| Declined card | 402 failed, card_declined | HTTP/1.1 402 amount:70.00 currency:USD failure_code:card_declined id:464a3865 status:failed | ✅ PASS |
| Provider rejects | 502 failed, provider_error | HTTP/1.1 502 amount:70.00 currency:USD failure_code:provider_error id:9ed98c08 status:failed | ✅ PASS |
| Cart still payable | cart active | active | ✅ PASS |
| Retry with a new key, default card | 201 succeeded | HTTP/1.1 201 amount:70.00 currency:USD id:78998cb2 status:succeeded | ✅ PASS |
| Cart is paid | cart checked_out | checked_out | ✅ PASS |

### 6. Idempotency (AC-2, AC-3, AC-6)

| Check | Expected | Actual | Result |
|---|---|---|---|
| Same key and body: replay | 201, Idempotent-Replayed, same payment | HTTP/1.1 201 Idempotent-Replayed: true amount:70.00 currency:USD id:78998cb2 status:succeeded | ✅ PASS |
| Replay of the declined attempt | 402, Idempotent-Replayed, same payment | HTTP/1.1 402 Idempotent-Replayed: true amount:70.00 currency:USD failure_code:card_declined id:464a3865 status:failed | ✅ PASS |
| Same key, other card | 422 idempotency_key_reused | HTTP/1.1 422 code:idempotency_key_reused | ✅ PASS |
| Same key, other cart | 422 idempotency_key_reused | HTTP/1.1 422 code:idempotency_key_reused | ✅ PASS |
| New key on the paid cart | 409 cart_not_active | HTTP/1.1 409 code:cart_not_active | ✅ PASS |

### 7. Timeout (AC-13, AC-14)

| Check | Expected | Actual | Result |
|---|---|---|---|
| Provider times out | 202 pending | HTTP/1.1 202 amount:45.00 currency:USD id:58a39edf status:pending | ✅ PASS |
| Replay while pending | 202, Idempotent-Replayed, pending | HTTP/1.1 202 Idempotent-Replayed: true amount:45.00 currency:USD id:58a39edf status:pending | ✅ PASS |
| New key while pending | 409 payment_in_progress with the pending payment_id | HTTP/1.1 409 code:payment_in_progress payment_id:58a39edf | ✅ PASS |

### 8. Cart rules (AC-7, AC-8)

| Check | Expected | Actual | Result |
|---|---|---|---|
| Empty cart | 422 cart_empty | HTTP/1.1 422 code:cart_empty | ✅ PASS |
| No default card (Bob) | 422 no_payment_method | HTTP/1.1 422 code:no_payment_method | ✅ PASS |

### 9. Parallel requests (AC-20, EC-1)

| Check | Expected | Actual | Result |
|---|---|---|---|
| Parallel, same key | same payment id twice, exactly one Idempotent-Replayed | HTTP/1.1 201 amount:45.00 currency:USD id:a1c431dd status:succeeded  /  HTTP/1.1 202 Idempotent-Replayed: true amount:45.00 currency:USD id:a1c431dd status:pending | ✅ PASS |
| Parallel, different keys | one 201, one 409 pointing at it | HTTP/1.1 201 amount:45.00 currency:USD id:a4147d60 status:succeeded  /  HTTP/1.1 409 code:payment_in_progress payment_id:a4147d60 | ✅ PASS |
| Only one payment for that cart | 1 payment | 1 | ✅ PASS |

### 10. TX2 failure (AC-18, EC-10)

| Check | Expected | Actual | Result |
|---|---|---|---|
| TX2 fails after the charge | 202 pending, not 500 | HTTP/1.1 202 amount:45.00 currency:USD id:7c7c1791 status:pending | ✅ PASS |
| Payment stays pending in the DB | payment pending | pending | ✅ PASS |

### 11. Totals come from the total service (A-3, EC-9, EC-11)

| Check | Expected | Actual | Result |
|---|---|---|---|
| 3 x 89.99 USD | 201, 269.97 USD | HTTP/1.1 201 amount:269.97 currency:USD id:6be62f75 status:succeeded | ✅ PASS |
| 2 x 10.00 EUR | 201, 20.00 EUR | HTTP/1.1 201 amount:20.00 currency:EUR id:e9675a0c status:succeeded | ✅ PASS |
| Zero total | 422 invalid_amount | HTTP/1.1 422 code:invalid_amount | ✅ PASS |
| 700 JPY | 422 unsupported_currency | HTTP/1.1 422 code:unsupported_currency | ✅ PASS |
| Negative total | 422 invalid_amount | HTTP/1.1 422 code:invalid_amount | ✅ PASS |
| Total too large for NUMERIC(12,2) | 422 invalid_amount | HTTP/1.1 422 code:invalid_amount | ✅ PASS |
| New cart (1 kettle) costs 45.00 | 201, 45.00 USD | HTTP/1.1 201 amount:45.00 currency:USD id:cfaa5039 status:succeeded | ✅ PASS |

## Observations

- **Amounts follow the cart.** With the mock total service, the sample cart is charged 70.00 USD, a cart with one kettle 45.00 USD, 3 × 89.99 USD 269.97 USD and 2 × 10.00 EUR 20.00 EUR. The payment part only asked the total service; it never calculated a total.
- **Parallel, same key (section 9):** the replayed response came back `202 pending` because it arrived while the first request was still at the provider; the first returned `201 succeeded` for the same payment. This is the documented behaviour: replaying again returns `201`.
- **Section 10 log line, checked in the server terminal.** The server logged `Provider result Succeeded(provider_payment_id='mock_ch_b06c…') was not recorded; answering pending`, with the traceback of the simulated failure, `[SQL parameters hidden due to hide_parameters=True]` and no card token. **Finding:** the line did not say which payment it was about: the payment, cart and user ids were attached to the log record but Flask's default format does not print them (NFR-7). Fixed after this run: every payment log line now starts with `[payment_id=… cart_id=… user_id=…]`, and `tests/test_transactions.py` asserts it.

## Retest after the log fix

The section 10 finding was fixed (payment log lines now start with the ids) and retested the
same day at 23:07 EEST, on a second local server started with the fixed code
(`flask --app app:create_local_app run --port 5001`) and the same database.

| Check | Expected | Actual | Result |
|---|---|---|---|
| Section 10: TX2 fails after the charge | 202 pending, not 500 | HTTP/1.1 202, id `be46b361-…`, status pending | ✅ PASS |
| Stored payment | pending | `be46b361-…` pending | ✅ PASS |
| Log line names the payment | `[payment_id=… cart_id=… user_id=…]` before the message | `[payment_id=be46b361-… cart_id=9cc9766d-… user_id=11111111-…] Provider result Succeeded(provider_payment_id='mock_ch_6435…') was not recorded; answering pending` | ✅ PASS |
| The ids match the response and the database | same payment and cart | same | ✅ PASS |
| Traceback | simulated failure, SQL parameters hidden, no card token | simulated TX2 failure; `[SQL parameters hidden …]`; no `tok_` in the log | ✅ PASS |
| Section 7: timeout warning also names the payment | ids before the message | `[payment_id=066ba42a-… cart_id=100e5bb9-… user_id=11111111-…] Provider timed out; payment stays pending` | ✅ PASS |

## Not covered by manual testing

As the guide explains, these need injected fakes and are covered by the automated tests only: fractional and `NaN` totals (EC-9), unconfirmed provider answers (FR-13), a payment finalized elsewhere during the call (FR-14), no open transaction during the provider call (AC-17), and the card token never reaching a log line (AC-16).

## State left behind

The local database now holds the run's data: 15 carts and 10 payments (6 `succeeded`, 2 `failed`, 2 `pending`). Two payments stay `pending` on purpose: the timeout from section 7 and the TX2 failure from section 10; with a real provider they would be reconciled. To start again from the sample data: `docker compose down -v && docker compose up -d --wait db`.
