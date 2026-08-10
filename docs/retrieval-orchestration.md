# Retrieval orchestration

> Applies to `agent_compass.retrieval`, added in 0.4.0.

## The problem

Give an agent a good memory store and it will drown in it.

A well-populated Agent Compass database holds hundreds of memories. Any naive
recall — "return everything above score 0.3", "return the whole matching
document" — produces a prompt that overruns the model's context window long
before it runs out of relevant material. The failure is quiet: the agent
either truncates mid-record, or the provider rejects the request, or the
useful memory is buried under forty lines of adjacent ones.

Scoring alone does not fix this. Ranking tells you *which* memories matter;
it says nothing about *how many* to send or *how big* they may be.

## The contract

**Retrieval returns summaries. It never returns full memory bodies.**

Every result carries a `memory_id`. When the agent decides a particular entry
matters, it fetches the body explicitly:

```python
result = compass.recall("why did the migration fail", token_budget=800)

for item in result:
    print(item.summary)          # bounded digest

full = compass.memory.get(result.items[0].memory_id)["content"]   # on demand
```

This is the whole design. Summaries out, bodies on request. A layer that
returns documents has only moved the overflow somewhere else.

## Three bounds, applied in order

### 1. Relevance boost — multiplicative, not additive

```
effective_score = score × (1 + keyword_hits × 0.5)
```

The multiplication matters. An `identity` memory accumulates access count
over months; its base activation sits permanently above everything else. An
additive boost of `+0.15` per keyword hit cannot dislodge it, so a freshly
written note that answers the question exactly still loses to "the user's
name is X".

Multiplying scales the boost to the memory's own footing: two hits doubles
whatever activation a memory already had, so a relevant mid-tier memory
overtakes a dominant irrelevant one, without letting keyword stuffing beat
activation outright.

### 2. Top-K

Default 7. This is a behavioural number, not a technical one — past roughly
seven items an agent stops meaningfully reading what it was handed and starts
pattern-matching the first two.

### 3. Token budget

Applied *after* Top-K, because seven one-line memories and seven thousand-word
ones are very different bills. Items are packed in rank order and packing
stops at the first item that does not fit — it does not skip ahead to a
smaller one. An agent reads top-down and expects the list sorted by relevance,
not by what happened to fit.

Token counts are estimated with a conservative `len(text) / 2` heuristic. It
is not any real tokenizer. It is a monotonic proxy that errs toward
over-estimating, because the failure mode that hurts is a blown context
window, not a slightly under-filled one.

## Nothing is dropped silently

Every result reports what it withheld:

```python
result.considered            # how many candidates were ranked
result.dropped_for_limit     # cut by Top-K
result.dropped_for_budget    # cut by the token budget
result.truncated             # either of the above
```

`render_digest()` puts this in the text handed to the model:

```text
Relevant memories (3):
1. [task_lesson] always run the migration dry-run before deploying
   id=mem_9f2c1a4b7e30 score=1.842 hits=2
...
(4 further match(es) withheld; ~186 tokens shown.)
```

The model is *told* its view is partial. A digest that silently drops four
matches reads exactly like a complete answer, and the agent will confidently
act as though it saw everything.

## Sources

Any object with a `name` and a `retrieve(query)` method satisfies the
`Retriever` protocol. The orchestrator fans out to all of them, dedupes by
`memory_id` (keeping the better-matching copy), and ranks the union.

```python
from agent_compass.retrieval import CallableRetriever

def fetch_from_wiki(query):
    return wiki_client.search(query.text, limit=50)

compass.retrieval.retrievers.append(
    CallableRetriever("wiki", fetch_from_wiki, field_map={"content": "body"})
)
```

The callable receives the whole `RetrievalQuery`, so filters can be pushed
down to the backend — most APIs search better server-side than we can
client-side.

### Failure is isolated

A retriever that raises does not fail the call. The orchestrator records the
error under that source's name and continues:

```python
result = compass.recall("deploy")
result.errors      # {"wiki": "ConnectionError: no network"}
result.items       # local results, still there
```

A flaky network-backed store must never take down local recall. Degrade,
don't collapse.

### Scoring across sources

Remote rows are keyword-scored locally using the same rules as the local
store, so a wiki page and a stored memory land on one comparable scale rather
than two lists stapled together. Rows carrying no score of their own get
`DEFAULT_REMOTE_SCORE` (0.5) — zero would make every external result
invisible next to scored local memories.

## Summarisation

Extractive, dependency-free, and query-aware. It runs on every result of
every call, so it must never load a model or make a network request.

- Content already under the limit is passed through untouched.
- With keywords, sentences are ranked by keyword density and the selected
  ones are re-emitted **in original reading order**, so the digest still
  parses as prose.
- Without keywords, a head excerpt is the honest default.

Callers who want abstractive summaries should wrap this, not replace it — the
bound has to hold even when the summariser is unavailable.

## Tuning

| Knob | Default | Raise it when |
| --- | --- | --- |
| `RetrievalQuery.limit` | 7 | the agent reliably reads long lists |
| `RetrievalQuery.token_budget` | unset | you are near the context ceiling |
| `KEYWORD_BOOST_PER_HIT` | 0.5 | query relevance should beat activation harder |
| `DEFAULT_SUMMARY_CHARS` | 240 | summaries are losing necessary detail |

`context_cap` in the scoring layer was set to 0.5 rather than 0.75 after an
A/B run over a real 40-document store: five queries at top-7 produced zero
recall difference between the two, so the tighter cap wins the tie-break.
