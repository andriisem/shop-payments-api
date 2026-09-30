"""Database access: one engine per app, and the session factory the service uses."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Config


def create_db_engine(config: Config) -> Engine:
    """An engine configured for the payment flow's guarantees."""
    return create_engine(
        config.database_url,
        # Test pooled connections before use, so a restarted database gives a fresh
        # connection instead of an error on the next request.
        pool_pre_ping=True,
        # The key re-check under the cart lock (EC-2) must see rows committed while it
        # waited for the lock. REPEATABLE READ would hide them, so the level is pinned.
        isolation_level="READ COMMITTED",
        # Names our connections in pg_stat_activity, so AC-17 can check that none of them
        # holds a transaction open during the provider call.
        connect_args={"application_name": config.application_name},
        # Keep SQL parameters out of error messages and logs (NFR-4, defence in depth).
        hide_parameters=True,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Sessions that keep loaded attributes after commit (NFR-3).

    With the default expire_on_commit=True, reading an attribute after commit runs a query,
    and SQLAlchemy's autobegin silently opens a new transaction for it: a transaction that
    could stay open during the provider call.
    """
    return sessionmaker(engine, expire_on_commit=False)
