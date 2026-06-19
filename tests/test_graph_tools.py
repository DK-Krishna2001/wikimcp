"""Per-tool tests (5c) and real-world scenario / end-to-end tests (5d) against
the realistic fixture, asserting the known truth documented in its README."""

from __future__ import annotations

from wikimcp.wiki import graph_queries as gq

ML = "topics/machine-learning.md"


# ---------------------------------------------------------------------------
# 5c — per-tool tests
# ---------------------------------------------------------------------------

def test_get_related_out(realistic_wiki):
    out = gq.get_related(realistic_wiki, ML, direction="out")
    paths = {r["path"] for r in out["related"]}
    # ML references these via explicit links.
    assert {"topics/neural-networks.md", "topics/gradient-descent.md",
            "topics/transformers.md", "topics/python.md",
            "topics/deep-learning.md"} <= paths
    # EXTRACTED entries are ordered before INFERRED.
    types = [r["type"] for r in out["related"]]
    assert types == sorted(types, key=lambda t: 0 if t == "EXTRACTED" else 1)


def test_get_related_in_backlinks(realistic_wiki):
    res = gq.get_related(realistic_wiki, ML, direction="in", limit=20)
    by_path = {r["path"]: r for r in res["related"]}
    assert by_path["projects/project-falcon.md"]["type"] == "EXTRACTED"
    assert by_path["projects/project-falcon.md"]["subtype"] == "wikilink"
    assert by_path["topics/gradient-descent.md"]["subtype"] == "wikilink"
    assert by_path["entities/alice.md"]["subtype"] == "title-mention"
    assert by_path["journal/2026-02-20.md"]["subtype"] == "title-mention"


def test_get_related_missing_page(realistic_wiki):
    assert "error" in gq.get_related(realistic_wiki, "topics/nope.md")


def test_get_related_resolves_by_title(realistic_wiki):
    res = gq.get_related(realistic_wiki, "Machine Learning", direction="out")
    assert res["page"] == ML


def test_get_related_respects_limit(realistic_wiki):
    res = gq.get_related(realistic_wiki, ML, direction="both", limit=3)
    assert len(res["related"]) == 3


def test_hubs_top_is_machine_learning(realistic_wiki):
    rows = gq.hubs(realistic_wiki)["hubs"]
    assert rows[0]["path"] == ML
    assert rows[0]["total"] == 9
    assert rows[0]["in_degree"] == 8
    assert rows[0]["out_degree"] == 6
    # Every reported hub has at least one neighbour.
    assert all(r["total"] >= 1 for r in rows)


def test_orphans_and_dead_ends(realistic_wiki):
    res = gq.orphans(realistic_wiki)
    assert {o["path"] for o in res["orphans"]} == {
        "misc/standalone.md", "topics/orphan-idea.md"
    }
    assert {d["path"] for d in res["dead_ends"]} == {
        "entities/charlie.md", "topics/attention.md"
    }


def test_surprising_links_top_pair(realistic_wiki):
    res = gq.surprising_links(realistic_wiki)["surprising_links"]
    top = res[0]
    assert {top["page_a"], top["page_b"]} == {"hobbies/chess.md", "misc/cooking.md"}
    assert top["common_neighbors"] == 0
    assert "no shared tags" in top["why"]
    # Every surprising pair shares zero tags and crosses sections by construction.
    assert all("different sections" in r["why"] for r in res)


def test_path_found_with_directions(realistic_wiki):
    res = gq.find_path(realistic_wiki, ML, "topics/time-blocking.md")
    assert res["found"] is True
    assert res["length"] == 2
    # Each hop reports a concrete edge with type/subtype/orientation.
    for hop in res["hops"]:
        assert hop["type"] in ("EXTRACTED", "INFERRED")
        assert hop["orientation"] in ("forward", "backward")
        assert hop["from"] and hop["to"]


def test_path_no_connection(realistic_wiki):
    res = gq.find_path(realistic_wiki, "topics/attention.md", "misc/cooking.md")
    assert res["found"] is False
    assert "No path" in res["reason"]


