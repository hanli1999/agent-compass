"""Sane defaults so a fresh ``Compass(...)`` is already useful.

A host that constructs ``Compass(CompassConfig())`` gets a v2
policy, no web adapter, and a default privacy level. None of
that is wrong — the v2 policy is the right choice for a host
that already knows its job — but it means the v3 action-bias
branches never fire, and a "go to the web" decision has
nowhere to land.

:func:`apply_smart_defaults` flips the bits a typical host wants
without removing the opt-out. A host that explicitly sets
``policy_v3_enabled=False`` is left alone; a host that explicitly
*disables* the DuckDuckGo adapter via ``remote_allowed=False``
does not get one wired in.
"""
from __future__ import annotations

from typing import Any

from .. import Compass
from ..config import CompassConfig
from ..adapters import DuckDuckGoAdapter


#: Default thresholds used by :func:`apply_smart_defaults` if the
#: config does not already have non-default values. These match the
#: values used in the golden tests; changing them here is a
#: contract change and deserves a CHANGELOG note.
DEFAULT_COMPLEXITY = 0.6
DEFAULT_UNCERTAINTY = 0.5
DEFAULT_PRESSURE = 3


def apply_smart_defaults(compass: Compass, *, force: bool = False) -> dict[str, Any]:
    """Idempotently turn on the v3 / web adapter defaults.

    Parameters
    ----------
    compass:
        The Compass instance to mutate. The function touches
        ``compass.config`` and ``compass.retrieval.retrievers``;
        it does not touch any user data.
    force:
        When True, override any explicit user setting (e.g. flip
        v3 on even if the user set it to False). Default False:
        the function respects an explicit opt-out.

    Returns
    -------
    dict
        A summary of the changes that were made, useful for the
        operator's ``doctor`` output. An empty dict means the
        function was a no-op (i.e. already in the smart-default
        state).
    """
    config = compass.config
    changes: dict[str, Any] = {}

    if (not config.policy_v3_enabled) or force:
        config.policy_v3_enabled = True
        changes["policy_v3_enabled"] = True

    if not _is_default_threshold(config.complexity_threshold, DEFAULT_COMPLEXITY) or force:
        config.complexity_threshold = DEFAULT_COMPLEXITY
        if not _is_default_threshold(config.complexity_threshold, DEFAULT_COMPLEXITY):
            changes["complexity_threshold"] = DEFAULT_COMPLEXITY
    if not _is_default_threshold(config.uncertainty_threshold, DEFAULT_UNCERTAINTY) or force:
        config.uncertainty_threshold = DEFAULT_UNCERTAINTY
        if not _is_default_threshold(config.uncertainty_threshold, DEFAULT_UNCERTAINTY):
            changes["uncertainty_threshold"] = DEFAULT_UNCERTAINTY
    if config.action_pressure_threshold != DEFAULT_PRESSURE or force:
        config.action_pressure_threshold = DEFAULT_PRESSURE
        if config.action_pressure_threshold != DEFAULT_PRESSURE:
            changes["action_pressure_threshold"] = DEFAULT_PRESSURE

    if config.remote_allowed and not _has_web_retriever(compass):
        adapter = DuckDuckGoAdapter(config=config)
        compass.retrieval.retrievers.append(adapter)
        changes["web_retriever_added"] = adapter.name

    return changes


def _is_default_threshold(current: float, default: float) -> bool:
    return abs(current - default) < 1e-9


def _has_web_retriever(compass: Compass) -> bool:
    for retriever in compass.retrieval.retrievers:
        name = getattr(retriever, "name", "")
        if name and ("web_search" in name or "web_fetch" in name):
            return True
    return False


def build_smart_default_config(
    base: CompassConfig | None = None,
    *,
    remote_allowed: bool | None = None,
    data_dir: "str | Path | None" = None,
) -> CompassConfig:
    """Return a fresh :class:`CompassConfig` with the smart defaults on.

    Useful for a host that wants to construct its config in one
    call rather than mutating one after the fact:

    .. code-block:: python

        from agent_compass import Compass
        from agent_compass.runtime import build_smart_default_config

        config = build_smart_default_config(remote_allowed=True)
        compass = Compass(config)
    """
    from pathlib import Path as _Path
    config = base or CompassConfig()
    if remote_allowed is not None:
        config.remote_allowed = bool(remote_allowed)
    if data_dir is not None:
        config.data_dir = _Path(data_dir)
    if not config.policy_v3_enabled:
        config.policy_v3_enabled = True
    if not _is_default_threshold(config.complexity_threshold, 0.6):
        config.complexity_threshold = DEFAULT_COMPLEXITY
    if not _is_default_threshold(config.uncertainty_threshold, 0.5):
        config.uncertainty_threshold = DEFAULT_UNCERTAINTY
    if config.action_pressure_threshold != 3:
        config.action_pressure_threshold = DEFAULT_PRESSURE
    return config


__all__ = ["apply_smart_defaults", "build_smart_default_config"]
