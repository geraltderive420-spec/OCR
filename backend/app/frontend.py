"""Serveur de l'interface sur son propre port : sert frontend/dist et relaie /api vers l'API.

    python -m app.frontend            (lancé et surveillé par python -m app.serve quand OCR_FRONTEND_PORT est défini)

Le navigateur ne parle qu'à ce serveur (même origine : cookies de session, CSP « 'self' » inchangés) ; il relaie /api,
sans rien stocker, vers l'API (OCR_API_PORT, en local). Les envois de fichiers sont relayés en flux, jamais en mémoire.
Si l'API est arrêtée, l'interface reste affichée et répond « service indisponible » au lieu de planter.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from app.core.config import get_settings

log = logging.getLogger("ocr.frontend")
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
# En-têtes propres à une liaison TCP : jamais relayés d'un côté à l'autre.
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "host"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'",
}


def upstream_url() -> str:
    s = get_settings()
    scheme = "https" if s.ssl_certfile and s.ssl_keyfile else "http"
    return f"{scheme}://127.0.0.1:{s.api_port}"


def create_app(upstream: str | None = None, transport: httpx.AsyncBaseTransport | None = None,
               dist: Path | None = None) -> FastAPI:
    base = upstream or upstream_url()
    root = dist or DIST

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # verify=False seulement vers 127.0.0.1 : le certificat interne est émis pour le nom du poste.
        client = httpx.AsyncClient(base_url=base, transport=transport, verify=False,
                                   timeout=httpx.Timeout(900.0, connect=5.0), follow_redirects=False)
        _app.state.client = client
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(title="Interface OCR", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(path: str, request: Request):
        client: httpx.AsyncClient = request.app.state.client
        headers = [(k, v) for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP]
        peer = request.client.host if request.client else "127.0.0.1"
        headers.append(("x-forwarded-for", peer))
        has_body = request.method in ("POST", "PUT", "PATCH") and (
            int(request.headers.get("content-length") or 0) > 0 or "transfer-encoding" in request.headers)
        try:
            req = client.build_request(request.method, f"/api/{path}", params=request.query_params.multi_items(),
                                       headers=headers, content=request.stream() if has_body else None)
            upstream_resp = await client.send(req, stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return JSONResponse({"detail": "Le service de traitement ne répond pas : réessayez dans un instant."},
                                status_code=503)
        except httpx.HTTPError as e:
            log.warning("relais en échec (%s)", type(e).__name__)
            return JSONResponse({"detail": "Erreur de communication avec le service de traitement."}, status_code=502)
        out = StreamingResponse(upstream_resp.aiter_raw(), status_code=upstream_resp.status_code,
                                background=BackgroundTask(upstream_resp.aclose))
        out.raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1"))  # plusieurs Set-Cookie possibles
                           for k, v in upstream_resp.headers.multi_items() if k.lower() not in HOP_BY_HOP]
        return out

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if not (root / "index.html").is_file():
            return Response("Interface non compilée (frontend/dist absent).", status_code=503, media_type="text/plain")
        f = (root / path).resolve()
        if path and f.is_file() and root.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(root / "index.html")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    s = get_settings()
    kwargs: dict = {}
    if s.ssl_certfile and s.ssl_keyfile and Path(s.ssl_certfile).is_file() and Path(s.ssl_keyfile).is_file():
        kwargs = {"ssl_certfile": str(s.ssl_certfile), "ssl_keyfile": str(s.ssl_keyfile)}
    uvicorn.run("app.frontend:app", host=s.frontend_host or s.api_host, port=s.frontend_port, server_header=False,
                log_level="info", **kwargs)


if __name__ == "__main__":
    main()
