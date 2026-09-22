"""ASGI entry point for a13n Service."""

from a13n_service_legacy.app import create_app

app = create_app()
