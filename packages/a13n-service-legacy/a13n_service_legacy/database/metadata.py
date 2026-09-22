"""Canonical metadata registry for every a13n Service ORM model."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base for service-owned models included in the migration history."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def core_metadata() -> MetaData:
    """Assemble shared models in a fresh composition process."""

    # The distribution descriptor imports every service-owned domain explicitly.
    # Deliberately avoid module scanning or plugin discovery.
    from a13n_service_legacy.agent_configuration import models as configuration_models
    from a13n_service_legacy.agents import models as agent_models
    from a13n_service_legacy.assets import models as asset_models
    from a13n_service_legacy.connectivity.accounts import models as account_models
    from a13n_service_legacy.connectivity.accounts import target_models
    from a13n_service_legacy.connectivity.connectors import models as connector_models
    from a13n_service_legacy.connectivity.ingress import admission_models as ingress_admission_models
    from a13n_service_legacy.connectivity.mcp import models as mcp_models
    from a13n_service_legacy.connectivity.providers.github import polling_models
    from a13n_service_legacy.connectivity.transports import models as transport_models
    from a13n_service_legacy.durable_operations import models as durable_operations_models
    from a13n_service_legacy.environments import models as environment_models
    from a13n_service_legacy.environments import mount_models
    from a13n_service_legacy.gateway import models as gateway_models
    from a13n_service_legacy.hooks import models as hook_models
    from a13n_service_legacy.iam import models as iam_models
    from a13n_service_legacy.interactions import control_models as interaction_control_models
    from a13n_service_legacy.interactions import models as interaction_models
    from a13n_service_legacy.lifecycle import models as lifecycle_models
    from a13n_service_legacy.memory import behaviors as memory_behaviors
    from a13n_service_legacy.memory import models as memory_models
    from a13n_service_legacy.models import models as model_models
    from a13n_service_legacy.object_retention import models as object_retention_models
    from a13n_service_legacy.secrets import models as secret_models
    from a13n_service_legacy.skills import models as skill_models
    from a13n_service_legacy.subagents import models as subagent_models
    from a13n_service_legacy.web import models as web_models

    del (
        agent_models,
        configuration_models,
        asset_models,
        account_models,
        connector_models,
        durable_operations_models,
        environment_models,
        mount_models,
        gateway_models,
        hook_models,
        iam_models,
        interaction_control_models,
        interaction_models,
        ingress_admission_models,
        target_models,
        lifecycle_models,
        mcp_models,
        transport_models,
        polling_models,
        memory_models,
        memory_behaviors,
        model_models,
        object_retention_models,
        web_models,
        secret_models,
        skill_models,
        subagent_models,
    )
    return Base.metadata


def service_metadata() -> MetaData:
    """OSS artifact: shared models plus explicit Bot application contributions."""
    from a13n_service_legacy.bots.connectivity import models as bot_models
    from a13n_service_legacy.bots.memory import bindings, models, settings
    from a13n_service_legacy.bots.progress import models as progress_models
    from a13n_service_legacy.bots.routines import models as routine_models

    del bot_models, bindings, models, settings, progress_models, routine_models
    return core_metadata()
