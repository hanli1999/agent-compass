# Memory Model

## Formula

The public formula is `activation-v1`:

```text
A = base + context + importance
base = log(1 + access_count) × retention(days, stability)
retention = exp(-days / stability)
stability = base_stability(type) × log2(access_count + 2)
```

Each score is recorded with its `formula_version` so callers can reproduce the calculation exactly.

## Memory types

| Type | Default importance | Default base stability (days) |
| --- | --- | --- |
| `identity` | 0.9 | 60 |
| `decision` | 0.8 | 45 |
| `preference` | 0.7 | 35 |
| `workflow_pattern` | 0.65 | 30 |
| `task_lesson` | 0.6 | 21 |
| `project_context` | 0.55 | 21 |
| `temporary_note` | 0.2 | 7 |

Callers may provide an explicit bounded `importance` value; otherwise the table is used.

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
* `prune(below, stale_below, dry_run)` — score every memory; demote to `archived` or `stale` based on thresholds.

The library does not automatically save raw transcripts. Applications should propose short, factual, privacy-classified memories and keep their `related_task_id`.
