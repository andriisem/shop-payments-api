from typing import Any

from flask import Blueprint, current_app, request

from app.payments.schemas import (
    http_status,
    parse_payment_request,
    parse_user_id,
    read_body,
    serialize_payment,
)
from app.payments.service import PaymentService

payments = Blueprint("payments", __name__)


@payments.post("/carts/<cart_id>/payments")
def create_payment(cart_id: str) -> tuple[dict[str, Any], int]:
    service: PaymentService = current_app.extensions["payment_service"]
    user_id = parse_user_id(request.headers)
    service.authenticate(user_id)  # 401 before any 400
    raw_body = read_body(request)
    payment_request = parse_payment_request(user_id, cart_id, request.headers, raw_body)
    payment = service.pay_cart(payment_request)
    return serialize_payment(payment), http_status(payment)
