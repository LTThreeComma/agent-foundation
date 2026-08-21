"""Environment-configured ASGI application for external servers."""

from converge_foundation_service.app import create_app

app = create_app()
