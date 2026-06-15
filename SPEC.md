# wikimcp — Package Specification v0.1

## What it is

`wikimcp` is a Python pip package that runs an MCP (Model Context Protocol) server
exposing a git-backed, multi-user personal wiki. Any MCP-compatible AI client
(Claude Desktop, LM Studio, Gemini CLI, ChatGPT, claude.ai) can read and write
to the wiki. Users browse their wiki via a web reader or locally in Obsidian.
Knowledge compounds across conversations instead of resetting every session.

Inspired by Andrej Karpathy's LLM Wiki pattern.

---

## Installation

```bash
pip install wikimcp
```

---

## CLI interface (complete)

### Local mode (single user, no server needed)

```bash
wikimcp init [--wiki-dir ~/llm-wiki]
# Scaffolds wiki folder, initialises git repo, writes CLAUDE.md schema,
# prints config snippet to paste into Claude Desktop / LM Studio / Gemini CLI

wikimcp serve [--wiki-dir ~/llm-wiki] [--transport stdio|http] [--host 127.0.0.1] [--port 8765]
# Starts the MCP server
# stdio = default, for Claude Desktop / LM Studio / Gemini CLI
# http  = for ChatGPT / claude.ai browser (requires ngrok or public server)
```

### Server mode (multi-user, always-on)

```bash
wikimcp server init [--dir /data/wikimcp] [--port 8765]
# Initialises the server data directory and config file
# Creates /data/wikimcp/users/ and /data/wikimcp/wikimcp.conf

wikimcp server start [--dir /data/wikimcp] [--port 8765] [--host 0.0.0.0]
# Starts the multi-user HTTP MCP server + web reader
# MCP endpoint:  https://host:port/mcp          (token auth)
# Web reader:    https://host:port/wiki/<user>   (token auth)

wikimcp server stop
wikimcp server status

wikimcp add-user <username> [--dir /data/wikimcp]
# Creates user folder, initialises git repo, generates bearer token
# Prints:
#   Token : wikimcp_<username>_<random>
#   MCP URL: https://yourserver.com/mcp
#   Wiki URL: https://yourserver.com/wiki/<username>

wikimcp remove-user <username> [--dir /data/wikimcp]
# Removes user folder and token (prompts for confirmation)

wikimcp list-users [--dir /data/wikimcp]
# Lists all users, their token (masked), wiki URL, page count, last active

wikimcp rotate-token <username> [--dir /data/wikimcp]
# Generates a new token for a user, invalidates the old one

wikimcp remote add <username> <remote-name> <git-url> [--dir /data/wikimcp]
# Adds a git remote to a user's wiki repo
# e.g. wikimcp remote add mohith github git@github.com:mohith/my-wiki.git

wikimcp remote remove <username> <remote-name>
wikimcp remote list <username>

wikimcp remote push <username> [--remote <name>]
# Manually push a user's wiki to one or all configured remotes

wikimcp export <username> [--format zip|tar] [--out ./]
# Exports a user's wiki as a zip/tar for download or backup

wikimcp install-service [--dir /data/wikimcp] [--port 8765]
# Writes and enables a systemd service file (Linux) or launchd plist (macOS)
# so the server starts on boot automatically
```

---

## Package structure

```
wikimcp/
  src/
    wikimcp/
      __init__.py          # version
      cli.py               # Click CLI entrypoint (all commands above)
      server/
        __init__.py
        mcp_server.py      # FastMCP server, tool definitions
        auth.py            # Token validation, per-request user resolution
        router.py          # Routes MCP tool calls to correct user's wiki
        web_reader.py      # Read-only web UI (FastAPI or Flask)
      wiki/
        __init__.py
        operations.py      # write_page, read_page, list_pages, search_wiki etc.
        retrieval.py       # ranked page retrieval and contextual snippets
        git_layer.py       # auto-commit, remote management, push, conflict handling
        schema.py          # CLAUDE.md template, wiki folder scaffolding
      user/
        __init__.py
        manager.py         # add-user, remove-user, list-users, rotate-token
        config.py          # wikimcp.conf read/write (JSON)
      service/
        __init__.py
        systemd.py         # systemd service file generation + installation
        launchd.py         # macOS launchd plist generation + installation
  tests/
    test_operations.py
    test_git_layer.py
    test_auth.py
    test_cli.py
    test_web_reader.py
  pyproject.toml
  README.md
  SPEC.md                  # this file
  CLAUDE.md                # schema template (copied to each user's wiki on init)
```

