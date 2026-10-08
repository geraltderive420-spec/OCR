"""Environnement Alembic : l'URL vient de la configuration de l'application (OCR_DATABASE_URL), jamais d'un fichier versionné."""
from alembic import context
from sqlalchemy import create_engine, event, pool

import app.models  # noqa: F401  (enregistre les tables)
from app.core.config import get_settings
from app.core.db import SQLITE_BUSY_MS, Base, sqlite_path

config = context.config
target_metadata = Base.metadata


def url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def _migration_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
    """SQLite : le mode « batch » recrée les tables modifiées (copie, suppression, renommage). Avec les clés
    étrangères actives, supprimer l'ancienne table `documents` effacerait en cascade pages, blocs et corrections :
    elles sont donc désactivées le temps de la migration, puis contrôlées (foreign_key_check)."""
    cur = dbapi_conn.cursor()
    try:
        cur.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_MS}")  # l'application peut tourner pendant la migration
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.execute("PRAGMA journal_mode=WAL")
    finally:
        cur.close()


def run_migrations_offline() -> None:
    context.configure(url=url(), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    path = sqlite_path(url())
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url(), poolclass=pool.NullPool)
    sqlite = url().startswith("sqlite")
    if sqlite:
        event.listen(engine, "connect", _migration_pragmas)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True, render_as_batch=sqlite)
        with context.begin_transaction():
            context.run_migrations()
        if sqlite:
            bad = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise RuntimeError(f"Migration : {len(bad)} référence(s) cassée(s) dans la base (foreign_key_check).")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
