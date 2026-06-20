from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import admin, admin_api, events, feishu, health, metrics, sources
from app.core.config import settings
from app.core.i18n import preferred_language, translate_error
from app.core.logging import configure_logging

configure_logging(settings.log_level)


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="0.1.0", debug=False)

    @app.exception_handler(StarletteHTTPException)
    async def localized_http_exception_handler(request, exc):
        language = preferred_language(request.headers.get("accept-language"))
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": translate_error(exc.detail, language=language)},
            headers=getattr(exc, "headers", None),
        )

    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["X-Admin-Token", "Content-Type"],
            allow_credentials=False,
        )
    app.include_router(health.router)
    app.include_router(events.router)
    app.include_router(sources.router)
    app.include_router(admin.router)
    app.include_router(admin_api.router)
    app.include_router(feishu.router)
    app.include_router(metrics.router)
    return app


app = create_app()
