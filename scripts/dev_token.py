"""Print an access token for local testing. Not part of the service.

Issuing tokens is the identity provider's job (OS-1); the service only verifies them.
This mints one with the same claims, signed with the JWT_SECRET the app uses:

    JWT_SECRET=... uv run python scripts/dev_token.py <user-id> [minutes]
"""

import os
import sys
import time

import jwt


def main() -> None:
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    user_id = sys.argv[1]
    minutes = int(sys.argv[2]) if len(sys.argv) == 3 else 5
    now = int(time.time())
    claims = {
        "sub": user_id,
        "iss": os.environ.get("JWT_ISSUER", "https://id.shop.local"),
        "aud": os.environ.get("JWT_AUDIENCE", "shop-payments-api"),
        "iat": now,
        "exp": now + minutes * 60,
    }
    print(
        jwt.encode(claims, os.environ["JWT_SECRET"], algorithm="HS256", headers={"typ": "at+jwt"})
    )


if __name__ == "__main__":
    main()
