"""
cli.py — Click CLI entrypoint for wikimcp.

Entry point: wikimcp.cli:main
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_WIKI_DIR = Path.home() / "llm-wiki"
DEFAULT_SERVER_DIR = Path("/data/wikimcp")
DEFAULT_PORT = 8765
DEFAULT_HOST = "0.0.0.0"
DEFAULT_LOCAL_HOST = "127.0.0.1"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _expand(path: str | Path) -> Path:
    """Expand ~ and return an absolute Path."""
    return Path(path).expanduser().resolve()


def _abort(msg: str) -> None:
    """Print an error message and exit non-zero."""
    console.print(f"[bold red]Error:[/bold red] {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    """Print a success message."""
    console.print(f"[bold green]OK:[/bold green] {msg}")


def _get_config_path(server_dir: Path) -> Path:
    from wikimcp.user.config import get_config_path
    return get_config_path(server_dir)


# ---------------------------------------------------------------------------
# PID file helpers (for server stop / status)
# ---------------------------------------------------------------------------

_PID_FILE = Path("/tmp/wikimcp-server.pid")


def _write_pid(pid: int) -> None:
    _PID_FILE.write_text(str(pid))


def _read_pid() -> Optional[int]:
    if not _PID_FILE.exists():
        return None
    try:
        return int(_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None


def _clear_pid() -> None:
    _PID_FILE.unlink(missing_ok=True)


def _process_running(pid: int) -> bool:
    """Return True if a process with the given PID exists."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(package_name="wikimcp")
def main() -> None:
    """wikimcp — Git-backed multi-user wiki MCP server.

    LTM (long-term memory) for AI: your knowledge compounds across
    conversations instead of resetting every session.

    Quick start (local mode):

    \b
        wikimcp init
        # paste the printed config into Claude Desktop / LM Studio

    Server mode (multi-user):

    \b
        wikimcp server init
        wikimcp server start
        wikimcp add-user alice
    """


# ---------------------------------------------------------------------------
# init — local mode setup
# ---------------------------------------------------------------------------


@main.command("init")
@click.option(
    "--wiki-dir",
    default=str(DEFAULT_WIKI_DIR),
    show_default=True,
    help="Directory to create the wiki in.",
)
def cmd_init(wiki_dir: str) -> None:
    """Scaffold a local wiki and print the MCP config snippet."""
    from wikimcp.wiki.schema import scaffold_wiki
    from wikimcp.wiki.git_layer import init_repo

    wiki_path = _expand(wiki_dir)

    console.print(f"\nInitialising wiki at [cyan]{wiki_path}[/cyan] ...")

    try:
        scaffold_wiki(wiki_path)
    except Exception as exc:
        _abort(f"Failed to scaffold wiki: {exc}")

    try:
        init_repo(wiki_path)
    except Exception as exc:
        _abort(f"Failed to initialise git repo: {exc}")

    _ok("Wiki scaffolded and git repo initialised.")

    # Show the MCP config snippet
    config_snippet = {
        "mcpServers": {
            "wikimcp": {
                "command": "wikimcp",
                "args": ["serve", "--wiki-dir", str(wiki_path)],
            }
        }
    }
    snippet_str = json.dumps(config_snippet, indent=2)

    console.print(
        Panel(
            snippet_str,
            title="[bold yellow]Paste into Claude Desktop / LM Studio / Gemini CLI[/bold yellow]",
            subtitle="[dim]Settings → MCP Servers[/dim]",
            border_style="yellow",
            expand=False,
        )
    )
    console.print(
        "\n[dim]Config file location (Claude Desktop):[/dim]\n"
        "  macOS: [cyan]~/Library/Application Support/Claude/claude_desktop_config.json[/cyan]\n"
        "  Linux: [cyan]~/.config/Claude/claude_desktop_config.json[/cyan]\n"
    )


# ---------------------------------------------------------------------------
# rebuild-search-index
# ---------------------------------------------------------------------------


