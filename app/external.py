"""Systems outside the payment part, and their local stand-ins.

The payment provider charges cards; the shop's existing total service says how much a cart
costs. The payment part only calls them: it never charges on its own or calculates a total.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import CartItem, Product


@dataclass(frozen=True)
class CartTotal:
    """What the total service answers. External input: the caller validates it (EC-9, EC-11)."""

    amount: Decimal
    currency: str


class TotalService(Protocol):
    """The shop's existing total service (A-3). Not ours to build.

    It is called inside TX1 while the cart row is locked and TX1 holds a connection, so an
    implementation must answer fast (a real client needs a short timeout), must not lock the
    cart itself, and must not take its connection from the payment part's pool.
    """

    def get_total(self, cart_id: UUID) -> CartTotal: ...


class MockTotalService:
    """Stand-in for the shop's existing total service in local runs.

    It answers the way that service would, so local payments charge realistic amounts: the
    sum of quantity x unit price over the cart's items, in the products' currency. It reads
    with its own session, like a separate service. The payment part never calculates; it only
    calls get_total().
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_total(self, cart_id: UUID) -> CartTotal:
        # One row per currency: a cart can only be charged in one of them.
        with self._session_factory() as session:
            totals = session.execute(
                select(Product.currency, func.sum(CartItem.unit_price * CartItem.quantity))
                .join(Product, Product.id == CartItem.product_id)
                .where(CartItem.cart_id == cart_id)
                .group_by(Product.currency)
            ).all()
        if len(totals) != 1:
            # Several currencies cannot give one total, and no rows is not expected (the
            # payment part rejects an empty cart before it asks, FR-6). Either way the
            # request ends in a 500, before anything is charged.
            raise ValueError(f"Cart {cart_id} has items in {len(totals)} currencies")
        currency, amount = totals[0]
        return CartTotal(amount, currency)


@dataclass(frozen=True)
class ChargeResult:
    status: Literal["succeeded", "declined"]
    provider_payment_id: str | None = None


class ProviderRejectedError(Exception):
    """The provider definitely did not charge the card."""


class ProviderTimeoutError(Exception):
    """The outcome is unknown: the card may have been charged."""


class PaymentProvider(Protocol):
    def charge(
        self, *, token: str, amount_minor: int, currency: str, idempotency_key: str
    ) -> ChargeResult: ...


class MockPaymentProvider:
    """Deterministic provider for local runs and tests.

    The token decides the result. Like a real provider, a repeated idempotency key returns
    the first result instead of charging again.
    """

    def __init__(self) -> None:
        # Per process only: enough for a mock, a real provider stores this on its side.
        self._results: dict[str, ChargeResult | type[Exception]] = {}

    def charge(
        self, *, token: str, amount_minor: int, currency: str, idempotency_key: str
    ) -> ChargeResult:
        if idempotency_key not in self._results:
            self._results[idempotency_key] = self._decide(token)
        result = self._results[idempotency_key]
        if isinstance(result, ChargeResult):
            return result
        raise result("mock provider failure")

    @staticmethod
    def _decide(token: str) -> ChargeResult | type[Exception]:
        if "decline" in token:
            return ChargeResult("declined")
        if "error" in token:
            return ProviderRejectedError
        if "timeout" in token:
            return ProviderTimeoutError
        return ChargeResult("succeeded", f"mock_ch_{uuid.uuid4().hex}")
