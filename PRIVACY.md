# Privacy Boundary

Agent Compass classifies data before it is persisted or sent to a remote provider:

- `public`: safe for external use.
- `local_only`: stored and processed locally by default.
- `sensitive`: may require explicit consent; redact before remote use.
- `secret`: blocked from memory, logs, and remote transfer.

The project never stores credentials. Configure adapters with an environment-variable reference, not a literal secret. Use synthetic fixtures in tests and inspect exports before publishing.
