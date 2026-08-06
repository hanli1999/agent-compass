"""Configuration with safe, environment-based paths."""
from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class CompassConfig:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("AGENT_COMPASS_DATA_DIR", Path.home() / ".agent-compass")))
    remote_allowed: bool = False
    default_privacy: str = "local_only"
    destructive_actions: set[str] = field(default_factory=lambda: {"delete", "publish", "send_external_message", "modify_production"})
    time_sensitive_keywords: tuple[str, ...] = ("latest", "current", "today", "now", "最新", "当前", "今天", "现在", "价格", "版本")

    def ensure(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "state.db"

    @classmethod
    def from_env(cls) -> "CompassConfig":
        remote = os.environ.get("AGENT_COMPASS_ALLOW_REMOTE", "false").lower() in {"1", "true", "yes"}
        return cls(remote_allowed=remote)
