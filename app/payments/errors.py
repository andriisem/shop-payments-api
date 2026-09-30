"""The payment endpoint's domain errors (the base class and the handler are in app.errors)."""

from typing import Any
from uuid import UUID

from app.errors import DomainError


class MissingIdempotencyKeyError(DomainError):
    code, status, message = "missing_idempotency_key", 400, "Idempotency-Key header is required."


class InvalidRequestError(DomainError):
    code, status, message = "invalid_request", 400, "The request is malformed."


class CartNotFoundError(DomainError):
    code, status, message = "cart_not_found", 404, "Cart not found."


class PaymentMethodNotFoundError(DomainError):
    code, status, message = "payment_method_not_found", 404, "Payment method not found."


class CartNotActiveError(DomainError):
    code, status, message = "cart_not_active", 409, "Cart is not active."


class PaymentInProgressError(DomainError):
    code, status, message = "payment_in_progress", 409, "A payment for this cart is in progress."

    def __init__(self, payment_id: UUID) -> None:
        super().__init__()
        self.payment_id = payment_id

    def body(self) -> dict[str, Any]:
        body = super().body()
        body["error"]["payment_id"] = str(self.payment_id)
        return body


class CartEmptyError(DomainError):
    code, status, message = "cart_empty", 422, "Cart has no items."


class NoPaymentMethodError(DomainError):
    code, status, message = "no_payment_method", 422, "No payment method given and no default."


class IdempotencyKeyReusedError(DomainError):
    code, status, message = (
        "idempotency_key_reused",
        422,
        "Idempotency-Key was already used for a different request.",
    )


class InvalidAmountError(DomainError):
    code, status, message = "invalid_amount", 422, "Cart total must be positive, in cents."


class UnsupportedCurrencyError(DomainError):
    code, status, message = "unsupported_currency", 422, "Cart currency is not supported."
