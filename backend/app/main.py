from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import admin, auth, documents
from app.core.config import DEFAULT_SECRET, get_settings
from app.core.db import init_engine

log = logging.getLogger("ocr.api")
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if s.secret_key == DEFAULT_SECRET:
        log.warning("OCR_SECRET_KEY par défaut : à changer avant toute mise en production.")
    for sub in ("uploads", "pages", "crops", "exports", "logs"):
        (s.data_dir / sub).mkdir(parents=True, exist_ok=True)
    init_engine()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="OCR PDF → DOCX", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # jamais de trace ni de contenu dans la réponse
        log.error("erreur non gérée sur %s (%s)", request.url.path, type(exc).__name__)
        return JSONResponse({"detail": "Erreur interne."}, status_code=500)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    for r in (auth.router, documents.router, admin.router):
        app.include_router(r, prefix="/api")

    if FRONTEND_DIST.is_dir():  # interface compilée servie par FastAPI (production)
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            f = (FRONTEND_DIST / path).resolve()
            if path and f.is_file() and FRONTEND_DIST.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()
