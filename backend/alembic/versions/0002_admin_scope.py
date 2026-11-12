"""Add the 'admin' key scope: only admin keys may manage keys (a read key can look, not touch)."""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_api_keys_scope", "api_keys", type_="check")
    op.create_check_constraint("ck_api_keys_scope", "api_keys", "scope IN ('ingest','read','admin')")


def downgrade() -> None:
    op.execute("DELETE FROM sessions WHERE key_id IN (SELECT id FROM api_keys WHERE scope='admin')")
    op.execute("DELETE FROM api_keys WHERE scope='admin'")
    op.drop_constraint("ck_api_keys_scope", "api_keys", type_="check")
    op.create_check_constraint("ck_api_keys_scope", "api_keys", "scope IN ('ingest','read')")
