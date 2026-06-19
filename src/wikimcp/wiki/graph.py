"""
graph.py — Deterministic, offline DIRECTED page-graph for the wiki.

This module builds an in-memory directed graph from the markdown pages under
``wiki/`` and caches it. It performs NO model calls, NO embeddings, NO
clustering — only deterministic, dependency-light parsing and string matching.
Building the same wiki twice always yields byte-identical node and edge sets.

Graph model
-----------
* **Nodes** = wiki pages, keyed by their POSIX path relative to ``wiki/``
  (e.g. ``topics/python.md``). ``index.md`` and ``log.md`` are bookkeeping and
  are excluded.
* **Edges** are DIRECTED (``source -> target``); this is what gives backlinks.

Edge inventory
--------------
EXTRACTED (author-declared, high confidence), directed ``source -> target``:
  * ``wikilink``  — an explicit ``[[wikilink]]`` or markdown link that resolves
    to another wiki page, found in the page body.
  * ``related``   — a link found under a ``## Related`` / ``Related:`` section.

INFERRED (derived, lower confidence):
  * ``title-mention`` (directed) — page A's title appears as a whole-word phrase
    in page B's body and B has no explicit link to A, giving an edge ``B -> A``.
    Guarded against degenerate matches (see ``TITLE_MENTION_*`` constants below).
  * ``shared-tag`` (bidirectional) — two pages share >= 1 frontmatter tag;
    symmetric, ``weight`` = number of shared tags.

Each :class:`Edge` carries ``source``, ``target``, ``type``
(``EXTRACTED``/``INFERRED``), ``subtype``
(``wikilink``/``related``/``title-mention``/``shared-tag``), ``direction``
(``directed``/``bidirectional``) and ``weight``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Pages that are bookkeeping rather than knowledge — never become graph nodes.
EXCLUDED_PAGES = frozenset({"index.md", "log.md"})

#: Edge type labels.
EXTRACTED = "EXTRACTED"
INFERRED = "INFERRED"

#: Edge direction labels.
DIRECTED = "directed"
BIDIRECTIONAL = "bidirectional"

#: --- Title-mention specificity guard ----------------------------------------
#: A page title must be at least this many characters before it is eligible to
#: produce title-mention edges. This stops trivially short titles (e.g. "AI",
#: "Go") from matching half the wiki. Documented, tunable constant.
TITLE_MENTION_MIN_CHARS = 4

#: A single-token title must be at least this long to be eligible. Multi-word
#: titles are inherently specific and only need to clear MIN_CHARS.
TITLE_MENTION_MIN_TOKEN_LEN = 4

#: Common/generic single-word titles that must NEVER explode into title-mention
#: edges even though they clear the length thresholds. A page titled "Notes"
#: must not link to every page that contains the word "notes".
TITLE_MENTION_STOPWORDS = frozenset(
    {
        "notes", "note", "index", "home", "todo", "todos", "misc", "ideas",
        "idea", "log", "logs", "readme", "page", "pages", "wiki", "draft",
        "drafts", "inbox", "untitled", "overview", "summary", "intro",
        "introduction", "general", "other", "others", "stuff", "things",
        "thing", "info", "data", "list", "lists", "the", "and", "for", "with",
        "about", "this", "that", "from", "into", "main", "test", "temp",
    }
)


# ---------------------------------------------------------------------------
# Tokenisation / text helpers
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
_WIKILINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_RELATED_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*related\b", re.IGNORECASE)
_RELATED_INLINE_RE = re.compile(r"^\s*related\s*:", re.IGNORECASE)
_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "ftp://", "tel:")


def _tokenize(text: str) -> List[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


def _humanize_stem(stem: str) -> str:
    return stem.replace("-", " ").replace("_", " ").strip()


# ---------------------------------------------------------------------------
# Frontmatter parsing (minimal, no external YAML dependency)
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Split a page into (frontmatter dict, body).

    Supports the subset of YAML wikimcp documents in CLAUDE.md:
      * ``title: "..."`` / ``title: ...``
      * ``tags: [a, b]`` (inline flow list)
      * ``tags:`` followed by ``  - item`` block-list lines

    Returns ``({}, text)`` when no frontmatter block is present. Dependency-light
    and deterministic — it never executes arbitrary YAML.
    """
    if not text.startswith("---"):
        return {}, text

    lines = text.splitlines(keepends=True)
    # First line must be a bare '---' fence.
    if lines[0].strip() != "---":
        return {}, text

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, text

    fm_lines = lines[1:end_idx]
    body = "".join(lines[end_idx + 1 :])

    meta: Dict[str, Any] = {}
    pending_list_key: Optional[str] = None
    for raw in fm_lines:
        line = raw.rstrip("\n")
        if not line.strip():
            pending_list_key = None
            continue

        # Block-list continuation: "  - value"
        stripped = line.strip()
        if pending_list_key and stripped.startswith("- "):
            value = _strip_scalar(stripped[2:].strip())
            if value:
                meta.setdefault(pending_list_key, [])
                if isinstance(meta[pending_list_key], list):
                    meta[pending_list_key].append(value)
            continue

        pending_list_key = None
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()

        if not value:
            # Possibly a block list follows.
            pending_list_key = key
            continue

        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            items = [
                _strip_scalar(item.strip())
                for item in inner.split(",")
                if item.strip()
            ]
            meta[key] = [item for item in items if item]
        else:
            meta[key] = _strip_scalar(value)

    return meta, body


