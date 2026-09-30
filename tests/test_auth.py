"""AC-21 / NFR-9: every request needs a valid access token for an existing user."""

import base64
import json
import logging
import time
import uuid
from typing import Any

import pytest
from flask.testing import FlaskClient
from sqlalchemy import Engine

from tests.factories import CartFixture
from tests.fakes import RecordingProvider
from tests.helpers import payment_count
from tests.tokens import JWT_SECRET, auth_headers, make_token


def post_with(client: FlaskClient, cart: CartFixture, headers: dict[str, str]) -> Any:
    return client.post(
        f"/carts/{cart.cart_id}/payments",
        headers={"Idempotency-Key": "key-1", **headers},
        json={},
    )


def unsigned_token(claims: dict[str, Any]) -> str:
    """alg=none: a token anyone can forge. It must never be accepted."""

    def encode(part: dict[str, Any]) -> str:
        raw = json.dumps(part).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{encode({'alg': 'none', 'typ': 'at+jwt'})}.{encode(claims)}."


def assert_unauthenticated(response: Any, engine: Engine, provider: RecordingProvider) -> None:
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "unauthenticated"
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert provider.calls == []
    assert payment_count(engine) == 0


def test_ac21_valid_token_is_accepted(client: FlaskClient, alice: CartFixture) -> None:
    response = post_with(client, alice, auth_headers(alice.user_id))

    assert response.status_code == 201


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Bearer", "Bearer ", "Basic dXNlcjpwYXNz", "not-a-jwt", "Bearer not.a.jwt"],
    ids=["missing", "empty", "no-token", "blank-token", "basic", "no-scheme", "garbage"],
)
def test_ac21_missing_or_malformed_authorization_is_rejected(
    client: FlaskClient,
    engine: Engine,
    alice: CartFixture,
    provider: RecordingProvider,
    authorization: str | None,
) -> None:
    headers = {} if authorization is None else {"Authorization": authorization}

    assert_unauthenticated(post_with(client, alice, headers), engine, provider)


@pytest.mark.parametrize(
    "token_options",
    [
        {"secret": "another-secret-that-is-also-32-bytes-long!"},
        {"algorithm": "HS512"},
        {"lifetime": 60, "issued_ago": 120},  # expired 60 s ago, beyond the 30 s leeway
        {"lifetime": 3600},  # longer than the 15-minute maximum
        {"iat": None},
        {"exp": None},
        {"typ": "JWT"},  # e.g. an ID token
        {"iss": "https://evil.example"},
        {"aud": "another-service"},
        {"iss": None},
        {"aud": None},
        {"sub": None},
        {"sub": "not-a-uuid"},
        {"sub": "{{{alice}}}"},  # Alice's real id, braced: only the canonical form counts
        {"sub": "urn:uuid:{alice}"},
        {"sub": 42},
        {"exp": "{exp}"},  # NumericDate as a string: must be 401, not a 500
        {"iat": "{iat}"},
        {"issued_ago": -600},  # iat 10 minutes in the future
        {"nbf": "{future}"},
        {"typ": None},  # no typ header at all
    ],
    ids=[
        "wrong-secret",
        "other-algorithm",
        "expired",
        "too-long-lived",
        "no-iat",
        "no-exp",
        "not-an-access-token",
        "wrong-issuer",
        "wrong-audience",
        "no-issuer",
        "no-audience",
        "no-sub",
        "sub-not-uuid",
        "sub-braced-uuid",
        "sub-urn-uuid",
        "sub-not-string",
        "exp-as-string",
        "iat-as-string",
        "iat-in-future",
        "nbf-in-future",
        "no-typ",
    ],
)
def test_ac21_invalid_token_is_rejected(
    client: FlaskClient,
    engine: Engine,
    alice: CartFixture,
    provider: RecordingProvider,
    token_options: dict[str, Any],
) -> None:
    now = int(time.time())
    placeholders = {"alice": alice.user_id, "exp": now + 300, "iat": now, "future": now + 600}
    options = {
        name: value.format(**placeholders) if isinstance(value, str) else value
        for name, value in token_options.items()
    }
    if options.get("nbf"):
        options["nbf"] = int(options["nbf"])  # a real NumericDate, just in the future
    headers = auth_headers(alice.user_id, **options)

    assert_unauthenticated(post_with(client, alice, headers), engine, provider)