@main.command("rebuild-search-index")
@click.option(
    "--wiki-dir",
    default=str(DEFAULT_WIKI_DIR),
    show_default=True,
    help="Path to the wiki directory.",
)
def cmd_rebuild_search_index(wiki_dir: str) -> None:
    """Rebuild the optional SQLite hybrid search index for a local wiki."""
    from wikimcp.wiki.operations import rebuild_search_index

    wiki_path = _expand(wiki_dir)
    if not wiki_path.exists():
        _abort(
            f"Wiki directory does not exist: {wiki_path}\n"
            "  Run [bold]wikimcp init[/bold] first."
        )

    try:
        stats = rebuild_search_index(wiki_path)
    except Exception as exc:
        _abort(f"Failed to rebuild search index: {exc}")

    _ok(f"Search index rebuilt ({stats['indexed_pages']} pages).")
    console.print(f"  Index file: [cyan]{stats['index_path']}[/cyan]")


# ---------------------------------------------------------------------------
# serve — local MCP server
# ---------------------------------------------------------------------------


@main.command("serve")
@click.option(
    "--wiki-dir",
    default=str(DEFAULT_WIKI_DIR),
    show_default=True,
    help="Path to the wiki directory.",
)
@click.option(
    "--transport",
    default="stdio",
    type=click.Choice(["stdio", "http"]),
    show_default=True,
    help="Transport mode: stdio (default) or http.",
)
@click.option(
    "--host",
    default=DEFAULT_LOCAL_HOST,
    show_default=True,
    help="Host to bind to (http transport only).",
)
@click.option(
    "--port",
    default=DEFAULT_PORT,
    show_default=True,
    type=int,
    help="Port to listen on (http transport only).",
)
@click.option(
    "--allowed-host",
    "allowed_hosts",
    multiple=True,
    help="Extra Host header value(s) to accept over HTTP (repeatable). "
         "e.g. --allowed-host host.docker.internal",
)
@click.option(
    "--allow-any-host",
    is_flag=True,
    default=False,
    help="Disable DNS-rebinding host validation entirely. "
         "Only use on a trusted/isolated network.",
)
def cmd_serve(
    wiki_dir: str,
    transport: str,
    host: str,
    port: int,
    allowed_hosts: tuple[str, ...],
    allow_any_host: bool,
) -> None:
    """Start the local MCP server (single-user, no auth).

    stdio (default): for Claude Desktop, LM Studio, Gemini CLI, Claude Code.

    http: for ChatGPT or claude.ai via ngrok or a public server.
    """
    from wikimcp.server.mcp_server import create_local_server

    wiki_path = _expand(wiki_dir)

    if not wiki_path.exists():
        _abort(
            f"Wiki directory does not exist: {wiki_path}\n"
            "  Run [bold]wikimcp init[/bold] first."
        )

    mcp = create_local_server(
        wiki_path,
        allowed_hosts=list(allowed_hosts),
        disable_host_check=allow_any_host,
    )

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        # HTTP transport — run the MCP server's streamable HTTP ASGI app via uvicorn
        try:
            import uvicorn
        except ImportError:
            _abort("uvicorn is required for http transport. Run: pip install uvicorn")

        console.print(
            f"Starting wikimcp HTTP server on [cyan]http://{host}:{port}[/cyan] ..."
        )
        app = mcp.streamable_http_app()
        uvicorn.run(app, host=host, port=port)


# ---------------------------------------------------------------------------
# server group
# ---------------------------------------------------------------------------


@main.group("server")
def server_group() -> None:
    """Multi-user server mode commands."""


@server_group.command("init")
@click.option(
    "--dir",
    "server_dir",
    default=str(DEFAULT_SERVER_DIR),
    show_default=True,
    help="Server data directory.",
)
@click.option(
    "--port",
    default=DEFAULT_PORT,
    show_default=True,
    type=int,
    help="Port the server will listen on.",
)
def server_init(server_dir: str, port: int) -> None:
    """Initialise the server data directory and config file."""
    from wikimcp.user.manager import init_server

    server_path = _expand(server_dir)
    console.print(f"Initialising server at [cyan]{server_path}[/cyan] ...")

    try:
        init_server(server_path, port)
    except Exception as exc:
        _abort(f"Failed to initialise server: {exc}")

    _ok(f"Server directory initialised at {server_path}")
    console.print(
        f"  Config: [cyan]{server_path / 'wikimcp.conf'}[/cyan]\n"
        f"  Users:  [cyan]{server_path / 'users'}[/cyan]\n"
        f"\nNext steps:\n"
        f"  [bold]wikimcp add-user <username> --dir {server_dir}[/bold]\n"
        f"  [bold]wikimcp server start --dir {server_dir}[/bold]\n"
    )


