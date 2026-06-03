# Changelog

All notable changes to wikimcp will be documented in this file.

## [0.1.5] - 2026-06-03

### Fixed
- streamable-HTTP transport returned HTTP 421 "Invalid Host header" for any
  Host other than localhost:<port> (e.g. host.docker.internal from a Docker
  client). The DNS-rebinding Host allowlist is now configurable via
  `--allowed-host` (repeatable) and `--allow-any-host`, defaulting to localhost only.
- `serverInfo.version` reported the MCP SDK version instead of wikimcp's own
  version; it now reports the wikimcp package version.

### Upgrade note
- If you run wikimcp over HTTP and connect from a non-localhost client
  (e.g. a Docker container via host.docker.internal), you MUST now pass
  `--allowed-host <that-host>` (or `--allow-any-host` on an isolated network),
  or requests will be rejected with HTTP 421. Operators upgrading an existing
  deployment must update their launch command (e.g. the systemd `ExecStart`)
  to add the appropriate `--allowed-host` flags.

## [0.1.1] - 2026-04-14

### Added
- Initial public release of wikimcp (0.1.0 was a name reservation)
- MCP server with 9 wiki tools (wiki_info, read_index, update_index, write_page, read_page, list_pages, search_wiki, append_log, delete_page)
- Git-backed wiki with auto-commit on every write
- Multi-user support with bearer token authentication
- Web reader UI (FastAPI + Jinja2) for browsing wikis
- CLI with full command set: init, serve, server management, user management, remote management, export, install-service
- systemd (Linux) and launchd (macOS) service installation
- CLAUDE.md schema template for AI workflow guidance
- Local mode (stdio/HTTP) and server mode (multi-user HTTP)