def _strip_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _extract_title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        match = _HEADING_RE.match(line)
        if match and match.group(1) == "#":
            heading = re.sub(r"\s+#*\s*$", "", match.group(2)).strip()
            if heading:
                return heading
    return fallback


def _normalize_tags(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        return ()
    tags = []
    seen = set()
    for tag in raw:
        norm = str(tag).strip().lower()
        if norm and norm not in seen:
            seen.add(norm)
            tags.append(norm)
    return tuple(sorted(tags))


# ---------------------------------------------------------------------------
# Per-page parsed data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PageData:
    """Everything parsed from a single page, independent of other pages."""

    path: str
    title: str
    tags: Tuple[str, ...]
    top_dir: str
    body: str
    # Raw link targets found in the body, with whether they came from a
    # "## Related" section. Resolution to page paths happens at graph-build time
    # because it depends on the full page set.
    links: Tuple[Tuple[str, bool], ...]  # (target_string, is_related)
    created: str = ""
    updated: str = ""


def _top_dir(path: str) -> str:
    parts = Path(path).parts
    return parts[0] if len(parts) > 1 else ""


def _extract_links(body: str) -> List[Tuple[str, bool]]:
    """Return [(target_string, is_related), ...] for every link in the body."""
    lines = body.splitlines()
    related_flags = _related_line_flags(lines)
    links: List[Tuple[str, bool]] = []
    for idx, line in enumerate(lines):
        is_related = related_flags[idx]
        for m in _WIKILINK_RE.finditer(line):
            target = m.group(1).split("|", 1)[0]
            links.append((target, is_related))
        for m in _MD_LINK_RE.finditer(line):
            links.append((m.group(1), is_related))
    return links


def _related_line_flags(lines: List[str]) -> List[bool]:
    """Mark which lines belong to a Related section.

    A ``## Related`` heading opens a section that runs until the next heading.
    A ``Related:`` inline label marks its own line and following non-blank lines
    until a blank line.
    """
    flags = [False] * len(lines)
    in_related_heading = False
    in_related_inline = False
    for idx, line in enumerate(lines):
        is_heading = bool(_HEADING_RE.match(line))
        if _RELATED_HEADING_RE.match(line):
            in_related_heading = True
            in_related_inline = False
            continue
        if is_heading:
            in_related_heading = False
        if _RELATED_INLINE_RE.match(line):
            in_related_inline = True
            flags[idx] = True
            continue
        if in_related_inline and not line.strip():
            in_related_inline = False
        flags[idx] = in_related_heading or in_related_inline
    return flags


def parse_page(rel_path: str, text: str) -> PageData:
    """Parse a single page's raw text into :class:`PageData`."""
    meta, body = parse_frontmatter(text)
    fallback = _humanize_stem(Path(rel_path).stem)
    fm_title = meta.get("title")
    title = (str(fm_title).strip() if fm_title else "") or _extract_title(
        body, fallback
    )
    tags = _normalize_tags(meta.get("tags"))
    links = tuple(_extract_links(body))
    return PageData(
        path=rel_path,
        title=title,
        tags=tags,
        top_dir=_top_dir(rel_path),
        body=body,
        links=links,
        created=str(meta.get("created", "")).strip(),
        updated=str(meta.get("updated", "")).strip(),
    )


# ---------------------------------------------------------------------------
# Edge + Graph
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    type: str       # EXTRACTED | INFERRED
    subtype: str    # wikilink | related | title-mention | shared-tag
    direction: str  # directed | bidirectional
    weight: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.type,
            "subtype": self.subtype,
            "direction": self.direction,
            "weight": self.weight,
        }


