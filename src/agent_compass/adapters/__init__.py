"""Provider adapter contracts and offline defaults.

The core library never imports a model SDK. Adapters are optional; an agent
runtime that does not need classification or summarization can rely solely on
the deterministic policy engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMAdapter(Protocol):
    """Optional adapter contract for model-assisted classification or summarization.

    Implementations must:

    * return structured data, never raise on provider output;
    * enforce timeouts and never log complete prompts or results;
    * treat provider output as untrusted data and feed it back through the
      deterministic privacy and policy gates.
    """

    name: str

    def classify(self, request: dict[str, Any]) -> dict[str, Any]: ...

    def summarize(self, request: dict[str, Any]) -> str: ...


@dataclass
class NullAdapter:
    """Offline default that refuses to call any model.

    Useful for unit tests, dry runs, and deployments where model assistance is
    not allowed. ``classify`` returns a ``{"refused": True}`` payload and
    ``summarize`` returns the empty string.
    """

    name: str = "null"

    def classify(self, request: dict[str, Any]) -> dict[str, Any]:
        return {"refused": True, "reason": "no adapter configured", "request_keys": sorted(request.keys())}

    def summarize(self, request: dict[str, Any]) -> str:
        return ""


__all__ = ["LLMAdapter", "NullAdapter"]
