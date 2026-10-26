"""Interactive API docs, served entirely by this API.

FastAPI's default docs page loads Swagger UI from a CDN and starts it with an
inline script. This page instead uses the pinned copy vendored in
``api/static/swagger-ui`` and an external init script, so its
Content-Security-Policy allows scripts from this origin only. Swagger UI sets
inline style attributes, hence ``'unsafe-inline'`` for styles. URLs are
relative, so the page works at ``/docs`` and behind the web proxy at
``/api/docs``.
"""

from importlib.resources import files
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse
from starlette.staticfiles import StaticFiles

ASSETS_PATH = "/docs-assets"
ASSETS_DIR = Path(str(files("hotel_curator.api") / "static" / "swagger-ui"))
DOCS_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)
DOCS_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>hotel-curator API docs</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="docs-assets/swagger-ui.css">
</head>
<body>
<div id="swagger-ui"></div>
<script src="docs-assets/swagger-ui-bundle.js"></script>
<script src="docs-assets/docs-init.js"></script>
</body>
</html>
"""

router = APIRouter(include_in_schema=False)


@router.get("/docs", response_class=HTMLResponse)
def docs_page() -> HTMLResponse:
    """The Swagger UI page for this API's OpenAPI schema."""
    return HTMLResponse(
        DOCS_HTML, headers={"Content-Security-Policy": DOCS_CSP}
    )


def mount_docs(app: FastAPI) -> None:
    """Add the docs page and its static assets to ``app``."""
    app.include_router(router)
    app.mount(ASSETS_PATH, StaticFiles(directory=ASSETS_DIR), name="docs")