#: Ranking helpers — EXTRACTED beats INFERRED, and within those a stable
#: subtype order. Lower number == higher priority.
_TYPE_RANK = {EXTRACTED: 0, INFERRED: 1}
_SUBTYPE_RANK = {"related": 0, "wikilink": 1, "title-mention": 2, "shared-tag": 3}


def _edge_sort_key(edge: Edge) -> Tuple:
    return (
        edge.source,
        edge.target,
        _TYPE_RANK.get(edge.type, 9),
        _SUBTYPE_RANK.get(edge.subtype, 9),
    )


def edge_priority(edge: Edge) -> Tuple[int, int, int]:
    """Priority for ranking related/subgraph results.

    Sort ascending: EXTRACTED first, then better subtype, then higher weight.
    """
    return (
        _TYPE_RANK.get(edge.type, 9),
        _SUBTYPE_RANK.get(edge.subtype, 9),
        -edge.weight,
    )


class Graph:
    """An immutable directed page graph with deterministic adjacency."""

    def __init__(self, pages: Dict[str, PageData], edges: List[Edge]):
        self.pages = pages
        self.edges = sorted(edges, key=_edge_sort_key)
        self._build_adjacency()

    # -- construction --------------------------------------------------------

    def _build_adjacency(self) -> None:
        out: Dict[str, List[Edge]] = {p: [] for p in self.pages}
        inc: Dict[str, List[Edge]] = {p: [] for p in self.pages}
        undirected: Dict[str, List[Tuple[str, Edge]]] = {p: [] for p in self.pages}

        for edge in self.edges:
            if edge.direction == BIDIRECTIONAL:
                out.setdefault(edge.source, []).append(edge)
                out.setdefault(edge.target, []).append(edge)
                inc.setdefault(edge.source, []).append(edge)
                inc.setdefault(edge.target, []).append(edge)
                undirected.setdefault(edge.source, []).append((edge.target, edge))
                undirected.setdefault(edge.target, []).append((edge.source, edge))
            else:
                out.setdefault(edge.source, []).append(edge)
                inc.setdefault(edge.target, []).append(edge)
                undirected.setdefault(edge.source, []).append((edge.target, edge))
                undirected.setdefault(edge.target, []).append((edge.source, edge))

        # Deterministic ordering. Among edges to the same neighbour, prefer the
        # stronger edge (EXTRACTED before INFERRED, higher weight) so traversal
        # reports the clearest connection.
        for adj in undirected.values():
            adj.sort(
                key=lambda item: (
                    item[0],
                    edge_priority(item[1]),
                    _edge_sort_key(item[1]),
                )
            )
        self._out = out
        self._in = inc
        self._undirected = undirected

    # -- accessors -----------------------------------------------------------

    def has_page(self, path: str) -> bool:
        return path in self.pages

    def title_of(self, path: str) -> str:
        page = self.pages.get(path)
        return page.title if page else _humanize_stem(Path(path).stem)

    def resolve(self, page: str) -> Optional[str]:
        """Resolve a user-supplied page reference to a node path.

        Accepts an exact wiki-relative path, a path missing the ``.md`` suffix,
        a page title, or a filename stem. Returns None when nothing matches.
        Resolution is deterministic (first match by sorted path order).
        """
        if not page:
            return None
        candidate = _normalize_relpath(page.strip())
        if candidate in self.pages:
            return candidate
        if not candidate.endswith(".md") and f"{candidate}.md" in self.pages:
            return f"{candidate}.md"
        lower = page.strip().lower()
        for path in sorted(self.pages):
            if self.pages[path].title.strip().lower() == lower:
                return path
        stem = Path(page.strip()).stem.lower()
        for path in sorted(self.pages):
            if Path(path).stem.lower() == stem:
                return path
        return None

    def out_edges(self, path: str) -> List[Edge]:
        return list(self._out.get(path, []))

    def in_edges(self, path: str) -> List[Edge]:
        return list(self._in.get(path, []))

    def out_neighbors(self, path: str) -> set:
        result = set()
        for edge in self._out.get(path, []):
            other = edge.target if edge.source == path else edge.source
            result.add(other)
        return result

    def in_neighbors(self, path: str) -> set:
        result = set()
        for edge in self._in.get(path, []):
            other = edge.source if edge.target == path else edge.target
            result.add(other)
        return result

    def all_neighbors(self, path: str) -> set:
        return self.out_neighbors(path) | self.in_neighbors(path)

    def undirected_adj(self, path: str) -> List[Tuple[str, Edge]]:
        return list(self._undirected.get(path, []))

    def signature(self) -> Tuple:
        """A hashable, order-independent fingerprint of the graph contents.

        Used by determinism tests to assert two builds are byte-identical.
        """
        node_sig = tuple(sorted(self.pages))
        edge_sig = tuple(
            (e.source, e.target, e.type, e.subtype, e.direction, e.weight)
            for e in self.edges
        )
        return (node_sig, edge_sig)


