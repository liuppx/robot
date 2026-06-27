from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from hub.api import public_router
from hub.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Robot Control Plane", version="0.1.0")

    app.include_router(public_router)

    ui_dir = settings.ui_dir
    if ui_dir.exists():
        app.mount("/assets", StaticFiles(directory=ui_dir), name="assets")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(ui_dir / "index.html")

    return app


def main() -> None:
    settings = Settings()
    host, port = settings.bind_addr.split(":")
    uvicorn.run(
        "hub.app:create_app",
        factory=True,
        host=host,
        port=int(port),
        reload=False,
    )


if __name__ == "__main__":
    main()
