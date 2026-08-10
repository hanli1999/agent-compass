"""Tests for the bounded retrieval layer."""
from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from agent_compass import Compass, CompassConfig
from agent_compass.retrieval import (
    LocalMemoryRetriever,
    RetrievalOrchestrator,
    RetrievalQuery,
    RetrievedItem,
    Retriever,
    count_keyword_hits,
    estimate_tokens,
    relevance_boost,
    render_digest,
    summarize,
)


def _compass(tmp_path):
    return Compass(CompassConfig(data_dir=tmp_path))


def _item(memory_id, score, hits=0, summary="x", age=0.0):
    return RetrievedItem(
        memory_id=memory_id, summary=summary, score=score, keyword_hits=hits, age_days=age
    )


# ------------------------------------------------------------------- the query

def test_query_rejects_nonsense_bounds():
    with pytest.raises(ValueError):
        RetrievalQuery(limit=0)
    with pytest.raises(ValueError):
        RetrievalQuery(token_budget=0)
    with pytest.raises(ValueError):
        RetrievalQuery(since_days=-1.0)


def test_query_merges_text_and_keywords_without_duplicates():
    q = RetrievalQuery(text="Merge The Tests", keywords=["merge", "ci"])
    assert q.effective_keywords() == ["merge", "ci", "the", "tests"]


# ------------------------------------------------------------------ summarize

def test_short_content_is_never_summarised():
    text = "run the tests"
    assert summarize(text, max_chars=100) == (text, False)


def test_long_content_is_truncated_and_flagged():
    summary, truncated = summarize("a" * 500, max_chars=100)
    assert truncated
    assert len(summary) <= 100


def test_summary_prefers_sentences_containing_the_keywords():
    content = (
        "The weather was pleasant. "
        "The deployment failed because the migration lock was never released. "
        "Lunch was fine."
    )
    summary, _ = summarize(content, keywords=["migration"], max_chars=80)
    assert "migration" in summary


def test_summary_keeps_original_reading_order():
    content = "Alpha happens first. Beta happens second. Gamma happens third."
    summary, _ = summarize(content, keywords=["gamma", "alpha"], max_chars=45)
    if "Alpha" in summary and "Gamma" in summary:
        assert summary.index("Alpha") < summary.index("Gamma")


def test_empty_content_summarises_to_empty():
    assert summarize("") == ("", False)


def test_summarize_rejects_bad_max_chars():
    with pytest.raises(ValueError):
        summarize("hello", max_chars=0)


@given(
    content=st.text(max_size=2000),
    max_chars=st.integers(min_value=1, max_value=500),
)
def test_summary_never_exceeds_its_budget(content, max_chars):
    summary, _ = summarize(content, keywords=["a"], max_chars=max_chars)
    assert len(summary) <= max_chars


# ------------------------------------------------------------------- keywords

def test_keyword_hits_count_distinct_terms_not_occurrences():
    memory = {"content": "test test test test", "keywords": ["test"], "memory_type": "test"}
    # "test" appears in every field many times, but it is one query term.
    assert count_keyword_hits(memory, ["test"]) == 1


def test_keyword_hits_across_different_terms_accumulate():
    memory = {"content": "deploy the migration", "keywords": ["urgent"]}
    assert count_keyword_hits(memory, ["deploy", "migration", "urgent"]) == 3


def test_no_keywords_means_no_hits():
    assert count_keyword_hits({"content": "anything"}, []) == 0


# ---------------------------------------------------------------------- boost

def test_boost_is_multiplicative():
    assert relevance_boost(2.0, 0) == pytest.approx(2.0)
    assert relevance_boost(2.0, 1) == pytest.approx(3.0)
    assert relevance_boost(2.0, 2) == pytest.approx(4.0)


def test_boost_lets_a_relevant_memory_overtake_a_dominant_one():
    """The reason the boost is multiplicative rather than additive.

    An established identity memory can sit permanently above everything on
    base activation alone. An additive +0.15/hit would never dislodge it.
    """
    identity_score = 3.0
    fresh_relevant = 1.5
    # 1.5 * (1 + 3*0.5) = 3.75 > 3.0, whereas 1.5 + 3*0.15 = 1.95 would lose.
    assert relevance_boost(fresh_relevant, 3) > relevance_boost(identity_score, 0)
    assert fresh_relevant + 3 * 0.15 < identity_score


