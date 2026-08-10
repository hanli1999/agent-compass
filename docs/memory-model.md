# Memory Model

## Formulas

Two formulas coexist. Which one runs is decided by the record's own
`formula_version` field, so upgrading the library never silently rescores
existing rows. Every score is recorded with the version that produced it, so
any calculation can be reproduced exactly.

### `activation-v1` (default)

```text
A = base + context + importance
base = log(1 + access_count) × retention(days, stability)
retention = exp(-days / stability)
stability = base_stability(type) × log2(access_count + 2)
context = min(keyword_hits × 0.15, 0.75)
```

### `activation-v2` (opt-in)

```text
A = base + (context + importance + emotion + instinct) / 4
base = log(1 + access_count) × retention_dual(days, stability)
```

The four auxiliary dimensions are each normalised to `[0, 1]` and averaged, so
no single one can outweigh base activation. `base` keeps its native ACT-R
magnitude: it means "probability this memory is activated", and squashing it
into `[0, 1]` would throw away the access-frequency signal that makes it
useful.

A memory opts into v2 automatically when it is proposed with a tag:

```python
compass.memory.propose(
    "the release took production down for 40 minutes",
    memory_type="event",
    instinct_tag="survival",
    emotion_tag="anxious",
)   # formula_version == "activation-v2"
```

#### Two-segment retention

v2 replaces the single exponential with a steep-then-flat curve, switching at
7 days:

```text
days ≤ 7:  exp(-days / (stability × 0.5))
days >  7:  r(7) × exp(-(days - 7) / (stability × 2.0))
```

A single exponential puts every memory on the same curve and cannot express
"this one was already reconsolidated, stop decaying it". The two-segment form
forgets unrehearsed material faster in the first week and then plateaus, which
is closer to how retention actually behaves and keeps long-lived `identity`
and `event` records reachable at 60+ days.

#### Emotion

Affect attached to the memory, saturating at 1.0.

| Tag | Weight |
| --- | --- |
| `happy` | 1.3 → 1.0 |
| `excited` | 1.2 → 1.0 |
| `longing` | 1.1 → 1.0 |
| `neutral` | 1.0 |
| `anxious` | 0.9 |
| `sad` | 0.8 |

#### Instinct

A five-class minimal drive set — deliberately the smallest set that is already
present in single-celled organisms, rather than a taxonomy of higher
motivations. The claim is not that these are complete; it is that they are
primitive enough to be uncontroversial as *floor*.

| Tag | Weight | Sensitive to |
| --- | --- | --- |
| `survival` | 0.85 | threat, loss, damage |
| `kin` | 0.75 | social distance, relationship chains |
| `resource` | 0.70 | energy and resource acquisition |
| `novelty` | 0.65 | novelty, conflict, contradiction |
| `transmit` | 0.60 | shareable, replicable information |

Unknown tags contribute 0 rather than raising, so free-form tags from upstream
systems pass through harmlessly.

## Memory types

| Type | Default importance | Default base stability (days) |
| --- | --- | --- |
| `identity` | 0.9 | 60 |
| `decision` | 0.8 | 45 |
| `event` | 0.75 | 365 |
| `preference` | 0.7 | 35 |
| `workflow_pattern` | 0.65 | 30 |
| `task_lesson` | 0.6 | 21 |
| `project_context` | 0.55 | 21 |
| `temporary_note` | 0.2 | 7 |

Callers may provide an explicit bounded `importance` value; otherwise the table is used.

`event` exists because timeline facts ("the first release shipped on the 7th")
are poorly served by activation scoring: they are rarely accessed, so their
base activation collapses, yet they never stop being true. A 365-day stability
keeps historical anchors reachable without special-casing them elsewhere.

## Lifecycle

```text
candidate → accepted → active → stale → archived/deleted
```

* `propose(content, ...)` — privacy-scanned, scored, stored as `candidate`.
* `accept(memory_id)` — promote from `candidate` or `stale` to `accepted`.
* `activate(memory_id)` — promote from `accepted` (or `stale`) to `active`.
* `touch(memory_id)` — record an access and recompute the score; a stale memory can become `active` again.
* `archive(memory_id)` — set status to `archived`.
* `delete(memory_id)` — remove the row.
* `get(memory_id)` — fetch one memory in full. This is the expand half of the retrieval contract: recall returns bounded summaries, and the caller pulls the one or two bodies it actually needs.
* `prune(below, stale_below, dry_run)` — score every memory; demote to `archived` or `stale` based on thresholds.

`touch`, `prune` and `consolidate` all rescore through `route_score`, which
dispatches on each record's stored `formula_version`. A v1 row stays on v1
forever unless it is explicitly migrated.

The library does not automatically save raw transcripts. Applications should propose short, factual, privacy-classified memories and keep their `related_task_id`.

## Retrieval

Scoring decides *which* memories matter. It says nothing about how many to
hand an agent or how large they may be — that bound is enforced separately by
the retrieval layer. See [retrieval-orchestration.md](retrieval-orchestration.md).
