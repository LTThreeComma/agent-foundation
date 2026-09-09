"""Keep the deterministic upstream alive while recovery tests crash Control."""

from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI

from .config import load_config
from .fixture_model import fixture_router
from .host import bearer_authenticator


def main():
    config = load_config()
    app = FastAPI()
    app.include_router(fixture_router(Path(config["workspace_root"]), bearer_authenticator(config)))

    @app.get("/readyz")
    async def ready():
        return {"status": "ready"}

    uvicorn.run(app, host="127.0.0.1", port=urlsplit(config["model_url"]).port)


if __name__ == "__main__":
    main()
