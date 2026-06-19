"""Robustness / edge-case tests (5g): empty wiki, single page, missing pages,
unresolvable links, circular links, duplicate titles, unicode/whitespace titles,
and a very large single page."""

from __future__ import annotations

from pathlib import Path

from wikimcp.wiki import graph, graph_queries as gq


def make_wiki(tmp_path: Path, pages: dict) -> Path:
    sub = tmp_path / "wiki"
    sub.mkdir(parents=True, exist_ok=True)
    for rel, content in pages.items():
        p = sub / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def test_empty_wiki(tmp_path):
    (tmp_path / "wiki").mkdir()
    g = graph.build_graph(tmp_path, use_cache=False)
    assert g.pages == {}
    assert g.edges == []
    assert gq.hubs(tmp_path)["hubs"] == []
    assert gq.orphans(tmp_path) == {"orphans": [], "dead_ends": []}
    assert gq.surprising_links(tmp_path)["surprising_links"] == []
    assert "Pages: 0" in gq.wiki_report(tmp_path)


def test_missing_wiki_subdir(tmp_path):
    # No wiki/ subdir at all.
    g = graph.build_graph(tmp_path, use_cache=False)
    assert g.pages == {}


def test_single_page(tmp_path):
    wiki = make_wiki(tmp_path, {"topics/only.md": "# Only\n\nalone."})
    g = graph.build_graph(wiki, use_cache=False)
    assert set(g.pages) == {"topics/only.md"}
    assert g.edges == []
    res = gq.orphans(wiki)
    assert res["orphans"] == [{"path": "topics/only.md", "title": "Only"}]


def test_unresolvable_wikilink(tmp_path):
    wiki = make_wiki(tmp_path, {"topics/a.md": "# Alpha\n\n[[Does Not Exist]]"})
    g = graph.build_graph(wiki, use_cache=False)
    assert g.edges == []  # link target resolves to nothing -> no edge


def test_circular_links(tmp_path):
    wiki = make_wiki(
        tmp_path,
        {
            "topics/a.md": "# Alpha\n\n[[Beta]]",
            "topics/b.md": "# Beta\n\n[[Gamma]]",
            "topics/c.md": "# Gamma\n\n[[Alpha]]",
        },
    )
    g = graph.build_graph(wiki, use_cache=False)
    # Three directed edges forming a cycle; path traversal must terminate.
    assert len(g.edges) == 3
    res = gq.find_path(wiki, "topics/a.md", "topics/c.md")
    assert res["found"] is True


def test_duplicate_titles(tmp_path):
    wiki = make_wiki(
        tmp_path,
        {
            "topics/a.md": "---\ntitle: Shared Name\n---\n# Shared Name\n",
            "entities/b.md": "---\ntitle: Shared Name\n---\n# Shared Name\n",
            "topics/c.md": "# Gamma\n\nI mention Shared Name once.",
        },
    )
    g = graph.build_graph(wiki, use_cache=False)
    # Resolution is deterministic (first by sorted path) and must not crash.
    res = gq.get_related(wiki, "Shared Name")
    assert res["page"] in {"topics/a.md", "entities/b.md"}
    assert res["page"] == "entities/b.md"  # sorted-path first match


def test_unicode_and_whitespace_titles(tmp_path):
    wiki = make_wiki(
        tmp_path,
        {
            "topics/u.md": "---\ntitle: Café Façade\n---\n# Café Façade\n",
            "topics/v.md": "# Mention\n\nI visited the Café Façade yesterday.",
            "topics/w.md": "---\ntitle:    Spaced   Title   \n---\ncontent",
        },
    )
    g = graph.build_graph(wiki, use_cache=False)
    assert g.pages["topics/u.md"].title == "Café Façade"
    # Whitespace in the title is collapsed for matching but stored trimmed.
    assert g.pages["topics/w.md"].title == "Spaced   Title"
    # Unicode title-mention resolves.
    es = [e for e in g.edges if e.source == "topics/v.md" and e.target == "topics/u.md"]
    assert es and es[0].subtype == "title-mention"


def test_very_large_single_page(tmp_path):
    big = "# Big Page\n\n" + ("lorem ipsum dolor sit amet " * 50000)
    wiki = make_wiki(
        tmp_path,
        {"topics/big.md": big, "topics/small.md": "# Small\n\n[[Big Page]]"},
    )
    g = graph.build_graph(wiki, use_cache=False)
    assert "topics/big.md" in g.pages
    es = [e for e in g.edges if e.source == "topics/small.md"]
    assert es and es[0].target == "topics/big.md"