@server_group.command("start")
@click.option(
    "--dir",
    "server_dir",
    default=str(DEFAULT_SERVER_DIR),
    show_default=True,
    help="Server data directory.",
)
@click.option(
    "--port",
    default=None,
    type=int,
    help="Port to listen on (overrides config file).",
)
@click.option(
    "--host",
    default=None,
    help="Host to bind to (overrides config file).",
)
@click.option(
    "--allowed-host",
    "allowed_hosts",
    multiple=True,
    help="Extra Host header value(s) to accept (repeatable). "
         "e.g. --allowed-host host.docker.internal",
)
@click.option(
    "--allow-any-host",
    is_flag=True,
    default=False,
    help="Disable DNS-rebinding host validation entirely. "
         "Only use on a trusted/isolated network.",
)
def server_start(
    server_dir: str,
    port: Optional[int],
    host: Optional[str],
    allowed_hosts: tuple[str, ...],
    allow_any_host: bool,
) -> None:
    """Start the multi-user HTTP MCP server + web reader."""
    from wikimcp.server.mcp_server import create_server_mode
    from wikimcp.server.web_reader import create_web_reader, mount_static
    from wikimcp.user.config import load_config, get_config_path

    try:
        import uvicorn
    except ImportError:
        _abort("uvicorn is required. Run: pip install uvicorn")

    server_path = _expand(server_dir)
    config_path = get_config_path(server_path)

    if not config_path.exists():
        _abort(
            f"Config file not found: {config_path}\n"
            f"  Run [bold]wikimcp server init --dir {server_dir}[/bold] first."
        )

    try:
        config = load_config(config_path)
    except Exception as exc:
        _abort(f"Failed to load config: {exc}")

    listen_host = host or config.get("host", DEFAULT_HOST)
    listen_port = port or config.get("port", DEFAULT_PORT)

    console.print(
        f"Starting wikimcp server on [cyan]http://{listen_host}:{listen_port}[/cyan] ..."
    )
    console.print(f"  MCP endpoint:  [cyan]http://{listen_host}:{listen_port}/mcp[/cyan]")
    console.print(f"  Web reader:    [cyan]http://{listen_host}:{listen_port}/wiki/<username>[/cyan]")
    console.print(f"  Health check:  [cyan]http://{listen_host}:{listen_port}/health[/cyan]")

    try:
        mcp, app = create_server_mode(
            config_path,
            allowed_hosts=list(allowed_hosts),
            disable_host_check=allow_any_host,
        )
    except Exception as exc:
        _abort(f"Failed to create server: {exc}")

    # Include the web reader router
    try:
        web_router = create_web_reader(config_path)
        app.include_router(web_router)
        mount_static(app)
    except Exception as exc:
        console.print(f"[yellow]Warning:[/yellow] Could not mount web reader: {exc}")

    # Include the git HTTP backend
    try:
        from wikimcp.server.git_http import create_git_http_router

        git_router = create_git_http_router(config_path)
        app.include_router(git_router)
        console.print(f"  Git hosting:   [cyan]http://{listen_host}:{listen_port}/git/<username>[/cyan]")
    except Exception as exc:
        console.print(f"[yellow]Warning:[/yellow] Could not mount git HTTP backend: {exc}")

    # Write PID file so `server stop` can find the process
    _write_pid(os.getpid())
    try:
        uvicorn.run(app, host=listen_host, port=listen_port)
    finally:
        _clear_pid()


