"""Domain errors. Each carries its API error code and HTTP status; one handler renders them."""

import logging
import re
from typing import Any, ClassVar
from uuid import UUID

from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)


class DomainError(Exception):
    code: ClassVar[str]
    status: ClassVar[int]
    message: ClassVar[str]

    def __init__(self) -> None:
        super().__init__(self.message)

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


class UnauthenticatedError(DomainError):
    code, status, message = "unauthenticated", 401, "Unknown or missing X-User-Id."


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


class MixedCurrenciesError(DomainError):
    code, status, message = "mixed_currencies", 422, "Cart items have different currencies."


class InvalidAmountError(DomainError):
    code, status, message = "invalid_amount", 422, "Cart total must be greater than zero."


def handle_domain_error(error: DomainError) -> tuple[dict[str, Any], int]:
    return error.body(), error.status


def handle_http_error(
    error: HTTPException,
) -> tuple[dict[str, Any], int, list[tuple[str, str]]]:
    """Framework errors (404, 405, 411, 413) in the same shape: "Not Found" -> not_found.

    Keeps the error's own headers (e.g. Allow on a 405) except its HTML content type.
    """
    name = error.name or "Error"
    code = re.sub(r"\W+", "_", name.lower())
    headers = [(k, v) for k, v in error.get_headers() if k.lower() != "content-type"]
    return {"error": {"code": code, "message": f"{name}."}}, error.code or 500, headers


def handle_unexpected_error(error: Exception) -> tuple[dict[str, Any], int]:
    """Never expose internal details. Payment-path errors never carry the card token."""
    logger.exception("Unhandled error")
    return {"error": {"code": "internal_error", "message": "Internal server error."}}, 500
