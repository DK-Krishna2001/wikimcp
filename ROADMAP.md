# Roadmap

## v0.1 (current) — Foundation

Released. The core package is complete and usable.

- [x] 9 MCP tools (wiki_info, read_index, update_index, write_page, read_page, list_pages, search_wiki, append_log, delete_page)
- [x] Git-backed wiki with auto-commit on every write
- [x] Local mode (stdio + HTTP transport)
- [x] Multi-user server mode with bearer token auth
- [x] Web reader UI (FastAPI + Jinja2)
- [x] Git HTTP hosting (/git/<username>/)
- [x] CLI with 12 commands
- [x] CLAUDE.md schema with Karpathy's LLM Wiki workflows
- [x] systemd + launchd service installation
- [x] Export as zip/tar
- [x] GitHub Actions CI/CD for PyPI publish

## v0.2 — Smarter search, auth, and integrations

Making the wiki more powerful and easier to deploy.

- [ ] **Embedding search / vector search** — hybrid BM25 + vector search for better query results as wikis grow beyond what index.md scanning can handle *(groundwork shipped: `search_pages`/`retrieve_context` BM25-style ranked retrieval; embedding index + vector similarity still pending)*
- [ ] **OAuth / SSO** — Google, GitHub, or generic OIDC login instead of bearer tokens only
- [ ] **Self-serve signup** — users create their own accounts via a web form instead of admin running `add-user`
- [ ] **Webhooks** — notify external services (Slack, Discord, n8n) on wiki changes
- [ ] **Auto-push for local mode** — `wikimcp serve --remote origin` to push after every commit
- [ ] **Page history UI** — view git diff history for any page in the web reader
- [ ] **Frontmatter-based search filters** — filter by tags, date range, page type in search_wiki
- [ ] **Dataview-style queries** — query pages by frontmatter fields (tags, dates) like Obsidian's Dataview plugin
- [ ] **Image support** — handle image attachments in wiki pages, serve them in the web reader
- [ ] **Marp slide generation** — generate presentation slides from wiki pages

## v0.3 — Scale, teams, and extensibility

Making wikimcp work for teams and larger deployments.

- [ ] **Docker image** — `docker run wikimcp` with volume mounts for data
- [ ] **Team wikis** — shared wikis where multiple users can read/write the same wiki
- [ ] **Role-based access** — admin, editor, reader roles per wiki
- [ ] **Plugin system** — custom MCP tools that extend the wiki (e.g. a tool that generates flashcards from topic pages)
- [ ] **Wiki templates** — domain-specific starter templates (research wiki, book notes, project wiki)
- [ ] **Backup and restore** — scheduled backups, one-command restore
- [ ] **Metrics dashboard** — wiki growth, activity, most-linked pages, orphan detection
- [ ] **Multi-wiki per user** — separate wikis for different domains (work, personal, research)
- [ ] **Conflict resolution UI** — resolve git merge conflicts in the web reader instead of the command line
- [ ] **Mobile-friendly web reader** — improved mobile layout, swipe navigation

## Contributing

We welcome contributions! Check the [issues](https://github.com/mohith-das/wikimcp/issues) for tasks labeled `good first issue` or `help wanted`. See the milestone labels (`v0.2`, `v0.3`) to understand what's planned.
