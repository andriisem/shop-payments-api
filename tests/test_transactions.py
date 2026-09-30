"""The transaction guarantees around the provider call (FR-9, NFR-3, EC-10).

The tests act *during* the provider call, through the fake provider's hook, and look at the
database from a separate connection, the way another request would see it.
"""

import logging

import pytest
from flask.testing import FlaskClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from app.config import Config
from app.external import ChargeResult
from tests.factories import CartFixture
from tests.fakes import ChargeCall, RecordingProvider
from tests.helpers import cart_status, pay, stored_payment


def test_ac17_payment_is_committed_and_nothing_is_locked_during_the_provider_call(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    seen: dict[str, object] = {}

    def inspect_the_database(call: ChargeCall) -> ChargeResult | None:
        with engine.begin() as conn:
            # FR-9: the pending payment was committed before the provider was called.
            seen["status"] = conn.execute(
                text("SELECT status FROM payments WHERE id = :id"),
                {"id": call.idempotency_key},
            ).scalar_one_or_none()
        # NFR-3: the cart is not locked. NOWAIT fails at once instead of waiting if it were;
        # recorded, not raised, because the service would turn a raise into a 202.
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("SELECT id FROM carts WHERE id = :id FOR UPDATE NOWAIT"),
                    {"id": alice.cart_id},
                )
            seen["cart_locked"] = False
        except OperationalError:
            seen["cart_locked"] = True
        # NFR-3: none of the app's connections sits inside an open transaction. Counting
        # them too proves the filter really sees the app (it would match nothing otherwise).
        with engine.connect() as conn:
            seen["app_connections"], seen["open_transactions"] = conn.execute(
                text(
                    "SELECT count(*), count(*) FILTER (WHERE state LIKE 'idle in transaction%')"
                    " FROM pg_stat_activity"
                    " WHERE datname = current_database() AND application_name = :app"
                ),
                {"app": Config().application_name},
            ).one()
        return None

    provider.on_charge = inspect_the_database

    response = pay(client, alice)

    assert seen["status"] == "pending"
    assert seen["cart_locked"] is False
    assert seen["app_connections"] >= 1  # type: ignore[operator]
    assert seen["open_transactions"] == 0
    assert response.status_code == 201


@pytest.mark.usefixtures("break_tx2")
def test_ac18_db_error_in_tx2_keeps_the_payment_pending(
    client: FlaskClient,
    engine: Engine,
    alice: CartFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR):
        response = pay(client, alice)

    assert response.status_code == 202  # the card was charged, but we could not record it
    payment = response.get_json()
    assert payment["status"] == "pending"
    assert stored_payment(engine, payment["id"])["status"] == "pending"
    assert cart_status(engine, alice) == "active"  # still blocked by the live payment
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [getattr(r, "payment_id", None) for r in errors] == [payment["id"]]
    message = errors[0].getMessage()
    assert "mock_ch_" in message  # the provider result, for reconciliation
    # NFR-7: the ids are in the text itself, so they show with any log format.
    assert f"payment_id={payment['id']}" in message
    assert f"cart_id={alice.cart_id}" in message
    assert f"user_id={alice.user_id}" in message


@pytest.mark.usefixtures("break_tx2")
def test_ec10_after_a_tx2_failure_the_cart_stays_blocked_and_nothing_is_charged_again(
    client: FlaskClient, alice: CartFixture, provider: RecordingProvider
) -> None:
    first = pay(client, alice, key="key-1")
    retry = pay(client, alice, key="key-1")
    other_key = pay(client, alice, key="key-2")

    assert first.status_code == retry.status_code == 202
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert other_key.status_code == 409
    assert other_key.get_json()["error"]["code"] == "payment_in_progress"
    assert other_key.get_json()["error"]["payment_id"] == first.get_json()["id"]
    assert len(provider.calls) == 1
