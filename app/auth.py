"""NFR-9: verify the caller's access token before any payment route runs."""

import logging
from dataclasses import dataclass, field
from uuid import UUID

import jwt
from flask import current_app, g, request
from sqlalchemy.orm import Session, sessionmaker

from app.errors import UnauthenticatedError
from app.models import User
from app.validation import parse_canonical_uuid

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"  # the only one accepted: never "none", never one the token picks
ACCESS_TOKEN_TYPES = {"at+jwt", "application/at+jwt"}  # RFC 9068
MAX_TOKEN_LIFETIME_SECONDS = 15 * 60
CLOCK_SKEW_SECONDS = 30
MIN_SECRET_BYTES = 32


def _is_numeric_date(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


class TokenError(Exception):
    """Why a token was rejected. Logged, never returned to the client."""


@dataclass(frozen=True)
class TokenVerifier:
    secret: str = field(repr=False)
    issuer: str
    audience: str

    def __post_init__(self) -> None:
        if len(self.secret.encode()) < MIN_SECRET_BYTES:
            raise ValueError(f"JWT_SECRET must be at least {MIN_SECRET_BYTES} bytes")

    def verify(self, token: str) -> UUID:
        """Return the user id from a valid access token, or raise TokenError."""
        try:
            decoded = jwt.decode_complete(
                token,
                self.secret,
                algorithms=[ALGORITHM],
                audience=self.audience,
                issuer=self.issuer,
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as error:
            raise TokenError(type(error).__name__) from None

        header, claims = decoded["header"], decoded["payload"]
        if str(header.get("typ", "")).lower() not in ACCESS_TOKEN_TYPES:
            raise TokenError("not an access token")
        # PyJWT accepts numeric strings for NumericDate claims; the lifetime needs numbers.
        if not all(_is_numeric_date(claims[name]) for name in ("exp", "iat")):
            raise TokenError("exp and iat must be numbers")
        if claims["exp"] - claims["iat"] > MAX_TOKEN_LIFETIME_SECONDS:
            raise TokenError("token lifetime is longer than allowed")
        user_id = parse_canonical_uuid(claims["sub"])
        if user_id is None:
            raise TokenError("sub is not a canonical UUID")
        return user_id


class Authenticator:
    def __init__(self, verifier: TokenVerifier, session_factory: sessionmaker[Session]) -> None:
        self._verifier = verifier
        self._session_factory = session_factory

    def authenticate(self, authorization: str | None) -> UUID:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            logger.info("Rejected request: no bearer token")
            raise UnauthenticatedError()
        try:
            user_id = self._verifier.verify(token.strip())
        except TokenError as error:
            logger.info("Rejected access token: %s", error)
            raise UnauthenticatedError() from None
        with self._session_factory() as session:
            if session.get(User, user_id) is None:
                logger.info("Rejected access token: unknown user")
                raise UnauthenticatedError()
        return user_id


def current_user_id() -> UUID:
    """The caller verified by require_authenticated_user for this request."""
    user_id: UUID = g.user_id
    return user_id


def require_authenticated_user() -> None:
    """before_request middleware: sets g.user_id, or answers 401 before the route runs."""
    if request.method == "OPTIONS":
        return  # CORS preflights carry no credentials; Flask's OPTIONS reply has no data
    authenticator: Authenticator = current_app.extensions["authenticator"]
    g.user_id = authenticator.authenticate(request.headers.get("Authorization"))
