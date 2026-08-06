# Provider Adapters

Provider adapters are optional. The core must run without an API key and must not import a model SDK. An adapter can use a model for classification or summarization, but the final privacy and approval gates remain deterministic.

Recommended adapter contract:

```python
class LLMAdapter(Protocol):
    def classify(self, request): ...
    def summarize(self, request): ...
```

Adapters should return structured data, enforce timeouts, avoid logging complete prompts/results, and treat provider output as untrusted data.