@server_group.command("stop")
def server_stop() -> None:
    """Stop the running wikimcp server."""
    pid = _read_pid()
    if pid is None:
        # Fall back to searching for the process by name
        console.print("[dim]No PID file found — searching for wikimcp process ...[/dim]")
        try:
            result = subprocess.run(
                ["pgrep", "-f", "wikimcp server start"],
                capture_output=True,
                text=True,
            )
            pids = [int(p) for p in result.stdout.strip().split() if p.strip()]
            if not pids:
                console.print("[yellow]No running wikimcp server found.[/yellow]")
                return
            pid = pids[0]
        except Exception as exc:
            _abort(f"Could not find wikimcp process: {exc}")

    if not _process_running(pid):
        console.print(f"[yellow]Process {pid} is not running.[/yellow]")
        _clear_pid()
        return

    console.print(f"Stopping wikimcp server (PID {pid}) ...")
    try:
        os.kill(pid, signal.SIGTERM)
        _clear_pid()
        _ok(f"Server stopped (PID {pid}).")
    except ProcessLookupError:
        console.print(f"[yellow]Process {pid} already stopped.[/yellow]")
        _clear_pid()
    except PermissionError:
        _abort(f"Permission denied killing PID {pid}. Try running with sudo.")


@server_group.command("status")
@click.option(
    "--dir",
    "server_dir",
    default=str(DEFAULT_SERVER_DIR),
    show_default=True,
    help="Server data directory.",
)
def server_status(server_dir: str) -> None:
    """Show whether the wikimcp server is running."""
    from wikimcp.user.config import load_config, get_config_path

    server_path = _expand(server_dir)
    config_path = get_config_path(server_path)

    pid = _read_pid()
    running = pid is not None and _process_running(pid)

    if running:
        console.print(f"[bold green]wikimcp server is running[/bold green] (PID {pid})")
    else:
        console.print("[bold red]wikimcp server is NOT running[/bold red]")

    if config_path.exists():
        try:
            config = load_config(config_path)
            host = config.get("host", DEFAULT_HOST)
            port = config.get("port", DEFAULT_PORT)
            user_count = len(config.get("users", {}))
            console.print(f"  Config:  [cyan]{config_path}[/cyan]")
            console.print(f"  Address: [cyan]http://{host}:{port}[/cyan]")
            console.print(f"  Users:   {user_count}")
        except Exception as exc:
            console.print(f"[yellow]Could not read config: {exc}[/yellow]")
    else:
        console.print(f"[yellow]No config found at {config_path}[/yellow]")


# ---------------------------------------------------------------------------
# add-user
# ---------------------------------------------------------------------------


@main.command("add-user")
@click.argument("username")
@click.option(
    "--dir",
    "server_dir",
    default=str(DEFAULT_SERVER_DIR),
    show_default=True,
    help="Server data directory.",
)
def cmd_add_user(username: str, server_dir: str) -> None:
    """Create a new user and generate their bearer token."""
    from wikimcp.user.manager import add_user
    from wikimcp.user.config import load_config, get_config_path

    server_path = _expand(server_dir)
    console.print(f"Adding user [bold]{username}[/bold] ...")

    try:
        result = add_user(server_path, username)
    except ValueError as exc:
        _abort(str(exc))
    except Exception as exc:
        _abort(f"Failed to add user: {exc}")

    # Try to get real host from config for display
    try:
        config = load_config(get_config_path(server_path))
        host = config.get("host", "0.0.0.0")
        port = config.get("port", DEFAULT_PORT)
        mcp_url = f"http://{host}:{port}/mcp"
        wiki_url = f"http://{host}:{port}/wiki/{username}"
    except Exception:
        mcp_url = result.get("mcp_url", "http://localhost:8765/mcp")
        wiki_url = result.get("wiki_url", f"http://localhost:8765/wiki/{username}")

    token = result["token"]

    info_lines = (
        f"[bold]Token:[/bold]    {token}\n"
        f"[bold]MCP URL:[/bold]  {mcp_url}\n"
        f"[bold]Wiki URL:[/bold] {wiki_url}\n"
        f"[bold]Wiki dir:[/bold] {result['wiki_dir']}\n\n"
        f"[dim]Store the token securely — it is shown only once.[/dim]"
    )

    console.print(
        Panel(
            info_lines,
            title=f"[bold green]User '{username}' created[/bold green]",
            border_style="green",
            expand=False,
        )
    )

    # MCP config snippet for the user
    mcp_config = {
        "mcpServers": {
            "wikimcp": {
                "url": mcp_url,
                "headers": {"Authorization": f"Bearer {token}"},
            }
        }
    }
    console.print(
        Panel(
            json.dumps(mcp_config, indent=2),
            title="[bold yellow]MCP config snippet for the user[/bold yellow]",
            border_style="yellow",
            expand=False,
        )
    )


