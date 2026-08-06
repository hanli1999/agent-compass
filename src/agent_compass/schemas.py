"""Optional JSON Schema validation for Agent Compass outputs.

The schemas are bundled in ``schemas/`` and used by ``agent-compass validate``
to confirm that a JSON document matches the contract for a decision, task,
memory, or feedback event. ``jsonschema`` is a development-time optional
dependency: when it is not installed, the validator returns a clear error.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
SUPPORTED = {
    "decision": "decision.schema.json",
    "task": "task.schema.json",
    "memory": "memory.schema.json",
    "feedback": "feedback.schema.json",
}


class SchemaUnavailable(RuntimeError):
    """Raised when the ``jsonschema`` package is not installed."""


def validate(name: str, document: Any) -> tuple[bool, list[str]]:
    if name not in SUPPORTED:
        return False, [f"unknown schema: {name}. supported: {sorted(SUPPORTED)}"]
    schema_path = SCHEMA_DIR / SUPPORTED[name]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        import jsonschema  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised manually
        raise SchemaUnavailable(
            "jsonschema is not installed. Install with: pip install jsonschema"
        ) from exc
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda e: e.path)
    return (not errors), [error.message for error in errors]
