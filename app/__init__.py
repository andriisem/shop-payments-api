import click
from flask import Flask
from sqlalchemy import create_engine

from app.config import Config
from app.db import create_session_factory
from app.migrations import apply_migrations


def create_app(config: Config | None = None) -> Flask:
    config = config or Config.from_env()
    engine = create_engine(config.database_url, pool_pre_ping=True)

    app = Flask(__name__)
    app.extensions["session_factory"] = create_session_factory(engine)

    @app.cli.command("migrate")
    def migrate() -> None:
        """Apply pending SQL migrations."""
        applied = apply_migrations(engine)
        click.echo("\n".join(applied) if applied else "Nothing to apply.")

    return app
