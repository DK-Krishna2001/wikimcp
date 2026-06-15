"""
operations.py — Core wiki operations.

All functions are synchronous and stateless: they take wiki_dir (Path) as their
first argument and operate on the filesystem directly, then delegate git work to
git_layer.auto_commit().

Path conventions:
  - wiki_dir          : root of the user's wiki (contains CLAUDE.md, wiki/, raw/, .git/)
  - wiki_dir / "wiki" : the wiki/ subdirectory — all user pages live here
  - path arguments    : relative paths inside wiki/ (e.g. "topics/python.md")
                        Never absolute, never containing ".."
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .git_layer import auto_commit


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _wiki_subdir(wiki_dir: Path) -> Path:
    """Return the wiki/ subdirectory path."""
    return Path(wiki_dir) / "wiki"


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_SEARCH_INDEX_FILENAME = ".wikimcp_search.sqlite3"
_EMBEDDING_DIM = 256

# Hybrid ranking parameters.
_BM25_K1 = 1.5
_BM25_B = 0.75
_HYBRID_BM25_WEIGHT = 0.65
_HYBRID_VECTOR_WEIGHT = 0.35
# Minimum cosine similarity for a page with no lexical (BM25) hit to still be
# considered a semantic candidate.
_VECTOR_MATCH_THRESHOLD = 0.20


def _search_index_path(wiki_dir: Path) -> Path:
    return Path(wiki_dir) / _SEARCH_INDEX_FILENAME


def _tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


def _stable_bucket(value: str, dimensions: int = _EMBEDDING_DIM) -> int:
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=False) % dimensions


def _token_ngrams(token: str, n: int = 3) -> list[str]:
    if len(token) <= n:
        return [token]
    return [token[i : i + n] for i in range(len(token) - n + 1)]


def _embed_tokens(tokens: list[str], dimensions: int = _EMBEDDING_DIM) -> list[float]:
    if not tokens:
        return [0.0] * dimensions

    vector = [0.0] * dimensions
    for token in tokens:
        token_bucket = _stable_bucket(f"t:{token}", dimensions)
        vector[token_bucket] += 2.0

        for ngram in _token_ngrams(token):
            ngram_bucket = _stable_bucket(f"g:{ngram}", dimensions)
            vector[ngram_bucket] += 1.0

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def _preview_line(text: str) -> tuple[str, int]:
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped:
            return stripped, line_number
    return "", 1


def _validate_path(path: str) -> None:
    """
    Raise ValueError if 'path' is dangerous (absolute or contains ..).

    Accepts POSIX-style relative paths like 'topics/python.md'.
    """
    if not path:
        raise ValueError("Page path must not be empty.")
    p = Path(path)
    if p.is_absolute():
        raise ValueError(f"Page path must be relative, got: {path!r}")
    # Resolve against a dummy root and check nothing escapes
    for part in p.parts:
        if part == "..":
            raise ValueError(
                f"Page path must not contain '..', got: {path!r}"
            )


def _resolve_page(wiki_dir: Path, path: str) -> Path:
    """
    Return the absolute filesystem path for a page relative to wiki/.

    Validates the path first.
    """
    _validate_path(path)
    return _wiki_subdir(wiki_dir) / path


def _search_wiki_regex(
    wiki_sub: Path,
    query: str,
    *,
    case_sensitive: bool = False,
) -> List[Dict[str, Any]]:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(re.escape(query), flags)
    except re.error as exc:
        raise ValueError(f"Invalid search query: {exc}") from exc

    results = []
    for page_path in sorted(wiki_sub.rglob("*.md")):
        if any(part.startswith(".") for part in page_path.parts):
            continue
        try:
            text = page_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        matches = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                matches.append({"line": line, "line_number": line_number})

        if matches:
            rel_path = page_path.relative_to(wiki_sub).as_posix()
            results.append(
                {
                    "path": rel_path,
                    "matches": matches,
                    # Score fields are only meaningful for index-backed hybrid
                    # search; kept here (as None) so both code paths return the
                    # same result schema.
                    "score": None,
                    "bm25_score": None,
                    "vector_score": None,
                }
            )

    return results


def _ensure_index_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            path TEXT PRIMARY KEY,
            token_counts_json TEXT NOT NULL,
            token_count INTEGER NOT NULL,
            preview_line TEXT NOT NULL,
            preview_line_number INTEGER NOT NULL,
            vector_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def _index_document(conn: sqlite3.Connection, rel_path: str, text: str) -> None:
    """Insert or update a single page in the search index."""
    tokens = _tokenize(text)
    token_counts: dict[str, int] = {}
    for token in tokens:
        token_counts[token] = token_counts.get(token, 0) + 1

    preview, preview_line_number = _preview_line(text)
    vector = _embed_tokens(tokens)
    conn.execute(
        """
        INSERT INTO documents (
            path,
            token_counts_json,
            token_count,
            preview_line,
            preview_line_number,
            vector_json
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            token_counts_json=excluded.token_counts_json,
            token_count=excluded.token_count,
            preview_line=excluded.preview_line,
            preview_line_number=excluded.preview_line_number,
            vector_json=excluded.vector_json
        """,
        (
            rel_path,
            json.dumps(token_counts, sort_keys=True),
            len(tokens),
            preview,
            preview_line_number,
            json.dumps(vector, separators=(",", ":")),
        ),
    )


def _touch_index_metadata(conn: sqlite3.Connection) -> None:
    built_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        INSERT INTO metadata (key, value)
        VALUES ('built_at', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (built_at,),
    )


def _ensure_search_index_ignored(wiki_dir: Path) -> None:
    """
    Make sure the SQLite search index is git-ignored in the wiki repo.

    The index lives at the wiki root and is rebuilt locally, so it must never be
    committed (it would churn a binary blob into history on every auto-commit).
    The trailing '*' also covers SQLite's -wal/-shm/-journal sidecar files.
    """
    gitignore = Path(wiki_dir) / ".gitignore"
    entry = f"{_SEARCH_INDEX_FILENAME}*"
    try:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    except OSError:
        return

    lines = existing.splitlines()
    if entry in lines or _SEARCH_INDEX_FILENAME in lines:
        return

    new_content = existing
    if new_content and not new_content.endswith("\n"):
        new_content += "\n"
    new_content += entry + "\n"
    try:
        gitignore.write_text(new_content, encoding="utf-8")
    except OSError:
        return


def _index_page_key(path: str) -> str:
    """Normalise a wiki-relative page path to its index key (POSIX form)."""
    return Path(path).as_posix()


def _sync_index_after_write(wiki_dir: Path, path: str, content: str) -> None:
    """Keep the index in sync when a page is created or overwritten.

    No-op when no index exists yet — the index is opt-in via
    rebuild_search_index(). When one does exist, this keeps newly written or
    edited pages searchable without a manual rebuild.
    """
    index_path = _search_index_path(wiki_dir)
    if not index_path.exists():
        return
    _ensure_search_index_ignored(wiki_dir)
    conn = sqlite3.connect(index_path)
    try:
        _ensure_index_schema(conn)
        _index_document(conn, _index_page_key(path), content)
        _touch_index_metadata(conn)
        conn.commit()
    finally:
        conn.close()


def _sync_index_after_delete(wiki_dir: Path, path: str) -> None:
    """Drop a page from the index when it is deleted (no-op without an index)."""
    index_path = _search_index_path(wiki_dir)
    if not index_path.exists():
        return
    conn = sqlite3.connect(index_path)
    try:
        conn.execute(
            "DELETE FROM documents WHERE path = ?", (_index_page_key(path),)
        )
        _touch_index_metadata(conn)
        conn.commit()
    finally:
        conn.close()


def rebuild_search_index(wiki_dir: Path) -> Dict[str, Any]:
    """
    Build or refresh the optional hybrid search index.

    The index stores per-page token statistics and compact vector embeddings in
    SQLite so search can avoid rescanning markdown files on each query. Once
    built, the index is kept in sync incrementally by write_page/update_index/
    append_log/delete_page; call this to do a full rebuild from scratch.
    """
    wiki_dir = Path(wiki_dir)
    wiki_sub = _wiki_subdir(wiki_dir)
    if not wiki_sub.exists():
        return {"indexed_pages": 0, "index_path": str(_search_index_path(wiki_dir))}

    index_path = _search_index_path(wiki_dir)
    _ensure_search_index_ignored(wiki_dir)
    conn = sqlite3.connect(index_path)
    try:
        _ensure_index_schema(conn)

        seen_paths: list[str] = []
        indexed_pages = 0
        for page_path in sorted(wiki_sub.rglob("*.md")):
            if any(part.startswith(".") for part in page_path.parts):
                continue
            try:
                text = page_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            rel_path = page_path.relative_to(wiki_sub).as_posix()
            _index_document(conn, rel_path, text)
            seen_paths.append(rel_path)
            indexed_pages += 1

        if seen_paths:
            placeholders = ", ".join("?" for _ in seen_paths)
            conn.execute(
                f"DELETE FROM documents WHERE path NOT IN ({placeholders})",
                seen_paths,
            )
        else:
            conn.execute("DELETE FROM documents")

        _touch_index_metadata(conn)
        conn.commit()
    finally:
        conn.close()

    return {
        "indexed_pages": indexed_pages,
        "index_path": str(index_path),
    }


def _search_wiki_hybrid(
    wiki_dir: Path,
    query: str,
    *,
    case_sensitive: bool = False,
) -> Optional[List[Dict[str, Any]]]:
    index_path = _search_index_path(wiki_dir)
    if not index_path.exists():
        return None

    wiki_sub = _wiki_subdir(wiki_dir)
    query_tokens = _tokenize(query)
    if not query_tokens:
        return _search_wiki_regex(wiki_sub, query, case_sensitive=case_sensitive)

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(re.escape(query), flags)
    except re.error as exc:
        raise ValueError(f"Invalid search query: {exc}") from exc

    conn = sqlite3.connect(index_path)
    try:
        rows = conn.execute(
            """
            SELECT
                path,
                token_counts_json,
                token_count,
                preview_line,
                preview_line_number,
                vector_json
            FROM documents
            """
        ).fetchall()
    finally:
        conn.close()

    # An empty index can't answer anything — signal the caller to fall back to
    # the regex scan rather than reporting "no matches".
    if not rows:
        return None

    docs: list[dict[str, Any]] = []
    for row in rows:
        try:
            docs.append(
                {
                    "path": row[0],
                    "token_counts": json.loads(row[1]),
                    "token_count": int(row[2]),
                    "preview_line": row[3],
                    "preview_line_number": int(row[4]),
                    "vector": [float(value) for value in json.loads(row[5])],
                }
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            continue

    if not docs:
        return None

    unique_terms = set(query_tokens)
    doc_count = len(docs)
    avg_doc_len = (
        sum(max(doc["token_count"], 1) for doc in docs) / max(doc_count, 1)
    ) or 1.0
    doc_freq = {
        term: sum(1 for doc in docs if doc["token_counts"].get(term, 0) > 0)
        for term in unique_terms
    }

    query_vector = _embed_tokens(query_tokens)

    scored_docs: list[dict[str, Any]] = []
    for doc in docs:
        token_counts = doc["token_counts"]
        doc_len = max(doc["token_count"], 1)
        bm25 = 0.0
        for term in unique_terms:
            tf = token_counts.get(term, 0)
            if tf <= 0:
                continue
            df = doc_freq.get(term, 0)
            idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
            numerator = tf * (_BM25_K1 + 1)
            denominator = tf + _BM25_K1 * (
                1 - _BM25_B + _BM25_B * (doc_len / avg_doc_len)
            )
            bm25 += idf * (numerator / denominator)

        vector_score = max(0.0, _cosine_similarity(query_vector, doc["vector"]))
        if bm25 <= 0.0 and vector_score < _VECTOR_MATCH_THRESHOLD:
            continue

        # Skip pages whose file no longer exists so a stale index can't surface
        # phantom hits for deleted pages.
        if not (wiki_sub / doc["path"]).exists():
            continue

        scored_docs.append(
            {
                "path": doc["path"],
                "bm25_score": bm25,
                "vector_score": vector_score,
                "preview_line": doc["preview_line"],
                "preview_line_number": doc["preview_line_number"],
            }
        )

    # Nothing matched in the index — fall back to a regex scan so substring or
    # partial-word queries the token index can't represent still work.
    if not scored_docs:
        return None

    max_bm25 = max(doc["bm25_score"] for doc in scored_docs) or 1.0
    for doc in scored_docs:
        bm25_normalized = doc["bm25_score"] / max_bm25 if max_bm25 > 0 else 0.0
        doc["score"] = (
            _HYBRID_BM25_WEIGHT * bm25_normalized
            + _HYBRID_VECTOR_WEIGHT * doc["vector_score"]
        )

    scored_docs.sort(key=lambda item: (-item["score"], item["path"]))

    results: list[dict[str, Any]] = []
    for doc in scored_docs:
        page_path = wiki_sub / doc["path"]
        try:
            text = page_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # File vanished after the existence check above — skip rather than
            # emit a phantom hit from the stale index.
            continue

        matches = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                matches.append({"line": line, "line_number": line_number})

        if not matches:
            # No literal line match (e.g. a semantic-only hit) — fall back to a
            # preview line read from the current file, not the stored index.
            preview, preview_line_number = _preview_line(text)
            if preview:
                matches.append(
                    {"line": preview, "line_number": preview_line_number}
                )

        if not matches:
            continue

        results.append(
            {
                "path": doc["path"],
                "matches": matches,
                "score": round(doc["score"], 6),
                "bm25_score": round(doc["bm25_score"], 6),
                "vector_score": round(doc["vector_score"], 6),
            }
        )

    return results


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------

def wiki_info(wiki_dir: Path) -> Dict[str, Any]:
    """
    Return summary information about the wiki.

    Returns a dict with:
      page_count  : int — number of .md files under wiki/ (excluding log.md)
      log_entries : int — number of log entries in wiki/log.md
      wiki_root   : str — absolute path to wiki_dir
    """
    wiki_dir = Path(wiki_dir)
    wiki_sub = _wiki_subdir(wiki_dir)

    # Count .md pages (exclude log.md from the page count so it doesn't inflate it)
    page_count = 0
    if wiki_sub.exists():
        for p in wiki_sub.rglob("*.md"):
            if p.name != "log.md":
                page_count += 1

    # Count log entries — each entry starts with a "## " heading
    log_entries = 0
    log_path = wiki_sub / "log.md"
    if log_path.exists():
        text = log_path.read_text(encoding="utf-8")
        log_entries = len(re.findall(r"^## ", text, re.MULTILINE))

    # Read CLAUDE.md (wiki schema / workflow instructions) if it exists
    schema = ""
    claude_md = wiki_dir / "CLAUDE.md"
    if claude_md.exists():
        schema = claude_md.read_text(encoding="utf-8")

    return {
        "page_count": page_count,
        "log_entries": log_entries,
        "wiki_root": str(wiki_dir.resolve()),
        "schema": schema,
    }


def read_index(wiki_dir: Path) -> str:
    """
    Return the string content of wiki/index.md.

    Raises FileNotFoundError if the file does not exist.
    """
    wiki_dir = Path(wiki_dir)
    index_path = _wiki_subdir(wiki_dir) / "index.md"
    if not index_path.exists():
        raise FileNotFoundError(
            f"wiki/index.md not found at {index_path}. "
            "Run scaffold_wiki() first."
        )
    return index_path.read_text(encoding="utf-8")


def update_index(wiki_dir: Path, content: str) -> None:
    """
    Overwrite wiki/index.md with content and auto-commit.

    Creates the file (and parent directories) if it does not exist.
    """
    wiki_dir = Path(wiki_dir)
    index_path = _wiki_subdir(wiki_dir) / "index.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(content, encoding="utf-8")
    _sync_index_after_write(wiki_dir, "index.md", content)
    auto_commit(wiki_dir, "wiki: update_index wiki/index.md")


def write_page(wiki_dir: Path, path: str, content: str) -> None:
    """
    Create or overwrite a page at wiki/<path> and auto-commit.

    path must be a relative path (e.g. "topics/python.md").
    Parent directories are created automatically.
    Raises ValueError for invalid paths.
    """
    wiki_dir = Path(wiki_dir)
    page_path = _resolve_page(wiki_dir, path)
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(content, encoding="utf-8")
    _sync_index_after_write(wiki_dir, path, content)
    auto_commit(wiki_dir, f"wiki: write_page wiki/{path}")


def read_page(wiki_dir: Path, path: str) -> str:
    """
    Return the string content of wiki/<path>.

    Raises ValueError for invalid paths.
    Raises FileNotFoundError if the page does not exist.
    """
    wiki_dir = Path(wiki_dir)
    page_path = _resolve_page(wiki_dir, path)
    if not page_path.exists():
        raise FileNotFoundError(f"Page not found: wiki/{path}")
    return page_path.read_text(encoding="utf-8")


def list_pages(
    wiki_dir: Path,
    subdirectory: Optional[str] = None,
) -> List[str]:
    """
    Return a sorted list of page paths relative to wiki/.

    If subdirectory is given (e.g. "topics"), only pages inside that
    subdirectory are returned.

    Hidden files (starting with '.') and non-.md files are excluded.
    """
    wiki_dir = Path(wiki_dir)
    wiki_sub = _wiki_subdir(wiki_dir)

    if subdirectory is not None:
        _validate_path(subdirectory)
        search_root = wiki_sub / subdirectory
    else:
        search_root = wiki_sub

    if not search_root.exists():
        return []

    pages = []
    for p in search_root.rglob("*.md"):
        if not any(part.startswith(".") for part in p.parts):
            rel = p.relative_to(wiki_sub)
            pages.append(str(rel))

    return sorted(pages)


def search_wiki(
    wiki_dir: Path,
    query: str,
    case_sensitive: bool = False,
) -> List[Dict[str, Any]]:
    """
    Full-text search across all .md pages under wiki/.

    Returns a list of dicts, one per matching page:
      {
        "path": "topics/python.md",          # relative to wiki/
        "matches": [
          {"line": "...", "line_number": 12},
          ...
        ]
      }

    Pages with no matching lines are omitted from the result.
    """
    wiki_dir = Path(wiki_dir)
    wiki_sub = _wiki_subdir(wiki_dir)

    if not wiki_sub.exists():
        return []

    hybrid_results = _search_wiki_hybrid(
        wiki_dir,
        query,
        case_sensitive=case_sensitive,
    )
    if hybrid_results is not None:
        return hybrid_results

    return _search_wiki_regex(
        wiki_sub,
        query,
        case_sensitive=case_sensitive,
    )


def append_log(
    wiki_dir: Path,
    entry: str,
    operation: Optional[str] = None,
) -> None:
    """
    Append a timestamped entry to wiki/log.md and auto-commit.

    Each entry is formatted as a level-2 heading with the UTC timestamp, an
    optional operation label, and the entry text:

        ## 2026-04-14T10:30:00Z [chat]

        Entry text here.

    Creates wiki/log.md if it does not exist.
    """
    wiki_dir = Path(wiki_dir)
    log_path = _wiki_subdir(wiki_dir) / "log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if not log_path.exists():
        log_path.write_text("# Activity Log\n\n", encoding="utf-8")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    heading = f"## {timestamp}"
    if operation:
        heading += f" [{operation}]"

    block = f"\n{heading}\n\n{entry.strip()}\n"

    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(block)

    _sync_index_after_write(
        wiki_dir, "log.md", log_path.read_text(encoding="utf-8")
    )

    op_label = operation or "log"
    auto_commit(wiki_dir, f"wiki: append_log {op_label} {timestamp}")


def delete_page(wiki_dir: Path, path: str) -> None:
    """
    Delete wiki/<path> and auto-commit.

    Raises ValueError for invalid paths.
    Raises FileNotFoundError if the page does not exist.
    Raises PermissionError if you attempt to delete index.md or log.md.
    """
    wiki_dir = Path(wiki_dir)
    _validate_path(path)

    # Guard the critical files
    if path in ("index.md", "log.md"):
        raise PermissionError(f"'{path}' is a protected file and cannot be deleted.")

    page_path = _resolve_page(wiki_dir, path)
    if not page_path.exists():
        raise FileNotFoundError(f"Page not found: wiki/{path}")

    page_path.unlink()
    _sync_index_after_delete(wiki_dir, path)

    # Remove any now-empty parent directories (but never remove wiki/ itself)
    wiki_sub = _wiki_subdir(wiki_dir)
    parent = page_path.parent
    while parent != wiki_sub and parent.exists():
        try:
            parent.rmdir()  # only removes if empty
            parent = parent.parent
        except OSError:
            break  # not empty — stop

    auto_commit(wiki_dir, f"wiki: delete_page wiki/{path}")