# ---------------------------------------------------------------------------
# remove-user
# ---------------------------------------------------------------------------


@main.command("remove-user")
@click.argument("username")
@click.option(
    "--dir",
    "server_dir",
    default=str(DEFAULT_SERVER_DIR),
    show_default=True,
    help="Server data directory.",
)
def cmd_remove_user(username: str, server_dir: str) -> None:
    """Remove a user and delete their wiki (requires confirmation)."""
    from wikimcp.user.manager import remove_user

    server_path = _expand(server_dir)

    click.confirm(
        f"This will permanently delete user '{username}' and all their wiki data. Continue?",
        abort=True,
    )

    console.print(f"Removing user [bold]{username}[/bold] ...")
    try:
        remove_user(server_path, username)
    except KeyError as exc:
        _abort(str(exc))
    except Exception as exc:
        _abort(f"Failed to remove user: {exc}")

    _ok(f"User '{username}' removed.")


# ---------------------------------------------------------------------------
# list-users
# ---------------------------------------------------------------------------


@main.command("list-users")
@click.option(
    "--dir",
    "server_dir",
    default=str(DEFAULT_SERVER_DIR),
    show_default=True,
    help="Server data directory.",
)
def cmd_list_users(server_dir: str) -> None:
    """List all users, their masked token, page count, and last active time."""
    from wikimcp.user.manager import list_users

    server_path = _expand(server_dir)

    try:
        users = list_users(server_path)
    except Exception as exc:
        _abort(f"Failed to list users: {exc}")

    if not users:
        console.print("[yellow]No users found.[/yellow]")
        return

    table = Table(title="wikimcp Users", show_header=True, header_style="bold cyan")
    table.add_column("Username", style="bold")
    table.add_column("Pages", justify="right")
    table.add_column("Last Active")
    table.add_column("Token (masked)")
    table.add_column("Wiki Directory", style="dim")

    for user in users:
        table.add_row(
            user["username"],
            str(user["page_count"]),
            user.get("last_active") or "[dim]never[/dim]",
            user.get("masked_token", "N/A"),
            user.get("wiki_dir", ""),
        )

    console.print(table)


# ---------------------------------------------------------------------------
# rotate-token
# ---------------------------------------------------------------------------


@main.command("rotate-token")
@click.argument("username")
@click.option(
    "--dir",
    "server_dir",
    default=str(DEFAULT_SERVER_DIR),
    show_default=True,
    help="Server data directory.",
)
def cmd_rotate_token(username: str, server_dir: str) -> None:
    """Generate a new bearer token for a user, invalidating the old one."""
    from wikimcp.user.manager import rotate_token

    server_path = _expand(server_dir)

    console.print(f"Rotating token for user [bold]{username}[/bold] ...")
    try:
        new_token = rotate_token(server_path, username)
    except KeyError as exc:
        _abort(str(exc))
    except Exception as exc:
        _abort(f"Failed to rotate token: {exc}")

    console.print(
        Panel(
            f"[bold]New Token:[/bold] {new_token}\n\n"
            f"[dim]The old token is now invalid.\n"
            f"Store the new token securely — it is shown only once.[/dim]",
            title=f"[bold green]Token rotated for '{username}'[/bold green]",
            border_style="green",
            expand=False,
        )
    )


# ---------------------------------------------------------------------------
# remote group
# ---------------------------------------------------------------------------


@main.group("remote")
def remote_group() -> None:
    """Manage git remotes for user wikis."""


