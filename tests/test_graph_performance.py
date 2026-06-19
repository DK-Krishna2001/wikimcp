"""Performance smoke test (5j): build ~300 synthetic pages and assert the full
build and a representative query complete under a sane threshold, and that the
graph is not degenerate — no single node is connected to a large fraction of
pages via title-mention (proves the guard holds at scale)."""

from __future__ import annotations

import time
from pathlib import Path

from wikimcp.wiki import graph, graph_queries as gq

N = 300


def _build_large_wiki(tmp_path: Path) -> Path:
    sub = tmp_path / "wiki" / "topics"
    sub.mkdir(parents=True)
    tags = ["alpha", "beta", "gamma", "delta", "epsilon"]
    for i in range(N):
        tag = tags[i % len(tags)]
        # Each page links to the next (a long chain) and mentions a neighbour
        # title, plus shares a tag with ~1/5 of pages.
        nxt = (i + 1) % N
        content = (
            f"---\ntitle: Concept Number {i}\ntags: [{tag}]\n---\n"
            f"# Concept Number {i}\n\n"
            f"This builds on [[Concept Number {nxt}]]. "
            f"It also discusses Concept Number {(i + 7) % N} in passing.\n"
        )
        (sub / f"page-{i:04d}.md").write_text(content, encoding="utf-8")
    return tmp_path


def test_build_and_query_under_threshold(tmp_path):
    wiki = _build_large_wiki(tmp_path)

    t0 = time.perf_counter()
    g = graph.build_graph(wiki, use_cache=False)
    build_s = time.perf_counter() - t0
    assert len(g.pages) == N
    assert build_s < 20.0, f"build took {build_s:.2f}s"

    t1 = time.perf_counter()
    gq.hubs(wiki, limit=10)
    gq.get_subgraph(wiki, "topics/page-0000.md", depth=2, max_nodes=40)
    gq.find_path(wiki, "topics/page-0000.md", "topics/page-0100.md")
    query_s = time.perf_counter() - t1
    assert query_s < 10.0, f"queries took {query_s:.2f}s"


def test_no_degenerate_title_mention_hub(tmp_path):
    wiki = _build_large_wiki(tmp_path)
    g = graph.build_graph(wiki, use_cache=False)

    # Count inbound title-mention edges per node.
    inbound = {}
    for e in g.edges:
        if e.subtype == "title-mention":
            inbound[e.target] = inbound.get(e.target, 0) + 1
    if inbound:
        worst = max(inbound.values())
        # No node should be a title-mention sink for more than 5% of pages.
        assert worst <= N * 0.05, f"degenerate hub: {worst} inbound title-mentions"


def test_subgraph_cap_holds_at_scale(tmp_path):
    wiki = _build_large_wiki(tmp_path)
    sg = gq.get_subgraph(wiki, "topics/page-0000.md", depth=4, max_nodes=25)
    assert len(sg["nodes"]) <= 25
