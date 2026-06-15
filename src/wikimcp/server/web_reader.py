"""
web_reader.py — Read-only web UI for wikimcp.

Serves wiki pages as HTML using FastAPI routes and Jinja2 templates.
Users authenticate via bearer token (Authorization header or ?token= query param).

Dependency note: requires the `markdown` library (pip install markdown).
Add `markdown>=3.5` to pyproject.toml dependencies.
"""

from __future__ import annotations

# import markdown  # dependency: pip install markdown>=3.5
import markdown  # noqa: F401 — requires `markdown` package (add to pyproject.toml)

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from wikimcp.server.auth import extract_token, validate_token
from wikimcp.server.router import resolve_wiki_dir
from wikimcp.user.config import load_config
from wikimcp.wiki.operations import list_pages, read_index, read_page, search_wiki

# Templates and static directories live next to this package's parent:
# src/wikimcp/server/web_reader.py  →  src/wikimcp/templates/
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_STATIC_DIR = _TEMPLATES_DIR / "static"

_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# Enable auto-escaping for all templates (XSS protection); rendered markdown
# HTML must be passed through the |safe filter explicitly.
_templates.env.autoescape = True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _unauthorized(templates: Jinja2Templates, request: Request) -> HTMLResponse:
    """Return a plain 401 HTML response."""
    return HTMLResponse(
        content="<h1>401 Unauthorized</h1><p>Invalid or missing token.</p>",
        status_code=401,
    )


def _not_found(page_path: str) -> HTMLResponse:
    """Return a plain 404 HTML response."""
    return HTMLResponse(
        content=f"<h1>404 Not Found</h1><p>Page <code>{page_path}</code> does not exist.</p>",
        status_code=404,
    )


def _render_markdown(text: str) -> str:
    """Convert markdown text to an HTML string."""
    return markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "toc", "nl2br"],
    )


def _build_page_tree(pages: list[str], username: str, token: str) -> list[dict]:
    """
    Build a hierarchical page tree structure for the sidebar.

    Returns a list of dicts with keys: path, label, depth, url, children.
    The list is flat (pre-order traversal) with depth tracked for CSS indentation.
    """
    tree: list[dict] = []
    for page_path in pages:
        parts = page_path.split("/")
        depth = len(parts) - 1
        label = parts[-1].replace(".md", "")
        url = f"/wiki/{username}/{page_path}?token={token}"
        tree.append({
            "path": page_path,
            "label": label,
            "depth": depth,
            "url": url,
        })
    return tree


def _breadcrumbs(path: str, username: str, token: str) -> list[dict]:
    """
    Build breadcrumb entries for a page path like 'topics/python.md'.

    Returns list of dicts with 'label' and 'url'. Last entry has no url (current page).
    """
    parts = path.replace(".md", "").split("/")
    crumbs: list[dict] = [{"label": "Home", "url": f"/wiki/{username}?token={token}"}]
    accumulated = ""
    for i, part in enumerate(parts):
        accumulated = f"{accumulated}/{part}" if accumulated else part
        is_last = (i == len(parts) - 1)
        if is_last:
            crumbs.append({"label": part, "url": None})
        else:
            # intermediate directories are not clickable pages (no .md)
            crumbs.append({"label": part, "url": None})
    return crumbs


