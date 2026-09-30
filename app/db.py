from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    # Reading an expired attribute after commit runs a query, and SQLAlchemy's autobegin
    # silently opens a new transaction for it. Keeping attributes loaded prevents a
    # transaction from being open during the provider call (NFR-3).
    return sessionmaker(engine, expire_on_commit=False)
