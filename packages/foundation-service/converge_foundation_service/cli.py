"""Command-line entry point for serving and database management."""

from __future__ import annotations

from pathlib import Path

import click
import uvicorn
from alembic import command
from alembic.config import Config

from converge_foundation_service.log import configure_logging
from converge_foundation_service.settings import ServiceRole, get_settings


def _alembic_config() -> Config:
    package_dir = Path(__file__).resolve().parent
    return Config(str(package_dir / "alembic.ini"))


@click.group()
def main() -> None:
    """Run and operate foundation-service."""


@main.command()
@click.option("--host", default=None, help="Bind host; defaults to FOUNDATION_HOST.")
@click.option("--role", default=None, type=click.Choice([role.value for role in ServiceRole]))
def serve(host: str | None, role: str | None) -> None:
    """Start the FastAPI service."""
    from converge_foundation_service.app import create_app

    settings = get_settings()
    if role is not None:
        settings = settings.model_copy(update={"role": ServiceRole(role)})
    configure_logging(settings)
    uvicorn.run(
        create_app(settings),
        host=host or settings.host,
        port=settings.port,
        log_config=None,
        workers=1,
    )


@main.group()
def db() -> None:
    """Inspect and migrate the service database."""
    configure_logging(get_settings())


@db.command()
@click.option("--revision", default="head", show_default=True)
def upgrade(revision: str) -> None:
    """Apply migrations through the requested revision."""
    command.upgrade(_alembic_config(), revision)
    click.echo(f"Database upgraded to {revision}.")


@db.command()
@click.option("--revision", default="-1", show_default=True)
def downgrade(revision: str) -> None:
    """Downgrade one revision or to an explicitly reviewed target."""
    command.downgrade(_alembic_config(), revision)
    click.echo(f"Database downgraded to {revision}.")


@db.command()
@click.option("--check-heads", is_flag=True, help="Fail unless every migration head is applied.")
def current(check_heads: bool) -> None:
    """Show the current database revision."""
    command.current(_alembic_config(), verbose=True, check_heads=check_heads)


@db.command()
def history() -> None:
    """Show migration history."""
    command.history(_alembic_config(), verbose=True)


@db.command()
@click.argument("message")
def migrate(message: str) -> None:
    """Autogenerate a revision; repository contributors should use make db-migrate."""
    command.revision(_alembic_config(), message=message, autogenerate=True)
    click.echo(f"Migration generated: {message}")
