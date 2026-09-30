"""Requests the endpoint must reject. A rejected request never calls the provider and never
creates a payment."""

import uuid
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from flask.testing import FlaskClient
from sqlalchemy import Engine, text
from werkzeug.test import TestResponse

from app import create_app
from app.external import CartTotal
from tests.factories import (
    CartFixture,
    insert_cart,
    insert_payment_method,
)
from tests.fakes import FakeTotalService, RecordingProvider
from tests.helpers import add_card, make_test_config, pay, payment_count, post_payment
from tests.tokens import auth_headers


def assert_rejected(
    response: TestResponse,
    status: int,
    code: str,
    engine: Engine,
    provider: RecordingProvider,
) -> dict[str, Any]:
    assert response.status_code == status
    error: dict[str, Any] = response.get_json()["error"]
    assert error["code"] == code
    assert error["message"]
    assert provider.calls == []
    assert payment_count(engine) == 0
    return error


@pytest.mark.parametrize("key", [None, ""], ids=["missing", "empty"])
def test_ac4_idempotency_key_is_required(
    client: FlaskClient,
    engine: Engine,
    alice: CartFixture,
    provider: RecordingProvider,
    key: str | None,
) -> None:
    headers = auth_headers(alice.user_id)
    if key is not None:
        headers["Idempotency-Key"] = key

    response = post_payment(client, alice.cart_id, headers, json={})

    assert_rejected(response, 400, "missing_idempotency_key", engine, provider)


@pytest.mark.parametrize("key", ["k" * 256, "ключ"], ids=["too-long", "non-ascii"])
def test_fr2_idempotency_key_must_be_printable_ascii_up_to_255(
    client: FlaskClient,
    engine: Engine,
    alice: CartFixture,
    provider: RecordingProvider,
    key: str,
) -> None:
    headers = {**auth_headers(alice.user_id), "Idempotency-Key": key}

    response = post_payment(client, alice.cart_id, headers, json={})

    assert_rejected(response, 400, "invalid_request", engine, provider)


@pytest.mark.parametrize(
    ("cart_id", "request_kwargs"),
    [
        ("not-a-uuid", {"json": {}}),
        ("urn:uuid:c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1", {"json": {}}),
        ("c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1", {"json": {}}),
        (None, {"json": {"payment_method_id": "not-a-uuid"}}),
        (None, {"json": {"payment_method_id": "{c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1}"}}),
        (None, {"json": {"payment_method_id": 42}}),
        (None, {"json": ["not", "an", "object"]}),
        (None, {"json": {"amount": "0.01"}}),  # FR-8: the client never sends the amount
        (None, {"data": "{not json", "content_type": "application/json"}),
        (None, {"data": "[" * 8_000 + "]" * 8_000, "content_type": "application/json"}),
    ],
    ids=[
        "bad-cart-uuid",
        "urn-cart-uuid",
        "cart-uuid-without-dashes",
        "bad-method-uuid",
        "braced-method-uuid",
        "method-not-a-string",
        "body-not-an-object",
        "unknown-field",
        "malformed-json",
        "deeply-nested-json",
    ],
)
def test_ec7_malformed_request_is_invalid(
    client: FlaskClient,
    engine: Engine,
    alice: CartFixture,
    provider: RecordingProvider,
    cart_id: str | None,
    request_kwargs: dict[str, Any],
) -> None:
    headers = {**auth_headers(alice.user_id), "Idempotency-Key": "key-1"}

    response = post_payment(client, cart_id or alice.cart_id, headers, **request_kwargs)

    assert_rejected(response, 400, "invalid_request", engine, provider)


@pytest.mark.parametrize(
    "request_kwargs",
    [{}, {"json": {}}, {"json": {"payment_method_id": None}}],
    ids=["no-body", "empty-object", "null-method"],
)
def test_request_without_a_card_uses_the_default_card(
    client: FlaskClient,
    alice: CartFixture,
    provider: RecordingProvider,
    request_kwargs: dict[str, Any],
) -> None:
    headers = {**auth_headers(alice.user_id), "Idempotency-Key": "key-1"}

    response = post_payment(client, alice.cart_id, headers, **request_kwargs)

    assert response.status_code == 201
    assert response.get_json()["payment_method_id"] == str(alice.payment_method_id)
    assert [call.token for call in provider.calls] == ["tok_test_visa"]


def test_fr2_idempotency_key_of_255_characters_is_accepted(
    client: FlaskClient, alice: CartFixture
) -> None:
    assert pay(client, alice, key="k" * 255).status_code == 201


