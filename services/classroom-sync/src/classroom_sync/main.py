"""HTTP application factory for the classroom synchronization service."""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .application import ClassroomServices
from .config import Settings
from .errors import ClassroomServiceError
from .routers.events import router as events_router
from .routers.plans import router as plans_router
from .routers.plugin import router as plugin_router
from .routers.student import router as student_router
from .routers.teacher import router as teacher_router


def create_app(
    settings: Settings,
    *,
    classroom_services: ClassroomServices | None = None,
) -> FastAPI:
    """Build an app whose readiness is safe to expose to a load balancer."""

    app = FastAPI(title="Classroom Sync", version="0.1.0")

    @app.exception_handler(ClassroomServiceError)
    def classroom_service_error(
        _request: Request, error: ClassroomServiceError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "schema_version": 1,
                "error": {
                    "code": error.code,
                    "message": "课堂服务请求未能完成。",
                    "retryable": error.retryable,
                    "request_id": _request.headers.get("X-Request-ID") or str(uuid4()),
                }
            },
        )

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

    if classroom_services is not None:
        app.state.classroom_services = classroom_services
        app.include_router(plans_router)
        app.include_router(plugin_router)
        app.include_router(student_router)
        app.include_router(teacher_router)
        app.include_router(events_router)

    return app