def test_path_same_page(realistic_wiki):
    res = gq.find_path(realistic_wiki, ML, ML)
    assert res["found"] is True
    assert res["length"] == 0
    assert res["hops"] == []


def test_path_missing_endpoint(realistic_wiki):
    assert "error" in gq.find_path(realistic_wiki, ML, "topics/nope.md")


def test_get_subgraph_respects_depth_and_cap(realistic_wiki):
    sg = gq.get_subgraph(realistic_wiki, ML, depth=1, max_nodes=4)
    assert sg["center"] == ML
    assert len(sg["nodes"]) <= 4
    assert sg["truncated"] is True  # ML has far more than 3 neighbours at depth 1
    # All distances within depth.
    assert all(n["distance"] <= 1 for n in sg["nodes"])
    # Center is always present.
    assert any(n["path"] == ML for n in sg["nodes"])


def test_get_subgraph_not_truncated_when_small(realistic_wiki):
    sg = gq.get_subgraph(realistic_wiki, "hobbies/chess.md", depth=1, max_nodes=40)
    assert sg["truncated"] is False
    paths = {n["path"] for n in sg["nodes"]}
    assert paths == {"hobbies/chess.md", "misc/cooking.md"}


def test_wiki_report_contains_sections(realistic_wiki):
    report = gq.wiki_report(realistic_wiki)
    for heading in ("# Wiki Report", "## Counts", "## Top Hubs", "## Orphans",
                    "## Dead-ends", "## Surprising Links", "## Recent Additions",
                    "## Suggested Questions"):
        assert heading in report
    assert "Pages: 24" in report
    assert "Machine Learning" in report


def test_wiki_report_no_questions_when_disabled(realistic_wiki):
    report = gq.wiki_report(realistic_wiki, suggested_questions=False)
    assert "## Suggested Questions" not in report


# ---------------------------------------------------------------------------
# 5d — real-world scenario / end-to-end tests (phrased as user questions)
# ---------------------------------------------------------------------------

def test_scenario_what_references_machine_learning(realistic_wiki):
    """User: 'what references Machine Learning?' -> backlinks."""
    res = gq.get_related(realistic_wiki, ML, direction="in", limit=20)
    paths = {r["path"] for r in res["related"]}
    assert "projects/project-falcon.md" in paths
    assert "topics/gradient-descent.md" in paths


def test_scenario_how_does_alice_connect_to_python(realistic_wiki):
    """User: 'how does Alice connect to Python?' -> path."""
    res = gq.find_path(realistic_wiki, "entities/alice.md", "topics/python.md")
    assert res["found"] is True
    assert res["length"] >= 1


def test_scenario_show_the_graph_around_ml(realistic_wiki):
    """User: 'show me the graph around Machine Learning' -> bounded subgraph."""
    sg = gq.get_subgraph(realistic_wiki, ML, depth=2, max_nodes=40)
    assert sg["nodes"] and sg["edges"]
    node_paths = {n["path"] for n in sg["nodes"]}
    # Edges only reference nodes that are present (render-safe).
    for e in sg["edges"]:
        assert e["source"] in node_paths and e["target"] in node_paths


def test_scenario_central_ideas(realistic_wiki):
    """User: 'what are my central ideas?' -> hubs."""
    rows = gq.hubs(realistic_wiki, limit=3)["hubs"]
    assert rows[0]["path"] == ML
    assert len(rows) == 3


def test_scenario_disconnected_ideas(realistic_wiki):
    """User: 'which ideas are disconnected?' -> orphans."""
    res = gq.orphans(realistic_wiki)
    assert len(res["orphans"]) == 2


def test_scenario_cross_domain_links(realistic_wiki):
    """User: 'surface cross-domain links' -> surprising_links."""
    res = gq.surprising_links(realistic_wiki, limit=1)["surprising_links"]
    assert {res[0]["page_a"], res[0]["page_b"]} == {
        "hobbies/chess.md", "misc/cooking.md"
    }


def test_scenario_wiki_digest(realistic_wiki):
    """User: 'give me a wiki digest' -> wiki_report."""
    report = gq.wiki_report(realistic_wiki)
    assert "Edges:" in report
    assert "chess" in report.lower() and "cooking" in report.lower()