def test_boost_rejects_negative_hits():
    with pytest.raises(ValueError):
        relevance_boost(1.0, -1)


# --------------------------------------------------------------- orchestration

class _StubRetriever:
    def __init__(self, name, items):
        self.name = name
        self._items = items

    def retrieve(self, query):
        return list(self._items)


class _ExplodingRetriever:
    name = "broken"

    def retrieve(self, query):
        raise RuntimeError("backend is down")


def test_stub_satisfies_the_protocol():
    assert isinstance(_StubRetriever("s", []), Retriever)


def test_results_are_ranked_by_boosted_score():
    orch = RetrievalOrchestrator(
        [_StubRetriever("s", [_item("low", 1.0, hits=3), _item("high", 2.0, hits=0)])]
    )
    result = orch.retrieve(RetrievalQuery(keywords=["x"]))
    assert result.memory_ids() == ["low", "high"]


def test_top_k_is_enforced_and_the_remainder_is_reported():
    items = [_item(f"m{i}", float(i)) for i in range(20)]
    result = RetrievalOrchestrator([_StubRetriever("s", items)]).retrieve(
        RetrievalQuery(limit=5)
    )
    assert len(result) == 5
    assert result.dropped_for_limit == 15
    assert result.considered == 20
    assert result.truncated


def test_token_budget_caps_the_payload():
    items = [_item(f"m{i}", 10.0 - i, summary="w" * 100) for i in range(5)]
    result = RetrievalOrchestrator([_StubRetriever("s", items)]).retrieve(
        RetrievalQuery(limit=5, token_budget=110)
    )
    assert result.estimated_tokens <= 110
    assert result.dropped_for_budget > 0
    assert result.truncated


def test_budget_packing_preserves_rank_order():
    """A smaller lower-ranked item must not jump the queue to fill the budget."""
    items = [_item("big", 10.0, summary="w" * 400), _item("small", 1.0, summary="w")]
    result = RetrievalOrchestrator([_StubRetriever("s", items)]).retrieve(
        RetrievalQuery(limit=5, token_budget=10)
    )
    assert result.memory_ids() == []
    assert result.dropped_for_budget == 2


def test_no_budget_means_everything_within_top_k_survives():
    items = [_item(f"m{i}", 1.0, summary="w" * 500) for i in range(3)]
    result = RetrievalOrchestrator([_StubRetriever("s", items)]).retrieve(
        RetrievalQuery(limit=7)
    )
    assert len(result) == 3
    assert result.dropped_for_budget == 0
    assert result.estimated_tokens > 0


def test_duplicate_memories_across_sources_are_merged():
    a = _StubRetriever("a", [_item("same", 1.0, hits=0)])
    b = _StubRetriever("b", [_item("same", 1.0, hits=4)])
    result = RetrievalOrchestrator([a, b]).retrieve(RetrievalQuery(keywords=["x"]))
    assert len(result) == 1
    # The better-matching copy wins.
    assert result.items[0].keyword_hits == 4


def test_a_failing_source_is_isolated_not_fatal():
    good = _StubRetriever("good", [_item("kept", 1.0)])
    result = RetrievalOrchestrator([_ExplodingRetriever(), good]).retrieve(RetrievalQuery())
    assert result.memory_ids() == ["kept"]
    assert "broken" in result.errors
    assert "backend is down" in result.errors["broken"]
    assert sorted(result.sources) == ["broken", "good"]


def test_a_plain_string_is_accepted_as_a_query():
    orch = RetrievalOrchestrator([_StubRetriever("s", [_item("m", 1.0, hits=1)])])
    assert len(orch.retrieve("some words")) == 1


def test_overrides_are_applied_to_a_query_object():
    items = [_item(f"m{i}", float(i)) for i in range(10)]
    result = RetrievalOrchestrator([_StubRetriever("s", items)]).retrieve(
        RetrievalQuery(limit=9), limit=2
    )
    assert len(result) == 2


def test_orchestrator_rejects_a_bad_default_limit():
    with pytest.raises(ValueError):
        RetrievalOrchestrator([], default_limit=0)


def test_empty_retriever_set_returns_an_empty_result():
    result = RetrievalOrchestrator([]).retrieve("anything")
    assert len(result) == 0
    assert not result.truncated


