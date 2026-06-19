"""Unit tests for graph building: frontmatter, link/title resolution, each edge
subtype, and the title-mention specificity guard (5b)."""

from __future__ import annotations

from pathlib import Path

import pytest

from wikimcp.wiki import graph
from wikimcp.wiki.graph import (
    BIDIRECTIONAL,
    DIRECTED,
    EXTRACTED,
    INFERRED,
    parse_frontmatter,
    parse_page,
)


def make_wiki(tmp_path: Path, pages: dict) -> Path:
    """Write {rel_path: content} under tmp_path/wiki/ and return tmp_path."""
    sub = tmp_path / "wiki"
    for rel, content in pages.items():
        p = sub / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def edges_between(g, source, target):
    return [e for e in g.edges if e.source == source and e.target == target]


# --- frontmatter parsing ----------------------------------------------------

def test_parse_frontmatter_inline_tags():
    meta, body = parse_frontmatter('---\ntitle: "X"\ntags: [a, b, c]\n---\n# X\nbody\n')
    assert meta["title"] == "X"
    assert meta["tags"] == ["a", "b", "c"]
    assert body.startswith("# X")


def test_parse_frontmatter_block_list_tags():
    meta, _ = parse_frontmatter("---\ntags:\n  - one\n  - two\n---\nbody\n")
    assert meta["tags"] == ["one", "two"]


def test_parse_frontmatter_absent():
    meta, body = parse_frontmatter("# No frontmatter\n\ntext")
    assert meta == {}
    assert body == "# No frontmatter\n\ntext"


def test_parse_page_title_priority():
    # Frontmatter title wins over the H1 heading.
    page = parse_page("topics/x.md", '---\ntitle: Frontmatter Title\n---\n# Heading Title\n')
    assert page.title == "Frontmatter Title"
    # Falls back to the H1 heading when no frontmatter title.
    page2 = parse_page("topics/y.md", "# Heading Only\n\nbody")
    assert page2.title == "Heading Only"
    # Falls back to the humanised filename stem when neither present.
    page3 = parse_page("topics/my-note.md", "no heading here")
    assert page3.title == "my note"


# --- EXTRACTED: wikilink + markdown link ------------------------------------

def test_wikilink_edge_extracted(tmp_path):
    wiki = make_wiki(
        tmp_path,
        {
            "topics/a.md": "# Alpha\n\nSee [[Beta]].",
            "topics/b.md": "# Beta\n\nContent.",
        },
    )
    g = graph.build_graph(wiki, use_cache=False)
    es = edges_between(g, "topics/a.md", "topics/b.md")
    assert len(es) == 1
    assert es[0].type == EXTRACTED
    assert es[0].subtype == "wikilink"
    assert es[0].direction == DIRECTED


def test_markdown_link_edge_extracted(tmp_path):
    wiki = make_wiki(
        tmp_path,
        {
            "topics/a.md": "# Alpha\n\nSee [Beta](b.md).",
            "topics/b.md": "# Beta\n\nContent.",
        },
    )
    g = graph.build_graph(wiki, use_cache=False)
    es = edges_between(g, "topics/a.md", "topics/b.md")
    assert es and es[0].subtype == "wikilink"


def test_external_links_are_ignored(tmp_path):
    wiki = make_wiki(
        tmp_path,
        {"topics/a.md": "# Alpha\n\n[ext](https://example.com) [mail](mailto:x@y.z)"},
    )
    g = graph.build_graph(wiki, use_cache=False)
    assert g.edges == []


# --- EXTRACTED: related section ---------------------------------------------

def test_related_section_subtype(tmp_path):
    wiki = make_wiki(
        tmp_path,
        {
            "topics/a.md": "# Alpha\n\n## Related\n- [[Beta]]\n",
            "topics/b.md": "# Beta\n",
        },
    )
    g = graph.build_graph(wiki, use_cache=False)
    es = edges_between(g, "topics/a.md", "topics/b.md")
    assert es and es[0].subtype == "related"
    assert es[0].type == EXTRACTED


# --- INFERRED: title-mention ------------------------------------------------

def test_title_mention_directed(tmp_path):
    wiki = make_wiki(
        tmp_path,
        {
            "topics/ml.md": "# Machine Learning\n\nA field of study.",
            "topics/j.md": "# Journal\n\nToday I studied Machine Learning all day.",
        },
    )
    g = graph.build_graph(wiki, use_cache=False)
    es = edges_between(g, "topics/j.md", "topics/ml.md")
    assert es and es[0].subtype == "title-mention"
    assert es[0].type == INFERRED
    assert es[0].direction == DIRECTED
    # The reverse direction must not exist (ml.md doesn't mention "Journal").
    assert edges_between(g, "topics/ml.md", "topics/j.md") == []


