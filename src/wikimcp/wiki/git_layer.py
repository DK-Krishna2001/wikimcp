"""
git_layer.py — Git operations for wiki repos using gitpython.

All functions are synchronous and stateless — they take wiki_dir as the first
parameter and operate on the git repo rooted there.

Commit author: wikimcp-bot <wikimcp@localhost>
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path
from typing import List, Dict, Optional

import git
from git.exc import GitCommandError, InvalidGitRepositoryError

_AUTHOR_NAME = "wikimcp-bot"
_AUTHOR_EMAIL = "wikimcp@localhost"
_ACTOR = git.Actor(_AUTHOR_NAME, _AUTHOR_EMAIL)


def _repo(wiki_dir: Path) -> git.Repo:
    """Return the git.Repo for wiki_dir, raising a clear error if not initialised."""
    try:
        return git.Repo(str(wiki_dir))
    except InvalidGitRepositoryError:
        raise RuntimeError(
            f"No git repository found at {wiki_dir}. "
            "Run init_repo() first."
        )


def init_repo(wiki_dir: Path) -> None:
    """
    Initialise a git repository at wiki_dir.

    If the repo already exists this is a no-op (safe to call multiple times).
    After initialisation, stages all existing files and creates an initial commit
    if there are any files to commit.
    """
    wiki_dir = Path(wiki_dir)
    try:
        repo = git.Repo(str(wiki_dir))
        # Repo already exists — nothing to do
        return
    except InvalidGitRepositoryError:
        pass

    repo = git.Repo.init(str(wiki_dir))

    # Configure user for this repo so commits don't fail on machines with no
    # global git config.
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", _AUTHOR_NAME)
        cfg.set_value("user", "email", _AUTHOR_EMAIL)

    # Stage everything that exists using the git binary (supports --all flag)
    # and make the first commit if there's anything to commit.
    repo.git.add("--all")
    if repo.index.entries:
        repo.index.commit(
            "wiki: initial commit",
            author=_ACTOR,
            committer=_ACTOR,
        )
        # Enable HTTP cloning
        try:
            repo.git.update_server_info()
        except GitCommandError:
            pass


def auto_commit(wiki_dir: Path, message: str) -> None:
    """
    Stage all changes (new, modified, deleted) and commit with the wikimcp-bot
    author.

    If there is nothing to commit (working tree clean) this is a no-op.
    """
    wiki_dir = Path(wiki_dir)
    repo = _repo(wiki_dir)

    # Stage all changes including deletions using the git binary
    repo.git.add("--all")

    # Determine whether there is anything staged to commit.
    # On a repo with no commits yet, repo.head.is_valid() is False.
    has_head = repo.head.is_valid()
    if has_head:
        # Compare index against HEAD — nothing staged means nothing to commit
        if not repo.index.diff("HEAD") and not repo.untracked_files:
            return
    else:
        # No commits yet — commit if the index has entries
        if not repo.index.entries:
            return

    repo.index.commit(
        message,
        author=_ACTOR,
        committer=_ACTOR,
    )

    # Update server info so the repo is cloneable over HTTP (dumb protocol)
    try:
        repo.git.update_server_info()
    except GitCommandError:
        pass  # non-critical — only needed for HTTP hosting


def add_remote(wiki_dir: Path, name: str, url: str) -> None:
    """
    Add a named git remote to the wiki repo.

    Raises ValueError if a remote with that name already exists.
    """
    wiki_dir = Path(wiki_dir)
    repo = _repo(wiki_dir)

    existing_names = [r.name for r in repo.remotes]
    if name in existing_names:
        raise ValueError(f"Remote '{name}' already exists. Remove it first.")

    repo.create_remote(name, url)


def remove_remote(wiki_dir: Path, name: str) -> None:
    """
    Remove a named git remote.

    Raises ValueError if the remote does not exist.
    """
    wiki_dir = Path(wiki_dir)
    repo = _repo(wiki_dir)

    existing_names = [r.name for r in repo.remotes]
    if name not in existing_names:
        raise ValueError(f"Remote '{name}' does not exist.")

    repo.delete_remote(name)


def list_remotes(wiki_dir: Path) -> List[Dict[str, str]]:
    """
    Return a list of dicts with keys 'name' and 'url' for every configured remote.

    Returns an empty list if no remotes are configured.
    """
    wiki_dir = Path(wiki_dir)
    repo = _repo(wiki_dir)

    result = []
    for remote in repo.remotes:
        result.append({"name": remote.name, "url": remote.url})
    return result


def push_remote(wiki_dir: Path, remote_name: str) -> str:
    """
    Push the current branch to a named remote.

    Returns an empty string on success.
    Returns a warning string (without raising) if the push fails due to a
    non-fast-forward conflict or any other git error.
    """
    wiki_dir = Path(wiki_dir)
    repo = _repo(wiki_dir)

    existing_names = [r.name for r in repo.remotes]
    if remote_name not in existing_names:
        return f"Warning: remote '{remote_name}' does not exist — push skipped."

    remote = repo.remote(remote_name)

    try:
        push_infos = remote.push()
        # Check for errors in push results
        for info in push_infos:
            # PushInfo flags — ERROR = 1024, REJECTED = 16, REMOTE_REJECTED = 32
            error_flags = (
                git.remote.PushInfo.ERROR
                | git.remote.PushInfo.REJECTED
                | git.remote.PushInfo.REMOTE_REJECTED
            )
            if info.flags & error_flags:
                return (
                    f"Warning: push to '{remote_name}' was rejected — "
                    "run `git pull` in your local clone to resolve the conflict. "
                    f"Details: {info.summary.strip()}"
                )
        return ""
    except GitCommandError as exc:
        return (
            f"Warning: push to '{remote_name}' failed — "
            "run `git pull` in your local clone to resolve the conflict. "
            f"Details: {exc}"
        )


# ---------------------------------------------------------------------------
# Graph post-commit hook
# ---------------------------------------------------------------------------

#: Marker line so we can recognise (and safely overwrite) our own hook.
_HOOK_MARKER = "# wikimcp-graph-hook"


def generate_post_commit_hook(wiki_dir: Path, python_exe: str) -> str:
    """Return the text of the post-commit hook that refreshes graph artifacts.

    The hook is fully offline: it asks wikimcp to re-index the pages changed in
    the just-made commit and regenerate any existing WIKI_REPORT.md / graph.html.
    It skips when HEAD is already a refresh commit, so it never loops.
    """
    wiki_dir = Path(wiki_dir).resolve()
    return f"""#!/bin/sh
{_HOOK_MARKER} (auto-generated by `wikimcp install-graph-hook`)
# Refresh the cached page graph + generated artifacts after each commit.
# Offline and deterministic. Skips its own refresh commit to avoid loops.
last=$(git log -1 --format=%s 2>/dev/null)
if [ "$last" = "wiki: refresh graph artifacts" ]; then
    exit 0