---

## MCP tools (exposed to AI clients)

All tools are identical in local and server mode.
In server mode, the user is identified from their bearer token before any tool runs.

| Tool | Args | Description |
|------|------|-------------|
| `wiki_info` | — | Page count, log entries, wiki root path. Call at session start. |
| `read_index` | — | Read index.md — starting point for any query |
| `update_index` | `content` | Overwrite index.md |
| `write_page` | `path`, `content` | Create or overwrite a wiki page |
| `read_page` | `path` | Read a wiki page |
| `list_pages` | `subdirectory?` | List all pages (or a subdirectory) |
| `search_wiki` | `query`, `case_sensitive?` | Full-text search across all pages |
| `search_pages` | `query`, `limit?` | Rank wiki pages by relevance and return compact snippets |
| `retrieve_context` | `query`, `limit?` | Return contextual snippets from the most relevant pages |
| `append_log` | `entry`, `operation?` | Append timestamped entry to log.md |
| `delete_page` | `path` | Delete a wiki page |

Every `write_page`, `update_index`, `append_log`, and `delete_page` call
triggers an auto git commit with message: `"wiki: <operation> <path>"`.

---

## Auth

### Local mode
No auth. Server runs on localhost, stdio transport.

### Server mode (HTTP)
Bearer token per user. Every request must include:
```
Authorization: Bearer wikimcp_<username>_<random32>
```

Token is validated in `auth.py` against `wikimcp.conf`.
Invalid token → 401. Valid token → user resolved → tool runs against that user's wiki.

No sessions. No passwords. No login flow.
Token is generated once by admin via `wikimcp add-user`.
Token can be rotated via `wikimcp rotate-token`.

---

## Git layer

### Per-user wiki is a git repo

Every user's wiki folder is initialised as a git repo on `add-user`.
Local mode: `wikimcp init` initialises the wiki as a git repo.

### Auto-commit on every write

Every mutating MCP tool call triggers a commit:
```
wiki: write_page topics/graph-algorithms.md
wiki: append_log chat 2026-04-14
wiki: delete_page old-topic.md
```

Commit author: `wikimcp-bot <wikimcp@localhost>`

### Remote support

Each user can have multiple named git remotes:
```json
{
  "remotes": {
    "github": "git@github.com:mohith/my-wiki.git",
    "work":   "git@github.com:acme/mohith-wiki.git"
  },
  "auto_push_remotes": ["github"]
}
```

`auto_push_remotes` — pushed automatically on every write.
Other remotes — pushed manually via `wikimcp remote push`.

### Local sync (user's machine)

User clones their wiki from the server:
```bash
git clone https://yourserver.com/git/mohith my-wiki
cd my-wiki
# open in Obsidian
```

User edits in Obsidian → `git commit` → `git push origin main` → server updated.
Next AI session picks up the user's edits automatically.

### Merge conflict handling

If a push fails due to conflict:
- Server logs the conflict
- MCP tool returns a warning alongside the result:
  `"⚠️ Git conflict on push — run git pull in your local clone to resolve"`
- The write itself still succeeds locally (conflict is in the remote sync, not the write)

### Git hosting on server

The server exposes user repos over HTTP via `git-http-backend` (CGI, built into git):
```
https://yourserver.com/git/<username>/
```

Users clone from this URL. No Gitea, no GitHub — just git's own HTTP backend.
Requires `git` to be installed on the server (always true on any Linux VPS).

---

## Web reader

