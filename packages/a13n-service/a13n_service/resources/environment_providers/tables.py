"""Environment provider rows; the shared provider shape is `ProviderColumns`."""

from a13n_service.infra.db import Base
from a13n_service.resources.provider_columns import ProviderColumns


class EnvironmentProviderRow(ProviderColumns, Base):
    __tablename__ = "environment_providers"
