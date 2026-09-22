"""Alembic runs only with the caller's explicit composed metadata and connection."""

from alembic import context

context.configure(
    connection=context.config.attributes["connection"],
    target_metadata=context.config.attributes["metadata"],
    compare_type=True,
    compare_server_default=True,
)
with context.begin_transaction():
    context.run_migrations()
