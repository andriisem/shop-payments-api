from pathlib import Path

from sqlalchemy import Connection, Engine, text

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Arbitrary constant: every runner takes the same advisory lock, so two app instances
# migrating at the same time apply each file exactly once.
_MIGRATION_LOCK_ID = 7_406_294


def apply_migrations(engine: Engine) -> list[str]:
    """Apply migrations/*.sql files not applied yet, in name order, in one transaction."""
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _MIGRATION_LOCK_ID})
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " filename TEXT PRIMARY KEY,"
            " applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )
        return _apply_new_files(conn)


def _apply_new_files(conn: Connection) -> list[str]:
    applied = set(conn.scalars(text("SELECT filename FROM schema_migrations")))
    newly_applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        conn.exec_driver_sql(path.read_text())
        conn.execute(
            text("INSERT INTO schema_migrations (filename) VALUES (:filename)"),
            {"filename": path.name},
        )
        newly_applied.append(path.name)
    return newly_applied
