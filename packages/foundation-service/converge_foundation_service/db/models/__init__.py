"""ORM metadata exported to application code and Alembic.

Import every concrete model in this module so Alembic autogenerate sees it.
"""

from converge_foundation_service.db.models.base import Base

__all__ = ["Base"]
