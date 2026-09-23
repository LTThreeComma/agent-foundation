"""managed connection authorization operations

Revision ID: 611cf188440e
Revises: 7e4f5c2b63b3
"""

from alembic import op

revision = "611cf188440e"
down_revision = "7e4f5c2b63b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic does not detect CHECK expression changes; review them explicitly.
    op.drop_constraint(op.f("ck_connections_auth"), "connections", type_="check")
    op.create_check_constraint(
        op.f("ck_connections_auth"), "connections", "auth IN ('none', 'bearer', 'headers', 'oauth', 'managed')"
    )
    op.drop_constraint(op.f("ck_connection_authorizations_operation_kind"), "connection_authorizations", type_="check")
    op.create_check_constraint(
        op.f("ck_connection_authorizations_operation_kind"),
        "connection_authorizations",
        "operation_kind IN ('exchange', 'refresh', 'setup', 'complete', 'revoke')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_connection_authorizations_operation_kind"), "connection_authorizations", type_="check")
    op.create_check_constraint(
        op.f("ck_connection_authorizations_operation_kind"),
        "connection_authorizations",
        "operation_kind IN ('exchange', 'refresh')",
    )
    op.drop_constraint(op.f("ck_connections_auth"), "connections", type_="check")
    op.create_check_constraint(
        op.f("ck_connections_auth"), "connections", "auth IN ('none', 'bearer', 'headers', 'oauth')"
    )
