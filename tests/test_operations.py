"""Tests for wikimcp.wiki.operations using a temporary wiki directory."""
import pytest
from pathlib import Path

from wikimcp.wiki.schema import scaffold_wiki
from wikimcp.wiki.git_layer import init_repo
from wikimcp.wiki.operations import (
    wiki_info,
    write_page,
    read_page,
    list_pages,
    search_wiki,
    rebuild_search_index,
    delete_page,
)


@pytest.fixture
def wiki_dir(tmp_path: Path) -> Path:
    """Create a scaffolded and git-initialised wiki in a temp directory."""
    scaffold_wiki(tmp_path)
    init_repo(tmp_path)
    return tmp_path


def test_wiki_info_returns_dict(wiki_dir: Path) -> None:
    info = wiki_info(wiki_dir)
    assert isinstance(info, dict)
    assert "page_count" in info
    assert "log_entries" in info
    assert "wiki_root" in info
    assert info["wiki_root"] == str(wiki_dir.resolve())


def test_wiki_info_page_count_starts_at_zero(wiki_dir: Path) -> None:
    # scaffold creates index.md and log.md; wiki_info excludes log.md from count
    info = wiki_info(wiki_dir)
    # index.md is counted (not excluded), log.md is excluded
    assert info["page_count"] >= 1


def test_write_and_read_page(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/python.md", "# Python\n\nNotes here.")
    content = read_page(wiki_dir, "topics/python.md")
    assert "# Python" in content
    assert "Notes here." in content


def test_read_page_not_found(wiki_dir: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_page(wiki_dir, "topics/nonexistent.md")


def test_write_page_invalid_path(wiki_dir: Path) -> None:
    with pytest.raises(ValueError):
        write_page(wiki_dir, "../escape.md", "bad")


def test_list_pages_empty(wiki_dir: Path) -> None:
    # Fresh wiki has index.md and log.md in wiki/
    pages = list_pages(wiki_dir)
    assert isinstance(pages, list)
    assert "index.md" in pages


def test_list_pages_with_written_pages(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/alpha.md", "alpha")
    write_page(wiki_dir, "topics/beta.md", "beta")
    pages = list_pages(wiki_dir)
    assert "topics/alpha.md" in pages
    assert "topics/beta.md" in pages


def test_list_pages_subdirectory(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/alpha.md", "alpha")
    write_page(wiki_dir, "entities/alice.md", "alice")
    topics = list_pages(wiki_dir, "topics")
    assert all(p.startswith("topics/") for p in topics)


def test_search_wiki_finds_match(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/python.md", "# Python\n\nGreat language.")
    results = search_wiki(wiki_dir, "Great language")
    assert len(results) >= 1
    assert any(r["path"] == "topics/python.md" for r in results)


def test_search_wiki_no_match(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/python.md", "# Python\n\nGreat language.")
    results = search_wiki(wiki_dir, "xyzzy_no_match_here")
    assert results == []


def test_search_wiki_case_insensitive(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/python.md", "Python is awesome")
    results = search_wiki(wiki_dir, "python is awesome", case_sensitive=False)
    assert len(results) >= 1


def test_search_wiki_falls_back_without_index(wiki_dir: Path) -> None:
    write_page(
        wiki_dir,
        "topics/authentication.md",
        "# Authentication\n\nAuthentication protects API calls.",
    )

    # Regex fallback requires an exact line match.
    fallback_results = search_wiki(wiki_dir, "authenticating api calls")
    assert fallback_results == []

    rebuild_search_index(wiki_dir)

    hybrid_results = search_wiki(wiki_dir, "authenticating api calls")
    assert any(result["path"] == "topics/authentication.md" for result in hybrid_results)
    assert "score" in hybrid_results[0]
    assert "vector_score" in hybrid_results[0]


def test_rebuild_search_index_creates_sqlite_file(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/python.md", "# Python\n\nGreat language.")

    stats = rebuild_search_index(wiki_dir)

    assert stats["indexed_pages"] >= 1
    assert Path(stats["index_path"]).exists()


def test_rebuild_search_index_gitignores_index_file(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/python.md", "# Python\n\nGreat language.")
    rebuild_search_index(wiki_dir)

    gitignore = (wiki_dir / ".gitignore").read_text(encoding="utf-8")
    assert ".wikimcp_search.sqlite3" in gitignore


def test_deleted_page_removed_from_index_no_phantom(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/python.md", "# Python\n\nGreat language indeed.")
    rebuild_search_index(wiki_dir)

    # Sanity: the page is found before deletion.
    assert any(r["path"] == "topics/python.md" for r in search_wiki(wiki_dir, "language"))

    delete_page(wiki_dir, "topics/python.md")

    # After deletion the stale index must not surface a phantom hit.
    results = search_wiki(wiki_dir, "language")
    assert not any(r["path"] == "topics/python.md" for r in results)


def test_new_page_auto_indexed_without_rebuild(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/python.md", "# Python\n\nGreat language.")
    rebuild_search_index(wiki_dir)

    # Written after the index exists — should be searchable without a rebuild.
    write_page(wiki_dir, "topics/rust.md", "# Rust\n\nMemory safety guarantees.")

    results = search_wiki(wiki_dir, "memory safety")
    assert any(r["path"] == "topics/rust.md" for r in results)


def test_edited_page_reindexed_without_rebuild(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/python.md", "# Python\n\nOriginal content.")
    rebuild_search_index(wiki_dir)

    write_page(wiki_dir, "topics/python.md", "# Python\n\nDecorators and generators.")

    results = search_wiki(wiki_dir, "decorators generators")
    assert any(r["path"] == "topics/python.md" for r in results)


def test_empty_index_falls_back_to_regex(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/python.md", "# Python\n\nGreat language.")
    rebuild_search_index(wiki_dir)
    delete_page(wiki_dir, "topics/python.md")

    # Index now empty; a fresh page that was never indexed must still be found
    # via the regex fallback rather than being silently invisible.
    (wiki_dir / "wiki" / "topics").mkdir(parents=True, exist_ok=True)
    (wiki_dir / "wiki" / "topics" / "go.md").write_text(
        "# Go\n\nGoroutines.", encoding="utf-8"
    )

    results = search_wiki(wiki_dir, "Goroutines")
    assert any(r["path"] == "topics/go.md" for r in results)


def test_search_result_schema_consistent_across_paths(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/python.md", "# Python\n\nGreat language.")

    # Regex path (no index): score fields present but None.
    regex_results = search_wiki(wiki_dir, "Great language")
    assert regex_results
    for key in ("score", "bm25_score", "vector_score"):
        assert key in regex_results[0]

    # Hybrid path (index present): same keys, populated.
    rebuild_search_index(wiki_dir)
    hybrid_results = search_wiki(wiki_dir, "Great language")
    assert hybrid_results
    for key in ("score", "bm25_score", "vector_score"):
        assert key in hybrid_results[0]


def test_delete_page(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/temp.md", "temporary")
    delete_page(wiki_dir, "topics/temp.md")
    with pytest.raises(FileNotFoundError):
        read_page(wiki_dir, "topics/temp.md")


def test_delete_page_not_found(wiki_dir: Path) -> None:
    with pytest.raises(FileNotFoundError):
        delete_page(wiki_dir, "topics/ghost.md")


def test_delete_page_protected_index(wiki_dir: Path) -> None:
    with pytest.raises(PermissionError):
        delete_page(wiki_dir, "index.md")


def test_delete_page_protected_log(wiki_dir: Path) -> None:
    with pytest.raises(PermissionError):
        delete_page(wiki_dir, "log.md")