# -------------------------------------------------------------------- rendering

def test_digest_announces_what_it_withheld():
    items = [_item(f"m{i}", float(i), summary="note") for i in range(10)]
    result = RetrievalOrchestrator([_StubRetriever("s", items)]).retrieve(
        RetrievalQuery(limit=3)
    )
    text = render_digest(result)
    assert "withheld" in text
    assert "7" in text


def test_digest_of_nothing_says_so():
    assert render_digest(RetrievalOrchestrator([]).retrieve("x")).endswith("none.")


def test_digest_reports_unavailable_sources():
    text = render_digest(RetrievalOrchestrator([_ExplodingRetriever(), _StubRetriever("s", [_item("m", 1.0)])]).retrieve("q"))
    assert "unavailable" in text


# ------------------------------------------------------------ end-to-end, local

def test_recall_returns_summaries_and_never_full_bodies(tmp_path):
    compass = _compass(tmp_path)
    body = "migration lock. " * 200
    memory = compass.memory.propose(body, keywords=["migration"])

    result = compass.recall("migration")
    assert len(result) == 1
    item = result.items[0]
    assert item.truncated
    assert len(item.summary) < len(body)
    # ...but the full body is one call away, via the id we handed back.
    assert compass.memory.get(item.memory_id)["content"] == body
    assert item.memory_id == memory.memory_id


def test_recall_filters_out_non_matching_memories(tmp_path):
    compass = _compass(tmp_path)
    compass.memory.propose("about databases", keywords=["database"])
    compass.memory.propose("about cooking", keywords=["recipe"])
    assert len(compass.recall("database")) == 1


def test_recall_without_keywords_ranks_by_activation(tmp_path):
    compass = _compass(tmp_path)
    compass.memory.propose("low value note", importance=0.1)
    compass.memory.propose("who the user is", memory_type="identity", importance=0.9)
    result = compass.recall("")
    assert len(result) == 2
    assert result.items[0].score > result.items[1].score


def test_recall_hides_archived_memories_by_default(tmp_path):
    compass = _compass(tmp_path)
    memory = compass.memory.propose("stale fact", keywords=["fact"])
    compass.memory.archive(memory.memory_id)
    assert len(compass.recall("fact")) == 0
    assert len(compass.recall("fact", include_archived=True)) == 1


def test_recall_can_filter_by_memory_type(tmp_path):
    compass = _compass(tmp_path)
    compass.memory.propose("a lesson about deploys", keywords=["deploy"])
    compass.memory.propose("a deploy preference", memory_type="preference", keywords=["deploy"])
    result = compass.recall("deploy", memory_type="preference")
    assert len(result) == 1
    assert result.items[0].memory_type == "preference"


def test_recall_respects_a_token_budget_end_to_end(tmp_path):
    compass = _compass(tmp_path)
    for i in range(10):
        compass.memory.propose(f"deploy note number {i} " + "detail " * 50, keywords=["deploy"])
    result = compass.recall("deploy", token_budget=200)
    assert result.estimated_tokens <= 200
    assert result.truncated


def test_local_retriever_carries_provenance_and_formula_version(tmp_path):
    compass = _compass(tmp_path)
    compass.memory.propose("tagged event", memory_type="event", instinct_tag="survival", keywords=["event"])
    item = compass.recall("event").items[0]
    assert item.source == "local"
    assert item.formula_version == "activation-v2"


def test_since_days_excludes_older_memories(tmp_path):
    compass = _compass(tmp_path)
    memory = compass.memory.propose("ancient history", keywords=["ancient"])
    stored = compass.memory.get(memory.memory_id)
    stored["created_at"] = "2000-01-01T00:00:00+00:00"
    compass.store.save_memory(stored)
    assert len(compass.recall("ancient", since_days=30)) == 0
    assert len(compass.recall("ancient")) == 1


def test_retriever_is_reusable_standalone(tmp_path):
    """The retriever must work without the Compass facade wrapping it."""
    compass = _compass(tmp_path)
    compass.memory.propose("standalone use", keywords=["standalone"])
    retriever = LocalMemoryRetriever(compass.store)
    assert len(retriever.retrieve(RetrievalQuery(keywords=["standalone"]))) == 1


# ---------------------------------------------------------------- token maths

