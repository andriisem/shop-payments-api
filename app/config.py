import os
from dataclasses import dataclass

DEFAULT_DATABASE_URL = "postgresql+psycopg://shop:shop@localhost:5434/shop_payments"


@dataclass(frozen=True)
class Config:
    database_url: str = DEFAULT_DATABASE_URL
    provider_timeout_seconds: float = 10.0  # NFR-6

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
            provider_timeout_seconds=float(os.environ.get("PROVIDER_TIMEOUT_SECONDS", "10")),
        )
