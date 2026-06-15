# CLAUDE.md — Wiki Schema & Workflow Guide

Read this at the start of every session. It tells you how this wiki works,
how knowledge is organised, and what you must do to maintain it.

---

## The Core Idea

This wiki is a **persistent, compounding knowledge base** — not a chat log,
not RAG. Instead of re-deriving knowledge from raw documents on every query,
you incrementally build and maintain a structured collection of interlinked
markdown pages. When a new source arrives you don't just index it — you read
it, extract the key information, and integrate it into existing pages.
Cross-references are already there. Contradictions have already been flagged.
The synthesis reflects everything the user has read and discussed.

The wiki keeps getting richer with every source added and every question asked.

**Division of labour:**
- The **user** curates sources, directs analysis, and asks good questions.
- **You** (the AI) do all the bookkeeping — summarising, cross-referencing,
  filing, and maintenance that makes a knowledge base useful over time.

The user never (or rarely) writes wiki pages directly. You write and maintain
all of it. The user reads the results in Obsidian, the web reader, or by
asking you questions.

---

## Three Layers

1. **Raw sources** (`raw/`) — the user's curated documents. Articles, papers,
   notes, images, data files. These are immutable — you read from them but
   **never modify them**. This is the source of truth.

2. **The wiki** (`wiki/`) — your generated markdown files. Summaries, entity
   pages, topic pages, comparisons, chat records. You own this layer entirely.
   You create pages, update them when new sources arrive, maintain
   cross-references, and keep everything consistent.

3. **The schema** (this file) — tells you how the wiki is structured, what
   conventions to follow, and what workflows to execute. You and the user
   co-evolve this over time.

---

## Directory Structure

```
<wiki-dir>/
  CLAUDE.md              ← this file — schema and workflow guide
  wiki/
    index.md             ← master catalog (read this first on every query)
    log.md               ← append-only activity log (one entry per session)
    chats/               ← one page per conversation (chat_YYYY-MM-DD_N.md)
    topics/              ← concept and topic pages
    entities/            ← people, tools, projects
  raw/                   ← user's source documents — never modify these
  .git/                  ← git repo — every write is auto-committed
```

---

## Page Frontmatter

Every wiki page (except log.md) should begin with YAML front matter:

```yaml
---
title: "Page Title"
tags: [tag1, tag2]
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

- Update the `updated` field on every write
- Omit fields you don't have values for

---

## Operations

### Ingest

When the user drops a new source into `raw/` and asks you to process it:

1. Read the source from `raw/<filename>`
2. Discuss key takeaways with the user if they're present
3. Extract entities, topics, and facts
4. Create or update pages under `wiki/topics/` and `wiki/entities/` — a
   single source might touch 10–15 wiki pages
5. Write a summary page under `wiki/chats/`
6. Update `wiki/index.md` to include new pages
7. Append to `wiki/log.md` with `operation="ingest"`

Do NOT modify anything inside `raw/`.

### Query

When the user asks a question:

1. Call `read_index` to load `wiki/index.md` — this is your map
2. Use `retrieve_context` when you need the most relevant snippets for a topic
3. Use `search_pages` to discover ranked pages, then `read_page` when full-page context is needed
4. If you need exact line matches, use `search_wiki`
5. Synthesise and answer — cite the pages you used

**Important:** good answers should be filed back into the wiki as new pages.
A comparison you produced, an analysis, a connection you discovered — these
are valuable and should not disappear into chat history. This way the user's
explorations compound in the knowledge base just like ingested sources do.

Do NOT guess or hallucinate wiki content. Always read first.

### Lint

Periodically (or when the user asks), health-check the wiki. Look for:

- **Contradictions** between pages that newer sources have superseded
- **Stale claims** that need updating
- **Orphan pages** with no inbound links from other pages
- **Missing pages** — important concepts mentioned but lacking their own page
- **Missing cross-references** between related pages
- **Data gaps** that could be filled with a web search or new source

Suggest new questions to investigate and new sources to look for. This keeps
the wiki healthy as it grows. Log lint passes with `operation="lint"`.

### Post-Chat

At the end of every conversation session, you MUST:

1. **Update relevant pages** — for every topic, entity, or concept discussed,
   update or create its page under `wiki/topics/` or `wiki/entities/`.

2. **Write a chat summary** — create `wiki/chats/chat_YYYY-MM-DD_N.md` with:
   - A 2–5 sentence summary of what was discussed
   - Key decisions or conclusions
   - Links to pages that were updated

3. **Append to the log** — call `append_log` with a one-line summary and
   `operation="chat"`.

4. **Update index.md** — if new pages were created or significant changes
   were made.

---

## Indexing & Logging

**index.md** is content-oriented — a catalog of everything in the wiki. Each
page listed with a link, a one-line summary, organised by category. You update
it on every ingest and significant change. When answering a query, read the
index first to find relevant pages, then drill into them.

```markdown
# Wiki Index

_Last updated: YYYY-MM-DD_

## Topics
- [Graph Algorithms](topics/graph-algorithms.md) — shortest paths, MSTs, BFS/DFS
- [Python](topics/python.md) — language notes, libraries, patterns

## Entities
- [Alice Smith](entities/alice-smith.md) — colleague, ML engineer
- [Project Falcon](entities/project-falcon.md) — Q3 2026 initiative

## Recent Activity
- YYYY-MM-DD: [Chat summary](chats/chat_YYYY-MM-DD_1.md)
```

Keep sections alphabetically sorted. Update the "Last updated" date on every
modification. "Recent Activity" shows the last 5–10 entries.

**log.md** is chronological — an append-only record of what happened and when
(ingests, queries, chats, lint passes). Each entry starts with a heading like
`## 2026-04-14T10:30:00Z [ingest]` so it's parseable and scannable.

---

## Tone & Style

- **Concise** — bullet points and short paragraphs over prose
- **Factual** — record what was said or decided; no speculation
- **Linked** — always cross-reference related pages with relative markdown links
- **Dated** — include explicit dates, never "recently" or "last week"
- **Neutral** — third person or imperative; no "I" or "we"
- **Evergreen** — write pages so they remain useful weeks or months later

---

## File Naming

| Location | Pattern | Example |
|----------|---------|---------|
| `wiki/chats/` | `chat_YYYY-MM-DD_N.md` | `chat_2026-04-14_1.md` |
| `wiki/topics/` | `kebab-case.md` | `graph-algorithms.md` |
| `wiki/entities/` | `kebab-case.md` | `alice-smith.md` |
| `raw/` | any (user's files) | `paper.pdf`, `notes.txt` |

---

## Rules

- Never modify files inside `raw/`
- Never delete `index.md` or `log.md`
- Never write outside the `wiki/` directory
- Always use paths relative to `wiki/` (e.g. `topics/python.md`)
- File back valuable answers as new wiki pages — explorations should compound
- When new information contradicts existing pages, update the pages and note
  the change
