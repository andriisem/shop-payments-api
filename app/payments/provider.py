import uuid
from dataclasses import dataclass
from typing import Literal, Protocol


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