@remote_group.command("add")
@click.argument("username")
@click.argument("remote_name")
@click.argument("git_url")
@click.option(
    "--dir",
    "server_dir",
    default=str(DEFAULT_SERVER_DIR),
    show_default=True,
    help="Server data directory.",
)
def remote_add(username: str, remote_name: str, git_url: str, server_dir: str) -> None:
    """Add a git remote to a user's wiki repo.

    Example: wikimcp remote add mohith github git@github.com:mohith/my-wiki.git
    """
    from wikimcp.user.manager import add_remote

    server_path = _expand(server_dir)
    console.print(
        f"Adding remote [bold]{remote_name}[/bold] -> [cyan]{git_url}[/cyan] "
        f"for user [bold]{username}[/bold] ..."
    )
    try:
        add_remote(server_path, username, remote_name, git_url)
    except (KeyError, ValueError) as exc:
        _abort(str(exc))
    except Exception as exc:
        _abort(f"Failed to add remote: {exc}")

    _ok(f"Remote '{remote_name}' added for user '{username}'.")


@remote_group.command("remove")
@click.argument("username")
@click.argument("remote_name")
@click.option(
    "--dir",
    "server_dir",
    default=str(DEFAULT_SERVER_DIR),
    show_default=True,
    help="Server data directory.",
)
def remote_remove(username: str, remote_name: str, server_dir: str) -> None:
    """Remove a git remote from a user's wiki repo."""
    from wikimcp.user.manager import remove_remote

    server_path = _expand(server_dir)
    console.print(
        f"Removing remote [bold]{remote_name}[/bold] from user [bold]{username}[/bold] ..."
    )
    try:
        remove_remote(server_path, username, remote_name)
    except (KeyError, ValueError) as exc:
        _abort(str(exc))
    except Exception as exc:
        _abort(f"Failed to remove remote: {exc}")

    _ok(f"Remote '{remote_name}' removed from user '{username}'.")


