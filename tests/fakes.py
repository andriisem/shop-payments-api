from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from app.external import CartTotal, ChargeResult, MockPaymentProvider


@dataclass(frozen=True)
class ChargeCall:
    token: str = field(repr=False)
    amount_minor: int
    currency: str
    idempotency_key: str


class RecordingProvider:
    """Wraps the mock provider: records every call and runs an optional hook inside charge().

    The hook lets a test act *during* the provider call, e.g. change the DB from another
    connection or raise an unexpected error. If the hook returns a result, the provider
    returns it instead of asking the mock.
    """

    def __init__(self) -> None:
        self.calls: list[ChargeCall] = []
        self.on_charge: Callable[[ChargeCall], ChargeResult | None] | None = None
        self._mock = MockPaymentProvider()

    def charge(
        self, *, token: str, amount_minor: int, currency: str, idempotency_key: str
    ) -> ChargeResult:
        call = ChargeCall(token, amount_minor, currency, idempotency_key)
        self.calls.append(call)
        if self.on_charge is not None and (result := self.on_charge(call)) is not None:
            return result
        return self._mock.charge(
            token=token,
            amount_minor=amount_minor,
            currency=currency,
            idempotency_key=idempotency_key,
        )


class FakeTotalService:
    """Stands in for the shop's existing total service: returns whatever the test sets."""

    def __init__(self) -> None:
        self.total = CartTotal(Decimal("70.00"), "USD")  # Alice's cart, as in AC-1
        self.calls: list[UUID] = []

    def get_total(self, cart_id: UUID) -> CartTotal:
        self.calls.append(cart_id)
        return self.total
