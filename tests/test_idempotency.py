"""FR-3, FR-4: the same key and request replay the original payment and never charge again."""

import uuid

from flask.testing import FlaskClient
from sqlalchemy import Engine, text

from tests.factories import CartFixture, insert_cart, insert_payment_method
from tests.fakes import RecordingProvider
from tests.helpers import add_card, pay, payment_count, post_payment
from tests.tokens import auth_headers


def test_ac2_same_key_and_body_replays_without_charging_again(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    first = pay(client, alice)

    replay = pay(client, alice)

    assert first.status_code == 201
    assert "Idempotent-Replayed" not in first.headers
    assert replay.status_code == 201
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.get_json() == first.get_json()
    assert len(provider.calls) == 1
    assert payment_count(engine) == 1


def test_fr3_replay_of_a_pending_payment_does_not_call_the_provider(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    slow_card = add_card(engine, alice, "tok_timeout")
    first = pay(client, alice, payment_method_id=slow_card)

    replay = pay(client, alice, payment_method_id=slow_card)

    assert first.status_code == 202
    assert replay.status_code == 202
    assert replay.get_json()["id"] == first.get_json()["id"]
    assert replay.get_json()["status"] == "pending"
    assert len(provider.calls) == 1


def test_fr3_replay_of_a_declined_payment_returns_the_decline(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    declining_card = add_card(engine, alice, "tok_decline")
    first = pay(client, alice, payment_method_id=declining_card)

    replay = pay(client, alice, payment_method_id=declining_card)

    assert replay.status_code == 402
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.get_json() == first.get_json()
    assert len(provider.calls) == 1


def test_fr3_replay_reflects_the_current_state(
    client: FlaskClient, engine: Engine, alice: CartFixture
) -> None:
    slow_card = add_card(engine, alice, "tok_timeout")
    first = pay(client, alice, payment_method_id=slow_card)
    with engine.begin() as conn:  # reconciliation found the charge
        conn.execute(
            text(
                "UPDATE payments SET status = 'succeeded', provider_payment_id = 'ch_1'"
                " WHERE id = :id"
            ),
            {"id": first.get_json()["id"]},
        )

    replay = pay(client, alice, payment_method_id=slow_card)

    assert replay.status_code == 201
    assert replay.get_json()["status"] == "succeeded"


def test_ac3_same_key_with_another_card_is_rejected(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    pay(client, alice)
    other_card = add_card(engine, alice, "tok_other")

    response = pay(client, alice, payment_method_id=other_card)

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "idempotency_key_reused"
    assert len(provider.calls) == 1
    assert payment_count(engine) == 1


def test_fr4_same_key_for_another_cart_is_rejected(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    pay(client, alice)
    with engine.begin() as conn:
        other_cart = insert_cart(conn, alice.user_id)

    response = pay(client, CartFixture(alice.user_id, other_cart, alice.payment_method_id))

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "idempotency_key_reused"
    assert len(provider.calls) == 1


def test_ac19_replay_still_works_after_the_default_card_changes(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    first = pay(client, alice)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE user_payment_methods SET is_default = false WHERE user_id = :id"),
            {"id": alice.user_id},
        )
        insert_payment_method(conn, alice.user_id, token="tok_new_default", is_default=True)

    replay = pay(client, alice)

    assert replay.status_code == 201
    assert replay.get_json()["id"] == first.get_json()["id"]
    assert len(provider.calls) == 1


def test_fr2_keys_are_scoped_per_user(
    client: FlaskClient, alice: CartFixture, bob: CartFixture, provider: RecordingProvider
) -> None:
    alices = pay(client, alice, key="shared-key")

    bobs = pay(client, bob, key="shared-key")

    assert bobs.status_code == 201
    assert "Idempotent-Replayed" not in bobs.headers
    assert bobs.get_json()["id"] != alices.get_json()["id"]
    assert len(provider.calls) == 2


def test_fr2_nfr5_key_lookup_never_crosses_users(
    client: FlaskClient, alice: CartFixture, bob: CartFixture
) -> None:
    pay(client, alice, key="shared-key")
    headers = {**auth_headers(bob.user_id), "Idempotency-Key": "shared-key"}

    response = post_payment(client, uuid.uuid4(), headers, json={})

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "cart_not_found"
