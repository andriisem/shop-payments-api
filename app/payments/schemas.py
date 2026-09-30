from typing import Any

from app.payments.service import PaymentView


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


def http_status(payment: PaymentView) -> int:
    """The status code follows the payment's current state."""
    if payment.status == "succeeded":
        return 201
    if payment.status == "pending":
        return 202
    return 402 if payment.failure_code == "card_declined" else 502