- URL: `https://yourserver.com/wiki/<username>`
- Auth: bearer token in URL param `?token=...` or Authorization header
- Read-only — no editing through the UI
- Pages rendered as HTML from markdown
- Search bar (calls `search_wiki` tool internally)
- Sidebar with page tree mirroring the wiki folder structure
- Responsive — works on mobile

Tech: FastAPI + Jinja2 templates + minimal CSS. No JS framework.

---

## Server config file (wikimcp.conf)

Location: `<server-dir>/wikimcp.conf`
Format: JSON

```json
{
  "version": "0.1",
  "port": 8765,
  "host": "0.0.0.0",
  "users": {
    "mohith": {
      "token_hash": "<sha256 of token>",
      "wiki_dir": "/data/wikimcp/users/mohith",
      "created_at": "2026-04-14T10:00:00Z",
      "last_active": "2026-04-14T12:00:00Z",
      "remotes": {
        "github": "git@github.com:mohith/my-wiki.git"
      },
      "auto_push_remotes": ["github"]
    }
  }
}
```

Token is stored as SHA-256 hash — never plaintext.

---

## Wiki folder structure (per user)

```
<wiki-dir>/
  CLAUDE.md              ← schema (copied from package on init)
  wiki/
    index.md             ← master catalog
    log.md               ← append-only activity log
    chats/               ← one page per conversation
    topics/              ← concept and topic pages
    entities/            ← people, tools, projects
  raw/                   ← user's source documents (never modified by AI)
  .git/                  ← git repo
```

---

## CLAUDE.md (schema baked into package)

Shipped as a template inside the package at `wikimcp/templates/CLAUDE.md`.
Copied to each user's wiki root on `init` / `add-user`.

Contains:
- Directory structure explanation
- Page frontmatter conventions
- Post-chat workflow (what the AI should do at end of every session)
- Ingest workflow
- Query workflow
- Lint workflow
- index.md format
- Tone and style guidelines

---

## systemd service (Linux)

`wikimcp install-service` writes to `/etc/systemd/system/wikimcp.service`:

```ini
[Unit]
Description=wikimcp MCP server
After=network.target

[Service]
Type=simple
User=wikimcp
ExecStart=/usr/local/bin/wikimcp server start --dir /data/wikimcp --port 8765
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then runs `systemctl daemon-reload && systemctl enable wikimcp && systemctl start wikimcp`.

---

## pyproject.toml (key fields)

```toml
[project]
name = "wikimcp"
version = "0.1.0"
description = "Git-backed multi-user wiki MCP server — LTM for any AI"
requires-python = ">=3.10"
dependencies = [
  "mcp[cli]>=1.0.0",
  "fastapi>=0.110.0",
  "uvicorn>=0.29.0",
  "jinja2>=3.1.0",
  "click>=8.1.0",
  "gitpython>=3.1.0",
  "rich>=13.0.0",
]

[project.scripts]
wikimcp = "wikimcp.cli:main"
```

---

## Dependencies rationale

| Package | Why |
|---------|-----|
| `mcp[cli]` | FastMCP — MCP server framework |
| `fastapi` | Web reader + HTTP MCP transport |
| `uvicorn` | ASGI server for FastAPI |
| `jinja2` | Web reader HTML templates |
| `click` | CLI framework |
| `gitpython` | Git operations (commit, push, remote management) |
| `rich` | Beautiful CLI output (token display, user tables) |

---

## Transport modes summary

| Transport | Command | Used by |
|-----------|---------|---------|
| stdio | `wikimcp serve` (default) | Claude Desktop, LM Studio, Gemini CLI, Claude Code |
| HTTP | `wikimcp serve --transport http` | ChatGPT, claude.ai browser (via ngrok) |
| HTTP | `wikimcp server start` | All clients (always-on server) |

---

## What is NOT in v0.1

- Self-serve signup (v0.2)
- Web UI editing (by design — AI writes, users read)
- Embedding support / vector search (v0.2)
- Webhooks (v0.2)
- Docker image (not needed — pip install is sufficient)
- OAuth / SSO (v0.2)
