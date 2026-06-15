"""
mcp_server.py — FastMCP server with all 9 wiki MCP tools.

Provides two factory functions:
  create_local_server(wiki_dir)    — single-user, no auth, stdio or HTTP
  create_server_mode(config_path)  — multi-user HTTP with bearer-token auth

In server mode, each request includes a bearer token that is validated to
resolve the calling user. The request is processed against that user's wiki.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ..wiki import operations
from ..wiki import retrieval
from ..wiki.git_layer import push_auto_remotes
from .auth import extract_token, validate_token, update_last_active
from .router import resolve_wiki_dir, get_auto_push_remotes


# ---------------------------------------------------------------------------
# Transport security (streamable-HTTP DNS-rebinding protection)
# ---------------------------------------------------------------------------

_DEFAULT_ALLOWED_HOST_NAMES = ["localhost", "127.0.0.1"]


def http_transport_security(
    extra_hosts: list[str] | None = None,
    *,
    disable_protection: bool = False,
) -> TransportSecuritySettings:
    """Build TransportSecuritySettings for the streamable-HTTP transport.

    The MCP SDK's DNS-rebinding protection defaults to a localhost-only Host
    allowlist and returns HTTP 421 "Invalid Host header" for anything else
    (e.g. host.docker.internal from a Docker-hosted client). This helper keeps
    a secure localhost-only default while allowing operators to add trusted
    hosts (--allowed-host) or disable the check on an isolated network
    (--allow-any-host).

    Each host is registered both bare ("localhost") and with a port wildcard
    ("localhost:*"), since the SDK matches the Host header exactly or via a
    trailing ":*" wildcard. Matching http/https origins are also allowed for
    browser-based clients; origin validation passes when no Origin header is
    present (the typical server-side MCP client case).
    """
    if disable_protection:
        # Only safe on a trusted/isolated network (e.g. firewalled, VPN-only).
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    names = list(_DEFAULT_ALLOWED_HOST_NAMES) + list(extra_hosts or [])
    allowed_hosts: list[str] = []
    allowed_origins: list[str] = []
    for name in names:
        allowed_hosts += [name, f"{name}:*"]
        for scheme in ("http", "https"):
            allowed_origins += [f"{scheme}://{name}", f"{scheme}://{name}:*"]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def _set_server_version(mcp: FastMCP) -> None:
    """Report wikimcp's own version in serverInfo (not the MCP SDK's).

    FastMCP does not pass a version to the underlying low-level Server, so it
    falls back to the installed ``mcp`` package version in
    ``create_initialization_options``. Set it explicitly to wikimcp's version.
    """
    from .. import __version__

    mcp._mcp_server.version = __version__


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_wiki_info(info: dict) -> str:
    """Format wiki_info dict as a readable string."""
    parts = [
        f"Wiki root: {info['wiki_root']}",
        f"Pages: {info['page_count']}",
        f"Log entries: {info['log_entries']}",
    ]
    schema = info.get("schema", "")
    if schema:
        parts.append("")
        parts.append("--- Wiki Schema & Workflow Instructions ---")
        parts.append(schema)
    return "\n".join(parts)


def _format_search_results(results: list) -> str:
    """Format search_wiki results as a readable string."""
    if not results:
        return "No matches found."

    lines = []
    for result in results:
        lines.append(f"### {result['path']}")
        for match in result["matches"]:
            lines.append(f"  Line {match['line_number']}: {match['line']}")
    return "\n".join(lines)


def _format_page_results(results: list) -> str:
    """Format ranked page retrieval results as a readable string."""
    if not results:
        return "No relevant pages found."

    lines = []
    for i, result in enumerate(results, start=1):
        lines.append(f"{i}. {result['path']} (score: {result['score']:.4f})")
        lines.append(f"   Title: {result['title']}")
        if result.get("matched_fields"):
            lines.append(f"   Matched: {', '.join(result['matched_fields'])}")
        if result.get("snippet"):
            lines.append(f"   Snippet: {result['snippet']}")
    return "\n".join(lines)


def _format_push_warnings(warnings: list) -> str:
    """Format push warnings for inclusion in tool responses."""
    if not warnings:
        return ""
    return "\n\n" + "\n".join(warnings)


# ---------------------------------------------------------------------------
# Local server factory (single-user, no auth)
# ---------------------------------------------------------------------------

def create_local_server(
    wiki_dir: Path,
    allowed_hosts: list[str] | None = None,
    disable_host_check: bool = False,
) -> FastMCP:
    """
    Create a FastMCP server for local/single-user mode.

    All tool calls go directly to the fixed wiki_dir. No authentication.
    Suitable for stdio transport (Claude Desktop, LM Studio, Gemini CLI)
    or local HTTP.

    ``allowed_hosts`` adds trusted Host header values for the streamable-HTTP
    transport; ``disable_host_check`` turns off DNS-rebinding protection
    entirely (only safe on an isolated network). Both are ignored for stdio.
    """
    wiki_dir = Path(wiki_dir)
    mcp = FastMCP(
        "wikimcp",
        transport_security=http_transport_security(
            allowed_hosts, disable_protection=disable_host_check
        ),
    )
    _set_server_version(mcp)

    @mcp.tool()
    def wiki_info() -> str:
        """Return wiki stats and schema instructions. MUST be called at the start of every session."""
        try:
            info = operations.wiki_info(wiki_dir)
            return _format_wiki_info(info)
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def read_index() -> str:
        """Read index.md — the master catalog and starting point for any query."""
        try:
            return operations.read_index(wiki_dir)
        except FileNotFoundError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def update_index(content: str) -> str:
        """Overwrite index.md with new content and auto-commit."""
        try:
            operations.update_index(wiki_dir, content)
            return "index.md updated successfully."
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def write_page(path: str, content: str) -> str:
        """Create or overwrite a wiki page at the given path and auto-commit."""
        try:
            operations.write_page(wiki_dir, path, content)
            return f"Page '{path}' written successfully."
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def read_page(path: str) -> str:
        """Read a wiki page at the given path."""
        try:
            return operations.read_page(wiki_dir, path)
        except (FileNotFoundError, ValueError) as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def list_pages(subdirectory: str = "") -> str:
        """List all wiki pages, optionally filtered to a subdirectory."""
        try:
            pages = operations.list_pages(wiki_dir, subdirectory or None)
            if not pages:
                return "No pages found."
            return "\n".join(pages)
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def search_wiki(query: str, case_sensitive: bool = False) -> str:
        """Full-text search across all wiki pages."""
        try:
            results = operations.search_wiki(wiki_dir, query, case_sensitive)
            return _format_search_results(results)
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def search_pages(query: str, limit: int = 10) -> str:
        """Rank wiki pages by relevance and return compact snippets."""
        try:
            results = retrieval.search_pages(wiki_dir, query, limit)
            return _format_page_results(results)
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def retrieve_context(query: str, limit: int = 5) -> str:
        """Return contextual snippets from the most relevant wiki pages."""
        try:
            return retrieval.retrieve_context(wiki_dir, query, limit)
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def append_log(entry: str, operation: str = "") -> str:
        """Append a timestamped entry to log.md and auto-commit."""
        try:
            operations.append_log(wiki_dir, entry, operation or None)
            return "Log entry appended successfully."
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def delete_page(path: str) -> str:
        """Delete a wiki page and auto-commit."""
        try:
            operations.delete_page(wiki_dir, path)
            return f"Page '{path}' deleted successfully."
        except (FileNotFoundError, ValueError, PermissionError) as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error: {exc}"

    return mcp


# ---------------------------------------------------------------------------
# Server mode factory (multi-user, HTTP with auth)
# ---------------------------------------------------------------------------

def create_server_mode(
    config_path: Path,
    allowed_hosts: list[str] | None = None,
    disable_host_check: bool = False,
):
    """
    Create a FastMCP server + FastAPI app for multi-user server mode.

    Returns a tuple of (FastMCP instance, FastAPI app).

    The FastAPI app handles bearer-token authentication via middleware.
    FastMCP is mounted on the FastAPI app at /mcp.

    Each tool resolves the calling user from their bearer token, then
    routes the call to that user's wiki directory.

    ``allowed_hosts`` adds trusted Host header values for the streamable-HTTP
    transport; ``disable_host_check`` turns off DNS-rebinding protection
    entirely (only safe on an isolated network).
    """
    # Import FastAPI here so it's only required in server mode
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.responses import JSONResponse
    except ImportError as exc:
        raise ImportError(
            "fastapi is required for server mode. "
            "Install it with: pip install fastapi"
        ) from exc

    config_path = Path(config_path)

    def _load_config() -> dict:
        """Load and return the server config from disk."""
        if not config_path.exists():
            raise RuntimeError(f"Config file not found: {config_path}")
        return json.loads(config_path.read_text(encoding="utf-8"))

    def _get_user_context(request: Request) -> tuple[str, Path, list[str], dict]:
        """
        Extract and validate the token from the request.

        Returns (username, wiki_dir, auto_push_remotes, config).
        Raises HTTPException(401) if auth fails.
        """
        config = _load_config()
        token = extract_token(request)
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Missing bearer token. "
                       "Provide Authorization: Bearer <token> header or ?token= param.",
            )

        username = validate_token(token, config)
        if not username:
            raise HTTPException(status_code=401, detail="Invalid or expired token.")

        try:
            wiki_dir = resolve_wiki_dir(username, config)
            auto_push = get_auto_push_remotes(username, config)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        # Update last_active in background (ignore errors)
        try:
            update_last_active(username, config, config_path)
        except Exception:
            pass

        return username, wiki_dir, auto_push, config

    # --- Build the FastMCP instance ---
    mcp = FastMCP(
        "wikimcp",
        transport_security=http_transport_security(
            allowed_hosts, disable_protection=disable_host_check
        ),
    )
    _set_server_version(mcp)

    # We need to pass per-request context (wiki_dir, auto_push) to the tools.
    # FastMCP's Context provides access to the raw MCP request but not HTTP headers.
    # The practical approach for HTTP transport: we'll use a thread-local / request-
    # scoped store accessed via a FastAPI dependency. Since FastMCP wraps its own
    # ASGI app, we integrate by wrapping the MCP ASGI app inside our FastAPI app
    # and using middleware to populate the context before forwarding.
    #
    # For tool execution we use a simple per-request context var approach:
    # the auth middleware sets a ContextVar, and the tools read from it.

    import contextvars

    _request_context: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
        "_wikimcp_request_context", default=None
    )

    # --- Tool definitions (read context from ContextVar) ---

    def _ctx() -> dict:
        """Get the current request context. Raises RuntimeError if not set."""
        ctx = _request_context.get()
        if ctx is None:
            raise RuntimeError(
                "No request context — tools must be called through the HTTP server."
            )
        return ctx

    @mcp.tool()
    def wiki_info() -> str:
        """Return wiki stats and schema instructions. MUST be called at the start of every session."""
        try:
            wiki_dir = _ctx()["wiki_dir"]
            info = operations.wiki_info(wiki_dir)
            return _format_wiki_info(info)
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def read_index() -> str:
        """Read index.md — the master catalog and starting point for any query."""
        try:
            wiki_dir = _ctx()["wiki_dir"]
            return operations.read_index(wiki_dir)
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def update_index(content: str) -> str:
        """Overwrite index.md with new content and auto-commit."""
        try:
            ctx = _ctx()
            wiki_dir = ctx["wiki_dir"]
            auto_push = ctx["auto_push_remotes"]
            operations.update_index(wiki_dir, content)
            warnings = push_auto_remotes(wiki_dir, auto_push) if auto_push else []
            return "index.md updated successfully." + _format_push_warnings(warnings)
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def write_page(path: str, content: str) -> str:
        """Create or overwrite a wiki page at the given path and auto-commit."""
        try:
            ctx = _ctx()
            wiki_dir = ctx["wiki_dir"]
            auto_push = ctx["auto_push_remotes"]
            operations.write_page(wiki_dir, path, content)
            warnings = push_auto_remotes(wiki_dir, auto_push) if auto_push else []
            return f"Page '{path}' written successfully." + _format_push_warnings(warnings)
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def read_page(path: str) -> str:
        """Read a wiki page at the given path."""
        try:
            wiki_dir = _ctx()["wiki_dir"]
            return operations.read_page(wiki_dir, path)
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def list_pages(subdirectory: str = "") -> str:
        """List all wiki pages, optionally filtered to a subdirectory."""
        try:
            wiki_dir = _ctx()["wiki_dir"]
            pages = operations.list_pages(wiki_dir, subdirectory or None)
            if not pages:
                return "No pages found."
            return "\n".join(pages)
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def search_wiki(query: str, case_sensitive: bool = False) -> str:
        """Full-text search across all wiki pages."""
        try:
            wiki_dir = _ctx()["wiki_dir"]
            results = operations.search_wiki(wiki_dir, query, case_sensitive)
            return _format_search_results(results)
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def search_pages(query: str, limit: int = 10) -> str:
        """Rank wiki pages by relevance and return compact snippets."""
        try:
            wiki_dir = _ctx()["wiki_dir"]
            results = retrieval.search_pages(wiki_dir, query, limit)
            return _format_page_results(results)
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def retrieve_context(query: str, limit: int = 5) -> str:
        """Return contextual snippets from the most relevant wiki pages."""
        try:
            wiki_dir = _ctx()["wiki_dir"]
            return retrieval.retrieve_context(wiki_dir, query, limit)
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def append_log(entry: str, operation: str = "") -> str:
        """Append a timestamped entry to log.md and auto-commit."""
        try:
            ctx = _ctx()
            wiki_dir = ctx["wiki_dir"]
            auto_push = ctx["auto_push_remotes"]
            operations.append_log(wiki_dir, entry, operation or None)
            warnings = push_auto_remotes(wiki_dir, auto_push) if auto_push else []
            return "Log entry appended successfully." + _format_push_warnings(warnings)
        except Exception as exc:
            return f"Error: {exc}"

    @mcp.tool()
    def delete_page(path: str) -> str:
        """Delete a wiki page and auto-commit."""
        try:
            ctx = _ctx()
            wiki_dir = ctx["wiki_dir"]
            auto_push = ctx["auto_push_remotes"]
            operations.delete_page(wiki_dir, path)
            warnings = push_auto_remotes(wiki_dir, auto_push) if auto_push else []
            return f"Page '{path}' deleted successfully." + _format_push_warnings(warnings)
        except Exception as exc:
            return f"Error: {exc}"

    # --- Build the FastAPI app with auth middleware ---

    app = FastAPI(title="wikimcp", description="Multi-user wiki MCP server")

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        """
        Authenticate every request to /mcp/*.

        For /mcp/ routes: validate token, populate _request_context, then forward.
        Other routes (e.g. /wiki/, /health) pass through without MCP context.
        """
        if request.url.path.startswith("/mcp"):
            try:
                config = _load_config()
                token = extract_token(request)
                if not token:
                    return JSONResponse(
                        status_code=401,
                        content={
                            "detail": "Missing bearer token. "
                                      "Provide Authorization: Bearer <token> header "
                                      "or ?token= param."
                        },
                    )

                username = validate_token(token, config)
                if not username:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Invalid or expired token."},
                    )

                try:
                    wiki_dir = resolve_wiki_dir(username, config)
                    auto_push = get_auto_push_remotes(username, config)
                except (KeyError, ValueError) as exc:
                    return JSONResponse(
                        status_code=500,
                        content={"detail": str(exc)},
                    )

                # Update last_active (ignore errors)
                try:
                    update_last_active(username, config, config_path)
                except Exception:
                    pass

                # Set context for this request's execution context
                token_ctx = _request_context.set({
                    "username": username,
                    "wiki_dir": wiki_dir,
                    "auto_push_remotes": auto_push,
                })
                try:
                    response = await call_next(request)
                finally:
                    _request_context.reset(token_ctx)
                return response
            except Exception as exc:
                return JSONResponse(
                    status_code=500,
                    content={"detail": f"Auth middleware error: {exc}"},
                )
        else:
            return await call_next(request)

    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "ok", "service": "wikimcp"}

    # Mount FastMCP on the FastAPI app at /mcp
    # FastMCP provides an ASGI app via .streamable_http_app()
    mcp_asgi = mcp.streamable_http_app()
    app.mount("/mcp", mcp_asgi)

    return mcp, app
