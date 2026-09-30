from pathlib import Path

from sqlalchemy import Engine, text

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def apply_migrations(engine: Engine) -> list[str]:
    """Apply migrations/*.sql files not applied yet, in name order, one transaction each."""
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " filename TEXT PRIMARY KEY,"
            " applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )
        applied = set(conn.scalars(text("SELECT filename FROM schema_migrations")))

    newly_applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        with engine.begin() as conn:
            conn.exec_driver_sql(path.read_text())
            conn.execute(
                text("INSERT INTO schema_migrations (filename) VALUES (:filename)"),
                {"filename": path.name},
            )
        newly_applied.append(path.name)
    return newly_applied
