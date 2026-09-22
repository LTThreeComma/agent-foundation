"""indexed workspace resolution and conversation collections

Revision ID: 78a48cf98d38
Revises: d42925a47446
"""

from alembic import op

revision = "78a48cf98d38"
down_revision = "d42925a47446"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_runs_thread_created", "runs", ["thread_id", "created_at", "id"], unique=False)
    op.create_index("ix_sessions_workspace_created", "sessions", ["workspace_id", "created_at", "id"], unique=False)
    op.create_index(
        "ix_threads_session_created", "threads", ["workspace_id", "session_id", "created_at", "id"], unique=False
    )
    op.create_index("ix_threads_workspace_created", "threads", ["workspace_id", "created_at", "id"], unique=False)
    op.create_index("ix_workspaces_key", "workspaces", ["key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_workspaces_key", table_name="workspaces")
    op.drop_index("ix_threads_workspace_created", table_name="threads")
    op.drop_index("ix_threads_session_created", table_name="threads")
    op.drop_index("ix_sessions_workspace_created", table_name="sessions")
    op.drop_index("ix_runs_thread_created", table_name="runs")
