"""Modèle de données (PLAN.md §6). Dates en UTC naïf."""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


# Text MariaDB = 64 Ko seulement : un grand tableau HTML le dépasserait.
LongText = Text().with_variant(LONGTEXT(), "mysql", "mariadb")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role(str, enum.Enum):
    admin = "admin"
    user = "user"


class DocStatus(str, enum.Enum):
    queued = "queued"
    waiting_gpu = "waiting_gpu"
    preprocessing = "preprocessing"
    ocr = "ocr"
    correcting = "correcting"
    review = "review"
    exporting = "exporting"
    done = "done"
    failed = "failed"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class CorrectionStatus(str, enum.Enum):
    proposed = "proposed"
    accepted = "accepted"
    rejected = "rejected"
    auto_applied = "auto_applied"
    reverted = "reverted"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default=Role.user.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Incrémenté à chaque changement de mot de passe ou déconnexion : invalide les anciens jetons de rafraîchissement.
    token_version: Mapped[int] = mapped_column(Integer, default=0)

    documents: Mapped[list["Document"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(64))  # nom aléatoire sur disque
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default=DocStatus.queued.value, index=True)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    progress: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="documents")
    pages: Mapped[list["Page"]] = relationship(back_populates="document", cascade="all, delete-orphan", order_by="Page.index")
    jobs: Mapped[list["Job"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    exports: Mapped[list["Export"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("document_id", "index", name="uq_page_doc_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)  # 0-based
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    dpi: Mapped[int] = mapped_column(Integer, default=200)
    image_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    preprocessed_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mean_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|rendered|ocr_done|error
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)

    document: Mapped[Document] = relationship(back_populates="pages")
    blocks: Mapped[list["Block"]] = relationship(back_populates="page", cascade="all, delete-orphan", order_by="Block.reading_order")


class Block(Base):
    __tablename__ = "blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    reading_order: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str] = mapped_column(String(32))
    bbox: Mapped[list[float]] = mapped_column(JSON)  # [x0, y0, x1, y1] en pixels de l'image de la page
    html_ocr: Mapped[str] = mapped_column(LongText, default="")
    html_current: Mapped[str] = mapped_column(LongText, default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    crop_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    direction: Mapped[str] = mapped_column(String(3), default="ltr")
    to_check: Mapped[bool] = mapped_column(Boolean, default=False)  # « à vérifier »
    error: Mapped[bool] = mapped_column(Boolean, default=False)

    page: Mapped[Page] = relationship(back_populates="blocks")
    corrections: Mapped[list["Correction"]] = relationship(back_populates="block", cascade="all, delete-orphan")


class Correction(Base):
    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    block_id: Mapped[int] = mapped_column(ForeignKey("blocks.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(8))  # llm | user
    before: Mapped[str] = mapped_column(LongText)
    after: Mapped[str] = mapped_column(LongText)
    status: Mapped[str] = mapped_column(String(16), default=CorrectionStatus.proposed.value)
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    block: Mapped[Block] = relationship(back_populates="corrections")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_id", "status", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(16), default="process")  # process | export
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.queued.value)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    document: Mapped[Document] = relationship(back_populates="jobs")


class Export(Base):
    __tablename__ = "exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    document: Mapped[Document] = relationship(back_populates="exports")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # pas de clé étrangère : survit à la suppression
    action: Mapped[str] = mapped_column(String(32), index=True)
    target: Mapped[str | None] = mapped_column(String(64), nullable=True)  # identifiants uniquement, jamais de contenu
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
