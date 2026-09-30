"""Access tokens for tests, signed the way the identity provider would sign them (NFR-9)."""

import time
from typing import Any
from uuid import UUID

import jwt

JWT_SECRET = "test-only-secret-long-enough-for-hs256-and-for-the-hs512-negative-test"
JWT_ISSUER = "https://id.shop.test"
JWT_AUDIENCE = "shop-payments-api"


def make_token(
    user_id: UUID | str,
    *,
    secret: str = JWT_SECRET,
    algorithm: str = "HS256",
    typ: str | None = "at+jwt",
    lifetime: int = 300,
    issued_ago: int = 0,
    **claims: Any,
) -> str:
    """A valid access token. Override or drop (claim=None) claims to build invalid ones."""
    issued_at = int(time.time()) - issued_ago
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": issued_at,
        "exp": issued_at + lifetime,
    }
    payload.update(claims)
    payload = {name: value for name, value in payload.items() if value is not None}
    return jwt.encode(payload, secret, algorithm=algorithm, headers={"typ": typ})


def auth_headers(user_id: UUID | str, **token_options: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(user_id, **token_options)}"}
