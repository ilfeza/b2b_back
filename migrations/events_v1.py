"""Frozen event table definitions for migration 0002. Never import runtime models here."""

from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa


def utcnow():
    return datetime.now(UTC)


@dataclass(frozen=True)
class EventTables:
    outbox: sa.Table
    inbox: sa.Table
    activity: sa.Table
    failures: sa.Table


def define_tables(metadata) -> EventTables:
    """Only tables of this service's schema; no cross-schema access or FKs."""
    if "event_outbox" in {t.name for t in metadata.tables.values()}:
        return EventTables(
            *(
                metadata.tables[f"{metadata.schema}.{name}"]
                for name in ("event_outbox", "event_inbox", "event_activity", "event_failures")
            )
        )
    outbox = sa.Table(
        "event_outbox",
        metadata,
        sa.Column("sequence", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True),
        sa.Column("event_id", sa.Uuid, nullable=False, unique=True),
        sa.Column("topic", sa.String(249), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("aggregate_id", sa.String(200), nullable=False),
        sa.Column("message", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, default=utcnow),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, default=utcnow),
        sa.Column("last_error", sa.String(100)),
        sa.CheckConstraint("attempts >= 0", name="nonnegative_attempts"),
    )
    sa.Index("ix_event_outbox_pending", outbox.c.published_at, outbox.c.sequence)
    inbox = sa.Table(
        "event_inbox",
        metadata,
        sa.Column("consumer_group", sa.String(160), primary_key=True),
        sa.Column("event_id", sa.Uuid, primary_key=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("aggregate_id", sa.String(200), nullable=False),
        sa.Column("seller_id", sa.Uuid, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, default=utcnow),
        sa.Column("message_hash", sa.String(64), nullable=False),
        sa.Column("topic", sa.String(249), nullable=False),
        sa.Column("partition", sa.Integer, nullable=False),
        sa.Column("offset", sa.BigInteger, nullable=False),
    )
    activity = sa.Table(
        "event_activity",
        metadata,
        sa.Column("consumer_group", sa.String(160), primary_key=True),
        sa.Column("seller_id", sa.Uuid, primary_key=True),
        sa.Column("event_type", sa.String(80), primary_key=True),
        sa.Column("processed_count", sa.BigInteger, nullable=False),
        sa.Column("last_occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    failures = sa.Table(
        "event_failures",
        metadata,
        sa.Column("consumer_group", sa.String(160), primary_key=True),
        sa.Column("topic", sa.String(249), primary_key=True),
        sa.Column("partition", sa.Integer, primary_key=True),
        sa.Column("offset", sa.BigInteger, primary_key=True),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("reason_code", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("original_sha256", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, default=utcnow),
    )
    return EventTables(outbox, inbox, activity, failures)


NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
