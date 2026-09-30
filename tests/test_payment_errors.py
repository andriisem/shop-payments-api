"""Requests the endpoint must reject. A rejected request never calls the provider and never
creates a payment."""

import uuid
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from flask.testing import FlaskClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from app import create_app
from app.payments.totals import CartTotal
from tests.conftest import make_test_config
from tests.factories import (
    CartFixture,
    insert_cart,
    insert_cart_item,
    insert_payment_method,
    insert_product,
)
from tests.fakes import RecordingProvider
from tests.helpers import add_card, pay, payment_count
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


def post(
    client: FlaskClient,
    cart_id: object,
    headers: dict[str, str],
    **kwargs: Any,
) -> TestResponse:
    return client.post(f"/carts/{cart_id}/payments", headers=headers, **kwargs)


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

    response = post(client, alice.cart_id, headers, json={})

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

    response = post(client, alice.cart_id, headers, json={})

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

    response = post(client, cart_id or alice.cart_id, headers, **request_kwargs)

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

    response = post(client, alice.cart_id, headers, **request_kwargs)

    assert response.status_code == 201
    assert response.get_json()["payment_method_id"] == str(alice.payment_method_id)
    assert [call.token for call in provider.calls] == ["tok_test_visa"]


def test_fr2_idempotency_key_of_255_characters_is_accepted(
    client: FlaskClient, alice: CartFixture
) -> None:
    assert pay(client, alice, key="k" * 255).status_code == 201


def test_oversized_body_is_rejected_as_json(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    headers = {**auth_headers(alice.user_id), "Idempotency-Key": "key-1"}
    body = '{"payment_method_id": "' + "x" * 20_000 + '"}'

    response = post(client, alice.cart_id, headers, data=body, content_type="application/json")

    assert_rejected(response, 413, "request_entity_too_large", engine, provider)


def test_missing_key_is_reported_before_a_bad_body(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    headers = auth_headers(alice.user_id)

    response = post(client, alice.cart_id, headers, data="{not json")

    assert_rejected(response, 400, "missing_idempotency_key", engine, provider)


def test_chunked_body_is_rejected(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    # A chunked body can be cut or dropped by the WSGI layer, which would charge the
    # default card instead of the requested one. The body is tiny, so require a length.
    headers = {
        **auth_headers(alice.user_id),
        "Idempotency-Key": "key-1",
        "Transfer-Encoding": "chunked",
    }

    response = post(client, alice.cart_id, headers, json={"payment_method_id": str(uuid.uuid4())})

    assert_rejected(response, 411, "length_required", engine, provider)


def test_wrong_method_is_rejected_as_json(client: FlaskClient, alice: CartFixture) -> None:
    response = client.get(f"/carts/{alice.cart_id}/payments")

    assert response.status_code == 405
    assert response.get_json()["error"]["code"] == "method_not_allowed"
    assert set(response.headers["Allow"].split(", ")) == {"OPTIONS", "POST"}


def test_unknown_route_is_rejected_as_json(client: FlaskClient) -> None:
    response = client.post("/carts")

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_unexpected_error_returns_generic_json_500(
    engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    class BrokenTotals:
        def calculate(self, session: Session, cart_id: UUID) -> CartTotal:
            raise RuntimeError("secret internal detail")

    app = create_app(make_test_config(), provider=provider, totals=BrokenTotals())
    try:
        response = pay(app.test_client(), alice)
    finally:
        app.extensions["engine"].dispose()

    error = assert_rejected(response, 500, "internal_error", engine, provider)
    assert "secret" not in response.get_data(as_text=True)
    assert error["message"] == "Internal server error."


def test_ac5_another_users_cart_is_not_found(
    client: FlaskClient,
    engine: Engine,
    alice: CartFixture,
    bob: CartFixture,
    provider: RecordingProvider,
) -> None:
    headers = {**auth_headers(bob.user_id), "Idempotency-Key": "key-1"}

    response = post(client, alice.cart_id, headers, json={})

    assert_rejected(response, 404, "cart_not_found", engine, provider)


def test_fr5_missing_cart_is_not_found(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    headers = {**auth_headers(alice.user_id), "Idempotency-Key": "key-1"}

    response = post(client, uuid.uuid4(), headers, json={})

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


def test_ec8_mixed_currencies_are_rejected(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    with engine.begin() as conn:
        euro_product = insert_product(conn, Decimal("10.00"), currency="EUR")
        insert_cart_item(conn, alice.cart_id, euro_product, 1, Decimal("10.00"))

    response = pay(client, alice)

    assert_rejected(response, 422, "mixed_currencies", engine, provider)


@pytest.mark.parametrize("unit_price", [Decimal("0.00"), Decimal("-5.00")])
def test_ec9_total_must_be_positive(
    client: FlaskClient,
    engine: Engine,
    alice: CartFixture,
    provider: RecordingProvider,
    unit_price: Decimal,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE cart_items SET unit_price = :price WHERE cart_id = :id"),
            {"price": unit_price, "id": alice.cart_id},
        )

    response = pay(client, alice)

    assert_rejected(response, 422, "invalid_amount", engine, provider)
