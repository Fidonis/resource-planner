"""The mapping layer: efforts per skill, states, teams, groups and absence types."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from planner_api.providers.base import ExternalAbsence, ExternalPerson, ExternalTask
from planner_api.sync.mapping import (
    AbsenceMapping,
    DirectoryMapping,
    TaskMapping,
    UnmappedTypeError,
    map_absence,
    map_efforts,
    map_person,
    map_status,
    map_task,
    wanted,
)

SKILLS = {"backend": "Backend", "frontend": "Frontend", "qa": "QA"}


def known(label: str) -> str | None:
    return SKILLS.get(label.strip().lower())


def task(**fields: object) -> ExternalTask:
    return ExternalTask(external_id="1", key="K-1", title="T", **fields)  # type: ignore[arg-type]


def test_manual_imports_no_effort() -> None:
    assert map_efforts(task(labels=("Backend",)), TaskMapping(), known).efforts is None


def test_an_even_split_shares_the_estimate_and_keeps_the_sum() -> None:
    mapping = TaskMapping(effort_strategy="EVEN_SPLIT", unit_factor=1)
    result = map_efforts(
        task(estimate=Decimal(10), labels=("Backend", "Frontend", "QA")), mapping, known
    )
    assert result.efforts == {
        "Backend": Decimal("3.34"),
        "Frontend": Decimal("3.33"),
        "QA": Decimal("3.33"),
    }
    assert sum(result.efforts.values()) == Decimal(10)


def test_hours_become_person_days_through_the_unit_factor() -> None:
    mapping = TaskMapping(effort_strategy="EVEN_SPLIT", unit_factor=0.125)
    result = map_efforts(task(estimate=Decimal(20), labels=("Backend",)), mapping, known)
    assert result.efforts == {"Backend": Decimal("2.50")}


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("be-3", {"Backend": Decimal("3.00")}),
        ("Backend:2,5", {"Backend": Decimal("2.50")}),
        ("Frontend=1.5", {"Frontend": Decimal("1.50")}),
        ("urgent", {}),  # an ordinary label
    ],
)
def test_labels_can_carry_the_effort(label: str, expected: dict[str, Decimal]) -> None:
    mapping = TaskMapping(effort_strategy="LABEL_ENCODED", label_skills={"be": "Backend"})
    assert map_efforts(task(labels=(label,)), mapping, known).efforts == expected


def test_a_pattern_of_your_own_needs_both_groups() -> None:
    with pytest.raises(ValidationError):
        TaskMapping(effort_strategy="LABEL_ENCODED", label_pattern=r"^(?P<skill>.+)$")
    with pytest.raises(ValidationError):
        TaskMapping(label_pattern="(")
    TaskMapping(label_pattern=r"^(?P<skill>[a-z]+)#(?P<effort>\d+)$")


def test_custom_fields_give_one_effort_per_skill() -> None:
    mapping = TaskMapping(
        effort_strategy="CUSTOM_FIELDS",
        custom_field_skills={"Backend": "Effort Backend", "QA": "effort_qa"},
    )
    result = map_efforts(
        task(custom_fields={"effortbackend": "2", "effortqa": "0", "other": "9"}), mapping, known
    )
    assert result.efforts == {"Backend": Decimal("2.00")}


@pytest.mark.parametrize(
    ("policy", "efforts", "created", "noted"),
    [
        ("IGNORE", {"Backend": Decimal("1.00")}, [], False),
        ("REPORT", {"Backend": Decimal("1.00")}, [], True),
        ("CREATE_SKILL", {"Backend": Decimal("1.00"), "Ops": Decimal("2.00")}, ["Ops"], False),
    ],
)
def test_unknown_labels_follow_the_policy(
    policy: str, efforts: dict[str, Decimal], created: list[str], noted: bool
) -> None:
    mapping = TaskMapping.model_validate(
        {"effortStrategy": "LABEL_ENCODED", "unknownLabels": policy}
    )
    result = map_efforts(task(labels=("Backend:1", "Ops:2")), mapping, known)
    assert result.efforts == efforts and result.create_skills == created
    assert bool(result.notes) is noted


def test_states_have_defaults_and_can_be_overridden() -> None:
    mapping = TaskMapping(status_map={"Ready for QA": "open", "Shipped": "done"})
    assert map_status("In Progress", mapping) == "open"
    assert map_status("CLOSED", mapping) == "done"
    assert map_status("Won't do", mapping) == "cancelled"
    assert map_status("Shipped", mapping) == "done"
    assert map_status("something new", mapping) == "open"
    with pytest.raises(ValidationError):
        TaskMapping(status_map={"a": "finished"})


def test_the_team_hint_picks_the_owner_team_and_the_source_keeps_its_fields() -> None:
    mapping = TaskMapping(team_map={"web": "Team Web"}, effort_strategy="LABEL_ENCODED")
    record = map_task(task(team_hint="WEB", labels=("Backend:1",)), mapping, known)
    assert record.owner_team == "Team Web"
    assert record.owned == ("title", "status", "efforts")
    manual = map_task(task(), TaskMapping(), known)
    assert manual.owned == ("title", "status") and manual.efforts is None


def person(*groups: str, roles: tuple[str, ...] = ()) -> ExternalPerson:
    return ExternalPerson("1", "ada", "Ada", "ada@example.test", groups=groups, roles=roles)


def test_the_first_listed_group_decides_the_team() -> None:
    mapping = DirectoryMapping(group_teams=["Frontend=Team F", "Backend=Team B", "Ops"])
    assert map_person(person("/Backend", "/Frontend"), mapping).team == "Team F"
    assert map_person(person("/Ops"), mapping).team == "Ops"  # the team has the group's name
    assert map_person(person("/Other"), mapping).team is None


def test_a_subgroup_counts_for_its_parents_but_not_for_its_siblings() -> None:
    mapping = DirectoryMapping(group_teams=["/Eng/Backend=Back", "Eng=Engineering"])
    assert map_person(person("/Eng/Backend/API"), mapping).team == "Back"
    assert map_person(person("/Eng/Frontend"), mapping).team == "Engineering"
    only_api = DirectoryMapping(group_teams=["/Eng/Backend/API=API team"])
    assert map_person(person("/Eng/Backend/Web"), only_api).team is None


def test_the_default_team_is_marked_as_such() -> None:
    record = map_person(person("/Nowhere"), DirectoryMapping(default_team="Everyone"))
    assert record.team == "Everyone" and record.team_is_default


def test_who_is_imported_can_be_narrowed_by_group_or_role() -> None:
    assert wanted(person("/Anything"), DirectoryMapping())
    by_group = DirectoryMapping(include_groups=["Backend"])
    assert wanted(person("/Backend/API"), by_group) and not wanted(person("/Frontend"), by_group)
    both = DirectoryMapping(include_groups=["Backend"], include_roles=["rp-planner"])
    assert wanted(person("/Frontend", roles=("rp-planner",)), both)
    assert not wanted(person("/Frontend", roles=("other",)), both)


def test_group_skills_add_skills_for_the_groups_of_a_person() -> None:
    mapping = DirectoryMapping(group_skills=["Backend=Backend", "QA Team=QA"])
    assert map_person(person("/Backend", "/QA Team"), mapping).skills == ("Backend", "QA")


def test_absence_types_are_mapped_and_unmapped_ones_refused() -> None:
    mapping = AbsenceMapping(type_map={"U": "vacation"})
    absence = ExternalAbsence("1", "ada@example.test", date(2030, 1, 8), date(2030, 1, 8), "u")
    record = map_absence(absence, mapping)
    assert record.type_key == "vacation" and record.end_part == "FULL"
    with pytest.raises(UnmappedTypeError):
        map_absence(ExternalAbsence("2", "x", date(2030, 1, 8), date(2030, 1, 8), "Z"), mapping)


def test_settings_with_a_typo_are_refused() -> None:
    with pytest.raises(ValidationError):
        TaskMapping.model_validate({"effortStrategie": "EVEN_SPLIT"})
    assert TaskMapping.model_validate({"effortStrategy": "EVEN_SPLIT"}).effort_strategy == (
        "EVEN_SPLIT"
    )
