"""per-user auth: users.password_hash + sessions table

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-08

Adds the column and table needed for username + password login backed by
opaque server-side session ids in cookies. ``users.password_hash`` is nullable
(NULL means "no password set; cannot log in" — covers users created before
this migration). Sessions are stored in a new ``sessions`` table; the cookie
value is the row's primary key (a 256-bit random token).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("password_hash", sa.String(), nullable=True))

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sessions_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_sessions_expires_at"), ["expires_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sessions_expires_at"))
        batch_op.drop_index(batch_op.f("ix_sessions_user_id"))
    op.drop_table("sessions")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("password_hash")
