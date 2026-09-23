"""immutable assets and upload identity

Revision ID: c07418d97b28
Revises: 9c172cfae8e8
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c07418d97b28"
down_revision = "9c172cfae8e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("digest", sa.String(), nullable=False),
        sa.Column("content_ref", sa.String(), nullable=False),
        sa.Column("source", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("size >= 0", name=op.f("ck_assets_size")),
        sa.ForeignKeyConstraint(["created_by_id"], ["principals.id"], name=op.f("fk_assets_created_by_id_principals")),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name=op.f("fk_assets_organization_id_workspaces"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name=op.f("fk_assets_organization_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
        sa.UniqueConstraint("workspace_id", "content_ref", name=op.f("uq_assets_workspace_id_content_ref")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_assets_workspace_id_id")),
    )
    op.create_index("ix_assets_workspace_created", "assets", ["workspace_id", "created_at", "id"], unique=False)

    op.execute("""
        CREATE FUNCTION guard_asset() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'asset content is retained'; END IF;
            IF (to_jsonb(NEW) - ARRAY['retired_at', 'version', 'updated_at'])
                IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['retired_at', 'version', 'updated_at'])
            THEN RAISE EXCEPTION 'asset content and identity are immutable'; END IF;
            IF OLD.retired_at IS NOT NULL AND NEW.retired_at IS DISTINCT FROM OLD.retired_at
            THEN RAISE EXCEPTION 'asset retirement is permanent'; END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER guard_asset BEFORE UPDATE OR DELETE ON assets FOR EACH ROW EXECUTE FUNCTION guard_asset()"
    )
    op.execute("CREATE TRIGGER stamp_resource BEFORE UPDATE ON assets FOR EACH ROW EXECUTE FUNCTION stamp_resource()")


def downgrade() -> None:
    op.execute("DROP TRIGGER stamp_resource ON assets")
    op.execute("DROP TRIGGER guard_asset ON assets")
    op.execute("DROP FUNCTION guard_asset()")
    op.drop_index("ix_assets_workspace_created", table_name="assets")
    op.drop_table("assets")
