from flask import Flask
from sqlalchemy import create_engine

from app.auth import Authenticator, TokenVerifier
from app.config import Config
from app.db import create_session_factory
from app.payments.errors import (
    DomainError,
    handle_domain_error,
)
from app.payments.provider import MockPaymentProvider, PaymentProvider
from app.payments.routes import payments
from app.payments.service import PaymentService
from app.payments.totals import CartTotalService, DefaultCartTotalService


def create_app(
    config: Config | None = None,
    *,
    provider: PaymentProvider | None = None,
    totals: CartTotalService | None = None,
) -> Flask:
    config = config or Config.from_env()
    engine = create_engine(
        config.database_url,
        pool_pre_ping=True,
        # The key re-check under the cart lock (EC-2) relies on each statement seeing rows
        # committed while it waited. REPEATABLE READ would hide them, so pin the level.
        isolation_level="READ COMMITTED",
        connect_args={"application_name": config.application_name},
    )

    app = Flask(__name__)
    session_factory = create_session_factory(engine)
    verifier = TokenVerifier(config.jwt_secret, config.jwt_issuer, config.jwt_audience)
    app.extensions["engine"] = engine
    app.extensions["authenticator"] = Authenticator(verifier, session_factory)
    app.extensions["payment_service"] = PaymentService(
        session_factory,
        provider=provider or MockPaymentProvider(),
        totals=totals or DefaultCartTotalService(),
    )
    app.register_blueprint(payments)
    app.register_error_handler(DomainError, handle_domain_error)

    return app
