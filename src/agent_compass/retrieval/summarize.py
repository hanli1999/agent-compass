"""Summarisation: turn a memory body into a bounded, query-aware digest.

The iron rule of this layer is that retrieval never returns full content.
A memory store that hands back whole documents just relocates the context
overflow problem; it does not solve it.

The summariser is extractive and dependency-free on purpose. It runs on every
retrieval call, so it must be fast and must never make a network request or
load a model. Callers who want abstractive summaries can wrap this.
"""
from __future__ import annotations

import re

DEFAULT_SUMMARY_CHARS = 240

#: Sentence-ish split. Handles both Western and CJK terminators.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？\n])\s*")

_ELLIPSIS = "…"


def _sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text) if p and p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _hits(sentence: str, keywords: list[str]) -> int:
    low = sentence.lower()
    return sum(1 for k in keywords if k in low)


def summarize(
    content: str,
    *,
    keywords: list[str] | None = None,
    max_chars: int = DEFAULT_SUMMARY_CHARS,
) -> tuple[str, bool]:
    """Return ``(summary, truncated)``.

    Short content is passed through untouched — summarising a one-liner only
    loses information.

    For longer content we pick sentences by keyword density, keeping the
    original reading order so the digest still parses as prose. The first
    sentence is always a candidate: memories written by agents tend to lead
    with the point.

    ``truncated`` tells the caller whether anything was dropped, so a UI can
    offer "expand" rather than pretending the digest is the whole record.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be positive")

    text = (content or "").strip()
    if not text:
        return "", False
    if len(text) <= max_chars:
        return text, False

    keywords = [k.lower() for k in (keywords or []) if k]
    sentences = _sentences(text)

    if not keywords:
        # No query signal: a head excerpt is the honest default.
        return text[: max_chars - 1].rstrip() + _ELLIPSIS, True

    ranked = sorted(
        range(len(sentences)),
        key=lambda i: (-_hits(sentences[i], keywords), i),
    )

    chosen: set[int] = set()
    used = 0
    for i in ranked:
        cost = len(sentences[i]) + 1
        if used + cost > max_chars:
            continue
        chosen.add(i)
        used += cost
    if not chosen:
        return text[: max_chars - 1].rstrip() + _ELLIPSIS, True

    digest = " ".join(sentences[i] for i in sorted(chosen)).strip()
    truncated = len(chosen) < len(sentences)
    if truncated and not digest.endswith(_ELLIPSIS):
        digest = digest[: max_chars - 1].rstrip() + _ELLIPSIS
    return digest, truncated
