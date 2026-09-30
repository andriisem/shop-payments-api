from typing import Any
from uuid import UUID

from flask import Blueprint, current_app, request

from app.payments.schemas import http_status, serialize_payment
from app.payments.service import PaymentRequest, PaymentService

payments = Blueprint("payments", __name__)


@payments.post("/carts/<cart_id>/payments")
def create_payment(cart_id: str) -> tuple[dict[str, Any], int]:
    body = request.get_json(silent=True) or {}
    payment_method_id = body.get("payment_method_id")
    payment_request = PaymentRequest(
        user_id=UUID(request.headers["X-User-Id"]),
        cart_id=UUID(cart_id),
        idempotency_key=request.headers["Idempotency-Key"],
        payment_method_id=UUID(payment_method_id) if payment_method_id else None,
    )
    service: PaymentService = current_app.extensions["payment_service"]
    payment = service.pay_cart(payment_request)
    return serialize_payment(payment), http_status(payment)
