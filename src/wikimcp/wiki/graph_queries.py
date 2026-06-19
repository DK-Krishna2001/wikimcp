"""
graph_queries.py — Deterministic, offline queries over the directed page graph.

These functions back the seven graph MCP tools. Each takes ``wiki_dir`` as its
first argument, builds (or reuses the cached) :class:`~wikimcp.wiki.graph.Graph`,
and returns token-lean, JSON-serialisable structures. NO model calls.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .git_layer import auto_commit
from .graph import (
    BIDIRECTIONAL,
    EXTRACTED,
    INFERRED,
    Edge,
    Graph,
    build_graph,
    edge_priority,
    get_graph,
)

#: Filename for the written wiki digest (lives at the wiki repo root).
WIKI_REPORT_FILENAME = "WIKI_REPORT.md"

#: Commit message used when the post-commit hook re-commits refreshed artifacts.
#: The hook skips when HEAD already carries this message, preventing a loop.
REFRESH_COMMIT_MESSAGE = "wiki: refresh graph artifacts"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _not_found(page: str) -> Dict[str, Any]:
    return {"error": f"Page not found: {page}"}


def _node(graph: Graph, path: str, **extra: Any) -> Dict[str, Any]:
    node = {"path": path, "title": graph.title_of(path)}
    node.update(extra)
    return node


def _edge_dict(edge: Edge) -> Dict[str, Any]:
    return edge.as_dict()


def _other(edge: Edge, path: str) -> str:
    return edge.target if edge.source == path else edge.source


# ---------------------------------------------------------------------------
# 1. get_related
# ---------------------------------------------------------------------------

def get_related(
    wiki_dir: Path,
    page: str,
    direction: str = "both",
    limit: int = 10,
) -> Dict[str, Any]:
    """Pages related to ``page``.

    ``direction``:
      * ``"out"``  — pages this page references (outgoing edges).
      * ``"in"``   — BACKLINKS: pages that reference this page.
      * ``"both"`` — union, each entry labelled ``out``/``in``/``both``.

    Results are ordered EXTRACTED before INFERRED, then by descending weight,
    then by path. ``limit`` caps the number of entries.
    """
    graph = get_graph(wiki_dir)
    resolved = graph.resolve(page)
    if resolved is None:
        return _not_found(page)
    direction = direction if direction in ("both", "in", "out") else "both"
    limit = max(1, int(limit))

    # neighbor -> best edge, per relation side
    out_best: Dict[str, Edge] = {}
    for edge in graph.out_edges(resolved):
        nb = _other(edge, resolved)
        if nb == resolved:
            continue
        cur = out_best.get(nb)
        if cur is None or edge_priority(edge) < edge_priority(cur):
            out_best[nb] = edge
    in_best: Dict[str, Edge] = {}
    for edge in graph.in_edges(resolved):
        nb = _other(edge, resolved)
        if nb == resolved:
            continue
        cur = in_best.get(nb)
        if cur is None or edge_priority(edge) < edge_priority(cur):
            in_best[nb] = edge

    if direction == "out":
        candidates = {nb: ("out", e) for nb, e in out_best.items()}
    elif direction == "in":
        candidates = {nb: ("in", e) for nb, e in in_best.items()}
    else:
        candidates = {}
        for nb in set(out_best) | set(in_best):
            in_e = in_best.get(nb)
            out_e = out_best.get(nb)
            if in_e and out_e:
                relation = "both"
                edge = in_e if edge_priority(in_e) <= edge_priority(out_e) else out_e
            elif out_e:
                relation, edge = "out", out_e
            else:
                relation, edge = "in", in_e
            candidates[nb] = (relation, edge)

    entries: List[Dict[str, Any]] = []
    for nb, (relation, edge) in candidates.items():
        entries.append(
            {
                "path": nb,
                "title": graph.title_of(nb),
                "relation": relation,
                "type": edge.type,
                "subtype": edge.subtype,
                "direction": edge.direction,
                "weight": edge.weight,
            }
        )
    entries.sort(
        key=lambda e: (
            0 if e["type"] == EXTRACTED else 1,
            -e["weight"],
            e["path"],
        )
    )
    return {
        "page": resolved,
        "title": graph.title_of(resolved),
        "direction": direction,
        "related": entries[:limit],
    }


# ---------------------------------------------------------------------------
# 2. get_subgraph
# ---------------------------------------------------------------------------

def get_subgraph(
    wiki_dir: Path,
    page: str,
    depth: int = 2,
    max_nodes: int = 40,
    direction: str = "both",
) -> Dict[str, Any]:
    """A bounded neighbourhood centred on ``page`` — render-ready.

    BFS to ``depth`` hops, hard-capped at ``max_nodes`` nodes. When capping,
    closer nodes and stronger (EXTRACTED, higher-weight) connections are kept;
    ``truncated`` is set when nodes were dropped. The returned ``nodes`` /
    ``edges`` shape is trivially convertible to mermaid/SVG by the caller.
    """
    graph = get_graph(wiki_dir)
    resolved = graph.resolve(page)
    if resolved is None:
        return _not_found(page)
    direction = direction if direction in ("both", "in", "out") else "both"
    depth = max(0, int(depth))
    max_nodes = max(1, int(max_nodes))

    # BFS recording distance.
    dist: Dict[str, int] = {resolved: 0}
    queue: deque = deque([resolved])
    while queue:
        cur = queue.popleft()
        if dist[cur] >= depth:
            continue
        for nb in sorted(_expand(graph, cur, direction)):
            if nb not in dist:
                dist[nb] = dist[cur] + 1
                queue.append(nb)

    discovered = [p for p in dist if p != resolved]
    # Best incident edge priority per node (closeness to strong structure).
    def node_strength(path: str) -> Tuple:
        best = min(
            (edge_priority(e) for e in graph.out_edges(path) + graph.in_edges(path)),
            default=(9, 9, 0),
        )
        return (dist[path], best, path)

    discovered.sort(key=node_strength)

    keep = [resolved] + discovered[: max_nodes - 1]
    truncated = len(discovered) + 1 > max_nodes
    keep_set = set(keep)

    nodes = [
        _node(graph, p, distance=dist[p])
        for p in sorted(keep_set, key=lambda x: (dist[x], x))
    ]
    edges = [
        _edge_dict(e)
        for e in graph.edges
        if e.source in keep_set and e.target in keep_set
    ]
    return {
        "center": resolved,
        "title": graph.title_of(resolved),
        "depth": depth,
        "max_nodes": max_nodes,
        "direction": direction,
        "truncated": truncated,
        "nodes": nodes,
        "edges": edges,
    }


def _expand(graph: Graph, path: str, direction: str) -> set:
    if direction == "out":
        return {_other(e, path) for e in graph.out_edges(path) if _other(e, path) != path}
    if direction == "in":
        return {_other(e, path) for e in graph.in_edges(path) if _other(e, path) != path}
    return {nb for nb, _ in graph.undirected_adj(path) if nb != path}


# ---------------------------------------------------------------------------
# 3. path
# ---------------------------------------------------------------------------

def find_path(
    wiki_dir: Path,
    page_a: str,
    page_b: str,
    max_hops: int = 6,
) -> Dict[str, Any]:
    """Shortest connection between two pages.

    Reachability traverses directed and bidirectional edges (a directed edge may
    be walked either way for connectivity); each hop reports the edge's actual
    stored direction, type/subtype, and traversal orientation
    (``forward`` = with the edge, ``backward`` = against it).
    """
    graph = get_graph(wiki_dir)
    a = graph.resolve(page_a)
    b = graph.resolve(page_b)
    if a is None:
        return _not_found(page_a)
    if b is None:
        return _not_found(page_b)
    max_hops = max(1, int(max_hops))

    if a == b:
        return {
            "found": True,
            "page_a": a,
            "page_b": b,
            "length": 0,
            "hops": [],
        }

    # BFS over the undirected view; remember the edge used to reach each node.
    prev: Dict[str, Tuple[str, Edge]] = {}
    visited = {a}
    queue: deque = deque([(a, 0)])
    found = False
    while queue:
        cur, d = queue.popleft()
        if d >= max_hops:
            continue
        for nb, edge in graph.undirected_adj(cur):
            if nb in visited:
                continue
            visited.add(nb)
            prev[nb] = (cur, edge)
            if nb == b:
                found = True
                queue.clear()
                break
            queue.append((nb, d + 1))

    if not found:
        return {
            "found": False,
            "page_a": a,
            "page_b": b,
            "reason": f"No path within {max_hops} hops.",
        }

    # Reconstruct.
    chain: List[Tuple[str, Edge]] = []
    node = b
    while node != a:
        frm, edge = prev[node]
        chain.append((node, edge))
        node = frm
    chain.reverse()

    hops = []
    cursor = a
    for to_node, edge in chain:
        orientation = "forward" if edge.source == cursor else "backward"
        hops.append(
            {
                "from": cursor,
                "to": to_node,
                "type": edge.type,
                "subtype": edge.subtype,
                "direction": edge.direction,
                "orientation": orientation,
            }
        )
        cursor = to_node

    return {
        "found": True,
        "page_a": a,
        "page_b": b,
        "length": len(hops),
        "hops": hops,
    }


# ---------------------------------------------------------------------------
# 4. surprising_links
# ---------------------------------------------------------------------------

def surprising_links(wiki_dir: Path, limit: int = 10) -> Dict[str, Any]:
    """Likely cross-domain INFERRED connections.

    Pairs connected by a ``title-mention`` edge that share ZERO tags AND live in
    different top-level directories. Ranked by fewest common neighbours (the
    fewer shared neighbours, the more surprising the link).
    """
    graph = get_graph(wiki_dir)
    limit = max(1, int(limit))

    seen: set = set()
    candidates: List[Dict[str, Any]] = []
    for edge in graph.edges:
        if not (edge.type == INFERRED and edge.subtype == "title-mention"):
            continue
        a, b = edge.source, edge.target
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        page_a = graph.pages.get(a)
        page_b = graph.pages.get(b)
        if not page_a or not page_b:
            continue
        if set(page_a.tags) & set(page_b.tags):
            continue  # share a tag -> not surprising
        if page_a.top_dir == page_b.top_dir:
            continue  # same section -> not cross-domain
        seen.add(key)
        common = len(graph.all_neighbors(a) & graph.all_neighbors(b))
        candidates.append(
            {
                "page_a": key[0],
                "page_b": key[1],
                "title_a": graph.title_of(key[0]),
                "title_b": graph.title_of(key[1]),
                "common_neighbors": common,
                "why": (
                    f"connected via title-mention, no shared tags, "
                    f"different sections ({page_a.top_dir or '/'} vs "
                    f"{page_b.top_dir or '/'})"
                ),
            }
        )

    candidates.sort(key=lambda c: (c["common_neighbors"], c["page_a"], c["page_b"]))
    return {"surprising_links": candidates[:limit]}


# ---------------------------------------------------------------------------
# 5. hubs
# ---------------------------------------------------------------------------

def hubs(wiki_dir: Path, limit: int = 10) -> Dict[str, Any]:
    """Pages ranked by degree centrality (distinct in + out neighbours)."""
    graph = get_graph(wiki_dir)
    limit = max(1, int(limit))

    rows: List[Dict[str, Any]] = []
    for path in graph.pages:
        ins = graph.in_neighbors(path)
        outs = graph.out_neighbors(path)
        total = len(ins | outs)
        if total == 0:
            continue
        rows.append(
            {
                "path": path,
                "title": graph.title_of(path),
                "in_degree": len(ins),
                "out_degree": len(outs),
                "total": total,
            }
        )
    rows.sort(key=lambda r: (-r["total"], -r["in_degree"], r["path"]))
    return {"hubs": rows[:limit]}


# ---------------------------------------------------------------------------
# 6. orphans
# ---------------------------------------------------------------------------

def orphans(wiki_dir: Path) -> Dict[str, Any]:
    """Zero-edge pages (no in, no out) plus dead-ends (in but no out)."""
    graph = get_graph(wiki_dir)
    orphan_rows: List[Dict[str, str]] = []
    dead_end_rows: List[Dict[str, str]] = []
    for path in sorted(graph.pages):
        ins = graph.in_neighbors(path)
        outs = graph.out_neighbors(path)
        if not ins and not outs:
            orphan_rows.append(_node(graph, path))
        elif ins and not outs:
            dead_end_rows.append(_node(graph, path))
    return {"orphans": orphan_rows, "dead_ends": dead_end_rows}


# ---------------------------------------------------------------------------
# 7. wiki_report
# ---------------------------------------------------------------------------

def _recent_additions(graph: Graph, wiki_dir: Path, limit: int = 10) -> List[Dict[str, str]]:
    """Most recently created/updated pages, from frontmatter or git history."""
    dated: List[Tuple[str, str, str]] = []  # (date, path, source-field)
    for path in sorted(graph.pages):
        page = graph.pages[path]
        date = page.updated or page.created
        if date:
            dated.append((date, path, "updated" if page.updated else "created"))
    if dated:
        dated.sort(key=lambda d: (d[0], d[1]), reverse=True)
        return [
            {"path": p, "title": graph.title_of(p), "date": d, "source": src}
            for d, p, src in dated[:limit]
        ]

    # Fallback: git log (deterministic, offline — reads the local repo only).
    return _recent_from_git(graph, wiki_dir, limit)


def _recent_from_git(graph: Graph, wiki_dir: Path, limit: int) -> List[Dict[str, str]]:
    try:
        import git
        from git.exc import InvalidGitRepositoryError
    except Exception:
        return []
    try:
        repo = git.Repo(str(wiki_dir))
    except Exception:
        return []
    rows: List[Tuple[str, str]] = []
    for path in graph.pages:
        rel = f"wiki/{path}"
        try:
            commits = list(repo.iter_commits(paths=rel, max_count=1))
        except Exception:
            commits = []
        if commits:
            ts = commits[0].committed_datetime.astimezone(timezone.utc)
            rows.append((ts.strftime("%Y-%m-%d"), path))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return [
        {"path": p, "title": graph.title_of(p), "date": d, "source": "git"}
        for d, p in rows[:limit]
    ]


def _suggested_questions(
    graph: Graph,
    top_hubs: List[Dict[str, Any]],
    orphan_data: Dict[str, Any],
    surprising: List[Dict[str, Any]],
) -> List[str]:
    """Deterministic questions templated from graph structure."""
    questions: List[str] = []
    if len(top_hubs) >= 2:
        questions.append(
            f"What connects {top_hubs[0]['title']} and {top_hubs[1]['title']}?"
        )
    if top_hubs:
        questions.append(f"What references {top_hubs[0]['title']}?")
    if orphan_data["orphans"] and top_hubs:
        orphan = orphan_data["orphans"][0]
        questions.append(
            f"Should {orphan['title']} link to {top_hubs[0]['title']}?"
        )
    if orphan_data["dead_ends"]:
        de = orphan_data["dead_ends"][0]
        questions.append(
            f"Does {de['title']} need outgoing links to related pages?"
        )
    if surprising:
        s = surprising[0]
        questions.append(
            f"Is the link between {s['title_a']} and {s['title_b']} intentional?"
        )
    return questions


def wiki_report(
    wiki_dir: Path,
    suggested_questions: bool = True,
    write_file: bool = False,
) -> str:
    """Build a deterministic markdown digest of the wiki graph.

    When ``write_file`` is True, the digest is written to ``WIKI_REPORT.md`` at
    the wiki repo root and auto-committed (git-tracked).
    """
    graph = get_graph(wiki_dir)

    # Counts.
    type_counts: Dict[str, int] = {EXTRACTED: 0, INFERRED: 0}
    subtype_counts: Dict[str, int] = {}
    for edge in graph.edges:
        type_counts[edge.type] = type_counts.get(edge.type, 0) + 1
        subtype_counts[edge.subtype] = subtype_counts.get(edge.subtype, 0) + 1

    top_hubs = hubs(wiki_dir, limit=10)["hubs"]
    orphan_data = orphans(wiki_dir)
    surprising = surprising_links(wiki_dir, limit=5)["surprising_links"]
    recent = _recent_additions(graph, wiki_dir, limit=10)

    lines: List[str] = []
    lines.append("# Wiki Report")
    lines.append("")
    lines.append(
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d')} "
        f"— deterministic graph digest._"
    )
    lines.append("")

    lines.append("## Counts")
    lines.append(f"- Pages: {len(graph.pages)}")
    lines.append(f"- Edges: {len(graph.edges)}")
    lines.append(
        f"  - EXTRACTED: {type_counts.get(EXTRACTED, 0)} "
        f"(wikilink: {subtype_counts.get('wikilink', 0)}, "
        f"related: {subtype_counts.get('related', 0)})"
    )
    lines.append(
        f"  - INFERRED: {type_counts.get(INFERRED, 0)} "
        f"(title-mention: {subtype_counts.get('title-mention', 0)}, "
        f"shared-tag: {subtype_counts.get('shared-tag', 0)})"
    )
    lines.append("")

    lines.append("## Top Hubs")
    if top_hubs:
        for h in top_hubs[:10]:
            lines.append(
                f"- [{h['title']}]({h['path']}) — total {h['total']} "
                f"(in {h['in_degree']}, out {h['out_degree']})"
            )
    else:
        lines.append("- _none_")
    lines.append("")

    lines.append("## Orphans")
    if orphan_data["orphans"]:
        for o in orphan_data["orphans"]:
            lines.append(f"- [{o['title']}]({o['path']})")
    else:
        lines.append("- _none_")
    lines.append("")

    lines.append("## Dead-ends (inbound only)")
    if orphan_data["dead_ends"]:
        for d in orphan_data["dead_ends"]:
            lines.append(f"- [{d['title']}]({d['path']})")
    else:
        lines.append("- _none_")
    lines.append("")

    lines.append("## Surprising Links")
    if surprising:
        for s in surprising:
            lines.append(
                f"- [{s['title_a']}]({s['page_a']}) ↔ "
                f"[{s['title_b']}]({s['page_b']}) — {s['why']} "
                f"(common neighbors: {s['common_neighbors']})"
            )
    else:
        lines.append("- _none_")
    lines.append("")

    lines.append("## Recent Additions")
    if recent:
        for r in recent:
            lines.append(f"- {r['date']} — [{r['title']}]({r['path']})")
    else:
        lines.append("- _none_")
    lines.append("")

    if suggested_questions:
        lines.append("## Suggested Questions")
        questions = _suggested_questions(graph, top_hubs, orphan_data, surprising)
        if questions:
            for q in questions:
                lines.append(f"- {q}")
        else:
            lines.append("- _none_")
        lines.append("")

    report = "\n".join(lines).rstrip() + "\n"

    if write_file:
        report_path = Path(wiki_dir) / WIKI_REPORT_FILENAME
        report_path.write_text(report, encoding="utf-8")
        try:
            auto_commit(wiki_dir, f"wiki: wiki_report {WIKI_REPORT_FILENAME}")
        except Exception:
            pass

    return report


# ---------------------------------------------------------------------------
# Incremental refresh (driven by the git post-commit hook)
# ---------------------------------------------------------------------------

def refresh_after_commit(
    wiki_dir: Path,
    changed_paths: Optional[List[str]] = None,
    *,
    commit: bool = True,
) -> Dict[str, Any]:
    """Re-index changed pages and refresh any generated artifacts.

    ``changed_paths`` may be repo-relative (``wiki/topics/x.md``) or wiki-relative
    (``topics/x.md``) — both are normalised. The graph build is incremental: with
    a warm in-process cache only the changed pages are re-parsed, and the result
    is byte-identical to a full rebuild. Fully offline.

    Only artifacts that already exist on disk are refreshed
    (``WIKI_REPORT.md`` and ``graph.html``); they are not created here. When
    ``commit`` is True and an artifact changed, the refresh is committed with a
    fixed message the hook recognises so it never re-triggers itself.
    """
    wiki_dir = Path(wiki_dir)

    hint: List[str] = []
    for raw in changed_paths or []:
        rel = raw.strip()
        if not rel:
            continue
        if rel.startswith("wiki/"):
            rel = rel[len("wiki/") :]
        elif "/wiki/" in rel:
            rel = rel.split("/wiki/", 1)[1]
        else:
            continue
        if rel:
            hint.append(rel)

    # Incremental build (full rebuild when the cache is cold, e.g. in the hook).
    build_graph(wiki_dir, changed_paths=hint or None)

    refreshed: List[str] = []
    report_path = wiki_dir / WIKI_REPORT_FILENAME
    if report_path.exists():
        content = wiki_report(wiki_dir, suggested_questions=True, write_file=False)
        report_path.write_text(content, encoding="utf-8")
        refreshed.append(WIKI_REPORT_FILENAME)

    html_path = wiki_dir / "graph.html"
    if html_path.exists():
        from .graph_export import export_html

        export_html(wiki_dir, html_path)
        refreshed.append("graph.html")

    if commit and refreshed:
        try:
            auto_commit(wiki_dir, REFRESH_COMMIT_MESSAGE)
        except Exception:
            pass

    return {"refreshed": refreshed}
