from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CartItem, Product


@dataclass(frozen=True)
class CartTotal:
    amount: Decimal
    currency: str


class CartTotalService(Protocol):
    def calculate(self, session: Session, cart_id: UUID) -> CartTotal: ...


class DefaultCartTotalService:
    """A-3: sum(quantity * unit_price) over the cart items.

    Runs inside TX1 on the caller's session while the cart is locked, so the amount matches
    the locked items. It must stay in-process: a remote call here would be I/O under a lock.
    """

    def calculate(self, session: Session, cart_id: UUID) -> CartTotal:
        rows = session.execute(
            select(Product.currency, func.sum(CartItem.unit_price * CartItem.quantity))
            .join(Product, Product.id == CartItem.product_id)
            .where(CartItem.cart_id == cart_id)
            .group_by(Product.currency)
        ).all()
        if len(rows) != 1:
            raise ValueError(f"Expected items in exactly one currency, got {len(rows)}")
        currency, amount = rows[0]
        return CartTotal(amount=amount, currency=currency)