def test_token_estimate_is_monotonic_in_length():
    assert estimate_tokens("") == 0
    assert estimate_tokens("ab") <= estimate_tokens("abcd")


@given(text=st.text(min_size=1, max_size=1000))
def test_token_estimate_is_always_positive_for_non_empty_text(text):
    assert estimate_tokens(text) >= 1


# ------------------------------------------------------------ external sources

def test_callable_retriever_maps_rows_to_items():
    from agent_compass.retrieval import CallableRetriever

    rows = [{"memory_id": "r1", "content": "the migration lock was stuck", "score": 1.2}]
    retriever = CallableRetriever("wiki", lambda q: rows)
    items = retriever.retrieve(RetrievalQuery(keywords=["migration"]))
    assert len(items) == 1
    assert items[0].source == "wiki"
    assert items[0].memory_id == "r1"
    assert items[0].keyword_hits == 1
    assert items[0].score == pytest.approx(1.2)


def test_callable_retriever_honours_a_custom_field_map():
    from agent_compass.retrieval import CallableRetriever

    rows = [{"id": "x", "notes": "some text here"}]
    retriever = CallableRetriever(
        "bitable", lambda q: rows, field_map={"memory_id": "id", "content": "notes"}
    )
    assert retriever.retrieve(RetrievalQuery())[0].memory_id == "x"


def test_callable_retriever_reads_attributes_as_well_as_dicts():
    from agent_compass.retrieval import CallableRetriever

    class Row:
        memory_id = "obj"
        content = "attribute access works"

    assert CallableRetriever("o", lambda q: [Row()]).retrieve(RetrievalQuery())[0].memory_id == "obj"


def test_callable_retriever_gives_unscored_rows_a_usable_default():
    from agent_compass.retrieval import CallableRetriever, DEFAULT_REMOTE_SCORE

    item = CallableRetriever("r", lambda q: [{"content": "no score field"}]).retrieve(
        RetrievalQuery()
    )[0]
    assert item.score == pytest.approx(DEFAULT_REMOTE_SCORE)
    assert item.score > 0.0  # must not sort to the bottom by default


def test_callable_retriever_skips_empty_rows():
    from agent_compass.retrieval import CallableRetriever

    rows = [{"content": ""}, {"content": None}, {"content": "real"}]
    assert len(CallableRetriever("r", lambda q: rows).retrieve(RetrievalQuery())) == 1


def test_callable_retriever_caps_row_count():
    from agent_compass.retrieval import CallableRetriever

    rows = [{"content": f"row {i}"} for i in range(500)]
    retriever = CallableRetriever("r", lambda q: rows, max_rows=10)
    assert len(retriever.retrieve(RetrievalQuery())) == 10


def test_callable_retriever_requires_a_name():
    from agent_compass.retrieval import CallableRetriever

    with pytest.raises(ValueError):
        CallableRetriever("", lambda q: [])
    with pytest.raises(ValueError):
        CallableRetriever("r", lambda q: [], max_rows=0)


def test_callable_retriever_receives_the_full_query_for_pushdown():
    from agent_compass.retrieval import CallableRetriever

    seen = {}

    def fetch(query):
        seen["limit"] = query.limit
        seen["text"] = query.text
        return []

    CallableRetriever("r", fetch).retrieve(RetrievalQuery(text="hello", limit=3))
    assert seen == {"limit": 3, "text": "hello"}


def test_external_and_local_sources_rank_together(tmp_path):
    from agent_compass.retrieval import CallableRetriever

    compass = _compass(tmp_path)
    compass.memory.propose("local note about deploys", keywords=["deploy"])
    compass.retrieval.retrievers.append(
        CallableRetriever("wiki", lambda q: [{"memory_id": "w1", "content": "wiki deploy runbook"}])
    )
    result = compass.recall("deploy")
    assert sorted(i.source for i in result) == ["local", "wiki"]


def test_a_broken_external_source_does_not_break_local_recall(tmp_path):
    from agent_compass.retrieval import CallableRetriever

    def boom(query):
        raise ConnectionError("no network")

    compass = _compass(tmp_path)
    compass.memory.propose("local survives", keywords=["survives"])
    compass.retrieval.retrievers.append(CallableRetriever("flaky", boom))
    result = compass.recall("survives")
    assert len(result) == 1
    assert "flaky" in result.errors
