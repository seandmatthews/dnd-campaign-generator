"""Campaign Studio server — FastAPI app, mounts, and the boot entry point.

`python studio/server.py` starts uvicorn on 127.0.0.1:8765 (env STUDIO_PORT
overrides) and prints the URL (D2, D6: localhost only, no auth).

Hardening (this wave):
- Host allowlist middleware: only loopback Host values (with or without the
  bound port), the TestClient, and STUDIO_EXTRA_HOSTS are served — a DNS-
  rebinding page (attacker domain resolving at 127.0.0.1) gets 403
  {"error": "unknown host header"}. Mutating methods (POST/PUT/PATCH/DELETE)
  must additionally prove same-origin (exact loopback Origin, or
  Sec-Fetch-Site: same-origin) or they get 403
  {"error": "cross-site request refused"} — a browser sends the real
  loopback Host on cross-site POSTs too, so the Host check alone answers
  rebinding but not a hostile page's no-preflight POSTs (ticket 61).
- Rotating file log (1 MB x 5) at <data root>/logs/studio.log
  (STUDIO_LOG_FILE overrides): every "studio.*" logger — app modules and
  job progress lines (jobs.py mirrors log() there). Uvicorn's access log
  stays console-only.
- On boot (lifespan), a left-behind week-run flag triggers the engine-home
  restore (routers/simulation.restore_interrupted_week), and legacy
  (pre-campaign-data) data dirs trigger a loud banner pointing at
  `python studio/doctor.py --migrate-data-root` — nothing migrates itself
  (ticket 35).

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
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent))

import publication
import proposal_flows
import sessions
import spine
from routers import (
    campaigns,
    knowledge,
    lore,
    maps,
    places,
    quests,
    review,
    simulation,
    system,
    table,
)
from routers import sessions as sessions_router
import engines  # boot-time engine-home restore lives with the home manager

STUDIO_DIR = Path(__file__).resolve().parent
PORT = int(os.environ.get("STUDIO_PORT", "8765"))

# ------------------------------------------------------------------- logging


def _log_file_path() -> Path:
    """Where the rotating log lives: $STUDIO_LOG_FILE if set (absolute, or
    relative to the studio dir), else the data root's logs/ dir
    (campaign-data/logs — ticket 35). Tests set it to their own temp file
    before importing this module so a test run never rotates the user's
    active log (rollover of a file held open by the live server fails on
    Windows with WinError 32)."""
    configured = os.environ.get("STUDIO_LOG_FILE")
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else STUDIO_DIR / path
    return spine.data_root() / "logs" / "studio.log"


def _setup_logging() -> None:
    """Attach the rotating file handler to the "studio" logger tree
    (idempotent — importing twice must not duplicate handlers)."""
    log_path = _log_file_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("studio")
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        handler = RotatingFileHandler(
            log_path, maxBytes=1_000_000, backupCount=5,
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
    """Boot repair, before serving: a .week-run-flag left by a crashed week
    run restores the engine home (engines.restore_interrupted_week);
    open publication transactions roll forward or park as conflicts
    (publication.recover_campaign); legacy (pre-campaign-data) data dirs
    trigger the migration banner — nothing moves on its own (ticket 35)."""
    engines.restore_interrupted_week()
    legacy = engines.legacy_data_dirs()
    if legacy:
        log.warning(
            "LEGACY DATA LOCATIONS found outside %s: %s — your campaigns "
            "are not visible until they are moved. Run  python "
            "studio/doctor.py --migrate-data-root  to migrate (refused "
            "while anything is running); nothing is moved automatically.",
            spine.data_root(),
            ", ".join(str(old) for old, _ in legacy))
    for camp in spine.list_campaigns():
        try:
            recovered = publication.recover_campaign(camp["name"])
        except Exception as e:  # noqa: BLE001 — one bad campaign boots alone
            log.error("publication recovery failed for %s: %s", camp["name"], e)
            continue
        if recovered["recovered"]:
            log.info("publication recovery for %s: rolled forward %s",
                     camp["name"], ", ".join(recovered["recovered"]))
        if recovered["conflicts"]:
            log.warning("publication recovery for %s: %s parked as conflict — "
                        "resolve before approving in that campaign",
                        camp["name"], ", ".join(recovered["conflicts"]))
    log.info("campaign studio up — http://127.0.0.1:%d", PORT)
    yield


app = FastAPI(title="Campaign Studio", version="0.1.0", lifespan=lifespan)

for r in (campaigns, system, maps, simulation, lore, review, knowledge, places, quests, sessions_router, table):
    app.include_router(r.router)


@app.get("/tutorial", response_class=PlainTextResponse)
def tutorial():
    """The first-campaign walkthrough, served from docs/for-new-gms/ —
    the help modal links here; one file, never a stale copy."""
    path = STUDIO_DIR.parent / "docs" / "for-new-gms" / "TUTORIAL.md"
    if not path.is_file():
        raise StarletteHTTPException(
            404, "TUTORIAL.md is missing — it lives at "
                 "docs/for-new-gms/TUTORIAL.md")
    return PlainTextResponse(path.read_text(encoding="utf-8"))


@app.get("/atlas", response_class=PlainTextResponse)
def atlas():
    """The at-the-table walkthrough (ticket 46), served the same way as
    /tutorial — the table-setup card links here, and the bundle stages the
    file so installed copies serve the real thing."""
    path = STUDIO_DIR.parent / "docs" / "for-new-gms" / "ATLAS.md"
    if not path.is_file():
        raise StarletteHTTPException(
            404, "ATLAS.md is missing — it lives at "
                 "docs/for-new-gms/ATLAS.md")
    return PlainTextResponse(path.read_text(encoding="utf-8"))


# loopback-only Host allowlist (DNS-rebinding protection): a browser page
# from e.g. attacker.com (rebinding to 127.0.0.1) sends Host: attacker.com
# and is refused before any route runs.
_LOCAL_NAMES = ("127.0.0.1", "localhost", "[::1]")

# Cross-site mutations (ticket 61): a browser sets the real loopback Host on
# cross-origin requests too, so the allowlist above answers DNS rebinding
# but not forgery — any page can fire *simple* requests (no preflight: no
# custom headers, no JSON body) at no-body mutating endpoints. Mutating
# methods must therefore carry same-origin proof: an Origin exactly matching
# the bound loopback origin, or — the fallback for clients that omit Origin —
# Sec-Fetch-Site: same-origin. Browsers attach these themselves and page
# script can neither strip nor forge them; non-browser local clients (curl,
# scripts) are outside the threat model. GETs stay Host-only: a read leaks
# nothing but cached state. No CORS layer — the UI is same-origin.
_MUTATING_METHODS = ("POST", "PUT", "PATCH", "DELETE")


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


def _same_origin(origin: str, port: int = PORT) -> bool:
    o = (origin or "").strip().lower()
    return any(o == f"http://{name}:{port}" for name in _LOCAL_NAMES)


def _origin_allowed(headers, port: int = PORT) -> bool:
    """When Origin is present it decides (a foreign Origin paired with
    same-origin fetch metadata can only come from a non-browser client);
    without it, Sec-Fetch-Site: same-origin is the proof."""
    origin = headers.get("origin", "")
    if origin.strip():
        return _same_origin(origin, port)
    return headers.get("sec-fetch-site", "").strip().lower() == "same-origin"


@app.middleware("http")
async def host_allowlist(request: Request, call_next):
    host = request.headers.get("host", "")
    if not _host_allowed(host):
        return JSONResponse({"error": "unknown host header"}, status_code=403)
    if (request.method in _MUTATING_METHODS
            and host.strip().lower() != "testserver"):  # harness: no Origin
        if not _origin_allowed(request.headers):
            return JSONResponse(
                {"error": "cross-site request refused"}, status_code=403)
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


@app.exception_handler(spine.CampaignFileError)
async def campaign_file_error(request: Request, exc: spine.CampaignFileError):
    """A campaign file that does not parse is the DM's input problem, not a
    server fault: readable 422 naming the file, never an opaque 500."""
    return JSONResponse({"error": str(exc)}, status_code=422)


@app.exception_handler(proposal_flows.ProposalFlowError)
async def proposal_flow_error(request: Request,
                              exc: proposal_flows.ProposalFlowError):
    """The proposal service raises typed errors (status + detail) — the
    router stays a mapping-free caller and the CLI catches the same type."""
    return JSONResponse({"error": exc.detail}, status_code=exc.status)


@app.exception_handler(sessions.SessionConflict)
async def session_conflict_error(request: Request, exc):
    """Stale editor / racing writer / a pending proposal in the way."""
    return JSONResponse({"error": str(exc)}, status_code=409)


@app.exception_handler(sessions.SessionError)
async def session_error(request: Request, exc):
    """A session-store input problem (unresolved suggestion, refused
    batch, missing record)."""
    return JSONResponse({"error": str(exc)}, status_code=422)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    """The contract says every error response is {"error": ...} — without
    this, any unexpected exception (OSError from a reserved name, YAML
    PermissionError mid-read) leaks Starlette's plain-text 500."""
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"error": f"internal error: {type(exc).__name__}"},
                        status_code=500)


