"""workspace connections with encrypted credentials

Revision ID: c9a942c61130
Revises: 78a48cf98d38
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9a942c61130"
down_revision = "78a48cf98d38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("auth", sa.String(), nullable=False),
        sa.Column("credential", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("updated_by_id", sa.String(length=72), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("auth <> 'none' OR credential IS NULL", name=op.f("ck_connections_no_anonymous_credential")),
        sa.CheckConstraint("auth IN ('none', 'bearer', 'headers')", name=op.f("ck_connections_auth")),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["principals.id"], name=op.f("fk_connections_created_by_id_principals")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name=op.f("fk_connections_organization_id_workspaces"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name=op.f("fk_connections_organization_id_organizations")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["principals.id"], name=op.f("fk_connections_updated_by_id_principals")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connections")),
        sa.UniqueConstraint(
            "organization_id", "workspace_id", "id", name=op.f("uq_connections_organization_id_workspace_id_id")
        ),
    )
    op.create_index("ix_connections_workspace_id_id", "connections", ["workspace_id", "id"], unique=False)

    op.execute(
        "CREATE TRIGGER stamp_resource BEFORE UPDATE ON connections FOR EACH ROW EXECUTE FUNCTION stamp_resource()"
    )
    op.execute(
        "CREATE TRIGGER guard_resource_identity BEFORE UPDATE ON connections FOR EACH ROW EXECUTE FUNCTION guard_resource_identity()"
    )


def downgrade() -> None:
    op.drop_index("ix_connections_workspace_id_id", table_name="connections")
    op.drop_table("connections")