def test_missing_key_is_reported_before_a_bad_body(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    headers = auth_headers(alice.user_id)

    response = post_payment(client, alice.cart_id, headers, data="{not json")

    assert_rejected(response, 400, "missing_idempotency_key", engine, provider)


def test_chunked_body_is_rejected(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    # A chunked body can be cut or dropped by the WSGI layer, which would charge the
    # default card instead of the requested one. The body is tiny, so it must have a length.
    headers = {
        **auth_headers(alice.user_id),
        "Idempotency-Key": "key-1",
        "Transfer-Encoding": "chunked",
    }

    response = post_payment(
        client, alice.cart_id, headers, json={"payment_method_id": str(uuid.uuid4())}
    )

    assert_rejected(response, 400, "invalid_request", engine, provider)


def test_ac5_another_users_cart_is_not_found(
    client: FlaskClient,
    engine: Engine,
    alice: CartFixture,
    bob: CartFixture,
    provider: RecordingProvider,
) -> None:
    headers = {**auth_headers(bob.user_id), "Idempotency-Key": "key-1"}

    response = post_payment(client, alice.cart_id, headers, json={})

    assert_rejected(response, 404, "cart_not_found", engine, provider)


def test_fr5_missing_cart_is_not_found(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    headers = {**auth_headers(alice.user_id), "Idempotency-Key": "key-1"}

    response = post_payment(client, uuid.uuid4(), headers, json={})

    assert_rejected(response, 404, "cart_not_found", engine, provider)


@pytest.mark.parametrize("status", ["checked_out", "abandoned"])
def test_ac6_inactive_cart_cannot_be_paid(
    client: FlaskClient,
    engine: Engine,
    alice: CartFixture,
    provider: RecordingProvider,
    status: str,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE carts SET status = :status WHERE id = :id"),
            {"status": status, "id": alice.cart_id},
        )

    response = pay(client, alice)

    assert_rejected(response, 409, "cart_not_active", engine, provider)


def test_ac7_empty_cart_cannot_be_paid(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    with engine.begin() as conn:
        empty_cart = insert_cart(conn, alice.user_id)
    empty = CartFixture(alice.user_id, empty_cart, alice.payment_method_id)

    response = pay(client, empty)

    assert_rejected(response, 422, "cart_empty", engine, provider)


def test_ac8_no_default_card_and_no_card_in_request(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE user_payment_methods SET is_default = false WHERE user_id = :id"),
            {"id": alice.user_id},
        )

    response = pay(client, alice)

    assert_rejected(response, 422, "no_payment_method", engine, provider)


def test_ac9_another_users_card_is_not_found(
    client: FlaskClient,
    engine: Engine,
    alice: CartFixture,
    bob: CartFixture,
    provider: RecordingProvider,
) -> None:
    response = pay(client, alice, payment_method_id=bob.payment_method_id)

    assert_rejected(response, 404, "payment_method_not_found", engine, provider)


def test_fr7_missing_card_is_not_found(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    response = pay(client, alice, payment_method_id=uuid.uuid4())

    assert_rejected(response, 404, "payment_method_not_found", engine, provider)


def test_a5_newest_default_card_wins(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    with engine.begin() as conn:
        insert_payment_method(conn, alice.user_id, token="tok_newest_default", is_default=True)

    response = pay(client, alice)

    assert response.status_code == 201
    assert [call.token for call in provider.calls] == ["tok_newest_default"]


def test_ac14_live_payment_blocks_a_new_key(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    slow_card = add_card(engine, alice, "tok_timeout")
    pending = pay(client, alice, key="key-1", payment_method_id=slow_card)
    assert pending.status_code == 202

    response = pay(client, alice, key="key-2")

    assert response.status_code == 409
    error = response.get_json()["error"]
    assert error["code"] == "payment_in_progress"
    assert error["payment_id"] == pending.get_json()["id"]
    assert len(provider.calls) == 1
    assert payment_count(engine) == 1


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0.00"),
        Decimal("-5.00"),
        Decimal("70.005"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1E+30"),
        Decimal("10000000000.00"),  # does not fit NUMERIC(12,2)
    ],
    ids=["zero", "negative", "fraction-of-a-cent", "nan", "infinity", "huge", "too-many-digits"],
)
def test_ec9_total_must_be_positive_whole_cents(
    client: FlaskClient,
    engine: Engine,
    alice: CartFixture,
    provider: RecordingProvider,
    total_service: FakeTotalService,
    amount: Decimal,
) -> None:
    total_service.total = CartTotal(amount, "USD")

    response = pay(client, alice)

    assert_rejected(response, 422, "invalid_amount", engine, provider)


def test_a3_amount_is_whatever_the_total_service_says(
    client: FlaskClient, alice: CartFixture, total_service: FakeTotalService
) -> None:
    total_service.total = CartTotal(Decimal("12.3"), "EUR")

    response = pay(client, alice)

    assert response.status_code == 201
    assert response.get_json()["amount"] == "12.30"
    assert response.get_json()["currency"] == "EUR"
    assert total_service.calls == [alice.cart_id]


@pytest.mark.parametrize(
    "currency",
    ["usd", "US", "", "JPY", "KWD"],
    ids=["lower-case", "two-letters", "empty", "zero-decimal-jpy", "three-decimal-kwd"],
)
def test_ec11_currency_must_be_a_supported_2_decimal_code(
    client: FlaskClient,
    engine: Engine,
    alice: CartFixture,
    provider: RecordingProvider,
    total_service: FakeTotalService,
    currency: str,
) -> None:
    # 70 JPY would otherwise be sent as 7000 minor units: 100 times too much (NFR-1).
    total_service.total = CartTotal(Decimal("70.00"), currency)

    response = pay(client, alice)

    assert_rejected(response, 422, "unsupported_currency", engine, provider)


def test_unexpected_error_is_a_generic_500(
    engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    class BrokenTotalService:
        def get_total(self, cart_id: UUID) -> CartTotal:
            raise RuntimeError("secret internal detail")

    app = create_app(make_test_config(), provider=provider, total_service=BrokenTotalService())
    try:
        response = pay(app.test_client(), alice)
    finally:
        app.extensions["engine"].dispose()

    assert response.status_code == 500
    assert "secret" not in response.get_data(as_text=True)
    assert provider.calls == []
    assert payment_count(engine) == 0
