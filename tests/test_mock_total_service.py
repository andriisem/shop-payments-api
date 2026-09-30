"""The local mock of the shop's existing total service (A-3).

It answers the way that service would, so local runs charge realistic amounts. The payment
code never calculates: it only calls get_total(). Endpoint tests use FakeTotalService instead.
"""

from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from flask import Flask
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from app import create_app, create_local_app
from app.config import Config
from app.external import CartTotal, ChargeResult, MockPaymentProvider, MockTotalService
from tests.factories import (
    CartFixture,
    fetch_value,
    insert_cart,
    insert_cart_item,
    insert_product,
)
from tests.fakes import RecordingProvider
from tests.helpers import TEST_DATABASE_URL, make_test_config, pay
from tests.tokens import JWT_AUDIENCE, JWT_ISSUER, JWT_SECRET


def cart_with(engine: Engine, owner: CartFixture, *items: tuple[int, str, str]) -> UUID:
    """A new cart for the owner; each item is (quantity, unit price, currency)."""
    with engine.begin() as conn:
        cart_id = insert_cart(conn, owner.user_id)
        for quantity, price, currency in items:
            product = insert_product(conn, Decimal(price), currency=currency)
            insert_cart_item(conn, cart_id, product, quantity, Decimal(price))
    return cart_id


@pytest.fixture
def mock(engine: Engine) -> MockTotalService:
    return MockTotalService(sessionmaker(engine))


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ([(1, "45.00", "USD")], CartTotal(Decimal("45.00"), "USD")),
        ([(3, "89.99", "USD")], CartTotal(Decimal("269.97"), "USD")),
        ([(1, "45.00", "USD"), (2, "12.50", "USD")], CartTotal(Decimal("70.00"), "USD")),
        ([(2, "10.00", "EUR")], CartTotal(Decimal("20.00"), "EUR")),
        ([(1, "0.00", "USD")], CartTotal(Decimal("0.00"), "USD")),  # the service rejects it
    ],
    ids=["one-item", "quantity", "sample-cart", "euro", "zero"],
)
def test_mock_answers_like_the_existing_service(
    engine: Engine,
    alice: CartFixture,
    mock: MockTotalService,
    items: list[tuple[int, str, str]],
    expected: CartTotal,
) -> None:
    assert mock.get_total(cart_with(engine, alice, *items)) == expected


def test_mock_cannot_total_a_cart_in_two_currencies(
    engine: Engine, alice: CartFixture, mock: MockTotalService
) -> None:
    cart_id = cart_with(engine, alice, (1, "45.00", "USD"), (1, "10.00", "EUR"))

    with pytest.raises(ValueError, match="currencies"):
        mock.get_total(cart_id)


@pytest.fixture
def local_app(engine: Engine, provider: RecordingProvider) -> Iterator[Flask]:
    """The app with the mock total service, reading through the test engine."""
    total_service = MockTotalService(sessionmaker(engine))
    app = create_app(make_test_config(), provider=provider, total_service=total_service)
    yield app
    app.extensions["engine"].dispose()


@pytest.mark.parametrize(
    ("items", "amount", "currency", "minor_units"),
    [
        ([(1, "45.00", "USD")], "45.00", "USD", 4500),
        ([(3, "89.99", "USD")], "269.97", "USD", 26997),
        ([(2, "10.00", "EUR")], "20.00", "EUR", 2000),
    ],
    ids=["one-item", "quantity", "euro"],
)
def test_local_app_charges_what_the_mock_answers(
    engine: Engine,
    alice: CartFixture,
    local_app: Flask,
    provider: RecordingProvider,
    items: list[tuple[int, str, str]],
    amount: str,
    currency: str,
    minor_units: int,
) -> None:
    cart = CartFixture(alice.user_id, cart_with(engine, alice, *items), alice.payment_method_id)

    response = pay(local_app.test_client(), cart)

    assert response.status_code == 201
    assert (response.get_json()["amount"], response.get_json()["currency"]) == (amount, currency)
    assert (provider.calls[0].amount_minor, provider.calls[0].currency) == (minor_units, currency)


def test_local_app_rejects_a_zero_total(
    engine: Engine, alice: CartFixture, local_app: Flask, provider: RecordingProvider
) -> None:
    cart = CartFixture(
        alice.user_id, cart_with(engine, alice, (1, "0.00", "USD")), alice.payment_method_id
    )

    response = pay(local_app.test_client(), cart)

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_amount"
    assert provider.calls == []


def test_create_local_app_uses_the_mocks_and_the_environment(
    engine: Engine, alice: CartFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # What `flask --app app:create_local_app run` does: settings from the environment,
    # the mock provider (Alice's card succeeds) and the mock total service.
    for name, value in {
        "DATABASE_URL": TEST_DATABASE_URL,
        "JWT_SECRET": JWT_SECRET,
        "JWT_ISSUER": JWT_ISSUER,
        "JWT_AUDIENCE": JWT_AUDIENCE,
    }.items():
        monkeypatch.setenv(name, value)
    cart = CartFixture(
        alice.user_id, cart_with(engine, alice, (3, "89.99", "USD")), alice.payment_method_id
    )

    # AC-17 for the real local wiring: when the provider is called, none of the app's
    # connections (the payment pool or the mock's own pool) is inside a transaction.
    open_transactions: list[object] = []
    real_charge = MockPaymentProvider.charge

    def charge_and_inspect(self: MockPaymentProvider, **kwargs: Any) -> ChargeResult:
        open_transactions.append(
            fetch_value(
                engine,
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                " AND application_name = :app AND state LIKE 'idle in transaction%'",
                app=Config().application_name,
            )
        )
        return real_charge(self, **kwargs)

    monkeypatch.setattr(MockPaymentProvider, "charge", charge_and_inspect)

    app = create_local_app()
    try:
        response = pay(app.test_client(), cart)
    finally:
        app.extensions["engine"].dispose()
        app.extensions["total_service_engine"].dispose()

    assert response.status_code == 201
    assert response.get_json()["amount"] == "269.97"
    assert open_transactions == [0]