def test_ac21_unsigned_token_is_rejected(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    now = int(time.time())
    token = unsigned_token(
        {
            "sub": str(alice.user_id),
            "iss": "https://id.shop.test",
            "aud": "shop-payments-api",
            "iat": now,
            "exp": now + 300,
        }
    )

    response = post_with(client, alice, {"Authorization": f"Bearer {token}"})

    assert_unauthenticated(response, engine, provider)


def test_ac21_token_for_unknown_user_is_rejected(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    response = post_with(client, alice, auth_headers(uuid.uuid4()))

    assert_unauthenticated(response, engine, provider)


def test_ac21_clock_skew_within_leeway_is_accepted(client: FlaskClient, alice: CartFixture) -> None:
    headers = auth_headers(alice.user_id, lifetime=60, issued_ago=80)  # expired 20 s ago

    assert post_with(client, alice, headers).status_code == 201


def test_nfr9_scheme_is_case_insensitive(client: FlaskClient, alice: CartFixture) -> None:
    token = make_token(alice.user_id)

    response = post_with(client, alice, {"Authorization": f"bearer {token}"})

    assert response.status_code == 201


def test_nfr9_authentication_comes_before_request_validation(
    client: FlaskClient, engine: Engine, alice: CartFixture, provider: RecordingProvider
) -> None:
    response = client.post(
        "/carts/not-a-uuid/payments",
        headers={"Authorization": "Bearer nope"},
        data="{not json",
    )

    assert_unauthenticated(response, engine, provider)


def test_nfr9_unknown_route_is_404_not_401(client: FlaskClient) -> None:
    assert client.post("/carts").status_code == 404


def test_nfr9_rejection_reason_is_not_returned(client: FlaskClient, alice: CartFixture) -> None:
    expired = auth_headers(alice.user_id, lifetime=60, issued_ago=600)

    body = post_with(client, alice, expired).get_data(as_text=True)

    assert "expired" not in body.lower()
    assert JWT_SECRET not in body


@pytest.mark.parametrize(
    "token_options",
    [
        {"typ": "application/at+jwt"},  # RFC 9068 media-type form
        {"typ": "AT+JWT"},
        {"aud": ["shop-payments-api", "another-service"]},  # RFC 7519: one of many audiences
    ],
    ids=["media-type-typ", "upper-case-typ", "audience-list"],
)
def test_nfr9_accepted_token_variants(
    client: FlaskClient, alice: CartFixture, token_options: dict[str, Any]
) -> None:
    headers = auth_headers(alice.user_id, **token_options)

    assert post_with(client, alice, headers).status_code == 201


def test_nfr9_options_is_answered_without_a_token(client: FlaskClient, alice: CartFixture) -> None:
    # Browsers send CORS preflights without credentials; OPTIONS returns no data.
    response = client.options(f"/carts/{alice.cart_id}/payments")

    assert response.status_code == 200
    assert "POST" in response.headers["Allow"]


def test_nfr9_tokens_and_secret_never_reach_the_logs(
    client: FlaskClient, alice: CartFixture, caplog: pytest.LogCaptureFixture
) -> None:
    valid = make_token(alice.user_id)
    forged = make_token(alice.user_id, secret="another-secret-that-is-also-32-bytes-long!")
    expired = make_token(alice.user_id, lifetime=60, issued_ago=600)

    with caplog.at_level(logging.DEBUG):
        for token in (forged, expired, valid):
            post_with(client, alice, {"Authorization": f"Bearer {token}"})

    assert "Rejected access token" in caplog.text  # the reason is logged...
    for secret_value in (valid, forged, expired, JWT_SECRET):
        assert secret_value not in caplog.text  # ...but never a token or the secret
