[![PyPI version](https://badge.fury.io/py/wikimcp.svg)](https://pypi.org/project/wikimcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

# wikimcp

**Long-term memory for AI — a git-backed personal wiki that any AI client can read and write to.**

Inspired by [Andrej Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

---

## Why this exists

Most people's experience with AI and documents is RAG: upload files, the AI retrieves chunks at query time, generates an answer. The AI rediscovers knowledge from scratch on every question. Nothing accumulates.

wikimcp is different. Instead of retrieving from raw documents each time, **the AI incrementally builds and maintains a personal wiki** — a structured, interlinked collection of markdown files. When you discuss a topic, the AI doesn't just respond — it updates topic pages, creates entity entries, cross-references related concepts, and logs what happened. The knowledge is compiled once and kept current, not re-derived every session.

**The wiki is a persistent, compounding artifact.** Cross-references are already there. Contradictions get flagged. The synthesis reflects everything you've ever discussed. It keeps getting richer with every conversation.

You never write the wiki yourself — the AI writes and maintains all of it. You curate sources, direct the analysis, and ask good questions. The AI does all the bookkeeping — summarising, cross-referencing, filing, and maintenance.

---

## How it works

Three layers:

1. **Raw sources** (`raw/`) — your documents. Articles, papers, notes. The AI reads from these but never modifies them.
2. **The wiki** (`wiki/`) — AI-generated markdown files. Topic pages, entity pages, chat summaries, the index. The AI owns this layer entirely.
3. **The schema** (`CLAUDE.md`) — tells the AI how the wiki is structured and what workflows to follow. Delivered automatically at the start of every session via `wiki_info`.

The AI performs three operations:

- **Ingest** — you drop a source into `raw/`, the AI reads it, extracts key information, creates/updates wiki pages, updates the index and log. A single source can touch 10-15 pages.
- **Query** — you ask a question, the AI reads the index, navigates to relevant pages, synthesises an answer. Good answers get filed back into the wiki as new pages.
- **Lint** — periodically health-check for contradictions, orphan pages, stale claims, missing cross-references.

Every write is auto-committed to git. Full history, branching, and remote sync.

---

## Use cases

- **Personal knowledge** — goals, health, self-improvement, journal entries, article notes. Build a structured picture of yourself over time.
- **Research** — go deep on a topic over weeks. Read papers, articles, reports. The wiki builds a comprehensive synthesis with an evolving thesis.
- **Reading a book** — file each chapter, build pages for characters, themes, plot threads. By the end you have a rich companion wiki.
- **Work** — meeting notes, project decisions, team knowledge. The wiki stays current because the AI does the maintenance nobody wants to do.
- **Learning** — course notes, concept maps, practice problems. Knowledge accumulates instead of fading.

---

## Quick start

```bash
pip install wikimcp
```

### Set up the wiki

```bash
# Default location: ~/llm-wiki
wikimcp init

# Or pick your own directory
wikimcp init --wiki-dir ~/my-wiki
```

This creates the wiki folder structure, initialises a git repo, and prints a JSON config snippet.

### Connect to Claude Desktop

Copy the printed JSON and paste it into your Claude Desktop config:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Linux:** `~/.config/Claude/claude_desktop_config.json`

The snippet looks like:

```json
{
  "mcpServers": {
    "wikimcp": {
      "command": "wikimcp",
      "args": ["serve", "--wiki-dir", "/Users/you/my-wiki"]
    }
  }
}
```

Restart Claude Desktop.

### Start using it

Open a new Claude conversation. The AI will call `wiki_info` at the start and receive the full wiki schema with workflow instructions.

Just have a normal conversation — discuss a topic, share an idea, ask the AI to process a document. At the end of the session, the AI should automatically:

- Create/update topic and entity pages
- Write a chat summary
- Append to the activity log
- Update the index

If it doesn't do this automatically, tell Claude: **"update the wiki with what we discussed"** — or add this to your Claude Desktop system prompt for hands-free operation:

> You have a wiki connected via MCP. Call wiki_info at the start of every conversation. At the end, update relevant wiki pages, write a chat summary, append to the log, and update the index.

### Browse in Obsidian

Open your wiki folder in [Obsidian](https://obsidian.md/). You'll see every page the AI creates in real time — topics, entities, chat summaries, the index. Use Obsidian's graph view to see how everything connects.

---

## Sync with GitHub

The wiki is a git repo from the start. To back it up to GitHub:

```bash
cd ~/my-wiki
gh repo create my-wiki --private --source=. --remote=origin --push
```

Then push periodically:

```bash
cd ~/my-wiki && git push
```

Or set up a cron job to auto-push every 5 minutes:

```bash
# add to crontab -e
*/5 * * * * cd ~/my-wiki && git push origin main 2>/dev/null
```

---

## Wiki folder structure

```
~/my-wiki/
  CLAUDE.md              ← schema and workflow guide (AI reads this at session start)
  wiki/
    index.md             ← master catalog of all pages
    log.md               ← append-only activity log
    chats/               ← one page per conversation
    topics/              ← concept and topic pages
    entities/            ← people, tools, projects
  raw/                   ← your source documents (AI never modifies these)
  .git/                  ← full git history
```

---

## MCP tools

11 tools exposed to any MCP-compatible AI client:

| Tool | Arguments | Description |
|------|-----------|-------------|
| `wiki_info` | — | Wiki stats + full schema instructions. Called at session start. |
| `read_index` | — | Read `wiki/index.md` — the master catalog. |
| `update_index` | `content` | Overwrite `wiki/index.md` and auto-commit. |
| `write_page` | `path`, `content` | Create or overwrite a wiki page and auto-commit. |
| `read_page` | `path` | Read a wiki page. |
| `list_pages` | `subdirectory?` | List all pages (or a subdirectory). |
| `search_wiki` | `query`, `case_sensitive?` | Hybrid BM25 + vector search when index exists; regex fallback otherwise. |
| `search_pages` | `query`, `limit?` | Rank wiki pages by relevance and return compact snippets. |
| `retrieve_context` | `query`, `limit?` | Return contextual snippets from the most relevant pages. |
| `append_log` | `entry`, `operation?` | Append timestamped entry to `wiki/log.md` and auto-commit. |
| `delete_page` | `path` | Delete a wiki page and auto-commit. |

Every write triggers an auto git commit with author `wikimcp-bot <wikimcp@localhost>`.

---

## Server mode (multi-user)

For teams or hosting wikis for multiple people on a VPS.

### Setup

```bash
# 1. Install
pip install wikimcp

# 2. Initialise server
wikimcp server init --dir /data/wikimcp --port 8765

# 3. Add users
wikimcp add-user alice --dir /data/wikimcp
wikimcp add-user bob   --dir /data/wikimcp

# 4. Start server
wikimcp server start --dir /data/wikimcp

# 5. (Optional) Install as system service
sudo wikimcp install-service --dir /data/wikimcp --port 8765
```

### What you get

- **MCP endpoint:** `http://host:port/mcp` (bearer-token auth)
- **Web reader:** `http://host:port/wiki/<username>` (read-only HTML UI)
- **Git hosting:** `http://host:port/git/<username>` (clone/push over HTTP)
- **Health check:** `http://host:port/health`

### User config

Each user configures their AI client with:

```json
{
  "mcpServers": {
    "wikimcp": {
      "url": "https://yourserver.com/mcp",
      "headers": { "Authorization": "Bearer wikimcp_alice_<token>" }
    }
  }
}
```

### Auth

Bearer token per user. Tokens stored as SHA-256 hashes — never plaintext. Manage with `wikimcp add-user`, `remove-user`, `rotate-token`, `list-users`.

---

## CLI reference

```
wikimcp init [--wiki-dir ~/llm-wiki]
wikimcp rebuild-search-index [--wiki-dir ~/llm-wiki]
wikimcp serve [--wiki-dir] [--transport stdio|http] [--host] [--port] [--allowed-host ...] [--allow-any-host]

wikimcp server init [--dir /data/wikimcp] [--port 8765]
wikimcp server start [--dir] [--port] [--host] [--allowed-host ...] [--allow-any-host]
wikimcp server stop
wikimcp server status [--dir]

wikimcp add-user <username> [--dir]
wikimcp remove-user <username> [--dir]
wikimcp list-users [--dir]
wikimcp rotate-token <username> [--dir]

wikimcp remote add <username> <remote-name> <git-url> [--dir]
wikimcp remote remove <username> <remote-name> [--dir]
wikimcp remote list <username> [--dir]
wikimcp remote push <username> [--remote <name>] [--dir]

wikimcp export <username> [--format zip|tar] [--out ./] [--dir]
wikimcp install-service [--dir] [--port]
```

---

## Transport modes

| Transport | Command | Used by |
|-----------|---------|---------|
| stdio | `wikimcp serve` (default) | Claude Desktop, LM Studio, Gemini CLI, Claude Code |
| HTTP | `wikimcp serve --transport http` | ChatGPT, claude.ai (via ngrok) |
| HTTP | `wikimcp server start` | All clients (always-on multi-user server) |

### Host header allowlist (HTTP transport)

The MCP SDK enforces DNS-rebinding protection on the streamable-HTTP transport: by
default it accepts the `Host` header only when it is `localhost` or `127.0.0.1`
(with any port). Any other host — including `host.docker.internal` from a client
running in a Docker container — is rejected with **HTTP 421 "Invalid Host header"**.

To allow additional hosts, pass `--allowed-host` (repeatable) to `wikimcp serve`
or `wikimcp server start`:

```bash
# Allow a LibreChat / Docker client connecting via host.docker.internal
wikimcp serve --transport http --host 0.0.0.0 --port 8765 \
    --wiki-dir ~/llm-wiki \
    --allowed-host host.docker.internal --allowed-host 100.85.70.2
```

On a fully trusted/isolated network you can disable the check entirely with
`--allow-any-host`. **Security note:** `--allow-any-host` turns off host/origin
validation completely — only use it behind a firewall or VPN, never on a host
exposed to untrusted networks.

> **Upgrade note:** Versions before 0.1.5 had a different (and in some
> deployments hot-patched) host policy. Starting in 0.1.5 the default is
> localhost-only, so if you connect from a non-localhost client you **must**
> add `--allowed-host <that-host>` (or `--allow-any-host`). Operators upgrading
> an existing deployment must update their launch command — e.g. the systemd
> `ExecStart`:
>
> ```
> wikimcp serve --transport http --host 0.0.0.0 --port 8765 \
>     --wiki-dir /home/ubuntu/wikimcp-data/users/mohith \
>     --allowed-host host.docker.internal --allowed-host 100.85.70.2
> ```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `mcp[cli]` | FastMCP — MCP server framework |
| `fastapi` | Web reader + HTTP transport |
| `uvicorn` | ASGI server |
| `jinja2` | HTML templates |
| `click` | CLI framework |
| `gitpython` | Git operations |
| `rich` | Terminal formatting |
| `markdown` | Markdown-to-HTML rendering |

---

## Credits

Based on the [LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) by Andrej Karpathy — the idea that LLMs should incrementally build and maintain a persistent wiki rather than re-deriving knowledge on every query.

## License

MIT
