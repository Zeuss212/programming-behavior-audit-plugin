"""HTTP application factory for the classroom synchronization service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .application import ClassroomServices
from .config import Settings
from .errors import ClassroomServiceError
from .routers.authoring import router as authoring_router
from .routers.events import router as events_router
from .routers.materials import router as materials_router
from .routers.plans import router as plans_router
from .routers.plugin import router as plugin_router
from .routers.student import router as student_router
from .routers.suggestions import router as suggestions_router
from .routers.teacher import router as teacher_router


def create_app(
    settings: Settings,
    *,
    classroom_services: ClassroomServices | None = None,
) -> FastAPI:
    """Build an app whose readiness is safe to expose to a load balancer."""

    services_closed = False

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        nonlocal services_closed
        try:
            yield
        finally:
            if (
                not services_closed
                and classroom_services is not None
                and classroom_services.shutdown is not None
            ):
                services_closed = True
                classroom_services.shutdown()

    app = FastAPI(title="Classroom Sync", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(ClassroomServiceError)
    def classroom_service_error(
        _request: Request, error: ClassroomServiceError
    ) -> JSONResponse:
        error_payload: dict[str, object] = {
            "code": error.code,
            "message": "课堂服务请求未能完成。",
            "retryable": error.retryable,
            "request_id": _request.headers.get("X-Request-ID") or str(uuid4()),
        }
        if error.details is not None:
            error_payload["details"] = error.details
        return JSONResponse(
            status_code=error.status_code,
            content={
                "schema_version": 1,
                "error": error_payload,
            },
        )

    @app.exception_handler(RequestValidationError)
    def request_validation_error(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "schema_version": 1,
                "error": {
                    "code": "classroom_request_validation_failed",
                    "message": "课堂服务请求未能完成。",
                    "retryable": False,
                    "request_id": _request.headers.get("X-Request-ID") or str(uuid4()),
                },
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
        app.include_router(authoring_router)
        app.include_router(plans_router)
        app.include_router(plugin_router)
        app.include_router(student_router)
        app.include_router(teacher_router)
        app.include_router(suggestions_router)
        app.include_router(events_router)
        app.include_router(materials_router)

    return app
