"""Determinism tests (5f): building the graph twice yields byte-identical node
and edge sets, and tool outputs are identical across builds."""

from __future__ import annotations

import json

from wikimcp.wiki import graph, graph_queries as gq

ML = "topics/machine-learning.md"


def test_build_twice_identical_signature(realistic_wiki):
    graph.invalidate_cache()
    g1 = graph.build_graph(realistic_wiki, use_cache=False)
    graph.invalidate_cache()
    g2 = graph.build_graph(realistic_wiki, use_cache=False)
    assert g1.signature() == g2.signature()


def test_edge_lists_byte_identical(realistic_wiki):
    g1 = graph.build_graph(realistic_wiki, use_cache=False)
    g2 = graph.build_graph(realistic_wiki, use_cache=False)
    serial1 = [e.as_dict() for e in g1.edges]
    serial2 = [e.as_dict() for e in g2.edges]
    assert json.dumps(serial1, sort_keys=True) == json.dumps(serial2, sort_keys=True)


def test_tool_outputs_stable(realistic_wiki):
    for fn in (
        lambda: gq.hubs(realistic_wiki),
        lambda: gq.orphans(realistic_wiki),
        lambda: gq.surprising_links(realistic_wiki),
        lambda: gq.get_related(realistic_wiki, ML, "both", 20),
        lambda: gq.get_subgraph(realistic_wiki, ML, 2, 40),
        lambda: gq.find_path(realistic_wiki, ML, "topics/time-blocking.md"),
    ):
        graph.invalidate_cache()
        a = json.dumps(fn(), sort_keys=True)
        graph.invalidate_cache()
        b = json.dumps(fn(), sort_keys=True)
        assert a == b


def test_wiki_report_stable(realistic_wiki):
    graph.invalidate_cache()
    r1 = gq.wiki_report(realistic_wiki)
    graph.invalidate_cache()
    r2 = gq.wiki_report(realistic_wiki)
    assert r1 == r2
