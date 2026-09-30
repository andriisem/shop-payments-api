"""The payments table's constraints are the last line of defence, so test them directly.

The constraint names are asserted because the service maps IntegrityError by constraint name
(EC-1, EC-2). Renaming one without updating the service would turn a backstop into a 500.
"""

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from tests.factories import CartFixture, insert_payment


def _violated_constraint(error: pytest.ExceptionInfo[IntegrityError]) -> str | None:
    constraint: str | None = error.value.orig.diag.constraint_name  # type: ignore[union-attr]
    return constraint


@pytest.mark.parametrize(
    "live_payment",
    [{"status": "pending"}, {"status": "succeeded", "provider_payment_id": "ch_1"}],
)
def test_nfr2_cart_can_have_only_one_live_payment(
    engine: Engine, alice: CartFixture, live_payment: dict[str, Any]
) -> None:
    with engine.begin() as conn:
        insert_payment(conn, alice, **live_payment)

    with pytest.raises(IntegrityError) as error, engine.begin() as conn:
        insert_payment(conn, alice)

    assert _violated_constraint(error) == "uq_payments_cart_live"


def test_fr12_failed_payments_do_not_block_a_new_attempt(
    engine: Engine, alice: CartFixture
) -> None:
    with engine.begin() as conn:
        insert_payment(conn, alice, status="failed", failure_code="card_declined")
        insert_payment(conn, alice, status="failed", failure_code="provider_error")
        insert_payment(conn, alice)


def test_nfr2_failed_payment_can_coexist_with_a_live_one(
    engine: Engine, alice: CartFixture
) -> None:
    with engine.begin() as conn:
        insert_payment(conn, alice)
        insert_payment(conn, alice, status="failed", failure_code="card_declined")


def test_fr2_idempotency_key_is_unique_per_user(
    engine: Engine, alice: CartFixture, bob: CartFixture
) -> None:
    with engine.begin() as conn:
        insert_payment(
            conn, alice, status="failed", failure_code="card_declined", idempotency_key="k"
        )
        insert_payment(conn, bob, idempotency_key="k")

    with pytest.raises(IntegrityError) as error, engine.begin() as conn:
        insert_payment(conn, alice, idempotency_key="k")

    assert _violated_constraint(error) == "uq_payments_user_idempotency_key"


def test_provider_payment_id_is_unique(
    engine: Engine, alice: CartFixture, bob: CartFixture
) -> None:
    with engine.begin() as conn:
        insert_payment(conn, alice, status="succeeded", provider_payment_id="ch_1")

    with pytest.raises(IntegrityError) as error, engine.begin() as conn:
        insert_payment(conn, bob, status="succeeded", provider_payment_id="ch_1")

    assert _violated_constraint(error) == "payments_provider_payment_id_key"


@pytest.mark.parametrize(
    ("payment", "constraint"),
    [
        ({"status": "succeeded"}, "chk_payments_succeeded_has_provider_id"),
        ({"status": "failed"}, "chk_payments_failed_has_code"),
        ({"status": "refunded"}, "payments_status_check"),
        ({"amount": Decimal("0.00")}, "payments_amount_check"),  # EC-9 backstop
        ({"idempotency_key": ""}, "payments_idempotency_key_check"),  # FR-2
        ({"idempotency_key": "k" * 256}, "payments_idempotency_key_check"),  # FR-2
    ],
    ids=[
        "succeeded-needs-provider-id",
        "failed-needs-code",
        "unknown-status",
        "ec9-zero-amount",
        "fr2-empty-key",
        "fr2-key-too-long",
    ],
)
def test_payment_state_invariants(
    engine: Engine, alice: CartFixture, payment: dict[str, Any], constraint: str
) -> None:
    with pytest.raises(IntegrityError) as error, engine.begin() as conn:
        insert_payment(conn, alice, **payment)

    assert _violated_constraint(error) == constraint
