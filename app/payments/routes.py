from typing import Any
from uuid import UUID

from flask import Blueprint, current_app, g, request

from app.auth import require_authenticated_user
from app.payments.schemas import (
    http_status,
    parse_payment_request,
    read_body,
    serialize_payment,
)
from app.payments.service import PaymentService

payments = Blueprint("payments", __name__)
# Per blueprint, not per app: Flask routes after before_request hooks, so an app-wide hook
# would answer 401 instead of 404 for unknown URLs.
payments.before_request(require_authenticated_user)


@payments.post("/carts/<cart_id>/payments")
def create_payment(cart_id: str) -> tuple[dict[str, Any], int, dict[str, str]]:
    service: PaymentService = current_app.extensions["payment_service"]
    user_id: UUID = g.user_id  # set by require_authenticated_user (NFR-9)
    raw_body = read_body(request)
    payment_request = parse_payment_request(user_id, cart_id, request.headers, raw_body)
    result = service.pay_cart(payment_request)
    headers = {"Idempotent-Replayed": "true"} if result.replayed else {}
    return serialize_payment(result.payment), http_status(result.payment), headers
