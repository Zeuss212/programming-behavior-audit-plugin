"""HTTP application factory for the classroom synchronization service."""

from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .application import ClassroomServices
from .config import Settings
from .errors import ClassroomServiceError
from .routers.events import router as events_router
from .routers.plans import router as plans_router
from .routers.plugin import router as plugin_router
from .routers.student import router as student_router
from .routers.suggestions import router as suggestions_router
from .routers.teacher import router as teacher_router

logger = logging.getLogger(__name__)


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
        request_id = _request.headers.get("X-Request-ID") or str(uuid4())
        logger.log(
            logging.WARNING if error.status_code >= 500 else logging.INFO,
            (
                "classroom_service_error method=%s path=%s status_code=%s "
                "error_code=%s retryable=%s request_id=%s"
            ),
            _request.method,
            _request.url.path,
            error.status_code,
            error.code,
            str(error.retryable).lower(),
            request_id,
            extra={
                "method": _request.method,
                "path": _request.url.path,
                "status_code": error.status_code,
                "error_code": error.code,
                "retryable": error.retryable,
                "request_id": request_id,
            },
        )
        return JSONResponse(
            status_code=error.status_code,
            content={
                "schema_version": 1,
                "error": {
                    "code": error.code,
                    "message": "课堂服务请求未能完成。",
                    "retryable": error.retryable,
                    "request_id": request_id,
                }
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
        app.include_router(plans_router)
        app.include_router(plugin_router)
        app.include_router(student_router)
        app.include_router(teacher_router)
        app.include_router(suggestions_router)
        app.include_router(events_router)

    return app
