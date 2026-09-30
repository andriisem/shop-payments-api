import json
import re
from typing import Any
from uuid import UUID

from flask import Request

from app.payments.errors import (
    DomainError,
    InvalidRequestError,
    MissingIdempotencyKeyError,
)
from app.payments.service import PaymentRequest, PaymentResult, PaymentView

# FR-2: 1-255 printable ASCII characters.
IDEMPOTENCY_KEY = re.compile(r"[\x20-\x7e]{1,255}")
# EC-7: only the canonical 8-4-4-4-12 form; uuid.UUID() alone also accepts urn:, braces, etc.
CANONICAL_UUID = re.compile(r"[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
# FR-8: the client never sends the amount, so any field but these is rejected.
ALLOWED_FIELDS = {"payment_method_id"}


def parse_payment_request(request: Request, cart_id: str, user_id: UUID) -> PaymentRequest:
    """Validate the request of an already authenticated caller: key first, then body."""
    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        raise MissingIdempotencyKeyError()
    if not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        raise InvalidRequestError()
    body = _parse_body(_read_body(request))
    payment_method_id = body.get("payment_method_id")
    if payment_method_id is not None:
        payment_method_id = _parse_uuid(payment_method_id, InvalidRequestError)
    return PaymentRequest(
        user_id=user_id,
        cart_id=_parse_uuid(cart_id, InvalidRequestError),
        idempotency_key=idempotency_key,
        payment_method_id=payment_method_id,
    )


def _read_body(request: Request) -> bytes:
    """A chunked body is rejected: depending on the WSGI server it can be dropped, and an
    empty body would silently mean "use the default card" instead of the requested one.
    """
    if "Transfer-Encoding" in request.headers:
        raise InvalidRequestError()
    return request.get_data()


def _parse_uuid(value: object, error: type[DomainError]) -> UUID:
    if not isinstance(value, str) or not CANONICAL_UUID.fullmatch(value):
        raise error()
    return UUID(value)


def _parse_body(raw_body: bytes) -> dict[str, Any]:
    """An empty body means "use the default card"; anything else must be a JSON object."""
    if not raw_body.strip():
        return {}
    try:
        body = json.loads(raw_body)
    except (ValueError, RecursionError):  # RecursionError: deeply nested JSON
        raise InvalidRequestError() from None
    if not isinstance(body, dict) or not body.keys() <= ALLOWED_FIELDS:
        raise InvalidRequestError()
    return body


def serialize_payment(payment: PaymentView) -> dict[str, Any]:
    return {
        "id": str(payment.id),
        "cart_id": str(payment.cart_id),
        "payment_method_id": str(payment.payment_method_id),
        "amount": str(payment.amount),  # NFR-1: amounts are strings in JSON
        "currency": payment.currency,
        "status": payment.status,
        "provider_payment_id": payment.provider_payment_id,
        "failure_code": payment.failure_code,
        "created_at": payment.created_at.isoformat(),
        "updated_at": payment.updated_at.isoformat(),
    }


def payment_response(result: PaymentResult) -> tuple[dict[str, Any], int, dict[str, str]]:
    headers = {"Idempotent-Replayed": "true"} if result.replayed else {}  # FR-3
    return serialize_payment(result.payment), http_status(result.payment), headers


def http_status(payment: PaymentView) -> int:
    """The status code follows the payment's current state."""
    if payment.status == "succeeded":
        return 201
    if payment.status == "pending":
        return 202
    return 402 if payment.failure_code == "card_declined" else 502
