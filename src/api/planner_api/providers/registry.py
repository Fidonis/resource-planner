"""Finding providers: the entry-point group ``resource_planner.providers``."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from importlib.metadata import EntryPoint, entry_points
from typing import Any

from planner_api.config import Settings
from planner_api.providers.base import KINDS, Provider

GROUP = "resource_planner.providers"
log = logging.getLogger("planner_api.providers")


class ProviderRegistry:
    """Provider classes by ``(kind, key)``. A class that fails to load is logged and left out."""

    def __init__(self, classes: Iterable[type[Any]] = ()) -> None:
        self._classes: dict[tuple[str, str], type[Any]] = {}
        for cls in classes:
            self.add(cls)

    def add(self, cls: type[Any]) -> None:
        kind, key = getattr(cls, "kind", None), getattr(cls, "key", None)
        if kind not in KINDS or not isinstance(key, str) or not key:
            raise ValueError(f"{cls!r} needs a 'kind' of {', '.join(KINDS)} and a 'key'")
        self._classes[(kind, key)] = cls

    def get(self, kind: str, key: str) -> type[Any] | None:
        return self._classes.get((kind, key))

    def create(self, kind: str, key: str, settings: Settings) -> Provider | None:
        cls = self.get(kind, key)
        return cls(settings) if cls is not None else None

    def all(self) -> list[type[Any]]:
        return [self._classes[k] for k in sorted(self._classes)]


def load_entry_points(
    points: Iterable[EntryPoint] | None = None,
    on_error: Callable[[EntryPoint, Exception], None] | None = None,
) -> ProviderRegistry:
    registry = ProviderRegistry()
    for point in points if points is not None else entry_points(group=GROUP):
        try:
            registry.add(point.load())
        except Exception as exc:  # a broken plug-in must not keep the planner from starting
            log.warning("provider %s could not be loaded: %s", point.name, exc)
            if on_error:
                on_error(point, exc)
    return registry


def default_registry() -> ProviderRegistry:
    return load_entry_points()