# Campaign files (map SVGs, chronicle markdown, geojson) — read-only intent,
# served straight off the campaigns root. StaticFiles refuses path escapes
# (404 outside the tree). Created on boot so a fresh clone can serve [].
class _NoDotGit(StaticFiles):
    """The campaigns root is its own git repo; .git (config, packed history)
    is nobody's business over HTTP — one deny at lookup, everything else
    serves exactly as before."""

    def lookup_path(self, path: str):
        # any depth: a nested repo at campaigns/<c>/<sub>/.git must not
        # serve either, so check every segment, not just the first two
        parts = path.replace("\\", "/").split("/")
        if any(p.lower() == ".git" for p in parts):
            return "", ""
        return super().lookup_path(path)


campaigns_root = spine.campaigns_root()
campaigns_root.mkdir(parents=True, exist_ok=True)
app.mount("/campaign-file", _NoDotGit(directory=str(campaigns_root)),
          name="campaign-file")


class _FreshStatic(StaticFiles):
    """StaticFiles + Cache-Control: no-cache, so the browser revalidates the
    ETag every load. Plain StaticFiles heuristic-caches the OLD file for tens
    of minutes — a fixed bug 'still reproduces' because the page never
    fetched the fix."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


# The one page (C's static/); mounted last so /api routes win.
STATIC_DIR = STUDIO_DIR / "static"
if STATIC_DIR.is_dir():
    app.mount("/", _FreshStatic(directory=str(STATIC_DIR), html=True),
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
