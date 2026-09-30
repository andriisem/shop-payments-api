"""The payment part's value objects. Plain and frozen, so they are safe to pass around
after their database session is closed (no lazy loading, no hidden transaction)."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.models import Payment


@dataclass(frozen=True)
class PaymentRequest:
    user_id: UUID
    cart_id: UUID
    idempotency_key: str
    payment_method_id: UUID | None


@dataclass(frozen=True)
class PaymentView:
    id: UUID
    cart_id: UUID
    payment_method_id: UUID
    amount: Decimal
    currency: str
    status: str
    provider_payment_id: str | None
    failure_code: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, payment: Payment) -> "PaymentView":
        return cls(
            id=payment.id,
            cart_id=payment.cart_id,
            payment_method_id=payment.payment_method_id,
            amount=payment.amount,
            currency=payment.currency,
            status=payment.status,
            provider_payment_id=payment.provider_payment_id,
            failure_code=payment.failure_code,
            created_at=payment.created_at,
            updated_at=payment.updated_at,
        )


@dataclass(frozen=True)
class PaymentResult:
    payment: PaymentView
    replayed: bool = False  # FR-3: sent back as Idempotent-Replayed: true


@dataclass(frozen=True)
class StoredPayment:
    """A payment found by its idempotency key, with the fingerprint of its request."""

    payment: PaymentView
    fingerprint: str


@dataclass(frozen=True)
class ChargeAttempt:
    """Everything the provider call and TX2 need, captured before TX1 commits."""

    payment: PaymentView
    token: str = field(repr=False)  # NFR-4: never shows up in logs or tracebacks
    amount_minor: int
    user_id: UUID


@dataclass(frozen=True)
class Succeeded:
    provider_payment_id: str


@dataclass(frozen=True)
class Failed:
    """The provider confirmed that no charge happened."""

    failure_code: str


@dataclass(frozen=True)
class Unknown:
    """The card may or may not have been charged: the payment must stay pending (FR-13)."""


Outcome = Succeeded | Failed | Unknown
