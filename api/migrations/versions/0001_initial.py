"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-05

Creates the 10 kernel tables and seeds the singleton settings row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "apps",
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("exe_paths", sa.JSON(), nullable=False),
        sa.Column("process_names", sa.JSON(), nullable=False),
        sa.Column("urls", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("slug", name=op.f("pk_apps")),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column(
            "target_kind",
            sa.Enum(
                "USER",
                "DEVICE",
                "APP",
                "AGENT",
                "PLUGIN",
                "SETTINGS",
                "MANIFEST",
                name="audittargetkind",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.create_index("ix_audit_log_occurred_at", ["occurred_at"], unique=False)
        batch_op.create_index(
            "ix_audit_log_target_kind_target_id",
            ["target_kind", "target_id"],
            unique=False,
        )

    op.create_table(
        "manifests",
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("type", name=op.f("pk_manifests")),
    )
    op.create_table(
        "plugins",
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("users", sa.JSON(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("type", "instance_id", name=op.f("pk_plugins")),
    )
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_tick_seconds", sa.Integer(), nullable=False),
        sa.Column("manifest_tick_seconds", sa.Integer(), nullable=False),
        sa.Column("plugin_resync_seconds", sa.Integer(), nullable=False),
        sa.Column("plugin_reconcile_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("audit_retention_days", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_settings_singleton")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settings")),
    )
    op.create_table(
        "users",
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("MEMBER", "MANAGER", "ADMIN", name="userrole", native_enum=False),
            nullable=False,
        ),
        sa.Column("managed", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("slug", name=op.f("pk_users")),
    )
    op.create_table(
        "devices",
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("owner", sa.String(), nullable=True),
        sa.Column(
            "type",
            sa.Enum("PC", "PHONE", "TABLET", "CONSOLE", "TV", name="devicetype", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "os",
            sa.Enum(
                "WINDOWS",
                "MACOS",
                "LINUX",
                "IOS",
                "ANDROID",
                name="deviceos",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("mac", sa.JSON(), nullable=False),
        sa.Column("managed", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["owner"], ["users.slug"], name=op.f("fk_devices_owner_users")),
        sa.PrimaryKeyConstraint("slug", name=op.f("pk_devices")),
    )
    op.create_table(
        "user_locks",
        sa.Column("user", sa.String(), nullable=False),
        sa.Column("manual_lock", sa.Boolean(), nullable=False),
        sa.Column("set_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("set_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user"], ["users.slug"], name=op.f("fk_user_locks_user_users")),
        sa.PrimaryKeyConstraint("user", name=op.f("pk_user_locks")),
    )
    op.create_table(
        "agent_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("device", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["device"], ["devices.slug"], name=op.f("fk_agent_tokens_device_devices")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_agent_tokens_token_hash")),
    )
    with op.batch_alter_table("agent_tokens", schema=None) as batch_op:
        batch_op.create_index(
            "ix_agent_tokens_device_revoked_at", ["device", "revoked_at"], unique=False
        )

    op.create_table(
        "agents",
        sa.Column("device", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_version", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["device"], ["devices.slug"], name=op.f("fk_agents_device_devices")
        ),
        sa.PrimaryKeyConstraint("device", name=op.f("pk_agents")),
    )

    # Seed the singleton settings row with documented defaults.
    op.execute(
        sa.text(
            "INSERT INTO settings ("
            "id, agent_tick_seconds, manifest_tick_seconds, "
            "plugin_resync_seconds, plugin_reconcile_timeout_seconds, "
            "audit_retention_days"
            ") VALUES (1, 60, 3600, 300, 30, 90)"
        )
    )


def downgrade() -> None:
    op.drop_table("agents")
    with op.batch_alter_table("agent_tokens", schema=None) as batch_op:
        batch_op.drop_index("ix_agent_tokens_device_revoked_at")
    op.drop_table("agent_tokens")
    op.drop_table("user_locks")
    op.drop_table("devices")
    op.drop_table("users")
    op.drop_table("settings")
    op.drop_table("plugins")
    op.drop_table("manifests")
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_log_target_kind_target_id")
        batch_op.drop_index("ix_audit_log_occurred_at")
    op.drop_table("audit_log")
    op.drop_table("apps")
