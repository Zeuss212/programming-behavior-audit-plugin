"""HTTP application factory for the classroom synchronization service."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import Settings


def create_app(settings: Settings) -> FastAPI:
    """Build an app whose readiness is safe to expose to a load balancer."""

    app = FastAPI(title="Classroom Sync", version="0.1.0")

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready", response_model=None)
    def ready() -> JSONResponse | dict[str, str]:
        if settings.database_url is None:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "dependency_unavailable",
                        "message": "同步服务依赖尚未就绪。",
                    }
                },
            )
        return {"status": "ready"}

    return app
