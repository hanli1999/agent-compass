"""Local privacy classification and conservative redaction."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum


class PrivacyLevel(IntEnum):
    PUBLIC = 0
    LOCAL_ONLY = 1
    SENSITIVE = 2
    SECRET = 3


@dataclass(frozen=True)
class Inspection:
    level: PrivacyLevel
    matches: tuple[str, ...]
    blocked: bool

_SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    ("api_key", re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{12,}")),
    ("password", re.compile(r"(?i)\bpassword\s*[:=]\s*\S+")),
)
_SENSITIVE_PATTERNS = (
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"(?<!\d)(?:\+?\d[\d -]{8,}\d)(?!\d)")),
)


class PrivacyBoundary:
    def inspect(self, text: str) -> Inspection:
        matches: list[str] = []
        for name, pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                matches.append(name)
        if matches:
            return Inspection(PrivacyLevel.SECRET, tuple(matches), True)
        for name, pattern in _SENSITIVE_PATTERNS:
            if pattern.search(text):
                matches.append(name)
        if matches:
            return Inspection(PrivacyLevel.SENSITIVE, tuple(matches), False)
        return Inspection(PrivacyLevel.LOCAL_ONLY, tuple(), False)

    def redact(self, text: str) -> str:
        result = text
        for name, pattern in _SECRET_PATTERNS + _SENSITIVE_PATTERNS:
            result = pattern.sub(f"[REDACTED:{name}]", result)
        return result

    def assert_safe_for_remote(self, text: str) -> str:
        inspection = self.inspect(text)
        if inspection.blocked:
            raise ValueError(f"secret data blocked from remote transfer: {', '.join(inspection.matches)}")
        return self.redact(text) if inspection.level >= PrivacyLevel.SENSITIVE else text