# ---------------------------------------------------------------------------
# Link resolution
# ---------------------------------------------------------------------------

class _Resolver:
    """Resolves raw link/title strings to page paths against a fixed page set."""

    def __init__(self, pages: Dict[str, PageData]):
        self._paths = set(pages)
        self._by_stem: Dict[str, str] = {}
        self._by_title: Dict[str, str] = {}
        for path, page in sorted(pages.items()):
            stem = Path(path).stem.lower()
            # First writer wins for deterministic resolution of collisions.
            self._by_stem.setdefault(stem, path)
            self._by_title.setdefault(page.title.strip().lower(), path)

    def resolve(self, target: str, source_path: str) -> Optional[str]:
        target = target.strip()
        # Drop anchors / queries.
        target = target.split("#", 1)[0].split("?", 1)[0].strip()
        if not target:
            return None
        if target.lower().startswith(_EXTERNAL_PREFIXES):
            return None
        if target.startswith("./"):
            target = target[2:]

        candidates: List[str] = []
        normals = [target]
        if not target.endswith(".md"):
            normals.append(target + ".md")
        for norm in normals:
            # Root-relative (paths in wiki are conventionally relative to wiki/).
            candidates.append(Path(norm).as_posix())
            # Directory-relative to the source page.
            src_dir = Path(source_path).parent
            candidates.append((src_dir / norm).as_posix())

        for cand in candidates:
            cand = _normalize_relpath(cand)
            if cand in self._paths:
                return cand

        # Fall back to title / stem lookup (covers [[Wikilinks by title]]).
        key = target.lower()
        if key.endswith(".md"):
            key = key[:-3]
        if target.lower() in self._by_title:
            return self._by_title[target.lower()]
        if key in self._by_stem:
            return self._by_stem[key]
        return None


