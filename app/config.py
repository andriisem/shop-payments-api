import os
from dataclasses import dataclass, field

DEFAULT_DATABASE_URL = "postgresql+psycopg://shop:shop@localhost:5434/shop_payments"


@dataclass(frozen=True)
class Config:
    database_url: str = DEFAULT_DATABASE_URL
    max_body_bytes: int = 16 * 1024  # the body holds one optional UUID
    application_name: str = "shop-payments-api"  # names our connections in pg_stat_activity
    # NFR-9: no default secret, so the app cannot start with a guessable one.
    jwt_secret: str = field(default="", repr=False)
    jwt_issuer: str = "https://id.shop.local"
    jwt_audience: str = "shop-payments-api"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
            jwt_secret=os.environ.get("JWT_SECRET", ""),
            jwt_issuer=os.environ.get("JWT_ISSUER", cls.jwt_issuer),
            jwt_audience=os.environ.get("JWT_AUDIENCE", cls.jwt_audience),
        )
