"""Base of all domain errors. Each carries its API error code and HTTP status; one handler
renders them."""

from typing import Any, ClassVar


class DomainError(Exception):
    code: ClassVar[str]
    status: ClassVar[int]
    message: ClassVar[str]

    def __init__(self) -> None:
        super().__init__(self.message)

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}

    def headers(self) -> dict[str, str]:
        return {}


class UnauthenticatedError(DomainError):
    code, status, message = "unauthenticated", 401, "Missing or invalid access token."

    def headers(self) -> dict[str, str]:
        return {"WWW-Authenticate": 'Bearer realm="shop-payments-api"'}


def handle_domain_error(error: DomainError) -> tuple[dict[str, Any], int, dict[str, str]]:
    return error.body(), error.status, error.headers()
