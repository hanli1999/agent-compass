# agent-compass (skill pack)

A drop-in host-side SDK for Agent Compass (v0.8.0+).

## What you get

- The four-line recipe that flips v3 on, wires `DuckDuckGoAdapter`,
  and gives you an `AutoTracker`.
- Every `DecisionAction` branch handled, with a working Python
  skeleton.
- The cold-start Claude Code hooks installer.
- The headline verification test as a runnable script.

## Quick start

```bash
pip install agent-compass
python verify.py
```

If `verify.py` prints `OK`, the SDK is alive on your install and you
can read `recipes/host_loop.py` and adapt it to your loop.

## Files

| File                         | Purpose                                          |
| ---------------------------- | ------------------------------------------------ |
| `SKILL.md`                   | Claude skill description + decision-branch table |
| `README.md`                  | this file                                        |
| `recipes/host_loop.py`       | four-line recipe + every `DecisionAction` branch |
| `recipes/hooks_install.py`   | bare `install_claude_code_hooks()` call          |
| `verify.py`                  | runnable headline test                           |

## Philosophy

- **No policy module.** This skill does not contain any
  politically-sensitive content; it is a state and decision layer for
  LLM agents.
- **Honest self-report.** The four v3 fields require the host to set
  `complexity_score` and `uncertainty_score` honestly. The skill
  documents this; it does not enforce it.
- **Local-first.** `remote_allowed=False` is the default. The web
  rescue mode is opt-in.
- **Privacy is a hard boundary.** Secrets raise; sensitive text is
  redacted. The detector is a baseline, not a complete DLP product.

## License

MIT — same as the parent project.