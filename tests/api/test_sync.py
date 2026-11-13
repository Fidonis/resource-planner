"""Integrations and the sync engine with the file providers (§9, acceptance 10 and 11)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from tests.api.support import Api
from tests.api.sync_support import INTEGRATIONS, counts, integration, sync, write
from tests.api.world import ASSIGNMENTS, AUDIT, TASKS, audit_count, member, skill, team, week

from planner_api.models import AuditLog, IntegrationConfig, SyncRun

TASK_FILE = (
    "Key;Title;Status;Labels;Estimate;Url\r\n"
    "SHOP-1;Checkout;In Progress;Backend:3|Frontend:2;;https://tracker.test/SHOP-1\r\n"
    "SHOP-2;Search;Done;Backend:1;;https://tracker.test/SHOP-2\r\n"
    "SHOP-3;Invoices;Open;Backend:2,5;;\r\n"
)
LABELS = {"effortStrategy": "LABEL_ENCODED"}


async def seeded(api: Api) -> Any:
    admin = await api.admin()
    await skill(admin, "Backend")
    await skill(admin, "Frontend")
    return admin


async def tasks_by_key(admin: Any) -> dict[str, dict[str, Any]]:
    response = await admin.get(TASKS, params={"pageSize": 200})
    assert response.status_code == 200, response.text
    return {t["key"]: t for t in response.json()["items"]}


# --- providers and settings --------------------------------------------------------------


async def test_providers_come_from_the_entry_point_group(sync_api: Api) -> None:
    admin = await sync_api.admin()
    response = await admin.get(f"{INTEGRATIONS}/providers")
    assert response.status_code == 200, response.text
    providers = response.json()["items"]
    found = {(p["type"], p["key"]) for p in providers}
    assert {
        ("task_source", "csv"),
        ("absence_source", "csv"),
        ("directory", "csv"),
        ("directory", "keycloak"),
        ("task_source", "fake"),  # registered from outside the package
    } <= found
    keycloak = next(p for p in providers if p["key"] == "keycloak")
    assert keycloak["configSchema"]["properties"]["clientSecret"]["writeOnly"] is True
    fake = next(p for p in providers if p["key"] == "fake")
    assert "effortStrategy" in fake["mappingSchema"]["properties"]


async def test_settings_are_checked_against_the_schema_of_the_provider(sync_api: Api) -> None:
    admin = await sync_api.admin()
    base = {"name": "X", "type": "task_source", "providerKey": "csv"}
    missing = await admin.post(INTEGRATIONS, {**base, "config": {}})
    assert missing.status_code == 422
    assert missing.json()["errors"][0]["field"] == "config.path"
    unknown = await admin.post(INTEGRATIONS, {**base, "providerKey": "nope", "config": {}})
    assert unknown.status_code == 422 and unknown.json()["code"] == "PROVIDER_UNKNOWN"
    cron = await admin.post(INTEGRATIONS, {**base, "config": {"path": "a.csv"}, "schedule": "soon"})
    assert cron.status_code == 422
    assert cron.json()["errors"][0]["field"] == "schedule"
    mapping = await admin.post(
        INTEGRATIONS,
        {**base, "config": {"path": "a.csv"}, "mapping": {"effortStrategy": "GUESS"}},
    )
    assert mapping.status_code == 422
    assert mapping.json()["errors"][0]["field"].startswith("mapping.")


async def test_secrets_are_stored_encrypted_and_never_returned(sync_api: Api) -> None:
    admin = await sync_api.admin()
    config = {
        "baseUrl": "http://keycloak.test:8080",
        "realm": "planner",
        "clientId": "planner-sync",
        "clientSecret": "super-secret-value",
    }
    body = {"name": "Keycloak", "type": "directory", "providerKey": "keycloak", "config": config}
    created = await admin.create(INTEGRATIONS, body)
    assert created["secretsSet"] == ["clientSecret"]
    assert "clientSecret" not in created["config"]
    assert "super-secret-value" not in (await admin.get(f"{INTEGRATIONS}/{created['id']}")).text
    assert "super-secret-value" not in (await admin.get(INTEGRATIONS)).text

    async with sync_api.factory() as session:
        stored = (await session.scalars(select(IntegrationConfig))).one().config["provider"]
        assert stored["clientSecret"].startswith("enc:v1:")
        assert "super-secret-value" not in str(stored)
        audit = await session.scalars(select(AuditLog).where(AuditLog.entity_type == "integration"))
        assert "super-secret-value" not in str([a.after for a in audit.all()])
        before = stored["clientSecret"]

    # leaving the secret out keeps it, and the other settings still change
    kept = await admin.put(
        f"{INTEGRATIONS}/{created['id']}",
        {
            "version": created["version"],
            "name": "Keycloak",
            "config": {**config, "clientSecret": "", "realm": "other"},
        },
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["config"]["realm"] == "other"
    assert kept.json()["secretsSet"] == ["clientSecret"]
    async with sync_api.factory() as session:
        row = (await session.scalars(select(IntegrationConfig))).one()
        assert (
            row.config["provider"]["clientSecret"] != ""
            and row.config["provider"]["realm"] == "other"
        )
    assert before.startswith("enc:v1:")


async def test_only_administrators_of_integrations_may_use_them(sync_api: Api) -> None:
    planner = await sync_api.as_role("planner")
    assert (await planner.get(INTEGRATIONS)).status_code == 403
    assert (await planner.get(f"{INTEGRATIONS}/providers")).status_code == 403
    assert (await planner.post(f"{INTEGRATIONS}/{uuid.uuid4()}/sync")).status_code == 403


# --- task sync (acceptance 10 and 11) ----------------------------------------------------


async def test_a_sync_run_twice_changes_nothing_the_second_time(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await seeded(sync_api)
    path = write(imports_dir, "tasks.csv", TASK_FILE)
    made = await integration(admin, "task_source", path, mapping=LABELS)

    first = await sync(admin, made["id"])
    assert counts(first) == (3, 0, 0, 0) and first["status"] == "success"
    second = await sync(admin, made["id"])
    assert counts(second) == (0, 0, 3, 0)

    found = await tasks_by_key(admin)
    checkout = found["SHOP-1"]
    assert checkout["status"] == "open" and found["SHOP-2"]["status"] == "done"
    assert {e["skillKey"]: e["personDays"] for e in checkout["efforts"]} == {
        "Backend": 3,
        "Frontend": 2,
    }
    assert found["SHOP-3"]["efforts"][0]["personDays"] == 2.5
    assert checkout["externalSource"] == made["id"]
    assert checkout["externalUrl"] == "https://tracker.test/SHOP-1"
    assert set(checkout["managedFields"]) == {"key", "title", "status", "efforts"}
    refreshed = (await admin.get(f"{INTEGRATIONS}/{made['id']}")).json()
    assert refreshed["lastSyncStatus"] == "success" and refreshed["lastSyncAt"]


async def test_imported_fields_are_locked_until_an_administrator_unlocks_them(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await seeded(sync_api)
    path = write(imports_dir, "tasks.csv", TASK_FILE)
    made = await integration(admin, "task_source", path, mapping=LABELS)
    await sync(admin, made["id"])
    shop1 = (await tasks_by_key(admin))["SHOP-1"]

    body = {
        "version": shop1["version"],
        "key": shop1["key"],
        "title": "Changed here",
        "status": shop1["status"],
        "lockedFields": shop1["lockedFields"],
    }
    locked = await admin.put(f"{TASKS}/{shop1['id']}", body)
    assert locked.status_code == 409 and locked.json()["code"] == "FIELD_LOCKED"

    # unlocking is a step of its own; the field can be changed afterwards
    unlocked = await admin.put(
        f"{TASKS}/{shop1['id']}",
        {**body, "title": shop1["title"], "lockedFields": ["status", "efforts"]},
    )
    assert unlocked.status_code == 200, unlocked.text
    changed = await admin.put(
        f"{TASKS}/{shop1['id']}",
        {
            "version": unlocked.json()["version"],
            "key": shop1["key"],
            "title": "Changed here",
            "status": shop1["status"],
        },
    )
    assert changed.status_code == 200, changed.text
    write(imports_dir, "tasks.csv", TASK_FILE.replace("Checkout", "Checkout v2"))
    run = await sync(admin, made["id"])
    assert counts(run)[3] == 0
    after = (await tasks_by_key(admin))["SHOP-1"]
    assert after["title"] == "Changed here"  # the unlocked field is left alone


async def test_the_strategies_turn_estimates_labels_and_fields_into_efforts(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await seeded(sync_api)
    even = write(
        imports_dir,
        "even.csv",
        "Key;Title;Labels;Estimate;Unit\nE-1;Split;Backend|Frontend|ui-polish;8;h\n",
    )
    made = await integration(
        admin,
        "task_source",
        even,
        name="Even",
        mapping={
            "effortStrategy": "EVEN_SPLIT",
            "unitFactor": 0.125,
            "unknownLabels": "REPORT",
        },
    )
    run = await sync(admin, made["id"])
    efforts = (await tasks_by_key(admin))["E-1"]["efforts"]
    assert {e["skillKey"]: e["personDays"] for e in efforts} == {"Backend": 0.5, "Frontend": 0.5}
    detail = (await admin.get(f"{INTEGRATIONS}/{made['id']}/runs/{run['id']}")).json()
    assert any("ui-polish" in entry["message"] for entry in detail["log"])

    fields = write(
        imports_dir,
        "fields.csv",
        "Key;Title;Effort Backend;Effort Frontend\nC-1;Fields;3;\nC-2;More;1,5;2\n",
    )
    made = await integration(
        admin,
        "task_source",
        fields,
        name="Fields",
        mapping={
            "effortStrategy": "CUSTOM_FIELDS",
            "customFieldSkills": {"Backend": "Effort Backend", "Frontend": "Effort Frontend"},
        },
    )
    await sync(admin, made["id"])
    found = await tasks_by_key(admin)
    assert {e["skillKey"]: e["personDays"] for e in found["C-2"]["efforts"]} == {
        "Backend": 1.5,
        "Frontend": 2,
    }
    assert len(found["C-1"]["efforts"]) == 1

    manual = write(imports_dir, "manual.csv", "Key;Title\nM-1;No effort\n")
    made = await integration(admin, "task_source", manual, name="Manual")
    await sync(admin, made["id"])
    only_basic = (await tasks_by_key(admin))["M-1"]
    assert only_basic["efforts"] == [] and "efforts" not in only_basic["lockedFields"]


async def test_unknown_labels_follow_the_policy_of_the_mapping(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await seeded(sync_api)
    path = write(imports_dir, "t.csv", "Key;Title;Labels\nU-1;Unknown;Ops:2|Backend:1\n")
    made = await integration(
        admin,
        "task_source",
        path,
        mapping={"effortStrategy": "LABEL_ENCODED", "unknownLabels": "CREATE_SKILL"},
    )
    await sync(admin, made["id"])
    efforts = (await tasks_by_key(admin))["U-1"]["efforts"]
    assert {e["skillKey"]: e["personDays"] for e in efforts} == {"Ops": 2, "Backend": 1}
    keys = {s["key"] for s in (await admin.get("/api/v1/skills")).json()["items"]}
    assert "Ops" in keys


async def test_a_task_that_left_the_source_is_cancelled_with_its_bookings_kept(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await seeded(sync_api)
    path = write(imports_dir, "tasks.csv", TASK_FILE)
    made = await integration(admin, "task_source", path, mapping=LABELS)
    await sync(admin, made["id"])
    t = await team(admin)
    skills = {s["key"]: s["id"] for s in (await admin.get("/api/v1/skills")).json()["items"]}
    ada = await member(admin, t["id"], skills=(skills["Backend"],))
    shop3 = (await tasks_by_key(admin))["SHOP-3"]
    booking = await admin.post(
        ASSIGNMENTS,
        {
            "taskId": shop3["id"],
            "skillId": skills["Backend"],
            "memberId": ada["id"],
            "weekStart": week(),
            "personDays": 2,
        },
    )
    assert booking.status_code == 201, booking.text

    write(imports_dir, "tasks.csv", TASK_FILE.split("SHOP-3")[0].rstrip("\r\n") + "\r\n")
    run = await sync(admin, made["id"])
    assert counts(run)[:2] == (0, 1)
    gone = (await tasks_by_key(admin))["SHOP-3"]
    assert gone["status"] == "cancelled" and gone["totalAssigned"] == 2

    # an empty source never closes everything
    write(imports_dir, "tasks.csv", "Key;Title\n")
    again = await sync(admin, made["id"])
    assert counts(again)[:2] == (0, 0)
    detail = (await admin.get(f"{INTEGRATIONS}/{made['id']}/runs/{again['id']}")).json()
    assert any("nothing was closed" in e["message"] for e in detail["log"])
    assert (await tasks_by_key(admin))["SHOP-1"]["status"] == "open"


async def test_an_effort_below_what_is_booked_is_reported_and_the_booking_stays(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await seeded(sync_api)
    path = write(imports_dir, "tasks.csv", "Key;Title;Labels\nB-1;Booked;Backend:4\n")
    made = await integration(admin, "task_source", path, mapping=LABELS)
    await sync(admin, made["id"])
    t = await team(admin)
    backend = next(
        s["id"]
        for s in (await admin.get("/api/v1/skills")).json()["items"]
        if s["key"] == "Backend"
    )
    ada = await member(admin, t["id"], skills=(backend,))
    b1 = (await tasks_by_key(admin))["B-1"]
    assert (
        await admin.post(
            ASSIGNMENTS,
            {
                "taskId": b1["id"],
                "skillId": backend,
                "memberId": ada["id"],
                "weekStart": week(),
                "personDays": 3,
            },
        )
    ).status_code == 201

    write(imports_dir, "tasks.csv", "Key;Title;Labels\nB-1;Booked;Backend:1\n")
    run = await sync(admin, made["id"])
    assert run["status"] == "partial" and run["errors"] == 1
    after = (await tasks_by_key(admin))["B-1"]
    assert after["efforts"][0]["personDays"] == 4 and after["totalAssigned"] == 3


async def test_a_bad_row_is_reported_and_the_others_still_go_in(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await seeded(sync_api)
    path = write(
        imports_dir,
        "tasks.csv",
        "Key;Title;Estimate\nOK-1;Fine;2\nBAD-1;Broken;lots\n;No key;1\nOK-2;Also fine;1\n",
    )
    made = await integration(admin, "task_source", path)
    run = await sync(admin, made["id"])
    assert run["status"] == "partial" and counts(run) == (2, 0, 0, 2)
    assert set(await tasks_by_key(admin)) == {"OK-1", "OK-2"}
    detail = (await admin.get(f"{INTEGRATIONS}/{made['id']}/runs/{run['id']}")).json()
    assert {e["level"] for e in detail["log"]} >= {"error"}


async def test_a_file_outside_the_import_directory_is_refused(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await seeded(sync_api)
    for path in ("../secrets.csv", "/etc/passwd", "missing.csv"):
        made = await integration(admin, "task_source", path, name=f"Bad {path}")
        run = await sync(admin, made["id"])
        assert run["status"] == "failed", path
        tested = (await admin.post(f"{INTEGRATIONS}/{made['id']}/test")).json()
        assert tested["ok"] is False, path


async def test_a_dry_run_changes_nothing_and_leaves_no_audit_entry(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await seeded(sync_api)
    path = write(imports_dir, "tasks.csv", TASK_FILE)
    made = await integration(admin, "task_source", path, mapping=LABELS)
    before = await audit_count(admin)
    run = await sync(admin, made["id"], dryRun="true")
    assert run["dryRun"] is True and counts(run) == (3, 0, 0, 0)
    assert await tasks_by_key(admin) == {}
    assert await audit_count(admin) == before
    listed = (await admin.get(f"{INTEGRATIONS}/{made['id']}/runs")).json()
    assert listed["total"] == 1 and listed["items"][0]["dryRun"] is True
    assert (await admin.get(f"{INTEGRATIONS}/{made['id']}")).json()["lastSyncAt"] is None
    real = await sync(admin, made["id"])
    assert counts(real) == (3, 0, 0, 0)


async def test_a_second_run_is_refused_while_one_is_going_on(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await seeded(sync_api)
    path = write(imports_dir, "tasks.csv", TASK_FILE)
    made = await integration(admin, "task_source", path, mapping=LABELS)
    key = int.from_bytes(uuid.UUID(made["id"]).bytes[:8], "big", signed=True)
    async with sync_api.app.state.sync_engine.engine.connect() as holder:
        await holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
        refused = await sync(admin, made["id"], expect=409)
        assert refused["code"] == "SYNC_RUNNING"
        await holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        await holder.commit()
    assert (await sync(admin, made["id"]))["status"] == "success"


async def test_a_run_that_a_stop_left_behind_is_marked_aborted(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await seeded(sync_api)
    path = write(imports_dir, "tasks.csv", TASK_FILE)
    made = await integration(admin, "task_source", path)
    async with sync_api.factory() as session:
        run = SyncRun(integration_id=uuid.UUID(made["id"]), status="running")
        session.add(run)
        await session.commit()
        run_id = run.id
    assert await sync_api.app.state.sync_engine.abort_stale_runs() == 1
    detail = (await admin.get(f"{INTEGRATIONS}/{made['id']}/runs/{run_id}")).json()
    assert detail["status"] == "aborted" and detail["finishedAt"]


async def test_a_provider_from_outside_the_package_runs_like_any_other(sync_api: Api) -> None:
    admin = await seeded(sync_api)
    made = await admin.create(
        INTEGRATIONS,
        {
            "name": "Fake",
            "type": "task_source",
            "providerKey": "fake",
            "config": {"count": 2},
            "mapping": {"effortStrategy": "EVEN_SPLIT"},
        },
    )
    tested = (await admin.post(f"{INTEGRATIONS}/{made['id']}/test")).json()
    assert tested["ok"] is True
    run = await sync(admin, made["id"])
    assert counts(run) == (2, 0, 0, 0)
    assert (await tasks_by_key(admin))["FAKE-1"]["efforts"][0]["personDays"] == 4


async def test_an_integration_with_data_is_deleted_only_when_detached(
    sync_api: Api, imports_dir: Path
) -> None:
    admin = await seeded(sync_api)
    path = write(imports_dir, "tasks.csv", TASK_FILE)
    made = await integration(admin, "task_source", path, mapping=LABELS)
    await sync(admin, made["id"])
    blocked = await admin.delete(
        f"{INTEGRATIONS}/{made['id']}", params={"version": made["version"]}
    )
    assert blocked.status_code == 409 and blocked.json()["code"] == "DELETE_BLOCKED"
    gone = await admin.delete(
        f"{INTEGRATIONS}/{made['id']}", params={"version": made["version"], "detach": "true"}
    )
    assert gone.status_code == 204
    kept = (await tasks_by_key(admin))["SHOP-1"]
    assert kept["externalSource"] is None and kept["lockedFields"] == []
    editable = await admin.put(
        f"{TASKS}/{kept['id']}",
        {"version": kept["version"], "key": "SHOP-1", "title": "Mine now", "status": "open"},
    )
    assert editable.status_code == 200, editable.text


async def test_the_audit_log_names_the_sync_as_the_actor(sync_api: Api, imports_dir: Path) -> None:
    admin = await seeded(sync_api)
    path = write(imports_dir, "tasks.csv", TASK_FILE)
    made = await integration(admin, "task_source", path, name="Tracker", mapping=LABELS)
    await sync(admin, made["id"])
    entries = (await admin.get(AUDIT, params={"entityType": "task", "pageSize": 50})).json()[
        "items"
    ]
    assert entries and all(e["via"] == "system" for e in entries)
    assert all(e["actorName"] == "sync:Tracker" for e in entries)
