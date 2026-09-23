"""Installed entry point; configure logging once and select explicit operations."""

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import click
import uvicorn
from a13n_logging import LogFormat, configure_logging
from pydantic import SecretStr

from a13n_service.app import build_app, check_schema
from a13n_service.distribution import OSS
from a13n_service.infra.db import Storage
from a13n_service.migrations.runner import check, generate, heads, upgrade
from a13n_service.settings import Settings, load_settings
from a13n_service.tenancy.bootstrap import BootstrapInput, bootstrap


@click.group()
@click.version_option(package_name="a13n-service")
@click.option("--config", type=click.Path(exists=True, path_type=Path))
@click.pass_context
def main(ctx: click.Context, config: Path | None) -> None:
    configure_logging(logger_names=("a13n_service", "a13n_harness"), log_format=LogFormat.json)
    try:
        ctx.obj = load_settings(config)
    except (ValueError, OSError) as error:
        # Validation errors can contain input values, including deployment secrets.
        raise click.ClickException(f"Invalid Service configuration ({type(error).__name__})") from None


@main.command()
@click.option("--role", type=click.Choice(["all", "control", "worker"]), default="all")
@click.pass_obj
def run(settings: Settings, role: str) -> None:
    if role not in ("all", "control", "worker"):
        raise click.ClickException("Invalid role")
    uvicorn.run(
        build_app(role=role, settings=settings),
        host=settings.server.host,
        port=settings.server.port,
        timeout_graceful_shutdown=settings.server.shutdown_timeout,
        log_config=None,
        access_log=False,
        ssl_certfile=str(settings.server.tls_certificate) if settings.server.tls_certificate else None,
        ssl_keyfile=str(settings.server.tls_key) if settings.server.tls_key else None,
    )


@main.command()
@click.option("--generate", "message", help="Autogenerate a revision against a disposable database.")
@click.option("--check", "check_only", is_flag=True, help="Verify migration/metadata parity without changing schema.")
@click.pass_obj
def migrate(settings: Settings, message: str | None, check_only: bool) -> None:
    if message and check_only:
        raise click.UsageError("--generate and --check are mutually exclusive")
    if message:
        generate(settings.database, OSS, message)
    elif check_only:
        check(settings.database, OSS)
    else:
        upgrade(settings.database, OSS)


@main.command("bootstrap")
@click.option("--email", required=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
@click.pass_obj
def bootstrap_command(settings: Settings, email: str, password: str) -> None:
    async def create() -> None:
        storage = Storage(
            settings.database.url.get_secret_value(),
            pool_size=settings.database.pool_size,
            connect_timeout=settings.database.connect_timeout,
            statement_timeout=settings.database.statement_timeout,
        )
        try:
            await check_schema(storage, heads(OSS))
            result = await bootstrap(storage, BootstrapInput(email=email, password=SecretStr(password)))
            click.echo(json.dumps(asdict(result)))
        finally:
            await storage.close()

    try:
        asyncio.run(create())
    except ValueError:
        raise click.ClickException("Bootstrap refused: invalid email/password or Service already initialized") from None
