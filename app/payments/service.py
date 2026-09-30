"""The payment flow: TX1 (reserve) -> provider call with no transaction open -> TX2 (finalize).

ORM objects never leave this module. Each step returns plain frozen dataclasses, so nothing
can lazy-load (and silently open a transaction) after its session is closed.
"""

import hashlib
import logging
from collections.abc import MutableMapping
from typing import Any
from uuid import UUID

import psycopg
from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.external import (
    CartTotal,
    PaymentProvider,
    ProviderRejectedError,
    ProviderTimeoutError,
    TotalService,
)
from app.models import (
    Cart,
    CartItem,
    CartStatus,
    FailureCode,
    Payment,
    PaymentStatus,
    UserPaymentMethod,
)
from app.payments.domain import (
    ChargeAttempt,
    Failed,
    Outcome,
    PaymentRequest,
    PaymentResult,
    PaymentView,
    StoredPayment,
    Succeeded,
    Unknown,
)
from app.payments.errors import (
    CartEmptyError,
    CartNotActiveError,
    CartNotFoundError,
    IdempotencyKeyReusedError,
    InvalidAmountError,
    NoPaymentMethodError,
    PaymentInProgressError,
    PaymentMethodNotFoundError,
    UnsupportedCurrencyError,
)
from app.payments.money import (
    is_chargeable_amount,
    is_supported_currency,
    to_cents,
    to_minor_units,
)

logger = logging.getLogger(__name__)

LIVE_STATUSES = (PaymentStatus.PENDING, PaymentStatus.SUCCEEDED)

# Unique constraints from migrations/002_payments.sql, used to route races (EC-1, EC-2).
UNIQUE_IDEMPOTENCY_KEY = "uq_payments_user_idempotency_key"
UNIQUE_LIVE_PAYMENT_PER_CART = "uq_payments_cart_live"


def request_fingerprint(request: PaymentRequest) -> str:
    """FR-4: identifies the request as sent, not the resolved payment method."""
    payment_method = str(request.payment_method_id or "")
    return hashlib.sha256(f"{request.cart_id}:{payment_method}".encode()).hexdigest()


def finalize_payment(
    session: Session, payment_id: UUID, outcome: Succeeded | Failed
) -> Payment | None:
    """FR-14: move a pending payment to its final state. Returns None if it was not pending."""
    if isinstance(outcome, Succeeded):
        values = {
            "status": PaymentStatus.SUCCEEDED,
            "provider_payment_id": outcome.provider_payment_id,
        }
    else:
        values = {"status": PaymentStatus.FAILED, "failure_code": outcome.failure_code}
    return session.scalars(
        update(Payment)
        .where(Payment.id == payment_id, Payment.status == PaymentStatus.PENDING)
        .values(**values)
        .returning(Payment)
    ).one_or_none()


class PaymentLogAdapter(logging.LoggerAdapter[logging.Logger]):
    """NFR-7: every log line about a payment carries its payment, cart and user ids.

    The ids are written into the message text, so they show up whatever the log format is,
    and an operator can see which payment to reconcile. They also stay on the log record as
    fields, for structured log handlers.
    """

    # Same signature as LoggerAdapter.process in the standard library's type stubs.
    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        msg, kwargs = super().process(msg, kwargs)
        ids = " ".join(f"{name}={value}" for name, value in (self.extra or {}).items())
        return f"[{ids}] {msg}", kwargs


def payment_log(attempt: ChargeAttempt) -> PaymentLogAdapter:
    # Every entry is printed into every payment log line: ids only. Never add the token,
    # the amount or anything else a log reader should not see (NFR-4).
    context = {
        "payment_id": str(attempt.payment.id),
        "cart_id": str(attempt.payment.cart_id),
        "user_id": str(attempt.user_id),
    }
    return PaymentLogAdapter(logger, context)


