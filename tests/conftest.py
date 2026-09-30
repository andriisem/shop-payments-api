import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import Engine, create_engine, make_url, text

from app import create_app
from app.config import Config
from tests.factories import CartFixture, create_cart_fixture
from tests.fakes import RecordingProvider
from tests.tokens import JWT_AUDIENCE, JWT_ISSUER, JWT_SECRET

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://shop:shop@localhost:5434/shop_payments_test"
)


def make_test_config() -> Config:
    return Config(
        database_url=TEST_DATABASE_URL,
        jwt_secret=JWT_SECRET,
        jwt_issuer=JWT_ISSUER,
        jwt_audience=JWT_AUDIENCE,
    )


MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

ALL_TABLES = "users, products, carts, cart_items, user_payment_methods, payments"


def _recreate_database(url: str) -> None:
    database = make_url(url).database or ""
    # The database is force-dropped, so never touch anything that is not clearly a test DB.
    # The strict pattern also makes the quoted identifier below safe.
    if not re.fullmatch(r"[a-z0-9_]+_test", database):
        raise RuntimeError(f"Refusing to recreate {database!r}: the name must end in '_test'")
    admin = create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{database}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    _recreate_database(TEST_DATABASE_URL)
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as conn:
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.exec_driver_sql(migration.read_text())
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(engine: Engine) -> None:
    # TRUNCATE instead of rollback-per-test: concurrency tests need real commits across
    # connections. Cleaning *before* each test also removes the base schema's sample data
    # and leaves a failed test's rows in place for inspection.
    with engine.begin() as conn:
        # Fail fast instead of hanging if a leaked connection still holds a lock.
        conn.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
        conn.exec_driver_sql(f"TRUNCATE {ALL_TABLES} CASCADE")


@pytest.fixture
def alice(engine: Engine) -> CartFixture:
    """Alice's active cart (1 x 45.00 + 2 x 12.50 USD) and her default card, as in AC-1."""
    return create_cart_fixture(engine, name="alice")


@pytest.fixture
def bob(engine: Engine) -> CartFixture:
    return create_cart_fixture(engine, name="bob")


@pytest.fixture
def provider() -> RecordingProvider:
    return RecordingProvider()


@pytest.fixture
def app(provider: RecordingProvider) -> Iterator[Flask]:
    app = create_app(make_test_config(), provider=provider)
    yield app
    app.extensions["engine"].dispose()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()