fi
changed=$(git diff-tree --no-commit-id --name-only -r HEAD 2>/dev/null | tr '\\n' ',')
"{python_exe}" -m wikimcp.cli graph-refresh --wiki-dir "{wiki_dir}" --changed "$changed" >/dev/null 2>&1 || true
exit 0
"""


def install_post_commit_hook(
    wiki_dir: Path,
    python_exe: Optional[str] = None,
) -> Path:
    """Install (or overwrite our own) post-commit hook for the wiki repo.

    Refuses to overwrite a pre-existing, non-wikimcp post-commit hook so we don't
    clobber user customisation. Returns the path to the installed hook.
    """
    wiki_dir = Path(wiki_dir)
    repo = _repo(wiki_dir)
    hooks_dir = Path(repo.git_dir) / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "post-commit"

    if hook_path.exists():
        existing = hook_path.read_text(encoding="utf-8", errors="ignore")
        if _HOOK_MARKER not in existing:
            raise FileExistsError(
                f"A non-wikimcp post-commit hook already exists at {hook_path}. "
                "Remove or merge it manually before installing."
            )

    python_exe = python_exe or sys.executable
    hook_path.write_text(
        generate_post_commit_hook(wiki_dir, python_exe), encoding="utf-8"
    )
    mode = hook_path.stat().st_mode
    hook_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook_path


def push_auto_remotes(wiki_dir: Path, remote_names: List[str]) -> List[str]:
    """
    Push to each remote in remote_names.

    Returns a list of warning strings (one per failed push). Empty list means
    all pushes succeeded.
    """
    wiki_dir = Path(wiki_dir)
    warnings = []
    for name in remote_names:
        warning = push_remote(wiki_dir, name)
        if warning:
            warnings.append(warning)
    return warnings
