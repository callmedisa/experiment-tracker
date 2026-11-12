"""Table definitions. The Alembic migration is the source of truth for DDL; this mirrors it for queries."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id = sa.Column(pg.UUID(as_uuid=True), primary_key=True)
    name = sa.Column(sa.Text, nullable=False)
    created_at = sa.Column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


class ApiKey(Base):
    __tablename__ = "api_keys"
    id = sa.Column(pg.UUID(as_uuid=True), primary_key=True)
    project_id = sa.Column(pg.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False)
    label = sa.Column(sa.Text, nullable=False)
    scope = sa.Column(sa.Text, nullable=False)
    prefix = sa.Column(sa.Text, nullable=False)
    key_hash = sa.Column(sa.Text, nullable=False, unique=True)
    created_at = sa.Column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    last_used_at = sa.Column(sa.DateTime(timezone=True))
    revoked_at = sa.Column(sa.DateTime(timezone=True))


class Session(Base):
    __tablename__ = "sessions"
    id = sa.Column(sa.Text, primary_key=True)
    project_id = sa.Column(pg.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False)
    key_id = sa.Column(pg.UUID(as_uuid=True), sa.ForeignKey("api_keys.id"), nullable=False)
    created_at = sa.Column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    expires_at = sa.Column(sa.DateTime(timezone=True), nullable=False)


class Run(Base):
    __tablename__ = "runs"
    id = sa.Column(sa.Text, primary_key=True)
    project_id = sa.Column(pg.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False)
    name = sa.Column(sa.Text, nullable=False)
    status = sa.Column(sa.Text, nullable=False, server_default="running")
    created_at = sa.Column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    finished_at = sa.Column(sa.DateTime(timezone=True))
    last_seq = sa.Column(sa.BigInteger, nullable=False, server_default="0")
    duplicates_ignored = sa.Column(sa.BigInteger, nullable=False, server_default="0")
    last_event_at = sa.Column(sa.DateTime(timezone=True))


class MetricEvent(Base):
    __tablename__ = "metric_events"
    run_id = sa.Column(sa.Text, sa.ForeignKey("runs.id"), primary_key=True)
    seq = sa.Column(sa.BigInteger, primary_key=True)
    point_id = sa.Column(sa.Text, nullable=False)
    metric = sa.Column(sa.Text, nullable=False)
    step = sa.Column(sa.BigInteger, nullable=False)
    value = sa.Column(pg.DOUBLE_PRECISION, nullable=False)
    client_ts = sa.Column(sa.DateTime(timezone=True))
    received_at = sa.Column(sa.DateTime(timezone=True), nullable=False)
    is_late = sa.Column(sa.Boolean, nullable=False)


class IngestBatch(Base):
    __tablename__ = "ingest_batches"
    run_id = sa.Column(sa.Text, sa.ForeignKey("runs.id"), primary_key=True)
    batch_id = sa.Column(sa.Text, primary_key=True)
    payload_hash = sa.Column(sa.Text, nullable=False)
    accepted = sa.Column(sa.Integer, nullable=False)
    duplicates = sa.Column(sa.Integer, nullable=False)
    first_seq = sa.Column(sa.BigInteger)
    last_seq = sa.Column(sa.BigInteger)
    created_at = sa.Column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