def _auth_check(
    request: Request,
    username: str,
    config: dict,
) -> Optional[str]:
    """
    Extract and validate the token from the request.

    Returns the validated username if valid and matches the requested username,
    or None if authentication fails.
    """
    token = extract_token(request)
    if not token:
        return None
    validated_user = validate_token(token, config)
    if validated_user != username:
        return None
    return token


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_web_reader(config_path: Path) -> APIRouter:
    """
    Create and return a FastAPI APIRouter for the read-only web reader.

    The router mounts static file serving and registers these routes:
      GET /wiki/{username}                  — wiki home (renders index.md)
      GET /wiki/{username}/_search?q=...    — search results page
      GET /wiki/{username}/{path:path}      — render any wiki page

    All routes require a valid bearer token matching the requested username,
    either via ?token= query param or Authorization: Bearer header.
    """
    config_path = Path(config_path)
    router = APIRouter()

    # ------------------------------------------------------------------
    # Helper: load config fresh per request so token rotations take effect
    # ------------------------------------------------------------------
    def _get_config() -> dict:
        return load_config(config_path)

    # ------------------------------------------------------------------
    # GET /wiki/{username}  — home page (index.md)
    # ------------------------------------------------------------------
    @router.get("/wiki/{username}", response_class=HTMLResponse)
    async def wiki_home(username: str, request: Request) -> HTMLResponse:
        config = _get_config()
        token = _auth_check(request, username, config)
        if token is None:
            return _unauthorized(_templates, request)

        try:
            wiki_dir = resolve_wiki_dir(username, config)
        except (KeyError, ValueError):
            return _not_found("index.md")

        try:
            md_content = read_index(wiki_dir)
        except FileNotFoundError:
            md_content = "# Welcome\n\nNo index page yet."

        html_content = _render_markdown(md_content)

        try:
            pages = list_pages(wiki_dir)
        except Exception:
            pages = []

        page_tree = _build_page_tree(pages, username, token)

        return _templates.TemplateResponse(
            request=request,
            name="page.html",
            context={
                "request": request,
                "username": username,
                "token": token,
                "title": "Home",
                "page_path": "index.md",
                "html_content": html_content,
                "page_tree": page_tree,
                "breadcrumbs": [{"label": "Home", "url": None}],
            },
        )

    # ------------------------------------------------------------------
    # GET /wiki/{username}/_search?q=...  — search results
    # ------------------------------------------------------------------
    @router.get("/wiki/{username}/_search", response_class=HTMLResponse)
    async def wiki_search(username: str, request: Request, q: str = "") -> HTMLResponse:
        config = _get_config()
        token = _auth_check(request, username, config)
        if token is None:
            return _unauthorized(_templates, request)

        try:
            wiki_dir = resolve_wiki_dir(username, config)
        except (KeyError, ValueError):
            return _not_found("_search")

        results: list[dict] = []
        if q.strip():
            try:
                raw_results = search_wiki(wiki_dir, q.strip())
                # Attach URLs to each result
                for r in raw_results:
                    r["url"] = f"/wiki/{username}/{r['path']}?token={token}"
                results = raw_results
            except ValueError:
                results = []

        try:
            pages = list_pages(wiki_dir)
        except Exception:
            pages = []

        page_tree = _build_page_tree(pages, username, token)

        return _templates.TemplateResponse(
            request=request,
            name="search.html",
            context={
                "request": request,
                "username": username,
                "token": token,
                "title": "Search",
                "query": q,
                "results": results,
                "page_tree": page_tree,
                "breadcrumbs": [
                    {"label": "Home", "url": f"/wiki/{username}?token={token}"},
                    {"label": "Search", "url": None},
                ],
            },
        )

    # ------------------------------------------------------------------
    # GET /wiki/{username}/{path:path}  — any wiki page
    # Note: _search route must be registered BEFORE this catch-all.
    # ------------------------------------------------------------------
    @router.get("/wiki/{username}/{path:path}", response_class=HTMLResponse)
    async def wiki_page(username: str, path: str, request: Request) -> HTMLResponse:
        config = _get_config()
        token = _auth_check(request, username, config)
        if token is None:
            return _unauthorized(_templates, request)

        try:
            wiki_dir = resolve_wiki_dir(username, config)
        except (KeyError, ValueError):
            return _not_found(path)

        # Normalise: if no extension, treat as markdown page
        if not path.endswith(".md"):
            path = f"{path}.md"

        try:
            md_content = read_page(wiki_dir, path)
        except FileNotFoundError:
            return _not_found(path)
        except ValueError:
            # Invalid path (e.g. contains ..)
            return HTMLResponse(
                content="<h1>400 Bad Request</h1><p>Invalid page path.</p>",
                status_code=400,
            )

        html_content = _render_markdown(md_content)

        try:
            pages = list_pages(wiki_dir)
        except Exception:
            pages = []

        page_tree = _build_page_tree(pages, username, token)
        breadcrumbs = _breadcrumbs(path, username, token)

        # Page title: last segment without .md extension
        title = path.split("/")[-1].replace(".md", "")

        return _templates.TemplateResponse(
            request=request,
            name="page.html",
            context={
                "request": request,
                "username": username,
                "token": token,
                "title": title,
                "page_path": path,
                "html_content": html_content,
                "page_tree": page_tree,
                "breadcrumbs": breadcrumbs,
            },
        )

    return router


def mount_static(app) -> None:
    """
    Mount the static files directory onto a FastAPI app instance.

    Call this from the main app setup after creating the router.
    Example:
        app = FastAPI()
        router = create_web_reader(config_path)
        app.include_router(router)
        mount_static(app)
    """
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
