"""Local privacy classification and conservative redaction."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterable


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


# ---- secrets (always blocked from remote transfer) ----

_SECRET_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    ("api_key", re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{12,}")),
    ("password", re.compile(r"(?i)\bpassword\s*[:=]\s*\S+")),
    ("ssh_key", re.compile(r"ssh-(?:rsa|dss|ed25519|ecdsa)\s+[A-Za-z0-9+/=]{40,}")),
)

# ---- sensitive (redacted before remote transfer) ----

_SENSITIVE_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"(?<!\d)(?:\+?\d[\d -]{8,}\d)(?!\d)")),
    # Mainland China mobile numbers (1[3-9]xxxxxxxxx). Conservative: word
    # boundary on each side, no overlap with longer digit runs.
    ("cn_mobile", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    # IPv4: four dotted octets, each 0-255.
    ("ipv4", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b")),
    # IPv6: keep this conservative (full form, no compressed).
    ("ipv6", re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b")),
    # Credit card-like 13-19 digit groups, separated by spaces or dashes.
    ("credit_card", re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")),
    # Mainland China resident ID (18 digits, last may be X). Conservative word
    # boundaries; do not match arbitrary 18-digit runs.
    ("cn_id", re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])\d{2}\d{3}[\dXx](?!\d)")),
    # Absolute POSIX paths under /home, /Users, /root, /etc, /var, or Windows
    # drive letters.
    ("absolute_path", re.compile(r"(?:/(?:home|Users|root|etc|var)/[^\s\"'<>|]+|[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\/:*?\"<>|\r\n]*)")),
    # ``user@host`` style identifiers in logs.
    ("user_at_host", re.compile(r"\b[a-zA-Z0-9._-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b(?!\.)")),
)


def _luhn_check(digits: str) -> bool:
    total = 0
    reverse = digits[::-1]
    for i, ch in enumerate(reverse):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


@dataclass(frozen=True)
class PrivacyConfig:
    """Pluggable privacy configuration.

    The bundled patterns cover the most common cases. Apps that need stricter
    detection can supply their own ``extra_secret`` and ``extra_sensitive``
    pattern tuples to overlay or replace the defaults.
    """

    extra_secret: tuple[tuple[str, "re.Pattern[str]"], ...] = field(default_factory=tuple)
    extra_sensitive: tuple[tuple[str, "re.Pattern[str]"], ...] = field(default_factory=tuple)

    def secret_patterns(self) -> tuple[tuple[str, "re.Pattern[str]"], ...]:
        return _SECRET_PATTERNS + self.extra_secret

    def sensitive_patterns(self) -> tuple[tuple[str, "re.Pattern[str]"], ...]:
        return _SENSITIVE_PATTERNS + self.extra_sensitive


def _normalize_digits(text: str) -> str:
    return re.sub(r"[ -]", "", text)


def _is_credit_card_match(text: str, match: "re.Match[str]") -> bool:
    digits = _normalize_digits(match.group(0))
    if not (13 <= len(digits) <= 19):
        return False
    # Cheap filter: at least one separator keeps false positives low; then
    # Luhn check rules out the rest.
    raw = match.group(0)
    has_separator = " " in raw or "-" in raw
    return has_separator and _luhn_check(digits)


class PrivacyBoundary:
    def __init__(self, config: PrivacyConfig | None = None):
        self.config = config or PrivacyConfig()
        self._sensitive_with_callbacks = tuple(
            (name, pattern, name == "credit_card")
            for name, pattern in self.config.sensitive_patterns()
        )

    def inspect(self, text: str) -> Inspection:
        matches: list[str] = []
        for name, pattern in self.config.secret_patterns():
            if pattern.search(text):
                matches.append(name)
        if matches:
            return Inspection(PrivacyLevel.SECRET, tuple(matches), True)
        for name, pattern, needs_callback in self._sensitive_with_callbacks:
            if needs_callback:
                if any(_is_credit_card_match(text, m) for m in pattern.finditer(text)):
                    matches.append(name)
            elif pattern.search(text):
                matches.append(name)
        if matches:
            return Inspection(PrivacyLevel.SENSITIVE, tuple(matches), False)
        return Inspection(PrivacyLevel.LOCAL_ONLY, tuple(), False)

    def redact(self, text: str) -> str:
        result = text
        for name, pattern in self.config.secret_patterns():
            result = pattern.sub(f"[REDACTED:{name}]", result)
        for name, pattern, needs_callback in self._sensitive_with_callbacks:
            if needs_callback:
                result = pattern.sub(
                    lambda m: f"[REDACTED:{name}]" if _is_credit_card_match(text, m) else m.group(0),
                    result,
                )
            else:
                result = pattern.sub(f"[REDACTED:{name}]", result)
        return result

    def assert_safe_for_remote(self, text: str) -> str:
        inspection = self.inspect(text)
        if inspection.blocked:
            raise ValueError(f"secret data blocked from remote transfer: {', '.join(inspection.matches)}")
        return self.redact(text) if inspection.level >= PrivacyLevel.SENSITIVE else text
