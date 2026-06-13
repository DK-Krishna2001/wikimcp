"""
wikimcp.wiki — Core wiki module.

Exports the public API for wiki operations, git layer, and schema scaffolding.
"""

from .operations import (
    wiki_info,
    read_index,
    update_index,
    write_page,
    read_page,
    list_pages,
    search_wiki,
    append_log,
    delete_page,
)
from .retrieval import (
    search_pages,
    retrieve_context,
)
from .git_layer import (
    init_repo,
    auto_commit,
    add_remote,
    remove_remote,
    list_remotes,
    push_remote,
    push_auto_remotes,
)
from .schema import (
    scaffold_wiki,
    get_claude_md_template,
)

__all__ = [
    # operations
    "wiki_info",
    "read_index",
    "update_index",
    "write_page",
    "read_page",
    "list_pages",
    "search_wiki",
    "append_log",
    "delete_page",
    "search_pages",
    "retrieve_context",
    # git_layer
    "init_repo",
    "auto_commit",
    "add_remote",
    "remove_remote",
    "list_remotes",
    "push_remote",
    "push_auto_remotes",
    # schema
    "scaffold_wiki",
    "get_claude_md_template",
]
