import click
from flask import Flask
from sqlalchemy import create_engine

from app.config import Config
from app.db import create_session_factory
from app.migrations import apply_migrations
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
    engine = create_engine(config.database_url, pool_pre_ping=True)

    app = Flask(__name__)
    app.extensions["engine"] = engine
    app.extensions["payment_service"] = PaymentService(
        create_session_factory(engine),
        provider=provider or MockPaymentProvider(),
        totals=totals or DefaultCartTotalService(),
    )
    app.register_blueprint(payments)

    @app.cli.command("migrate")
    def migrate() -> None:
        """Apply pending SQL migrations."""
        applied = apply_migrations(engine)
        click.echo("\n".join(applied) if applied else "Nothing to apply.")

    return app
