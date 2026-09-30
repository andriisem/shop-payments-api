import logging

import pytest
from flask.testing import FlaskClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from app.external import ChargeResult
from app.payments.domain import Failed
from app.payments.service import finalise_payment
from tests.factories import CartFixture, insert_payment
from tests.fakes import ChargeCall, RecordingProvider
from tests.helpers import add_card, cart_payment_statuses, cart_status, pay, stored_payment


def test_ac1_pays_active_cart_with_default_card(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    response = pay(client, alice)

    assert response.status_code == 201
    payment = response.get_json()
    assert payment["status"] == "succeeded"
    assert payment["amount"] == "70.00"
    assert payment["currency"] == "USD"
    assert payment["cart_id"] == str(alice.cart_id)
    assert payment["payment_method_id"] == str(alice.payment_method_id)
    assert payment["provider_payment_id"].startswith("mock_ch_")
    assert payment["failure_code"] is None
    assert stored_payment(engine, payment["id"]) == {
        "status": "succeeded",
        "failure_code": None,
        "provider_payment_id": payment["provider_payment_id"],
    }
    assert cart_status(engine, alice) == "checked_out"
    # FR-10: the provider's idempotency key is the payment id. NFR-1: minor units.
    assert provider.calls == [ChargeCall("tok_test_visa", 7000, "USD", payment["id"])]


def test_ac10_declined_card_fails_and_cart_stays_active(
    client: FlaskClient, engine: Engine, alice: CartFixture
) -> None:
    declining_card = add_card(engine, alice, "tok_decline")

    response = pay(client, alice, payment_method_id=declining_card)

    assert response.status_code == 402
    payment = response.get_json()
    assert payment["status"] == "failed"
    assert payment["failure_code"] == "card_declined"
    assert payment["provider_payment_id"] is None
    assert stored_payment(engine, payment["id"]) == {
        "status": "failed",
        "failure_code": "card_declined",
        "provider_payment_id": None,
    }
    assert cart_status(engine, alice) == "active"


def test_ac11_retry_with_new_key_after_decline_succeeds(
    client: FlaskClient, engine: Engine, alice: CartFixture
) -> None:
    declining_card = add_card(engine, alice, "tok_decline")
    declined = pay(client, alice, key="key-1", payment_method_id=declining_card)
    assert declined.status_code == 402

    response = pay(client, alice, key="key-2")

    assert response.status_code == 201
    assert response.get_json()["status"] == "succeeded"
    assert cart_payment_statuses(engine, alice) == ["failed", "succeeded"]
    assert cart_status(engine, alice) == "checked_out"


def test_ac12_provider_rejection_fails_with_provider_error(
    client: FlaskClient, engine: Engine, alice: CartFixture
) -> None:
    rejecting_card = add_card(engine, alice, "tok_error")

    response = pay(client, alice, payment_method_id=rejecting_card)

    assert response.status_code == 502
    payment = response.get_json()
    assert payment["status"] == "failed"
    assert payment["failure_code"] == "provider_error"
    assert stored_payment(engine, payment["id"])["status"] == "failed"
    assert cart_status(engine, alice) == "active"


def test_ac13_timeout_keeps_payment_pending(
    client: FlaskClient, engine: Engine, alice: CartFixture
) -> None:
    slow_card = add_card(engine, alice, "tok_timeout")

    response = pay(client, alice, payment_method_id=slow_card)

    assert response.status_code == 202
    payment = response.get_json()
    assert payment["status"] == "pending"
    assert stored_payment(engine, payment["id"])["status"] == "pending"
    assert cart_status(engine, alice) == "active"


def test_fr13_unexpected_provider_error_keeps_payment_pending(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    def crash(call: ChargeCall) -> ChargeResult | None:
        raise ConnectionResetError("connection lost mid-request")

    provider.on_charge = crash

    response = pay(client, alice)

    assert response.status_code == 202
    payment = response.get_json()
    assert payment["status"] == "pending"
    assert stored_payment(engine, payment["id"])["status"] == "pending"


def test_ac15_guarded_update_never_changes_a_final_payment(
    engine: Engine, alice: CartFixture
) -> None:
    with engine.begin() as conn:
        payment_id = insert_payment(conn, alice, status="succeeded", provider_payment_id="ch_1")

    with sessionmaker(engine).begin() as session:
        updated = finalise_payment(session, payment_id, Failed("card_declined"))

    assert updated is None
    assert stored_payment(engine, payment_id)["status"] == "succeeded"


@pytest.mark.parametrize(
    "result",
    [
        ChargeResult("succeeded", provider_payment_id=None),
        ChargeResult("processing", provider_payment_id="ch_1"),  # type: ignore[arg-type]
    ],
    ids=["success-without-id", "unknown-status"],
)
def test_fr13_unconfirmed_provider_result_keeps_payment_pending(
    client: FlaskClient,
    engine: Engine,
    alice: CartFixture,
    provider: RecordingProvider,
    result: ChargeResult,
) -> None:
    provider.on_charge = lambda call: result

    response = pay(client, alice)

    assert response.status_code == 202
    assert stored_payment(engine, response.get_json()["id"])["status"] == "pending"
    assert cart_status(engine, alice) == "active"


def test_ec5_cart_moved_on_during_the_call_is_not_overwritten(
    client: FlaskClient,
    engine: Engine,
    alice: CartFixture,
    provider: RecordingProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def abandon_cart(call: ChargeCall) -> ChargeResult | None:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE carts SET status = 'abandoned' WHERE id = :id"),
                {"id": alice.cart_id},
            )
        return None

    provider.on_charge = abandon_cart

    with caplog.at_level(logging.ERROR):
        response = pay(client, alice)

    assert response.status_code == 201  # the money moved, so the payment is still succeeded
    payment_id = response.get_json()["id"]
    assert stored_payment(engine, payment_id)["status"] == "succeeded"
    assert cart_status(engine, alice) == "abandoned"
    assert [getattr(r, "payment_id", None) for r in caplog.records] == [payment_id]


def test_fr14_payment_finalised_elsewhere_during_the_call_keeps_that_state(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    declining_card = add_card(engine, alice, "tok_decline")

    def reconcile_as_succeeded(call: ChargeCall) -> ChargeResult | None:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE payments SET status = 'succeeded', provider_payment_id = 'ch_1'"
                    " WHERE id = :id"
                ),
                {"id": call.idempotency_key},
            )
        return None

    provider.on_charge = reconcile_as_succeeded

    response = pay(client, alice, payment_method_id=declining_card)

    assert response.status_code == 201
    payment = response.get_json()
    assert payment["status"] == "succeeded"
    assert payment["provider_payment_id"] == "ch_1"
    assert stored_payment(engine, payment["id"])["status"] == "succeeded"


@pytest.mark.parametrize(
    ("token", "crash"),
    [
        ("tok_SECRET_visa", False),
        ("tok_decline_SECRET", False),
        ("tok_error_SECRET", False),
        ("tok_timeout_SECRET", False),
        ("tok_SECRET_visa", True),  # the provider's own error message contains the token
        ("tok_SECRET_visa", "tx2"),  # EC-10: the only log line with a full SQL traceback
    ],
    ids=["succeeded", "declined", "rejected", "timeout", "unexpected-error", "tx2-failure"],
)
def test_ac16_card_token_never_reaches_a_response_or_a_log_line(
    client: FlaskClient,
    engine: Engine,
    alice: CartFixture,
    provider: RecordingProvider,
    caplog: pytest.LogCaptureFixture,
    request: pytest.FixtureRequest,
    token: str,
    crash: bool | str,
) -> None:
    card = add_card(engine, alice, token)
    if crash == "tx2":
        request.getfixturevalue("break_tx2")
    elif crash:

        def leak(call: ChargeCall) -> ChargeResult | None:
            raise ConnectionError(f"could not charge {call.token}")

        provider.on_charge = leak

    with caplog.at_level(logging.DEBUG):
        first = pay(client, alice, payment_method_id=card)
        replay = pay(client, alice, payment_method_id=card)

    assert provider.calls[0].token == token  # the provider did get it
    for response in (first, replay):
        assert "SECRET" not in response.get_data(as_text=True)
    assert "SECRET" not in caplog.text
