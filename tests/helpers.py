"""Shared request and DB-read helpers for endpoint tests."""

from typing import Any
from uuid import UUID

from flask.testing import FlaskClient
from sqlalchemy import Engine, text
from werkzeug.test import TestResponse

from tests.factories import CartFixture, fetch_row, fetch_value, insert_payment_method
from tests.tokens import auth_headers


def pay(
    client: FlaskClient,
    cart: CartFixture,
    *,
    key: str = "key-1",
    payment_method_id: UUID | None = None,
) -> TestResponse:
    body: dict[str, Any] = {}
    if payment_method_id is not None:
        body["payment_method_id"] = str(payment_method_id)
    return client.post(
        f"/carts/{cart.cart_id}/payments",
        headers={**auth_headers(cart.user_id), "Idempotency-Key": key},
        json=body,
    )


def add_card(engine: Engine, cart: CartFixture, token: str) -> UUID:
    with engine.begin() as conn:
        return insert_payment_method(conn, cart.user_id, token=token, is_default=False)


def cart_status(engine: Engine, cart: CartFixture) -> object:
    return fetch_value(engine, "SELECT status FROM carts WHERE id = :id", id=cart.cart_id)


def stored_payment(engine: Engine, payment_id: object) -> dict[str, object]:
    """The payment as committed, read from a separate connection."""
    return fetch_row(
        engine,
        "SELECT status, failure_code, provider_payment_id FROM payments WHERE id = :id",
        id=payment_id,
    )


def cart_payment_statuses(engine: Engine, cart: CartFixture) -> list[object]:
    with engine.connect() as conn:
        return list(
            conn.scalars(
                text("SELECT status FROM payments WHERE cart_id = :id ORDER BY created_at"),
                {"id": cart.cart_id},
            )
        )


def payment_count(engine: Engine) -> object:
    return fetch_value(engine, "SELECT count(*) FROM payments")
