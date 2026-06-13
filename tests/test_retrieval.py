"""Tests for ranked wiki-page retrieval."""

from pathlib import Path

import pytest

from wikimcp.wiki.git_layer import init_repo
from wikimcp.wiki.operations import write_page
from wikimcp.wiki.retrieval import retrieve_context, search_pages
from wikimcp.wiki.schema import scaffold_wiki


@pytest.fixture
def wiki_dir(tmp_path: Path) -> Path:
    """Create a scaffolded and git-initialised wiki in a temp directory."""
    scaffold_wiki(tmp_path)
    init_repo(tmp_path)
    return tmp_path


def test_search_pages_ranks_title_matches_high(wiki_dir: Path) -> None:
    write_page(
        wiki_dir,
        "topics/authentication.md",
        "# Authentication\n\nToken validation protects MCP requests.",
    )
    write_page(
        wiki_dir,
        "topics/server-notes.md",
        "# Server Notes\n\nAuthentication is mentioned in passing.",
    )

    results = search_pages(wiki_dir, "authentication token")

    assert results
    assert results[0]["path"] == "topics/authentication.md"
    assert "title" in results[0]["matched_fields"]
    assert "Token validation" in results[0]["snippet"]


def test_search_pages_uses_heading_matches(wiki_dir: Path) -> None:
    write_page(
        wiki_dir,
        "topics/server.md",
        "# MCP Server\n\n## Authentication\n\nBearer tokens are validated per request.",
    )
    write_page(
        wiki_dir,
        "topics/random.md",
        "# Random Notes\n\nThis page mentions authentication once.",
    )

    results = search_pages(wiki_dir, "authentication bearer")

    assert results[0]["path"] == "topics/server.md"
    assert "heading" in results[0]["matched_fields"]


def test_search_pages_respects_limit(wiki_dir: Path) -> None:
    for i in range(3):
        write_page(
            wiki_dir,
            f"topics/memory-{i}.md",
            f"# Memory {i}\n\nPersistent memory for AI agents.",
        )

    results = search_pages(wiki_dir, "memory", limit=2)

    assert len(results) == 2


def test_search_pages_empty_query_returns_no_results(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/memory.md", "# Memory\n\nPersistent notes.")

    assert search_pages(wiki_dir, "   ") == []


def test_search_pages_no_match_returns_empty_list(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/memory.md", "# Memory\n\nPersistent notes.")

    assert search_pages(wiki_dir, "zzzz_not_present") == []


def test_retrieve_context_formats_top_snippets(wiki_dir: Path) -> None:
    write_page(
        wiki_dir,
        "topics/mcp-memory.md",
        "# MCP Memory\n\nWikiMCP stores long-term project memory in wiki pages.",
    )
    write_page(
        wiki_dir,
        "topics/python.md",
        "# Python\n\nPython syntax notes.",
    )

    context = retrieve_context(wiki_dir, "long term memory", limit=1)

    assert "## Retrieved Context" in context
    assert "### topics/mcp-memory.md" in context
    assert "WikiMCP stores long-term project memory" in context
    assert "topics/python.md" not in context


def test_retrieve_context_handles_no_results(wiki_dir: Path) -> None:
    write_page(wiki_dir, "topics/python.md", "# Python\n\nPython syntax notes.")

    context = retrieve_context(wiki_dir, "zzzz_not_present")

    assert context == "No relevant context found."


def test_search_pages_excludes_bookkeeping_pages(wiki_dir: Path) -> None:
    write_page(
        wiki_dir,
        "topics/authentication.md",
        "# Authentication\n\nBearer tokens protect MCP requests.",
    )
    write_page(
        wiki_dir,
        "chats/chat_2026-06-11.md",
        "# Chat Summary\n\nAuthentication tokens were discussed repeatedly.",
    )
    (wiki_dir / "wiki" / "index.md").write_text(
        "# Wiki Index\n\nAuthentication tokens authentication tokens authentication tokens.",
        encoding="utf-8",
    )
    (wiki_dir / "wiki" / "log.md").write_text(
        "# Activity Log\n\nAuthentication tokens authentication tokens authentication tokens.",
        encoding="utf-8",
    )

    results = search_pages(wiki_dir, "authentication tokens", limit=10)
    paths = [result["path"] for result in results]

    assert paths == ["topics/authentication.md"]


def test_retrieve_context_excludes_bookkeeping_pages(wiki_dir: Path) -> None:
    write_page(
        wiki_dir,
        "topics/authentication.md",
        "# Authentication\n\nBearer tokens protect MCP requests.",
    )
    write_page(
        wiki_dir,
        "chats/chat_2026-06-11.md",
        "# Chat Summary\n\nAuthentication tokens were discussed.",
    )
    (wiki_dir / "wiki" / "log.md").write_text(
        "# Activity Log\n\nAuthentication tokens were discussed.",
        encoding="utf-8",
    )

    context = retrieve_context(wiki_dir, "authentication tokens", limit=5)

    assert "topics/authentication.md" in context
    assert "chats/chat_2026-06-11.md" not in context
    assert "log.md" not in context
