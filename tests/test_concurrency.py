"""Races between parallel requests, with real threads and real locks (NFR-2, EC-1, EC-2).

Every race is forced, not hoped for: the test itself holds the lock the requests need
(a cart row lock, or an uncommitted conflicting row), waits until Postgres shows the app's
connections blocked on it, and only then releases it.
"""

import threading
import time
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
from functools import partial
from uuid import UUID

import pytest
from flask import Flask
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from werkzeug.test import TestResponse

from app.config import Config
from app.payments.errors import PaymentInProgressError
from app.payments.provider import ChargeResult
from app.payments.service import PaymentRequest, PaymentService
from tests.factories import (
    CartFixture,
    fetch_value,
    insert_cart,
    insert_cart_item,
    insert_payment,
    insert_product,
)
from tests.fakes import ChargeCall, RecordingProvider
from tests.helpers import pay, payment_count


@contextmanager
def holding_cart_locks(engine: Engine, *cart_ids: UUID) -> Generator[None]:
    with engine.begin() as conn:
        conn.execute(
            text("SELECT id FROM carts WHERE id = ANY(:ids) FOR UPDATE"),
            {"ids": list(cart_ids)},
        )
        yield


def wait_for_lock_waiters(engine: Engine, count: int, timeout: float = 5.0) -> None:
    """Wait until exactly `count` of the app's connections are blocked on a lock."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waiting = fetch_value(
            engine,
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
            " AND application_name = :app AND wait_event_type = 'Lock'",
            app=Config().application_name,
        )
        if waiting == count:
            return
        time.sleep(0.02)
    raise AssertionError(f"Expected {count} requests blocked on a lock")


def race(
    engine: Engine, cart_ids: list[UUID], requests: list[Callable[[], TestResponse]]
) -> list[TestResponse]:
    with ThreadPoolExecutor(len(requests)) as pool:
        with holding_cart_locks(engine, *cart_ids):
            futures = [pool.submit(request) for request in requests]
            wait_for_lock_waiters(engine, len(requests))
        return [future.result(timeout=10) for future in futures]


def test_ac20_parallel_requests_with_the_same_key_share_one_payment(
    app: Flask, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    responses = race(
        engine,
        [alice.cart_id],
        [lambda: pay(app.test_client(), alice, key="same-key") for _ in range(2)],
    )

    assert {r.status_code for r in responses} <= {201, 202}  # never 409 (EC-2)
    assert len({r.get_json()["id"] for r in responses}) == 1
    assert sorted(r.headers.get("Idempotent-Replayed", "") for r in responses) == ["", "true"]
    assert len(provider.calls) == 1
    assert payment_count(engine) == 1


def test_ec1_parallel_requests_with_different_keys_charge_once(
    app: Flask, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    responses = race(
        engine,
        [alice.cart_id],
        [partial(pay, app.test_client(), alice, key=key) for key in ("key-a", "key-b")],
    )

    by_status = {r.status_code: r.get_json() for r in responses}
    assert sorted(by_status) == [201, 409]
    assert by_status[409]["error"]["code"] == "payment_in_progress"
    assert by_status[409]["error"]["payment_id"] == by_status[201]["id"]
    assert len(provider.calls) == 1
    assert payment_count(engine) == 1


def add_cart(engine: Engine, owner: CartFixture) -> CartFixture:
    with engine.begin() as conn:
        cart_id = insert_cart(conn, owner.user_id)
        mug = insert_product(conn, Decimal("12.50"))
        insert_cart_item(conn, cart_id, mug, 1, Decimal("12.50"))
    return CartFixture(owner.user_id, cart_id, owner.payment_method_id)


def test_ec2_unique_key_backstop_rejects_a_key_used_on_another_cart(
    app: Flask, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    # Two carts share no lock, so only uq_payments_user_idempotency_key can stop this. The
    # other cart's payment is inserted but not committed: the request passes every check,
    # then blocks in its own INSERT on the unique index until the test commits.
    other_cart = add_cart(engine, alice)
    with ThreadPoolExecutor(1) as pool:
        with engine.begin() as conn:
            insert_payment(conn, other_cart, idempotency_key="same-key")
            future = pool.submit(pay, app.test_client(), alice, key="same-key")
            wait_for_lock_waiters(engine, 1)
        response = future.result(timeout=10)

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "idempotency_key_reused"
    assert provider.calls == []
    assert payment_count(engine) == 1


def test_ec1_live_payment_backstop_maps_to_payment_in_progress(
    app: Flask, engine: Engine, alice: CartFixture
) -> None:
    # Not reachable through the API: inserting a payment takes a key-share lock on the cart,
    # which conflicts with the FOR UPDATE in TX1, so the cart lock already serializes it.
    # The branch is a backstop, so feed it a real violation of uq_payments_cart_live.
    with engine.begin() as conn:
        live_payment = insert_payment(conn, alice)
    with pytest.raises(IntegrityError) as violation, engine.begin() as conn:
        insert_payment(conn, alice)
    service: PaymentService = app.extensions["payment_service"]
    request = PaymentRequest(alice.user_id, alice.cart_id, "key-2", None)

    with pytest.raises(PaymentInProgressError) as error:
        service._on_conflict(violation.value, request)

    assert error.value.payment_id == live_payment


def test_ec2_replay_while_the_first_request_is_charging_returns_pending(
    app: Flask, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    charging, release = threading.Event(), threading.Event()

    def hold_the_charge(call: ChargeCall) -> ChargeResult | None:
        charging.set()
        release.wait(timeout=5)
        return None

    provider.on_charge = hold_the_charge
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(pay, app.test_client(), alice)
        try:
            assert charging.wait(timeout=5)
            replay = pay(app.test_client(), alice)
        finally:
            release.set()
        first_response = first.result(timeout=10)

    assert replay.status_code == 202
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.get_json()["status"] == "pending"
    assert replay.get_json()["id"] == first_response.get_json()["id"]
    assert first_response.status_code == 201
    assert len(provider.calls) == 1