@remote_group.command("list")
@click.argument("username")
@click.option(
    "--dir",
    "server_dir",
    default=str(DEFAULT_SERVER_DIR),
    show_default=True,
    help="Server data directory.",
)
def remote_list(username: str, server_dir: str) -> None:
    """List all git remotes configured for a user's wiki."""
    from wikimcp.user.manager import list_remotes

    server_path = _expand(server_dir)
    try:
        remotes = list_remotes(server_path, username)
    except KeyError as exc:
        _abort(str(exc))
    except Exception as exc:
        _abort(f"Failed to list remotes: {exc}")

    if not remotes:
        console.print(f"[yellow]No remotes configured for user '{username}'.[/yellow]")
        return

    table = Table(
        title=f"Git Remotes for '{username}'",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Name", style="bold")
    table.add_column("URL")

    for r in remotes:
        table.add_row(r["name"], r["url"])

    console.print(table)


@remote_group.command("push")
@click.argument("username")
@click.option(
    "--remote",
    "remote_name",
    default=None,
    help="Remote name to push to (default: all remotes).",
)
@click.option(
    "--dir",
    "server_dir",
    default=str(DEFAULT_SERVER_DIR),
    show_default=True,
    help="Server data directory.",
)
def remote_push(username: str, remote_name: Optional[str], server_dir: str) -> None:
    """Push a user's wiki to one or all configured git remotes."""
    from wikimcp.user.manager import push_remote

    server_path = _expand(server_dir)
    target = f"remote '{remote_name}'" if remote_name else "all remotes"
    console.print(
        f"Pushing wiki for user [bold]{username}[/bold] to {target} ..."
    )
    try:
        warnings = push_remote(server_path, username, remote_name)
    except KeyError as exc:
        _abort(str(exc))
    except Exception as exc:
        _abort(f"Push failed: {exc}")

    if warnings:
        for w in warnings:
            console.print(f"[yellow]Warning:[/yellow] {w}")
    else:
        _ok(f"Pushed successfully.")


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@main.command("export")
@click.argument("username")
@click.option(
    "--format",
    "fmt",
    default="zip",
    type=click.Choice(["zip", "tar"]),
    show_default=True,
    help="Archive format.",
)
@click.option(
    "--out",
    "out_dir",
    default="./",
    show_default=True,
    help="Output directory for the archive.",
)
@click.option(
    "--dir",
    "server_dir",
    default=str(DEFAULT_SERVER_DIR),
    show_default=True,
    help="Server data directory.",
)
def cmd_export(username: str, fmt: str, out_dir: str, server_dir: str) -> None:
    """Export a user's wiki as a zip or tar.gz archive."""
    from wikimcp.user.manager import export_wiki

    server_path = _expand(server_dir)
    console.print(
        f"Exporting wiki for user [bold]{username}[/bold] as [bold]{fmt}[/bold] ..."
    )
    try:
        archive_path = export_wiki(server_path, username, fmt, out_dir)
    except KeyError as exc:
        _abort(str(exc))
    except ValueError as exc:
        _abort(str(exc))
    except Exception as exc:
        _abort(f"Export failed: {exc}")

    _ok(f"Exported to [cyan]{archive_path}[/cyan]")


# ---------------------------------------------------------------------------
# export-graph — visualization / Obsidian export for a local wiki
# ---------------------------------------------------------------------------
#
# Named ``export-graph`` (not ``export``) because ``wikimcp export <username>``
# already exists for server-mode archive export and takes a positional
# username; reusing ``export`` as a group would break that command.


@main.command("export-graph")
@click.argument("fmt", type=click.Choice(["html", "obsidian"]))
@click.option(
    "--wiki-dir",
    default=str(DEFAULT_WIKI_DIR),
    show_default=True,
    help="Path to the wiki directory.",
)
@click.option(
    "--out",
    "out",
    default=None,
    help="Output path: graph.html file (html) or vault copy dir (obsidian). "
         "Defaults to writing inside the wiki directory.",
)
def cmd_export_graph(fmt: str, wiki_dir: str, out: Optional[str]) -> None:
    """Export the wiki page-graph as self-contained HTML or an Obsidian vault.

    \b
    wikimcp export-graph obsidian     # add .obsidian config (open wiki in Obsidian)
    wikimcp export-graph html         # write a self-contained graph.html
    """
    from wikimcp.wiki.graph_export import export_html, export_obsidian

    wiki_path = _expand(wiki_dir)
    if not wiki_path.exists():
        _abort(
            f"Wiki directory does not exist: {wiki_path}\n"
            "  Run [bold]wikimcp init[/bold] first."
        )

    out_path = _expand(out) if out else None

    if fmt == "obsidian":
        try:
            result = export_obsidian(wiki_path, out_path)
        except Exception as exc:
            _abort(f"Obsidian export failed: {exc}")
        if result["copied"]:
            _ok(f"Obsidian vault copy written to [cyan]{result['vault_root']}[/cyan]")
        else:
            _ok(f"Obsidian config written to [cyan]{result['config_dir']}[/cyan]")
        console.print(
            f"  Open [cyan]{result['vault_root']}[/cyan] in Obsidian for "
            "graph view + backlinks."
        )
    else:
        try:
            result = export_html(wiki_path, out_path)
        except Exception as exc:
            _abort(f"HTML export failed: {exc}")
        _ok(
            f"Self-contained graph written to [cyan]{result['path']}[/cyan] "
            f"({result['nodes']} pages, {result['edges']} edges)."
        )


# ---------------------------------------------------------------------------
# graph-refresh — re-index changed pages + refresh artifacts (used by the hook)
# ---------------------------------------------------------------------------


@main.command("graph-refresh")
@click.option(
    "--wiki-dir",
    default=str(DEFAULT_WIKI_DIR),
    show_default=True,
    help="Path to the wiki directory.",
)
@click.option(
    "--changed",
    default="",
    help="Comma-separated list of changed paths (passed by the post-commit hook).",
)
def cmd_graph_refresh(wiki_dir: str, changed: str) -> None:
    """Re-index changed pages and refresh generated artifacts (offline)."""
    from wikimcp.wiki.graph_queries import refresh_after_commit

    wiki_path = _expand(wiki_dir)
    if not wiki_path.exists():
        _abort(f"Wiki directory does not exist: {wiki_path}")

    changed_paths = [p.strip() for p in changed.split(",") if p.strip()]
    try:
        result = refresh_after_commit(wiki_path, changed_paths or None)
    except Exception as exc:
        _abort(f"Graph refresh failed: {exc}")

    refreshed = result.get("refreshed", [])
    if refreshed:
        _ok(f"Refreshed: {', '.join(refreshed)}")
    else:
        _ok("Graph re-indexed (no artifacts to refresh).")


# ---------------------------------------------------------------------------
# install-graph-hook — install the post-commit refresh hook
# ---------------------------------------------------------------------------


@main.command("install-graph-hook")
@click.option(
    "--wiki-dir",
    default=str(DEFAULT_WIKI_DIR),
    show_default=True,
    help="Path to the wiki directory.",
)
def cmd_install_graph_hook(wiki_dir: str) -> None:
    """Install a git post-commit hook that refreshes the graph on every commit."""
    from wikimcp.wiki.git_layer import install_post_commit_hook

    wiki_path = _expand(wiki_dir)
    if not wiki_path.exists():
        _abort(
            f"Wiki directory does not exist: {wiki_path}\n"
            "  Run [bold]wikimcp init[/bold] first."
        )

    try:
        hook_path = install_post_commit_hook(wiki_path)
    except FileExistsError as exc:
        _abort(str(exc))
    except Exception as exc:
        _abort(f"Failed to install hook: {exc}")

    _ok(f"Post-commit hook installed at [cyan]{hook_path}[/cyan]")
    console.print(
        "  Every commit now re-indexes changed pages and refreshes any existing "
        "WIKI_REPORT.md / graph.html (offline)."
    )


# ---------------------------------------------------------------------------
# install-service
# ---------------------------------------------------------------------------


@main.command("install-service")
@click.option(
    "--dir",
    "server_dir",
    default=str(DEFAULT_SERVER_DIR),
    show_default=True,
    help="Server data directory.",
)
@click.option(
    "--port",
    default=DEFAULT_PORT,
    show_default=True,
    type=int,
    help="Port the service will listen on.",
)
def cmd_install_service(server_dir: str, port: int) -> None:
    """Install wikimcp as a system service (systemd on Linux, launchd on macOS)."""
    server_path = _expand(server_dir)
    platform = sys.platform

    console.print(f"Installing wikimcp service for platform [bold]{platform}[/bold] ...")
    console.print(f"  Server dir: [cyan]{server_path}[/cyan]")
    console.print(f"  Port:       {port}")

    if platform.startswith("linux"):
        from wikimcp.service.systemd import install_service, generate_service_file

        # Show the service file content before installing
        try:
            content = generate_service_file(str(server_path), port)
            console.print(
                Panel(
                    content,
                    title="[bold]/etc/systemd/system/wikimcp.service[/bold]",
                    border_style="cyan",
                    expand=False,
                )
            )
        except Exception:
            pass

        try:
            install_service(str(server_path), port)
        except PermissionError as exc:
            _abort(str(exc))
        except Exception as exc:
            _abort(f"Failed to install service: {exc}")

        _ok("systemd service installed and started.")
        console.print(
            "\nManage the service with:\n"
            "  [bold]systemctl status wikimcp[/bold]\n"
            "  [bold]systemctl restart wikimcp[/bold]\n"
            "  [bold]journalctl -u wikimcp -f[/bold]\n"
        )

    elif platform == "darwin":
        from wikimcp.service.launchd import install_service, generate_plist

        # Show the plist content before installing
        try:
            content = generate_plist(str(server_path), port)
            console.print(
                Panel(
                    content,
                    title="[bold]~/Library/LaunchAgents/com.wikimcp.server.plist[/bold]",
                    border_style="cyan",
                    expand=False,
                )
            )
        except Exception:
            pass

        try:
            install_service(str(server_path), port)
        except Exception as exc:
            _abort(f"Failed to install launchd service: {exc}")

        _ok("launchd service installed and loaded.")
        console.print(
            "\nManage the service with:\n"
            "  [bold]launchctl list | grep wikimcp[/bold]\n"
            "  [bold]launchctl stop com.wikimcp.server[/bold]\n"
            "  [bold]tail -f ~/Library/Logs/wikimcp.log[/bold]\n"
        )

    else:
        _abort(
            f"Unsupported platform: {platform}\n"
            "  wikimcp supports Linux (systemd) and macOS (launchd)."
        )


# Allow `python -m wikimcp.cli ...` (used by the git post-commit hook, which
# invokes the same interpreter wikimcp is installed under).
if __name__ == "__main__":
    main()
