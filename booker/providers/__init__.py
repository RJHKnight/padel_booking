"""
Provider registry.

Maps a provider name (e.g. "flow") to its implementation. A future frontend/DB
stores which provider a venue uses; the orchestrator looks it up here. Adding a
new platform is: write the adapter, register it below.
"""

from __future__ import annotations

from .base import BookingProvider
from .flow import FlowProvider

_REGISTRY: dict[str, type[BookingProvider]] = {
    FlowProvider.name: FlowProvider,
    # Future: BRSGolfProvider.name: BRSGolfProvider,
}


def get_provider(name: str) -> BookingProvider:
    """Instantiate a provider by name. Raises KeyError if unknown."""
    try:
        return _REGISTRY[name]()
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"Unknown provider {name!r}. Known providers: {known}")


def available_providers() -> list[str]:
    return sorted(_REGISTRY)