def test_title_mention_suppressed_when_explicit_link_exists(tmp_path):
    wiki = make_wiki(
        tmp_path,
        {
            "topics/j.md": "# Journal\n\n[[Machine Learning]] is mentioned and Machine Learning again.",
            "topics/ml.md": "# Machine Learning\n",
        },
    )
    g = graph.build_graph(wiki, use_cache=False)
    es = edges_between(g, "topics/j.md", "topics/ml.md")
    # Explicit wikilink present -> single EXTRACTED edge, no title-mention.
    assert len(es) == 1
    assert es[0].subtype == "wikilink"


def test_title_mention_whole_word_only(tmp_path):
    wiki = make_wiki(
        tmp_path,
        {
            "topics/cat.md": "# Cat\n\nfeline.",
            "topics/other.md": "# Other\n\nThe category is large and concatenated.",
        },
    )
    g = graph.build_graph(wiki, use_cache=False)
    # "category"/"concatenated" contain "cat" but must NOT match the title "Cat".
    # ("Cat" is also a short common-ish word; guard + word boundary both apply.)
    assert edges_between(g, "topics/other.md", "topics/cat.md") == []


# --- title-mention GUARD: common/short titles must not explode ---------------

def test_common_title_notes_does_not_explode(tmp_path):
    wiki = make_wiki(
        tmp_path,
        {
            "topics/notes.md": "# Notes\n\nmeta page",
            "topics/a.md": "# Alpha\n\nI took some notes today.",
            "topics/b.md": "# Beta\n\nMore notes here.",
            "topics/c.md": "# Gamma\n\nnotes notes notes.",
        },
    )
    g = graph.build_graph(wiki, use_cache=False)
    # "Notes" is a documented common-word title -> zero title-mention edges to it.
    inbound = [e for e in g.edges if e.target == "topics/notes.md"]
    assert inbound == []


def test_short_title_below_threshold_skipped(tmp_path):
    wiki = make_wiki(
        tmp_path,
        {
            "topics/go.md": "# Go\n\nA language.",
            "topics/a.md": "# Alpha\n\nLet's go to the store and go again.",
        },
    )
    g = graph.build_graph(wiki, use_cache=False)
    # Title "Go" is below TITLE_MENTION_MIN_CHARS -> no edges.
    assert edges_between(g, "topics/a.md", "topics/go.md") == []


def test_guard_scales_no_degenerate_hub(tmp_path):
    """A common-word title must not connect to a large fraction of pages."""
    pages = {"topics/notes.md": "# Notes\n\nindex"}
    for i in range(50):
        pages[f"topics/p{i}.md"] = f"# Page {i}\n\nthese are my notes for page {i}"
    g = graph.build_graph(make_wiki(tmp_path, pages), use_cache=False)
    inbound_to_notes = [e for e in g.edges if e.target == "topics/notes.md"]
    assert inbound_to_notes == []


# --- INFERRED: shared-tag (bidirectional) -----------------------------------

def test_shared_tag_bidirectional_weight(tmp_path):
    wiki = make_wiki(
        tmp_path,
        {
            "topics/a.md": "---\ntitle: Alpha\ntags: [x, y, z]\n---\n# Alpha\n",
            "topics/b.md": "---\ntitle: Beta\ntags: [x, y, w]\n---\n# Beta\n",
        },
    )
    g = graph.build_graph(wiki, use_cache=False)
    shared = [e for e in g.edges if e.subtype == "shared-tag"]
    assert len(shared) == 1
    e = shared[0]
    assert e.direction == BIDIRECTIONAL
    assert e.type == INFERRED
    assert e.weight == 2  # shared tags x and y


def test_no_shared_tag_without_overlap(tmp_path):
    wiki = make_wiki(
        tmp_path,
        {
            "topics/a.md": "---\ntitle: Alpha\ntags: [x]\n---\n# Alpha\n",
            "topics/b.md": "---\ntitle: Beta\ntags: [y]\n---\n# Beta\n",
        },
    )
    g = graph.build_graph(wiki, use_cache=False)
    assert [e for e in g.edges if e.subtype == "shared-tag"] == []


# --- exclusions -------------------------------------------------------------

def test_index_and_log_excluded_from_nodes(tmp_path):
    wiki = make_wiki(
        tmp_path,
        {
            "index.md": "# Index\n\n[[Alpha]]",
            "log.md": "# Log\n",
            "topics/a.md": "# Alpha\n",
        },
    )
    g = graph.build_graph(wiki, use_cache=False)
    assert set(g.pages) == {"topics/a.md"}
