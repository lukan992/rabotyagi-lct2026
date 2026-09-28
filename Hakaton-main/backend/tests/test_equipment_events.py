"""Equipment lifecycle ingest persists only interval boundaries and exposes scoped journal rows."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.db import SessionLocal, utcnow
from app.services.equipment_visits import mark_stale_visits
from tests.conftest import login_as

pytestmark = pytest.mark.anyio


async def _post(http, payload):  # noqa: ANN001, ANN202
    return await http.post("/api/ingest/equipment-events", json=payload, headers={"X-Api-Key": "ingest-test-key"})


def _payload(at, event_type="FIRST_SEEN", **changes):  # noqa: ANN001
    body = {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "camera_id": "c1",
        "tracker_session_id": "a1111111-1111-4111-8111-111111111111",
        "track_id": "7",
        "equipment_type": "excavator",
        "observed_at": at.isoformat(),
        "first_seen_at": at.isoformat(),
        "last_seen_at": at.isoformat(),
        "confidence": 0.81,
    }
    body.update(changes)
    return body


async def test_lifecycle_stores_boundaries_not_present_and_journal_is_scoped(client):
    base = utcnow() - timedelta(seconds=30)
    first = _payload(base, "FIRST_SEEN")
    assert (await _post(client, first)).status_code == 202
    present = _payload(
        base + timedelta(seconds=7),
        "PRESENT",
        first_seen_at=base.isoformat(),
        last_seen_at=(base + timedelta(seconds=7)).isoformat(),
    )
    assert (await _post(client, present)).json()["duplicate"] is False
    last = _payload(
        base + timedelta(seconds=8),
        "LAST_SEEN",
        first_seen_at=base.isoformat(),
        last_seen_at=(base + timedelta(seconds=7)).isoformat(),
    )
    assert (await _post(client, last)).status_code == 202

    admin = await login_as(client, "admin")
    events = (await client.get("/api/sites/s1/equipment-events", headers=admin)).json()
    assert [row["eventType"] for row in events["items"]] == ["disappeared", "appeared"]
    visits = (await client.get("/api/sites/s1/equipment-visits", headers=admin)).json()
    assert visits["total"] == 1
    row = visits["items"][0]
    assert row["status"] == "completed" and row["durationSeconds"] == 7
    assert row["equipmentClass"] == "excavator"
    assert (await client.get("/api/sites/s2/equipment-events", headers=admin)).json()["total"] == 0


async def test_boundary_event_id_is_idempotent_and_payload_conflict_is_rejected(client):
    payload = _payload(utcnow() - timedelta(seconds=10))
    first = await _post(client, payload)
    assert first.status_code == 202 and first.json()["duplicate"] is False
    normalized = dict(payload)
    for field in ("observed_at", "first_seen_at", "last_seen_at"):
        normalized[field] = datetime.fromisoformat(payload[field]).astimezone(timezone(timedelta(hours=3))).isoformat()
    normalized_repeat = await _post(client, normalized)
    assert normalized_repeat.status_code == 202 and normalized_repeat.json()["duplicate"] is True
    changed = dict(payload, track_id="different")
    assert (await _post(client, changed)).status_code == 409


async def test_last_before_first_creates_completed_visit_and_late_first_only_adds_appearance(client):
    base = utcnow() - timedelta(seconds=20)
    last = _payload(
        base + timedelta(seconds=5),
        "LAST_SEEN",
        first_seen_at=base.isoformat(),
        last_seen_at=(base + timedelta(seconds=4)).isoformat(),
    )
    assert (await _post(client, last)).status_code == 202
    first = _payload(
        base + timedelta(seconds=5),
        "FIRST_SEEN",
        tracker_session_id=last["tracker_session_id"],
        track_id=last["track_id"],
        first_seen_at=base.isoformat(),
        last_seen_at=(base + timedelta(seconds=1)).isoformat(),
    )
    assert (await _post(client, first)).json()["duplicate"] is False
    admin = await login_as(client, "admin")
    assert [x["eventType"] for x in (await client.get("/api/sites/s1/equipment-events", headers=admin)).json()["items"]] == [
        "disappeared",
        "appeared",
    ]
    assert (await client.get("/api/sites/s1/equipment-visits", headers=admin)).json()["items"][0]["status"] == "completed"


async def test_timeout_marks_active_visit_lost_without_disappearance(client):
    at = utcnow() - timedelta(seconds=200)
    assert (await _post(client, _payload(at))).status_code == 202
    async with SessionLocal() as session:
        assert await mark_stale_visits(session, utcnow()) == 1
    admin = await login_as(client, "admin")
    visit = (await client.get("/api/sites/s1/equipment-visits", headers=admin)).json()["items"][0]
    assert visit["status"] == "lost" and visit["closeReason"] == "observation_timeout"
    assert visit["hasObservationGap"] is True
    assert (await client.get("/api/sites/s1/equipment-events", headers=admin)).json()["total"] == 1





async def test_journal_respects_site_access_and_filters(client):
    at = utcnow() - timedelta(seconds=10)
    assert (await _post(client, _payload(at))).status_code == 202
    foreman = await login_as(client, "foreman")
    assert (await client.get("/api/sites/s1/equipment-visits?equipmentClass=excavator", headers=foreman)).status_code == 200
    assert (await client.get("/api/sites/s2/equipment-visits", headers=foreman)).status_code == 404
    admin = await login_as(client, "admin")
    assert (await client.get("/api/sites/s1/equipment-visits?cameraId=c4", headers=admin)).status_code == 422

async def test_timeout_can_resume_only_on_fresh_present_and_interrupt_replaces_its_inference(client):
    old = utcnow() - timedelta(seconds=200)
    first = _payload(old)
    assert (await _post(client, first)).status_code == 202
    async with SessionLocal() as session:
        assert await mark_stale_visits(session, utcnow()) == 1

    # A stale outbox heartbeat does not make the timeout loss look healthy again.
    stale = _payload(
        old + timedelta(seconds=1),
        "PRESENT",
        tracker_session_id=first["tracker_session_id"],
        first_seen_at=old.isoformat(),
        last_seen_at=(old + timedelta(seconds=1)).isoformat(),
    )
    assert (await _post(client, stale)).json()["duplicate"] is True
    fresh_at = utcnow() - timedelta(seconds=1)
    fresh = _payload(
        fresh_at,
        "PRESENT",
        tracker_session_id=first["tracker_session_id"],
        first_seen_at=old.isoformat(),
        last_seen_at=fresh_at.isoformat(),
    )
    assert (await _post(client, fresh)).json()["duplicate"] is False
    interrupt = _payload(
        utcnow() - timedelta(milliseconds=10),
        "INTERRUPTED",
        tracker_session_id=first["tracker_session_id"],
        first_seen_at=old.isoformat(),
        last_seen_at=fresh_at.isoformat(),
        reason="camera_offline",
    )
    assert (await _post(client, interrupt)).status_code == 202
    admin = await login_as(client, "admin")
    visit = (await client.get("/api/sites/s1/equipment-visits", headers=admin)).json()["items"][0]
    assert visit["status"] == "lost" and visit["closeReason"] == "camera_offline"
    assert visit["hasObservationGap"] is True


async def test_concurrent_same_boundary_does_not_duplicate_rows(client):
    import asyncio

    payload = _payload(utcnow() - timedelta(seconds=10))
    left, right = await asyncio.gather(_post(client, payload), _post(client, payload))
    assert sorted([left.status_code, right.status_code]) == [202, 202]
    admin = await login_as(client, "admin")
    assert (await client.get("/api/sites/s1/equipment-events", headers=admin)).json()["total"] == 1
    assert (await client.get("/api/sites/s1/equipment-visits", headers=admin)).json()["total"] == 1
async def test_rejects_bad_key_unknown_camera_and_invalid_lifecycle_time(client):
    at = utcnow() - timedelta(seconds=5)
    payload = _payload(at)
    assert (await client.post("/api/ingest/equipment-events", json=payload)).status_code == 401
    assert (await _post(client, dict(payload, event_id=str(uuid4()), camera_id="missing"))).status_code == 404
    invalid = dict(payload, event_id=str(uuid4()), first_seen_at=(at + timedelta(seconds=2)).isoformat())
    assert (await _post(client, invalid)).status_code == 422
