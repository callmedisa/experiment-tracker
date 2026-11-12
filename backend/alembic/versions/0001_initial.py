"""initial schema (the dev spec §4 + owner-approved additions, see JOURNAL.md)

Additions to §4: ingest_batches.payload_hash, nullable first_seq/last_seq,
runs.last_event_at. Enums are TEXT + CHECK constraints (decision logged in JOURNAL.md).
"""
revision = "0001"
down_revision = None

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("prefix", sa.Text, nullable=False),
        sa.Column("key_hash", sa.Text, nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("scope IN ('ingest','read')", name="ck_api_keys_scope"),
    )
    op.create_index("ix_api_keys_project", "api_keys", ["project_id"])
    op.create_table(
        "sessions",
        sa.Column("id", sa.Text, primary_key=True),  # HMAC of the cookie token, never the token itself
        sa.Column("project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("key_id", pg.UUID(as_uuid=True), sa.ForeignKey("api_keys.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="running"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("last_seq", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("duplicates_ignored", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("last_event_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('running','finished','failed')", name="ck_runs_status"),
    )
    op.create_index("ix_runs_project_created", "runs", ["project_id", "created_at"])
    op.create_table(
        "metric_events",
        sa.Column("run_id", sa.Text, sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("seq", sa.BigInteger, nullable=False),
        sa.Column("point_id", sa.Text, nullable=False),
        sa.Column("metric", sa.Text, nullable=False),
        sa.Column("step", sa.BigInteger, nullable=False),
        sa.Column("value", pg.DOUBLE_PRECISION, nullable=False),
        sa.Column("client_ts", sa.DateTime(timezone=True)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_late", sa.Boolean, nullable=False),
        sa.PrimaryKeyConstraint("run_id", "seq"),
        sa.UniqueConstraint("run_id", "point_id", name="uq_metric_events_point"),
    )
    op.create_index("ix_metric_events_run_metric_step", "metric_events", ["run_id", "metric", "step"])
    op.create_table(
        "ingest_batches",
        sa.Column("run_id", sa.Text, sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("batch_id", sa.Text, nullable=False),
        sa.Column("payload_hash", sa.Text, nullable=False),
        sa.Column("accepted", sa.Integer, nullable=False),
        sa.Column("duplicates", sa.Integer, nullable=False),
        sa.Column("first_seq", sa.BigInteger),
        sa.Column("last_seq", sa.BigInteger),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("run_id", "batch_id"),
    )


def downgrade() -> None:
    for t in ("ingest_batches", "metric_events", "runs", "sessions", "api_keys", "projects"):
        op.drop_table(t)
