"""principal OAuth authorization and claimed token operations

Revision ID: 7e4f5c2b63b3
Revises: c9a942c61130
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "7e4f5c2b63b3"
down_revision = "c9a942c61130"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_connections_auth"), "connections", type_="check")
    op.create_check_constraint(
        op.f("ck_connections_auth"), "connections", "auth IN ('none', 'bearer', 'headers', 'oauth')"
    )
    op.create_table(
        "connection_authorizations",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("principal_id", sa.String(length=72), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("credential", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("generation", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("operation_id", sa.String(length=72), nullable=True),
        sa.Column("operation_kind", sa.String(), nullable=True),
        sa.Column("operation_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("oauth_state_hash", sa.String(length=64), nullable=True),
        sa.Column("redirect_uri", sa.String(), nullable=True),
        sa.Column("return_uri", sa.String(), nullable=True),
        sa.Column("failure", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "operation_kind IN ('exchange', 'refresh')", name=op.f("ck_connection_authorizations_operation_kind")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'revoked', 'reauthorization_required')",
            name=op.f("ck_connection_authorizations_status"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('revoked', 'reauthorization_required') OR credential IS NULL",
            name=op.f("ck_connection_authorizations_inactive_credential"),
        ),
        sa.CheckConstraint(
            "(operation_id IS NULL) = (operation_kind IS NULL) AND (operation_id IS NULL) = (operation_deadline IS NULL)",
            name=op.f("ck_connection_authorizations_operation_fields"),
        ),
        sa.CheckConstraint("generation >= 0", name=op.f("ck_connection_authorizations_generation")),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id", "connection_id"],
            ["connections.organization_id", "connections.workspace_id", "connections.id"],
            name=op.f("fk_connection_authorizations_organization_id_connections"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_connection_authorizations_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principals.id"], name=op.f("fk_connection_authorizations_principal_id_principals")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connection_authorizations")),
        sa.UniqueConstraint(
            "connection_id", "principal_id", name=op.f("uq_connection_authorizations_connection_id_principal_id")
        ),
        sa.UniqueConstraint("oauth_state_hash", name=op.f("uq_connection_authorizations_oauth_state_hash")),
    )
    op.create_index(
        "ix_connection_authorizations_deadline", "connection_authorizations", ["operation_deadline", "id"], unique=False
    )
    op.create_index(
        "ix_connection_authorizations_expiry", "connection_authorizations", ["expires_at", "id"], unique=False
    )

    op.execute(
        "CREATE TRIGGER stamp_resource BEFORE UPDATE ON connection_authorizations FOR EACH ROW EXECUTE FUNCTION stamp_resource()"
    )
    op.execute("""
        CREATE FUNCTION guard_connection_authorization() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW(NEW.id, NEW.organization_id, NEW.workspace_id, NEW.connection_id, NEW.principal_id, NEW.created_at)
                IS DISTINCT FROM ROW(OLD.id, OLD.organization_id, OLD.workspace_id, OLD.connection_id, OLD.principal_id, OLD.created_at)
                OR NEW.generation < OLD.generation
            THEN RAISE EXCEPTION 'authorization identity and generation cannot regress'; END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER guard_authorization BEFORE UPDATE ON connection_authorizations FOR EACH ROW EXECUTE FUNCTION guard_connection_authorization()"
    )


def downgrade() -> None:
    op.drop_index("ix_connection_authorizations_expiry", table_name="connection_authorizations")
    op.drop_index("ix_connection_authorizations_deadline", table_name="connection_authorizations")
    op.drop_table("connection_authorizations")
    op.execute("DROP FUNCTION guard_connection_authorization()")
    op.drop_constraint(op.f("ck_connections_auth"), "connections", type_="check")
    op.create_check_constraint(op.f("ck_connections_auth"), "connections", "auth IN ('none', 'bearer', 'headers')")