def _normalize_relpath(path: str) -> str:
    parts: List[str] = []
    for part in Path(path).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


# ---------------------------------------------------------------------------
# Edge construction
# ---------------------------------------------------------------------------

def _title_mention_eligible(title: str) -> bool:
    """Apply the title-mention specificity guard.

    Returns False for titles that would create degenerate edges: too short,
    single common/stopword tokens, or stopword-only multi-word titles.
    """
    clean = title.strip()
    if len(clean) < TITLE_MENTION_MIN_CHARS:
        return False
    tokens = _tokenize(clean)
    if not tokens:
        return False
    # Every token is a stopword -> not specific enough.
    if all(tok in TITLE_MENTION_STOPWORDS for tok in tokens):
        return False
    if len(tokens) == 1:
        tok = tokens[0]
        if tok in TITLE_MENTION_STOPWORDS:
            return False
        if len(tok) < TITLE_MENTION_MIN_TOKEN_LEN:
            return False
    return True


def _title_pattern(title: str) -> re.Pattern:
    # Whole-word/phrase match, case-insensitive, whitespace-flexible.
    escaped = re.escape(title.strip())
    escaped = re.sub(r"\\\s+", r"\\s+", escaped)
    return re.compile(rf"\b{escaped}\b", re.IGNORECASE)


def _build_edges(pages: Dict[str, PageData]) -> List[Edge]:
    resolver = _Resolver(pages)
    edges: List[Edge] = []

    # --- EXTRACTED: wikilinks + related ---
    # explicit_targets[source] = set of resolved targets (suppresses title-mention)
    explicit_targets: Dict[str, set] = {p: set() for p in pages}
    # best extracted edge per (source, target)
    extracted: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for source in sorted(pages):
        page = pages[source]
        for target_str, is_related in page.links:
            target = resolver.resolve(target_str, source)
            if target is None or target == source:
                continue
            if target not in pages:
                continue
            explicit_targets[source].add(target)
            key = (source, target)
            subtype = "related" if is_related else "wikilink"
            slot = extracted.get(key)
            if slot is None:
                extracted[key] = {"subtype": subtype, "weight": 1}
            else:
                slot["weight"] += 1
                # "related" outranks plain "wikilink" for the pair.
                if subtype == "related":
                    slot["subtype"] = "related"

    for (source, target), info in extracted.items():
        edges.append(
            Edge(
                source=source,
                target=target,
                type=EXTRACTED,
                subtype=info["subtype"],
                direction=DIRECTED,
                weight=info["weight"],
            )
        )

    # --- INFERRED: title-mention (B mentions A's title -> B -> A) ---
    eligible_titles: List[Tuple[str, re.Pattern]] = []
    for path in sorted(pages):
        title = pages[path].title
        if _title_mention_eligible(title):
            eligible_titles.append((path, _title_pattern(title)))

    for b_path in sorted(pages):
        b_page = pages[b_path]
        b_body = b_page.body
        for a_path, pattern in eligible_titles:
            if a_path == b_path:
                continue
            if a_path in explicit_targets.get(b_path, set()):
                continue  # explicit link already present
            count = len(pattern.findall(b_body))
            if count <= 0:
                continue
            edges.append(
                Edge(
                    source=b_path,
                    target=a_path,
                    type=INFERRED,
                    subtype="title-mention",
                    direction=DIRECTED,
                    weight=count,
                )
            )

    # --- INFERRED: shared-tag (bidirectional) ---
    tag_to_pages: Dict[str, List[str]] = {}
    for path in sorted(pages):
        for tag in pages[path].tags:
            tag_to_pages.setdefault(tag, []).append(path)

    shared_counts: Dict[Tuple[str, str], int] = {}
    for tag, members in tag_to_pages.items():
        members = sorted(members)
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                key = (members[i], members[j])
                shared_counts[key] = shared_counts.get(key, 0) + 1

    for (a, b), weight in shared_counts.items():
        edges.append(
            Edge(
                source=a,
                target=b,
                type=INFERRED,
                subtype="shared-tag",
                direction=BIDIRECTIONAL,
                weight=weight,
            )
        )

    return edges


