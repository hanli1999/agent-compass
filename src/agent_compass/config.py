"""Configuration with safe, environment-based paths and optional YAML/JSON loading."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_TIME_SENSITIVE_KEYWORDS: tuple[str, ...] = (
    "latest",
    "current",
    "today",
    "now",
    "最新",
    "当前",
    "今天",
    "现在",
    "价格",
    "版本",
)

DEFAULT_DESTRUCTIVE_ACTIONS: set[str] = {
    "delete",
    "publish",
    "send_external_message",
    "modify_production",
}

DEFAULT_AMBIGUITY_THRESHOLD = 0.7
DEFAULT_MAX_RETRIES = 3
# New in 0.6.0 (policy-v3). complexity / uncertainty are interpreted as
# "this query deserves an outer action" — see docs/behavior-policy.md.
DEFAULT_COMPLEXITY_THRESHOLD = 0.6
DEFAULT_UNCERTAINTY_THRESHOLD = 0.5
# "Action pressure" — how many consecutive ANSWER_DIRECTLY calls before the
# engine forces a tool step. Three means a host that ignores complexity
# signals still gets nudged by the third silent answer in a row.
DEFAULT_ACTION_PRESSURE_THRESHOLD = 3


@dataclass
class CompassConfig:
    data_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("AGENT_COMPASS_DATA_DIR", Path.home() / ".agent-compass")
        )
    )
    remote_allowed: bool = False
    default_privacy: str = "local_only"
    destructive_actions: set[str] = field(default_factory=lambda: set(DEFAULT_DESTRUCTIVE_ACTIONS))
    time_sensitive_keywords: tuple[str, ...] = DEFAULT_TIME_SENSITIVE_KEYWORDS
    ambiguity_threshold: float = DEFAULT_AMBIGUITY_THRESHOLD
    max_retries: int = DEFAULT_MAX_RETRIES
    schema_version: str = "1"
    # When True, policy will scan DecisionContext.user_input / proposed_actions
    # for time-sensitive and destructive markers even if the caller did not set
    # the corresponding boolean flags explicitly.
    auto_detect: bool = True
    # New in 0.6.0 (policy-v3, opt-in). When False the engine behaves exactly
    # like policy-v2 even if the host sends v3 fields. This is the gate every
    # adopter has to flip, on purpose, after reading the migration notes.
    policy_v3_enabled: bool = False
    complexity_threshold: float = DEFAULT_COMPLEXITY_THRESHOLD
    uncertainty_threshold: float = DEFAULT_UNCERTAINTY_THRESHOLD
    action_pressure_threshold: int = DEFAULT_ACTION_PRESSURE_THRESHOLD

    def ensure(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "state.db"

    @classmethod
    def from_env(cls) -> "CompassConfig":
        remote = os.environ.get("AGENT_COMPASS_ALLOW_REMOTE", "false").lower() in {"1", "true", "yes"}
        v3 = os.environ.get("AGENT_COMPASS_POLICY_V3", "false").lower() in {"1", "true", "yes"}
        return cls(remote_allowed=remote, policy_v3_enabled=v3)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "CompassConfig":
        """Build a config from env, then optionally overlay a YAML/JSON file.

        Order of precedence (later overrides earlier):
        1. built-in defaults
        2. environment variables
        3. file at ``path`` (if given)
        4. ``AGENT_COMPASS_CONFIG`` (if ``path`` is not given)
        """
        config = cls.from_env()
        target = Path(path) if path else Path(os.environ.get("AGENT_COMPASS_CONFIG", "")) if os.environ.get("AGENT_COMPASS_CONFIG") else None
        if target and str(target):
            config = config._overlay_file(target)
        return config

    def _overlay_file(self, path: Path) -> "CompassConfig":
        if not path.exists():
            raise FileNotFoundError(f"agent-compass config file not found: {path}")
        raw = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        data: dict
        if suffix in {".yaml", ".yml"}:
            data = _parse_simple_yaml(raw)
        else:
            data = json.loads(raw)
        return _apply_overlay(self, data)


def _apply_overlay(config: CompassConfig, data: dict) -> CompassConfig:
    policy = data.get("policy") or {}
    privacy = data.get("privacy") or {}
    storage = data.get("storage") or {}

    new_remote = policy.get("retrieval", {}).get("remote_requires_explicit_or_high_confidence", None)
    if new_remote is False:
        config.remote_allowed = True

    destructive = (policy.get("approval") or {}).get("destructive_actions")
    if isinstance(destructive, list) and destructive:
        config.destructive_actions = {str(item) for item in destructive}

    keywords = (policy.get("retrieval") or {}).get("time_sensitive_keywords")
    if isinstance(keywords, list) and keywords:
        config.time_sensitive_keywords = tuple(str(k) for k in keywords)

    if "max_retries" in policy:
        config.max_retries = int(policy["max_retries"])
    if "ambiguity_threshold" in policy:
        config.ambiguity_threshold = float(policy["ambiguity_threshold"])
    # v3 gate and thresholds (0.6.0+)
    if "policy_v3_enabled" in policy:
        config.policy_v3_enabled = bool(policy["policy_v3_enabled"])
    if "complexity_threshold" in policy:
        config.complexity_threshold = float(policy["complexity_threshold"])
    if "uncertainty_threshold" in policy:
        config.uncertainty_threshold = float(policy["uncertainty_threshold"])
    if "action_pressure_threshold" in policy:
        config.action_pressure_threshold = int(policy["action_pressure_threshold"])

    default_privacy = privacy.get("default_classification")
    if isinstance(default_privacy, str) and default_privacy:
        config.default_privacy = default_privacy

    if "schema_version" in data:
        config.schema_version = str(data["schema_version"])

    if storage.get("backend") and storage["backend"] != "sqlite":
        raise ValueError(f"unsupported storage backend: {storage['backend']!r} (only 'sqlite' is supported)")

    return config


def _parse_simple_yaml(text: str) -> dict:
    """Minimal YAML subset parser: nested mappings, lists, scalars, and comments.

    This deliberately avoids a PyYAML dependency for the offline core. It
    supports the structure used by ``config/*.example.yaml``:

    * ``key: value`` mappings, two-space indented children
    * ``- item`` lists under a key
    * ``# comment`` and blank lines
    * quoted and unquoted scalar values

    For complex YAML features (anchors, multi-doc, flow style) users should
    install PyYAML and we will fall back to it.
    """
    try:
        import yaml  # type: ignore[import-not-found]

        return yaml.safe_load(text) or {}
    except ModuleNotFoundError:
        pass

    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        # pop stack until we find the parent indent
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else root
        if content.startswith("- "):
            value = _coerce_scalar(content[2:].strip())
            if parent == root and not root:
                root = []
                stack = [(-1, root)]  # type: ignore[assignment]
                parent = root  # type: ignore[assignment]
            if isinstance(parent, list):
                parent.append(value)
            else:
                # implicit: convert last dict value to a list
                last_key = next(reversed(list(parent.keys()))) if parent else None
                if last_key is not None and not isinstance(parent[last_key], list):
                    parent[last_key] = [parent[last_key]]
                    stack[-1] = (stack[-1][0], parent)
                if last_key is not None:
                    parent[last_key].append(value)
            continue
        if ":" in content:
            key, _, value = content.partition(":")
            key = key.strip()
            value = value.strip()
            if value == "":
                child: object = {}
                parent[key] = child
                stack.append((indent, child))  # type: ignore[arg-type]
            else:
                parent[key] = _coerce_scalar(value)
    return root


def _coerce_scalar(text: str) -> object:
    if not text:
        return ""
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "~"}:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text
