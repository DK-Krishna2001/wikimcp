"""
retrieval.py — Ranked wiki-page retrieval.

This module provides lightweight, dependency-free retrieval over the markdown
pages in wiki/. It is intentionally stateless: markdown files remain the source
of truth and are scanned live for each query.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
_DEFAULT_EXCLUDED_PAGES = {"index.md", "log.md"}
_DEFAULT_EXCLUDED_DIRS = {"chats"}


@dataclass
class _PageDocument:
    path: str
    title: str
    headings: list[str]
    text: str
    tokens: list[str]


def _wiki_subdir(wiki_dir: Path) -> Path:
    return Path(wiki_dir) / "wiki"


def _tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


def _strip_heading_markup(text: str) -> str:
    return re.sub(r"\s+#*\s*$", "", text).strip()


def _extract_title_and_headings(text: str, fallback_title: str) -> tuple[str, list[str]]:
    title = ""
    headings: list[str] = []

    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if not match:
            continue

        heading = _strip_heading_markup(match.group(2))
        if not heading:
            continue
        headings.append(heading)

        if match.group(1) == "#" and not title:
            title = heading

    return title or fallback_title, headings


def _iter_pages(wiki_dir: Path) -> list[_PageDocument]:
    wiki_sub = _wiki_subdir(wiki_dir)
    if not wiki_sub.exists():
        return []

    pages: list[_PageDocument] = []
    for page_path in sorted(wiki_sub.rglob("*.md")):
        if any(part.startswith(".") for part in page_path.parts):
            continue
        try:
            rel_path = str(page_path.relative_to(wiki_sub))
            rel_parts = Path(rel_path).parts
            if rel_path in _DEFAULT_EXCLUDED_PAGES or rel_parts[0] in _DEFAULT_EXCLUDED_DIRS:
                continue

            text = page_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        fallback_title = page_path.stem.replace("-", " ").replace("_", " ").strip()
        title, headings = _extract_title_and_headings(text, fallback_title)
        pages.append(
            _PageDocument(
                path=rel_path,
                title=title,
                headings=headings,
                text=text,
                tokens=_tokenize(text),
            )
        )

    return pages


def _bm25_scores(
    pages: list[_PageDocument],
    query_tokens: list[str],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> dict[str, float]:
    if not pages or not query_tokens:
        return {}

    unique_query_terms = set(query_tokens)
    doc_count = len(pages)
    avg_doc_len = sum(len(page.tokens) for page in pages) / max(doc_count, 1)
    avg_doc_len = avg_doc_len or 1.0

    doc_freq: dict[str, int] = {}
    term_freqs_by_path: dict[str, dict[str, int]] = {}
    for page in pages:
        counts: dict[str, int] = {}
        for token in page.tokens:
            if token in unique_query_terms:
                counts[token] = counts.get(token, 0) + 1
        term_freqs_by_path[page.path] = counts
        for token in counts:
            doc_freq[token] = doc_freq.get(token, 0) + 1

    scores: dict[str, float] = {}
    for page in pages:
        doc_len = len(page.tokens) or 1
        term_freqs = term_freqs_by_path[page.path]
        score = 0.0

        for term in unique_query_terms:
            tf = term_freqs.get(term, 0)
            if tf == 0:
                continue

            df = doc_freq.get(term, 0)
            idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * (doc_len / avg_doc_len))
            score += idf * (numerator / denominator)

        scores[page.path] = score

    return scores


def _metadata_score(page: _PageDocument, query_tokens: list[str]) -> tuple[float, list[str]]:
    title_tokens = set(_tokenize(page.title))
    heading_tokens = set(_tokenize(" ".join(page.headings)))
    body_tokens = set(page.tokens)

    score = 0.0
    matched_fields: list[str] = []

    if any(token in title_tokens for token in query_tokens):
        title_matches = sum(1 for token in set(query_tokens) if token in title_tokens)
        score += 2.0 * title_matches
        matched_fields.append("title")

    if any(token in heading_tokens for token in query_tokens):
        heading_matches = sum(1 for token in set(query_tokens) if token in heading_tokens)
        score += 1.25 * heading_matches
        matched_fields.append("heading")

    if any(token in body_tokens for token in query_tokens):
        matched_fields.append("body")

    return score, matched_fields


def _split_snippet_blocks(text: str) -> list[str]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    if blocks:
        return blocks
    return [line.strip() for line in text.splitlines() if line.strip()]


def _clean_snippet(text: str, max_chars: int = 320) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^#{1,6}\s+", "", text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def _best_snippet(page: _PageDocument, query_tokens: list[str]) -> str:
    blocks = _split_snippet_blocks(page.text)
    if not blocks:
        return ""

    unique_query_terms = set(query_tokens)

    def score_block(block: str) -> tuple[int, int, int]:
        block_tokens = _tokenize(block)
        overlap = sum(1 for token in block_tokens if token in unique_query_terms)
        unique_overlap = len(set(block_tokens) & unique_query_terms)
        is_body_text = 0 if _HEADING_RE.match(block) else 1
        return overlap, unique_overlap, is_body_text

    best = max(blocks, key=score_block)
    return _clean_snippet(best)


def search_pages(wiki_dir: Path, query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Return relevance-ranked wiki pages for a natural-language query.

    Results are ranked using a BM25 lexical score plus markdown-aware title and
    heading boosts. Each result includes path, title, score, matched fields, and
    a compact snippet from the best-matching block.
    """
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    limit = max(1, min(int(limit), 50))
    pages = _iter_pages(Path(wiki_dir))
    if not pages:
        return []

    bm25 = _bm25_scores(pages, query_tokens)
    results: list[dict[str, Any]] = []

    for page in pages:
        metadata_score, matched_fields = _metadata_score(page, query_tokens)
        score = bm25.get(page.path, 0.0) + metadata_score
        if score <= 0:
            continue

        results.append(
            {
                "path": page.path,
                "title": page.title,
                "score": round(score, 6),
                "snippet": _best_snippet(page, query_tokens),
                "matched_fields": matched_fields,
            }
        )

    results.sort(key=lambda item: (-item["score"], item["path"]))
    return results[:limit]


def retrieve_context(wiki_dir: Path, query: str, limit: int = 5) -> str:
    """
    Return compact, ready-to-use markdown context for the top ranked pages.
    """
    results = search_pages(wiki_dir, query, limit)
    if not results:
        return "No relevant context found."

    lines = ["## Retrieved Context"]
    for result in results:
        lines.append("")
        lines.append(f"### {result['path']}")
        lines.append(f"Title: {result['title']}")
        lines.append(f"Score: {result['score']:.4f}")
        if result["matched_fields"]:
            lines.append(f"Matched: {', '.join(result['matched_fields'])}")
        if result["snippet"]:
            lines.append("")
            lines.append(result["snippet"])

    return "\n".join(lines)
