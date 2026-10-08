"""Base de données : SQLite embarquée (un fichier, aucun serveur à installer ni à surveiller).

L'API et le worker sont deux processus qui écrivent dans le même fichier : journal WAL (les lectures ne bloquent
jamais les écritures) et attente sur verrou de 30 s au lieu d'une erreur « database is locked » immédiate.
Une autre base SQLAlchemy reste utilisable par OCR_DATABASE_URL.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

SQLITE_BUSY_MS = 30_000


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def sqlite_path(url: str) -> Path | None:
    """Chemin du fichier d'une URL SQLite (None : autre base ou base en mémoire)."""
    u = make_url(url)
    if not u.drivername.startswith("sqlite") or not u.database or u.database == ":memory:":
        return None
    return Path(u.database)


def _sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
    cur = dbapi_conn.cursor()
    try:
        cur.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_MS}")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")  # persistant : sans effet s'il est déjà actif
        cur.execute("PRAGMA synchronous=NORMAL")  # sûr en WAL : au pire la dernière transaction après une coupure
    finally:
        cur.close()


def init_engine(url: str | None = None) -> Engine:
    """Crée (ou recrée) le moteur. Appelé au démarrage et par les tests."""
    global _engine, _SessionLocal
    url = url or get_settings().database_url
    kwargs: dict = {"pool_pre_ping": True}
    path = sqlite_path(url)
    if url.startswith("sqlite"):
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": SQLITE_BUSY_MS / 1000}
    else:
        kwargs["pool_recycle"] = 1800
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(_engine, "connect", _sqlite_pragmas)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def SessionLocal() -> Session:  # noqa: N802
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """Dépendance FastAPI : une session par requête."""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
