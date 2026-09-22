"""Campaign Studio server — FastAPI app, mounts, and the boot entry point.

`python studio/server.py` starts uvicorn on 127.0.0.1:8765 (env STUDIO_PORT
overrides) and prints the URL (D2, D6: localhost only, no auth).

Hardening (this wave):
- Host allowlist middleware: only loopback Host values (with or without the
  bound port), the TestClient, and STUDIO_EXTRA_HOSTS are served — a DNS-
  rebinding page (attacker domain resolving at 127.0.0.1) gets 403
  {"error": "unknown host header"}.
- Rotating file log at studio/logs/studio.log (1 MB x 5): every "studio.*"
  logger — app modules and job progress lines (jobs.py mirrors log() there).
  Uvicorn's access log stays console-only.
- On boot (lifespan), a left-behind week-run flag triggers the engine-home
  restore (routers/simulation.restore_interrupted_week).

Every error response is {"error": "..."} with a 4xx/5xx status: both
HTTPException and request-validation errors are rewritten below.
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent))

import spine  # noqa: E402
from routers import campaigns, lore, maps, review, simulation, system  # noqa: E402

STUDIO_DIR = Path(__file__).resolve().parent
PORT = int(os.environ.get("STUDIO_PORT", "8765"))

# ------------------------------------------------------------------- logging


def _setup_logging() -> None:
    """Attach the rotating file handler to the "studio" logger tree
    (idempotent — importing twice must not duplicate handlers)."""
    logs_dir = STUDIO_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("studio")
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        handler = RotatingFileHandler(
            logs_dir / "studio.log", maxBytes=1_000_000, backupCount=5,
            encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.propagate = False  # uvicorn's console handlers stay untouched


_setup_logging()
log = logging.getLogger("studio.server")


# ------------------------------------------------------------------ the app


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Boot repair: a .week-run-flag left by a crashed week run restores the
    engine home from its backup (see routers/simulation.restore_interrupted_week)."""
    simulation.restore_interrupted_week()
    log.info("campaign studio up — http://127.0.0.1:%d", PORT)
    yield


app = FastAPI(title="Campaign Studio", version="0.1.0", lifespan=lifespan)

for r in (campaigns, system, maps, simulation, lore, review):
    app.include_router(r.router)


# loopback-only Host allowlist (DNS-rebinding protection): a browser page
# from e.g. attacker.com (rebinding to 127.0.0.1) sends Host: attacker.com
# and is refused before any route runs.
_LOCAL_NAMES = ("127.0.0.1", "localhost", "[::1]")


def _host_allowed(host: str, port: int = PORT) -> bool:
    h = (host or "").strip().lower()
    if not h:
        return False
    if h == "testserver":  # starlette TestClient default
        return True
    for extra in os.environ.get("STUDIO_EXTRA_HOSTS", "").split(","):
        if extra.strip() and h == extra.strip().lower():
            return True
    if h.startswith("["):  # [::1] or [::1]:<port>
        if h == "[::1]":
            return True
        return h.startswith("[::1]:") and h.rpartition(":")[2] == str(port)
    name, sep, hport = h.rpartition(":")
    if sep:
        return name in _LOCAL_NAMES and hport == str(port)
    return h in _LOCAL_NAMES


@app.middleware("http")
async def host_allowlist(request: Request, call_next):
    if not _host_allowed(request.headers.get("host", "")):
        return JSONResponse({"error": "unknown host header"}, status_code=403)
    return await call_next(request)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code,
                        headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
    msg = first.get("msg", "invalid request")
    detail = f"{loc}: {msg}" if loc else msg
    return JSONResponse({"error": detail}, status_code=422)


# Campaign files (map SVGs, chronicle markdown, geojson) — read-only intent,
# served straight off the campaigns root. StaticFiles refuses path escapes
# (404 outside the tree). Created on boot so a fresh clone can serve [].
campaigns_root = spine.campaigns_root()
campaigns_root.mkdir(parents=True, exist_ok=True)
app.mount("/campaign-file", StaticFiles(directory=str(campaigns_root)),
          name="campaign-file")

# The one page (C's static/); mounted last so /api routes win.
STATIC_DIR = STUDIO_DIR / "static"
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True),
              name="static")
else:
    @app.get("/", response_class=HTMLResponse)
    async def placeholder():
        return ("<!doctype html><title>Campaign Studio</title>"
                "<p>Campaign Studio API is up; the page (studio/static/) "
                "is not built yet. Contract: <code>/API.md</code></p>")


if __name__ == "__main__":
    url = f"http://127.0.0.1:{PORT}"
    print(f"Campaign Studio serving at {url}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
