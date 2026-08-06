# Memory Model

The public formula is `activation-v1`:

```text
A = base + context + importance
base = log(1 + access_count) × retention(days, stability)
retention = exp(-days / stability)
```

The implementation records the formula version with each score. Memory types have different default importance and stability, but callers may provide an explicit bounded importance value.

Memory lifecycle:

```text
candidate → accepted → active → stale → archived/deleted
```

The library does not automatically save raw transcripts. Applications should propose short, factual, privacy-classified memories and keep their source task ID.
