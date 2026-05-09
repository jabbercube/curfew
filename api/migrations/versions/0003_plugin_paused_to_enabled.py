"""plugin assignments: paused -> enabled (default true)

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-08

Inverts the plugin-assignment toggle so the field name reads naturally —
operators ask "is this plugin enabled?", not "is it paused?". Reconciliation
now skips assignments where ``enabled = false`` (was: ``paused = true``).

Drop+recreate (chosen over a rename-and-flip) because no production assignments
exist yet — a row that was previously paused becomes enabled on upgrade and
vice versa, which is fine for a pre-prod schema flip.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the old column + add the new one with a temporary server_default
    # so existing rows backfill to ``enabled = true``. Drop the server_default
    # afterwards so future inserts route through the SQLModel default and the
    # column behaviour matches the rest of the kernel schema.
    with op.batch_alter_table("plugins", schema=None) as batch_op:
        batch_op.drop_column("paused")
        batch_op.add_column(
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )
    with op.batch_alter_table("plugins", schema=None) as batch_op:
        batch_op.alter_column("enabled", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("plugins", schema=None) as batch_op:
        batch_op.drop_column("enabled")
        batch_op.add_column(
            sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    with op.batch_alter_table("plugins", schema=None) as batch_op:
        batch_op.alter_column("paused", server_default=None)
