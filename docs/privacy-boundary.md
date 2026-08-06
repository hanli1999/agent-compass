# Privacy Boundary

The privacy boundary is conservative:

```text
inspect → classify → redact or block → persist/transfer
```

`secret` patterns (private keys, JWTs, bearer tokens, API keys, passwords) are blocked. Sensitive patterns such as email addresses and phone numbers are redacted before remote transfer. Production applications should add domain-specific detectors.

The included detector is intentionally a baseline, not a complete DLP product. Always review exports and configure a stricter detector for regulated data.
