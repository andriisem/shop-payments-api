"""Domain errors. Each carries its API error code and HTTP status; one handler renders them."""

from typing import Any, ClassVar
from uuid import UUID


class DomainError(Exception):
    code: ClassVar[str]
    status: ClassVar[int]
    message: ClassVar[str]

    def __init__(self) -> None:
        super().__init__(self.message)

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}

    def headers(self) -> dict[str, str]:
        return {}


class UnauthenticatedError(DomainError):
    code, status, message = "unauthenticated", 401, "Missing or invalid access token."

    def headers(self) -> dict[str, str]:
        return {"WWW-Authenticate": 'Bearer realm="shop-payments-api"'}


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


def handle_domain_error(error: DomainError) -> tuple[dict[str, Any], int, dict[str, str]]:
    return error.body(), error.status, error.headers()
