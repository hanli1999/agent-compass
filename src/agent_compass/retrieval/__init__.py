"""Bounded, summary-first memory retrieval.

Retrieval here never returns full memory bodies. It returns a ranked digest
under an explicit Top-K and token budget, plus the ``memory_id`` needed to
fetch a body on demand. See ``orchestrator`` for the reasoning.
"""
from .models import (
    CHARS_PER_TOKEN,
    RetrievalQuery,
    RetrievalResult,
    RetrievedItem,
    estimate_tokens,
)
from .protocol import Retriever
from .summarize import DEFAULT_SUMMARY_CHARS, summarize
from .local import LocalMemoryRetriever, count_keyword_hits
from .orchestrator import (
    DEFAULT_TOP_K,
    KEYWORD_BOOST_PER_HIT,
    RetrievalOrchestrator,
    relevance_boost,
    render_digest,
)

__all__ = [
    "CHARS_PER_TOKEN",
    "DEFAULT_SUMMARY_CHARS",
    "DEFAULT_TOP_K",
    "KEYWORD_BOOST_PER_HIT",
    "LocalMemoryRetriever",
    "RetrievalOrchestrator",
    "RetrievalQuery",
    "RetrievalResult",
    "RetrievedItem",
    "Retriever",
    "count_keyword_hits",
    "estimate_tokens",
    "relevance_boost",
    "render_digest",
    "summarize",
]
