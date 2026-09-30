"""App factories: the only place where the app's parts are wired together."""

from flask import Flask

from app.auth import Authenticator, TokenVerifier
from app.config import Config
from app.db import create_db_engine, create_session_factory
from app.errors import DomainError, handle_domain_error
from app.external import MockPaymentProvider, MockTotalService, PaymentProvider, TotalService
from app.payments.routes import payments
from app.payments.service import PaymentService


def create_app(config: Config, *, provider: PaymentProvider, total_service: TotalService) -> Flask:
    """Assemble the app from explicit dependencies.

    The provider and the total service are required on purpose: if a deployment forgets to
    wire the real ones, it fails at startup instead of quietly charging through a mock.
    Callers: a production entry point passes the real clients, the tests pass fakes, and
    create_local_app() passes the mocks.
    """
    engine = create_db_engine(config)
    session_factory = create_session_factory(engine)
    # Fails at startup if JWT_SECRET is missing or shorter than 32 bytes (NFR-9).
    verifier = TokenVerifier(config.jwt_secret, config.jwt_issuer, config.jwt_audience)

    app = Flask(__name__)
    # Shared objects, one per app. The routes and the auth middleware read them through small
    # accessors (payment_service(), require_authenticated_user); tests dispose the engine.
    app.extensions["engine"] = engine
    app.extensions["authenticator"] = Authenticator(verifier, session_factory)
    app.extensions["payment_service"] = PaymentService(
        session_factory, provider=provider, total_service=total_service
    )
    app.register_blueprint(payments)
    # Domain errors become their JSON error body; anything else is Flask's generic 500.
    app.register_error_handler(DomainError, handle_domain_error)
    return app


def create_local_app() -> Flask:
    """The app for local runs: settings from the environment, mocks for the outside systems.

    Run it with:  uv run --env-file .env flask --app app:create_local_app run
    """
    config = Config.from_env()
    # The mock needs its own connection pool. It is called while TX1 holds a connection, so
    # with a shared pool N concurrent payments could each hold one connection and wait for a
    # second one, and all of them would stall until the pool timeout.
    total_service_engine = create_db_engine(config)
    total_service = MockTotalService(create_session_factory(total_service_engine))
    app = create_app(config, provider=MockPaymentProvider(), total_service=total_service)
    app.extensions["total_service_engine"] = total_service_engine  # so it can be disposed
    return app
