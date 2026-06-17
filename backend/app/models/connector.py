"""
SQLAlchemy 2.0 ORM models for the Connector Framework.

Tables
------
connector_configs      — one row per configured connector instance
connector_secrets      — encrypted credential entries for a connector
connector_run_states   — incremental-sync cursor + last-run metadata

All models inherit from ``app.db.postgres.Base`` so that ``init_db()``
can create every table in a single ``create_all`` call.

SQLAlchemy 2.0 ``Mapped[T]`` syntax
------------------------------------
Every column is declared as ``Mapped[T]`` with ``mapped_column(...)``.
This gives mypy / pyright full attribute-level type inference at
zero runtime cost — assigning the wrong type is a compile-time error
rather than a silent runtime surprise.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.postgres import Base

# ---------------------------------------------------------------------------
# ConnectorConfig
# ---------------------------------------------------------------------------


class ConnectorConfig(Base):
    """
    One row per connector instance configured for an agent.

    ``type_id``    — matches a key in ``ConnectorRegistry`` (e.g. ``"github"``)
    ``agent_id``   — owning SpAIder agent namespace
    ``config_json`` — non-secret connector parameters (repo URL, bucket name,
                       page IDs, etc.)  Stored as JSONB for index-ability.
    """

    __tablename__ = "connector_configs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    type_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="Connector type key registered in ConnectorRegistry.",
    )
    agent_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
        comment="Owning SpAIder agent_id — matches the Neo4j namespace.",
    )
    config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="Non-secret connector parameters (URLs, IDs, options).",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    secrets: Mapped[list["ConnectorSecret"]] = relationship(
        "ConnectorSecret",
        back_populates="config",
        cascade="all, delete-orphan",
        lazy="select",
    )
    run_state: Mapped[Optional["ConnectorRunState"]] = relationship(
        "ConnectorRunState",
        back_populates="config",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"ConnectorConfig(id={self.id!r}, type_id={self.type_id!r}, "
            f"agent_id={self.agent_id!r})"
        )


# ---------------------------------------------------------------------------
# ConnectorSecret
# ---------------------------------------------------------------------------


class ConnectorSecret(Base):
    """
    One AES-256-GCM encrypted credential entry for a connector.

    A single connector may have multiple secrets (e.g. ``access_token`` +
    ``refresh_token``).  Each row holds one logical credential identified
    by ``key_name``.

    ``ciphertext``   — raw ciphertext bytes output by ``SecretsService.encrypt``
                        (includes the 128-bit GCM authentication tag).
    ``nonce``        — 12-byte IV used during encryption; stored plaintext
                        alongside the ciphertext (it is NOT secret).
    ``kek_version``  — monotone integer bumped when the Master Key is rotated,
                        allowing the runner to detect which key generation was
                        used for re-encryption on the next rotation.
    """

    __tablename__ = "connector_secrets"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    connector_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("connector_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key_name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="Logical name of this credential (e.g. 'access_token', 'api_key').",
    )
    ciphertext: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
        comment="AES-256-GCM ciphertext (includes 16-byte GCM auth tag).",
    )
    nonce: Mapped[bytes] = mapped_column(
        LargeBinary(12),
        nullable=False,
        comment="12-byte AES-GCM nonce (IV). Not secret — stored alongside ciphertext.",
    )
    kek_version: Mapped[int] = mapped_column(
        nullable=False,
        default=1,
        comment="Master Key version used to encrypt this row. Bump on key rotation.",
    )

    # Relationship
    config: Mapped["ConnectorConfig"] = relationship(
        "ConnectorConfig",
        back_populates="secrets",
    )

    def __repr__(self) -> str:
        return (
            f"ConnectorSecret(id={self.id!r}, connector_id={self.connector_id!r}, "
            f"key_name={self.key_name!r}, kek_version={self.kek_version})"
        )


# ---------------------------------------------------------------------------
# ConnectorRunState
# ---------------------------------------------------------------------------


class ConnectorRunState(Base):
    """
    Incremental-sync cursor and last-run metadata for a connector.

    One row per ``ConnectorConfig``.  The ``connector_id`` column is both the
    primary key and a FK — it enforces a strict one-to-one relationship and
    means no separate ``id`` column is needed.

    ``cursor``           — opaque string understood only by the connector
                            implementation (e.g. a timestamp, page token, or
                            ETag).  ``None`` means first-ever run (full sync).
    ``last_run_at``      — UTC timestamp of the most recent run attempt.
    ``last_run_status``  — ``"success"`` | ``"failed"`` | ``"partial"``
    ``last_error``       — sanitised error message from the last failed run.
                            MUST NOT contain credential material.
    """

    __tablename__ = "connector_run_states"

    connector_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("connector_configs.id", ondelete="CASCADE"),
        primary_key=True,
        comment="1-to-1 FK to connector_configs.id.",
    )
    cursor: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        default=None,
        comment="Opaque resumption cursor — None signals a full sync.",
    )
    last_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    last_run_status: Mapped[Optional[str]] = mapped_column(
        String(16),
        nullable=True,
        default=None,
        comment="'success' | 'failed' | 'partial'",
    )
    last_error: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        default=None,
        comment="Sanitised error message — must never contain credential material.",
    )

    # Relationship
    config: Mapped["ConnectorConfig"] = relationship(
        "ConnectorConfig",
        back_populates="run_state",
    )

    def __repr__(self) -> str:
        return (
            f"ConnectorRunState(connector_id={self.connector_id!r}, "
            f"status={self.last_run_status!r}, cursor={self.cursor!r})"
        )
