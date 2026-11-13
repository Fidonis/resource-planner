"""Providers: the modules that bring data from other systems into the planner (§9).

Each provider is a class registered in the entry-point group ``resource_planner.providers``. The
core knows only the protocols in :mod:`planner_api.providers.base`.
"""

from planner_api.providers.base import (
    KIND_ABSENCE_SOURCE,
    KIND_DIRECTORY,
    KIND_TASK_SOURCE,
    KINDS,
    AbsenceSourceProvider,
    DirectoryProvider,
    ExternalAbsence,
    ExternalGroup,
    ExternalPerson,
    ExternalTask,
    Provider,
    ProviderError,
    SourceProblem,
    TaskSourceProvider,
    TestResult,
)
from planner_api.providers.registry import ProviderRegistry, default_registry

__all__ = [
    "KINDS",
    "KIND_ABSENCE_SOURCE",
    "KIND_DIRECTORY",
    "KIND_TASK_SOURCE",
    "AbsenceSourceProvider",
    "DirectoryProvider",
    "ExternalAbsence",
    "ExternalGroup",
    "ExternalPerson",
    "ExternalTask",
    "Provider",
    "ProviderError",
    "ProviderRegistry",
    "SourceProblem",
    "TaskSourceProvider",
    "TestResult",
    "default_registry",
]