# ---------------------------------------------------------------------------
# Page iteration + build + cache
# ---------------------------------------------------------------------------

def _wiki_subdir(wiki_dir: Path) -> Path:
    return Path(wiki_dir) / "wiki"


def _iter_page_files(wiki_dir: Path) -> List[Path]:
    wiki_sub = _wiki_subdir(wiki_dir)
    if not wiki_sub.exists():
        return []
    files = []
    for p in sorted(wiki_sub.rglob("*.md")):
        if any(part.startswith(".") for part in p.parts):
            continue
        rel = p.relative_to(wiki_sub).as_posix()
        if rel in EXCLUDED_PAGES:
            continue
        files.append(p)
    return files


def _file_fingerprint(path: Path) -> Tuple[int, int]:
    st = path.stat()
    return (st.st_size, st.st_mtime_ns)


# Module-level cache: wiki_dir(str) -> {"fp": {...}, "pages": {...}, "graph": Graph}
_CACHE: Dict[str, Dict[str, Any]] = {}


def build_graph(
    wiki_dir: Path,
    *,
    changed_paths: Optional[Iterable[str]] = None,
    use_cache: bool = True,
) -> Graph:
    """Build (or incrementally refresh) the directed page graph for a wiki.

    The build is deterministic and offline. Per-page parse results are cached so
    that an incremental refresh only re-reads files whose on-disk fingerprint
    changed; the edge set is always recomputed from the full parsed page set, so
    an incremental build is byte-identical to a full rebuild.

    ``changed_paths`` is an optional hint (wiki-relative paths) of pages known to
    have changed — used by the git post-commit hook to force a re-parse — but
    fingerprint comparison already detects changes regardless.
    """
    wiki_dir = Path(wiki_dir)
    key = str(wiki_dir.resolve())
    wiki_sub = _wiki_subdir(wiki_dir)

    files = _iter_page_files(wiki_dir)
    current_fp: Dict[str, Tuple[int, int]] = {}
    for f in files:
        rel = f.relative_to(wiki_sub).as_posix()
        try:
            current_fp[rel] = _file_fingerprint(f)
        except OSError:
            continue

    cached = _CACHE.get(key) if use_cache else None
    forced = set(changed_paths or ())

    if cached is not None and cached["fp"] == current_fp and not forced:
        return cached["graph"]

    prev_pages: Dict[str, PageData] = cached["pages"] if cached else {}
    prev_fp: Dict[str, Tuple[int, int]] = cached["fp"] if cached else {}

    pages: Dict[str, PageData] = {}
    for rel, fp in current_fp.items():
        reuse = (
            rel in prev_pages
            and prev_fp.get(rel) == fp
            and rel not in forced
        )
        if reuse:
            pages[rel] = prev_pages[rel]
            continue
        try:
            text = (wiki_sub / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        pages[rel] = parse_page(rel, text)

    edges = _build_edges(pages)
    graph = Graph(pages, edges)

    if use_cache:
        _CACHE[key] = {"fp": current_fp, "pages": pages, "graph": graph}
    return graph


def get_graph(wiki_dir: Path) -> Graph:
    """Return the cached graph for ``wiki_dir``, building/refreshing as needed."""
    return build_graph(wiki_dir)


def invalidate_cache(wiki_dir: Optional[Path] = None) -> None:
    """Drop the cached graph for one wiki (or all wikis)."""
    if wiki_dir is None:
        _CACHE.clear()
        return
    _CACHE.pop(str(Path(wiki_dir).resolve()), None)
