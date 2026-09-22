"""Campaign Studio server — FastAPI app, mounts, and the boot entry point.

`python studio/server.py` starts uvicorn on 127.0.0.1:8765 (env STUDIO_PORT
overrides) and prints the URL (D2, D6: localhost only, no auth).

Every error response is {"error": "..."} with a 4xx/5xx status: both
HTTPException and request-validation errors are rewritten below.
"""

import os
import sys
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

app = FastAPI(title="Campaign Studio", version="0.1.0")

for r in (campaigns, system, maps, simulation, lore, review):
    app.include_router(r.router)


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
STATIC_DIR = Path(__file__).resolve().parent / "static"
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
    port = int(os.environ.get("STUDIO_PORT", "8765"))
    url = f"http://127.0.0.1:{port}"
    print(f"Campaign Studio serving at {url}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
