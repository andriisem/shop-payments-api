"""ORM mappings. Base-schema tables map only the columns this service uses."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    currency: Mapped[str]


class Cart(Base):
    __tablename__ = "carts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID]
    status: Mapped[str]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now())


class CartItem(Base):
    __tablename__ = "cart_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    cart_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("carts.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class UserPaymentMethod(Base):
    __tablename__ = "user_payment_methods"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID]
    provider_token: Mapped[str]
    is_default: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Payment(Base):
    __tablename__ = "payments"
    # Fetch server defaults (created_at, updated_at) with RETURNING on insert, so building a
    # response never needs a second query.
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    cart_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("carts.id"))
    user_id: Mapped[uuid.UUID]
    payment_method_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_payment_methods.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending")
    idempotency_key: Mapped[str]
    request_fingerprint: Mapped[str]
    provider_payment_id: Mapped[str | None]
    failure_code: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
