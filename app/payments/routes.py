from typing import cast

from flask import Blueprint, current_app, request
from flask.typing import ResponseReturnValue

from app.auth import current_user_id, require_authenticated_user
from app.payments.schemas import parse_payment_request, payment_response
from app.payments.service import PaymentService

payments = Blueprint("payments", __name__)
# Per blueprint, not per app: Flask routes after before_request hooks, so an app-wide hook
# would answer 401 instead of 404 for unknown URLs.
payments.before_request(require_authenticated_user)


def payment_service() -> PaymentService:
    return cast(PaymentService, current_app.extensions["payment_service"])


@payments.post("/carts/<cart_id>/payments")
def create_payment(cart_id: str) -> ResponseReturnValue:
    payment_request = parse_payment_request(request, cart_id, user_id=current_user_id())
    result = payment_service().pay_cart(payment_request)
    return payment_response(result)
