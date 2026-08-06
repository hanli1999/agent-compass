# Security Policy

## Supported versions

Only the latest `main` branch and the latest release receive security fixes during the 0.x phase.

## Reporting

Please do not open a public issue for a suspected secret-leak, privacy-boundary bypass, path traversal, or unintended external side effect. Contact the maintainers privately through the repository's configured security channel.

Reports should include a minimal reproduction, affected version, impact, and whether a real secret was involved. Do not include the secret itself.

## Security design

The default is local-only. Secrets are blocked from memory proposals and remote transfer. Adapters must not log complete prompts, tool results, credentials, or private paths.
