"""Test data inserted with plain SQL, so tests do not depend on the code under test."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, Engine, text


@dataclass(frozen=True)
class CartFixture:
    user_id: UUID
    cart_id: UUID
    payment_method_id: UUID


def _insert(conn: Connection, sql: str, **params: object) -> UUID:
    row_id: UUID = conn.execute(text(sql + " RETURNING id"), params).scalar_one()
    return row_id


def insert_user(conn: Connection, name: str) -> UUID:
    return _insert(
        conn,
        "INSERT INTO users (email, name) VALUES (:email, :name)",
        email=f"{name}-{uuid.uuid4().hex[:8]}@example.com",
        name=name,
    )


def insert_product(conn: Connection, price: Decimal, currency: str = "USD") -> UUID:
    return _insert(
        conn,
        "INSERT INTO products (name, price, currency) VALUES ('Product', :price, :currency)",
        price=price,
        currency=currency,
    )


def insert_cart(conn: Connection, user_id: UUID, status: str = "active") -> UUID:
    return _insert(
        conn,
        "INSERT INTO carts (user_id, status) VALUES (:user_id, :status)",
        user_id=user_id,
        status=status,
    )


def insert_cart_item(
    conn: Connection, cart_id: UUID, product_id: UUID, quantity: int, unit_price: Decimal
) -> UUID:
    return _insert(
        conn,
        "INSERT INTO cart_items (cart_id, product_id, quantity, unit_price)"
        " VALUES (:cart_id, :product_id, :quantity, :unit_price)",
        cart_id=cart_id,
        product_id=product_id,
        quantity=quantity,
        unit_price=unit_price,
    )


def insert_payment_method(
    conn: Connection, user_id: UUID, token: str = "tok_test_visa", is_default: bool = True
) -> UUID:
    return _insert(
        conn,
        "INSERT INTO user_payment_methods (user_id, provider_token, last_four, is_default)"
        " VALUES (:user_id, :token, '4242', :is_default)",
        user_id=user_id,
        token=token,
        is_default=is_default,
    )


def insert_payment(
    conn: Connection,
    cart: CartFixture,
    *,
    status: str = "pending",
    idempotency_key: str | None = None,
    amount: Decimal = Decimal("70.00"),
    provider_payment_id: str | None = None,
    failure_code: str | None = None,
) -> UUID:
    return _insert(
        conn,
        "INSERT INTO payments (cart_id, user_id, payment_method_id, amount, currency, status,"
        " idempotency_key, request_fingerprint, provider_payment_id, failure_code)"
        " VALUES (:cart_id, :user_id, :payment_method_id, :amount, 'USD', :status,"
        " :idempotency_key, 'fingerprint', :provider_payment_id, :failure_code)",
        cart_id=cart.cart_id,
        user_id=cart.user_id,
        payment_method_id=cart.payment_method_id,
        amount=amount,
        status=status,
        idempotency_key=uuid.uuid4().hex if idempotency_key is None else idempotency_key,
        provider_payment_id=provider_payment_id,
        failure_code=failure_code,
    )


def create_cart_fixture(engine: Engine, name: str) -> CartFixture:
    with engine.begin() as conn:
        user_id = insert_user(conn, name)
        cart_id = insert_cart(conn, user_id)
        kettle = insert_product(conn, Decimal("45.00"))
        mug = insert_product(conn, Decimal("12.50"))
        insert_cart_item(conn, cart_id, kettle, 1, Decimal("45.00"))
        insert_cart_item(conn, cart_id, mug, 2, Decimal("12.50"))
        payment_method_id = insert_payment_method(conn, user_id)
    return CartFixture(user_id, cart_id, payment_method_id)


def fetch_value(engine: Engine, sql: str, **params: object) -> object:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def fetch_row(engine: Engine, sql: str, **params: object) -> dict[str, object]:
    with engine.connect() as conn:
        return dict(conn.execute(text(sql), params).mappings().one())
