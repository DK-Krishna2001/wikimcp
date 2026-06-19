"""In-process coverage for refresh_after_commit, the git-based recent-additions
fallback, and directional subgraph expansion. (The post-commit hook test runs
the refresh in a subprocess, which coverage cannot observe; these exercise the
same code paths directly.)"""

from __future__ import annotations

from pathlib import Path

from wikimcp.wiki import graph, graph_queries as gq
from wikimcp.wiki.git_layer import init_repo
from wikimcp.wiki.operations import write_page


def _wiki(tmp_path: Path, pages: dict) -> Path:
    sub = tmp_path / "wiki"
    for rel, content in pages.items():
        p = sub / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


# --- refresh_after_commit (in-process) --------------------------------------

def test_refresh_regenerates_existing_artifacts(realistic_wiki_copy):
    wiki = realistic_wiki_copy
    init_repo(wiki)
    gq.wiki_report(wiki, write_file=True)
    from wikimcp.wiki.graph_export import export_html
    export_html(wiki)

    # Add a page on disk, then refresh with a repo-relative changed hint.
    (wiki / "wiki" / "topics" / "fresh.md").write_text(
        "---\ntitle: Fresh Topic\ntags: [ai]\n---\n# Fresh Topic\n\nMachine Learning.",
        encoding="utf-8",
    )
    result = gq.refresh_after_commit(
        wiki, changed_paths=["wiki/topics/fresh.md"], commit=False
    )
    assert set(result["refreshed"]) == {"WIKI_REPORT.md", "graph.html"}
    assert "Fresh Topic" in (wiki / "WIKI_REPORT.md").read_text()


def test_refresh_path_normalization_variants(realistic_wiki_copy):
    wiki = realistic_wiki_copy
    # Mix of repo-relative, nested, and non-wiki paths; must not raise.
    result = gq.refresh_after_commit(
        wiki,
        changed_paths=[
            "wiki/topics/python.md",          # repo-relative
            "/abs/repo/wiki/entities/bob.md",  # absolute with /wiki/ infix
            "README.md",                      # outside wiki/ -> ignored
            "",                                # empty -> ignored
        ],
        commit=False,
    )
    # No artifacts present yet -> nothing refreshed, but the call succeeds.
    assert result["refreshed"] == []


def test_refresh_no_artifacts(realistic_wiki_copy):
    result = gq.refresh_after_commit(realistic_wiki_copy, commit=False)
    assert result == {"refreshed": []}


def test_refresh_commits_when_artifact_changes(realistic_wiki_copy):
    import subprocess
    wiki = realistic_wiki_copy
    init_repo(wiki)
    gq.wiki_report(wiki, write_file=True)
    write_page(wiki, "topics/another.md", "---\ntitle: Another\ntags: [ai]\n---\n# Another\n")
    gq.refresh_after_commit(wiki, commit=True)
    subj = subprocess.run(
        ["git", "-C", str(wiki), "log", "--format=%s", "-n", "1"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert subj == "wiki: refresh graph artifacts"


# --- git-based recent additions fallback ------------------------------------

def test_recent_additions_from_git_when_no_frontmatter_dates(tmp_path):
    wiki = _wiki(
        tmp_path,
        {
            "topics/a.md": "# Alpha\n\n[[Beta]]",  # no frontmatter dates
            "topics/b.md": "# Beta\n\ncontent",
        },
    )
    init_repo(wiki)
    graph.invalidate_cache()
    report = gq.wiki_report(wiki)
    # No frontmatter dates -> recent additions come from git commit dates.
    assert "## Recent Additions" in report
    assert "topics/a.md" in report or "topics/b.md" in report


# --- directional subgraph expansion -----------------------------------------

def test_subgraph_direction_out(realistic_wiki):
    sg = gq.get_subgraph(realistic_wiki, "topics/machine-learning.md",
                         depth=1, max_nodes=40, direction="out")
    paths = {n["path"] for n in sg["nodes"]}
    # Out-expansion reaches pages ML references.
    assert "topics/neural-networks.md" in paths


def test_subgraph_direction_in(realistic_wiki):
    sg = gq.get_subgraph(realistic_wiki, "topics/machine-learning.md",
                         depth=1, max_nodes=40, direction="in")
    paths = {n["path"] for n in sg["nodes"]}
    # In-expansion reaches backlink sources.
    assert "projects/project-falcon.md" in paths


def test_subgraph_missing_page(realistic_wiki):
    assert "error" in gq.get_subgraph(realistic_wiki, "topics/nope.md")


def test_surprising_links_missing_and_empty(tmp_path):
    wiki = _wiki(tmp_path, {"topics/a.md": "# Alpha\n\nalone"})
    graph.invalidate_cache()
    assert gq.surprising_links(wiki)["surprising_links"] == []
