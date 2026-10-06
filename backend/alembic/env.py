"""Environnement Alembic : l'URL vient de la configuration de l'application (OCR_DATABASE_URL), jamais d'un fichier versionné."""
from alembic import context
from sqlalchemy import create_engine, pool

import app.models  # noqa: F401  (enregistre les tables)
from app.core.config import get_settings
from app.core.db import Base

config = context.config
target_metadata = Base.metadata


def url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=url(), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True,
                          render_as_batch=url().startswith("sqlite"))
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