class PaymentService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        provider: PaymentProvider,
        total_service: TotalService,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._total_service = total_service

    def pay_cart(self, request: PaymentRequest) -> PaymentResult:
        with self._session_factory() as session:
            stored = self._find_by_key(session, request)
        if stored is not None:
            return self._replay(stored, request)

        try:
            reserved = self._reserve(request)
        except IntegrityError as error:
            return self._on_conflict(error, request)
        if isinstance(reserved, StoredPayment):
            return self._replay(reserved, request)
        return self._charge_and_finalize(reserved)

    def _charge_and_finalize(self, attempt: ChargeAttempt) -> PaymentResult:
        """The provider call, then TX2, for the payment that TX1 committed as pending."""
        log = payment_log(attempt)
        outcome = self._charge(attempt, log)
        if isinstance(outcome, Unknown):
            return PaymentResult(attempt.payment)  # still pending, as committed in TX1
        try:
            return PaymentResult(self._finalize(attempt, outcome, log))
        except Exception:
            # EC-10: the provider answered but TX2 failed. The payment is almost always still
            # pending (TX2 rolled back); only if the connection dropped during COMMIT may it
            # already be final, and a same-key replay then reports the true state. Answer 202,
            # never a 500, and log the provider result for reconciliation. The DB may be down,
            # so nothing is re-read.
            log.exception("Provider result %s was not recorded; answering pending", outcome)
            return PaymentResult(attempt.payment)

    @staticmethod
    def _find_by_key(session: Session, request: PaymentRequest) -> StoredPayment | None:
        payment = session.scalars(
            select(Payment).where(
                Payment.user_id == request.user_id,
                Payment.idempotency_key == request.idempotency_key,
            )
        ).one_or_none()
        if payment is None:
            return None
        return StoredPayment(PaymentView.from_model(payment), payment.request_fingerprint)

    @staticmethod
    def _replay(stored: StoredPayment, request: PaymentRequest) -> PaymentResult:
        """FR-3: the current state, no provider call. FR-4: only for the same request."""
        if stored.fingerprint != request_fingerprint(request):
            raise IdempotencyKeyReusedError()
        return PaymentResult(stored.payment, replayed=True)

    def _on_conflict(self, error: IntegrityError, request: PaymentRequest) -> PaymentResult:
        """Backstops for races that got past the checks in TX1 (EC-1, EC-2)."""
        constraint = (
            error.orig.diag.constraint_name if isinstance(error.orig, psycopg.Error) else None
        )
        with self._session_factory() as session:
            if constraint == UNIQUE_IDEMPOTENCY_KEY:
                stored = self._find_by_key(session, request)
                if stored is not None:
                    return self._replay(stored, request)
            elif constraint == UNIQUE_LIVE_PAYMENT_PER_CART:
                self._raise_if_live_payment(session, request)
        raise error

    def _reserve(self, request: PaymentRequest) -> ChargeAttempt | StoredPayment:
        """TX1: lock the cart, create the pending payment and commit it (FR-9).

        Any domain error rolls TX1 back, so a rejected request leaves nothing behind.
        """
        with self._session_factory.begin() as session:
            cart = self._lock_cart(session, request)
            # EC-2: a request with the same key may have committed while we waited for the
            # lock. Checked before the cart rules: that payment may have checked the cart out.
            stored = self._find_by_key(session, request)
            if stored is not None:
                return stored
            self._check_payable(session, cart, request)
            payment_method = self._resolve_payment_method(session, request)
            total = self._get_total(cart)
            amount_minor = to_minor_units(total.amount)
            payment = Payment(
                cart_id=cart.id,
                user_id=request.user_id,
                payment_method_id=payment_method.id,
                amount=to_cents(total.amount),  # "70" and "70.00" serialize the same
                currency=total.currency,
                idempotency_key=request.idempotency_key,
                request_fingerprint=request_fingerprint(request),
            )
            session.add(payment)
            session.flush()
            return ChargeAttempt(
                payment=PaymentView.from_model(payment),
                token=payment_method.provider_token,
                amount_minor=amount_minor,
                user_id=request.user_id,
            )

    @staticmethod
    def _lock_cart(session: Session, request: PaymentRequest) -> Cart:
        """FR-5. The row lock serializes concurrent payments for one cart (NFR-2)."""
        cart = session.scalars(
            select(Cart)
            .where(Cart.id == request.cart_id, Cart.user_id == request.user_id)
            .with_for_update()
        ).one_or_none()
        if cart is None:
            raise CartNotFoundError()
        return cart

    def _check_payable(self, session: Session, cart: Cart, request: PaymentRequest) -> None:
        """FR-6: active, has items, no live payment."""
        if cart.status != CartStatus.ACTIVE:
            raise CartNotActiveError()
        if not session.scalar(select(exists().where(CartItem.cart_id == cart.id))):
            raise CartEmptyError()
        self._raise_if_live_payment(session, request)

    @staticmethod
    def _raise_if_live_payment(session: Session, request: PaymentRequest) -> None:
        live_payment_id = session.scalar(
            select(Payment.id).where(
                Payment.cart_id == request.cart_id,
                Payment.user_id == request.user_id,
                Payment.status.in_(LIVE_STATUSES),
            )
        )
        if live_payment_id is not None:
            raise PaymentInProgressError(live_payment_id)

    @staticmethod
    def _resolve_payment_method(session: Session, request: PaymentRequest) -> UserPaymentMethod:
        """FR-7: the caller's own card, or their default (A-5)."""
        query = select(UserPaymentMethod).where(UserPaymentMethod.user_id == request.user_id)
        if request.payment_method_id is not None:
            payment_method = session.scalars(
                query.where(UserPaymentMethod.id == request.payment_method_id)
            ).one_or_none()
            if payment_method is None:
                raise PaymentMethodNotFoundError()
            return payment_method

        # A-5: several defaults are possible; the most recently created one wins.
        default = session.scalars(
            query.where(UserPaymentMethod.is_default)
            .order_by(UserPaymentMethod.created_at.desc(), UserPaymentMethod.id.desc())
            .limit(1)
        ).one_or_none()
        if default is None:
            raise NoPaymentMethodError()
        return default

    def _get_total(self, cart: Cart) -> CartTotal:
        """FR-8: ask the existing total service (A-3), under the cart lock. Never calculate.

        Its answer is external input: only a finite, positive amount in whole cents that
        fits NUMERIC(12,2) (EC-9), in a supported 2-decimal currency (EC-11), is charged.
        """
        total = self._total_service.get_total(cart.id)
        if not is_chargeable_amount(total.amount):
            raise InvalidAmountError()
        if not is_supported_currency(total.currency):
            raise UnsupportedCurrencyError()
        return total

    def _charge(self, attempt: ChargeAttempt, log: PaymentLogAdapter) -> Outcome:
        """Call the provider with no session open (NFR-3). Only a confirmed no-charge is Failed."""
        try:
            result = self._provider.charge(
                token=attempt.token,
                amount_minor=attempt.amount_minor,
                currency=attempt.payment.currency,
                idempotency_key=str(attempt.payment.id),  # FR-10
            )
        except ProviderRejectedError:
            log.warning("Provider rejected the charge")
            return Failed(FailureCode.PROVIDER_ERROR)
        except ProviderTimeoutError:
            log.warning("Provider timed out; payment stays pending")
            return Unknown()
        except Exception as error:
            # The message may contain provider details, so log only the error type (NFR-4).
            log.error("Provider call failed with %s; payment stays pending", type(error).__name__)
            return Unknown()

        # result.status is the provider's own vocabulary, not our PaymentStatus.
        if result.status == "succeeded" and result.provider_payment_id:
            return Succeeded(result.provider_payment_id)
        if result.status == "declined":
            return Failed(FailureCode.CARD_DECLINED)
        # Anything else (a new provider status, success without an id) is not a confirmed
        # outcome, so it must not free the cart or check it out.
        log.error("Provider returned an unconfirmed result; payment stays pending")
        return Unknown()

    def _finalize(
        self,
        attempt: ChargeAttempt,
        outcome: Succeeded | Failed,
        log: PaymentLogAdapter,
    ) -> PaymentView:
        """TX2: record the provider's answer. Lock order matches TX1: cart, then payment."""
        payment_id = attempt.payment.id
        # The cart was already scoped to the user in TX1; the filter keeps every cart query
        # scoped the same way (NFR-5).
        owned_cart = (Cart.id == attempt.payment.cart_id, Cart.user_id == attempt.user_id)
        with self._session_factory.begin() as session:
            if isinstance(outcome, Succeeded):
                session.execute(select(Cart.id).where(*owned_cart).with_for_update())

            payment = finalize_payment(session, payment_id, outcome)
            if payment is None:
                log.warning("Payment was no longer pending; returning its current state")
                return PaymentView.from_model(session.get_one(Payment, payment_id))

            if isinstance(outcome, Succeeded):
                checked_out = session.scalars(
                    update(Cart)
                    .where(*owned_cart, Cart.status == CartStatus.ACTIVE)
                    .values(status=CartStatus.CHECKED_OUT)
                    .returning(Cart.id)
                ).one_or_none()
                if checked_out is None:
                    # EC-5: never overwrite a cart that another service already moved on.
                    log.error("Cart was not active when its payment succeeded; needs review")
            return PaymentView.from_model(payment)
