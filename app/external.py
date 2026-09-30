"""Systems outside the payment part, and their local stand-ins.

The payment provider charges cards; the shop's existing total service says how much a cart
costs. The payment part only calls them: it never charges on its own or calculates a total.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID


@dataclass(frozen=True)
class CartTotal:
    amount: Decimal
    currency: str


class TotalService(Protocol):
    """The shop's existing total service (A-3). Not ours to build."""

    def get_total(self, cart_id: UUID) -> CartTotal: ...


# The base schema's sample cart: 1 x 45.00 + 2 x 12.50 USD.
SAMPLE_CART_TOTAL = CartTotal(Decimal("70.00"), "USD")


class FixedTotalService:
    """Stand-in for the existing total service in local runs: always the same total.

    It never looks at the cart, so there is no calculation here.
    """

    def __init__(self, total: CartTotal = SAMPLE_CART_TOTAL) -> None:
        self._total = total

    def get_total(self, cart_id: UUID) -> CartTotal:
        return self._total


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
