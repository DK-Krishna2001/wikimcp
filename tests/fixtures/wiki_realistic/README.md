# `wiki_realistic` test fixture

A hand-authored, real-world-shaped knowledge wiki used to ground graph tests in
**known truth**. The graph built from these pages is deterministic; the facts
below are asserted directly by the test suite.

Build it with `graph.build_graph(<this dir>)` — pages live under `wiki/`.

## Shape

- **24 pages** across six top-level sections: `topics/`, `entities/`,
  `projects/`, `journal/`, `hobbies/`, `misc/`.
- **69 edges**: 29 EXTRACTED (25 `wikilink`, 4 `related`) and 40 INFERRED
  (12 `title-mention`, 28 `shared-tag`).

## Clusters

- **ML / AI** (`topics/`): Machine Learning, Neural Networks, Deep Learning,
  Gradient Descent, Transformers, Attention Mechanism — tied together by
  wikilinks, markdown links, title-mentions, and the `ai`/`ml` tags.
- **Programming** (`topics/`): Python, NumPy, Pandas.
- **Productivity** (`topics/`): Productivity, Deep Work, Time Blocking
  (`habits`/`work` tags).
- **People / projects** (`entities/`, `projects/`): Alice Johnson, Bob Smith,
  Charlie Davis, PyTorch, Project Falcon, Project Phoenix.

## Known facts asserted by tests

### Hubs
- **`topics/machine-learning.md` is the top hub** — degree-centrality total `9`
  (in-degree `8`, out-degree `6`). `projects/project-falcon.md` is second.

### Backlinks (`get_related` direction="in")
- Machine Learning is referenced by, among others:
  - `projects/project-falcon.md` and `topics/gradient-descent.md` via
    **EXTRACTED `wikilink`**,
  - `entities/alice.md`, `journal/2026-02-20.md`, `topics/deep-learning.md`,
    `topics/neural-networks.md` via **INFERRED `title-mention`**.

### Orphans & dead-ends
- **Orphans** (no edges at all): `misc/standalone.md`, `topics/orphan-idea.md`.
- **Dead-ends** (inbound edges but no outbound): `entities/charlie.md`,
  `topics/attention.md`. (Both have no tags, so they pick up no bidirectional
  `shared-tag` edges; they are only ever linked *to*.)

### Surprising links (cross-domain inferred connections)
- The top surprising pair is **`hobbies/chess.md` ↔ `misc/cooking.md`** — they
  title-mention each other, share **zero** tags, sit in different sections, and
  have **0 common neighbours**. This is the deliberate cross-domain pair.
- Journal entries also surface (e.g. `journal/2026-02-20.md` ↔ Python / ML), but
  rank below chess/cooking because they have ≥1 common neighbour.

### Paths
- `topics/machine-learning.md` → `topics/time-blocking.md`: a path of length `2`
  exists (via the `work`/`habits`-tag bridge through the productivity cluster).
- `topics/attention.md` → `misc/cooking.md`: **no path** — these are in
  different connected components.

### Title-mention guard
- `entities/charlie.md` ("Charlie Davis") and `topics/attention.md` ("Attention
  Mechanism") are never *mentioned* by other pages, so they have no inbound
  title-mention edges beyond their explicit wikilinks.
- No degenerate hub exists: no single page is connected to a large fraction of
  the wiki purely through title-mention. Short/common titles do not explode.
