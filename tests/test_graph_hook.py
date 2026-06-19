"""Hook / incremental tests (5i): an incremental rebuild re-indexes only the
changed pages and produces a graph byte-identical to a full rebuild; the
post-commit hook refreshes artifacts offline without looping."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from wikimcp.wiki import graph, graph_queries as gq
from wikimcp.wiki.git_layer import init_repo, install_post_commit_hook
from wikimcp.wiki.operations import write_page

ML = "topics/machine-learning.md"


def _edit(wiki: Path, rel: str, text: str) -> None:
    p = wiki / "wiki" / rel
    # Ensure the mtime advances so the fingerprint changes.
    time.sleep(0.01)
    p.write_text(text, encoding="utf-8")


def test_incremental_reparses_only_changed(realistic_wiki_copy):
    wiki = realistic_wiki_copy
    g1 = graph.build_graph(wiki)  # warm the cache
    before = dict(g1.pages)

    _edit(wiki, "topics/python.md", "---\ntitle: Python\ntags: [programming]\n---\n# Python\n\nedited body.")
    g2 = graph.build_graph(wiki, changed_paths=["topics/python.md"])

    # Changed page is a fresh object; all others are reused (not re-parsed).
    assert g2.pages["topics/python.md"] is not before["topics/python.md"]
    for rel, page in before.items():
        if rel == "topics/python.md":
            continue
        assert g2.pages[rel] is page


def test_incremental_equals_full_rebuild(realistic_wiki_copy):
    wiki = realistic_wiki_copy
    graph.build_graph(wiki)  # warm cache
    _edit(wiki, "topics/python.md", "---\ntitle: Python\ntags: [programming, data]\n---\n# Python\n\n[[NumPy]] only now.")

    incremental = graph.build_graph(wiki, changed_paths=["topics/python.md"])
    full = graph.build_graph(wiki, use_cache=False)
    assert incremental.signature() == full.signature()


def test_incremental_after_add_and_delete(realistic_wiki_copy):
    wiki = realistic_wiki_copy
    graph.build_graph(wiki)
    # Add a new page and delete an existing one.
    _edit(wiki, "topics/newpage.md", "---\ntitle: New Page\ntags: [ai]\n---\n# New Page\n\nAbout Machine Learning.")
    (wiki / "wiki" / "topics" / "orphan-idea.md").unlink()

    incremental = graph.build_graph(wiki, changed_paths=["topics/newpage.md"])
    full = graph.build_graph(wiki, use_cache=False)
    assert incremental.signature() == full.signature()
    assert "topics/newpage.md" in incremental.pages
    assert "topics/orphan-idea.md" not in incremental.pages


def test_post_commit_hook_refreshes_without_loop(realistic_wiki_copy):
    wiki = realistic_wiki_copy
    init_repo(wiki)
    # Generate artifacts so the hook has something to refresh.
    gq.wiki_report(wiki, write_file=True)
    install_post_commit_hook(wiki)

    # A write -> auto_commit -> post-commit -> one refresh commit (no loop).
    write_page(wiki, "topics/zzz-new.md",
               "---\ntitle: Brand New Topic\ntags: [ai]\n---\n# Brand New Topic\n\nMachine Learning here.")

    subjects = subprocess.run(
        ["git", "-C", str(wiki), "log", "--format=%s", "-n", "5"],
        capture_output=True, text=True,
    ).stdout.splitlines()
    # Exactly one refresh commit sits on top, then the page write below it.
    assert subjects[0] == "wiki: refresh graph artifacts"
    assert subjects[1] == "wiki: write_page wiki/topics/zzz-new.md"
    assert subjects[2] != "wiki: refresh graph artifacts"  # no loop

    # Working tree is clean and the report reflects the new page.
    status = subprocess.run(
        ["git", "-C", str(wiki), "status", "--porcelain"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert status == ""
    assert "Brand New Topic" in (wiki / "WIKI_REPORT.md").read_text()


def test_install_hook_refuses_to_clobber_foreign_hook(realistic_wiki_copy):
    wiki = realistic_wiki_copy
    init_repo(wiki)
    hook = Path(wiki) / ".git" / "hooks" / "post-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho custom\n", encoding="utf-8")
    try:
        install_post_commit_hook(wiki)
        assert False, "should have refused"
    except FileExistsError:
        pass
