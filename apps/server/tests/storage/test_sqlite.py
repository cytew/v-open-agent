"""TASK-15 metadata CRUD, schema and retention acceptance (DAT-01, SPEC 8.1)."""

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest

from tests.builders import build_observation
from voa.contracts.decision import Alert, RiskDecision
from voa.contracts.observation import AsrPayload, ModelRef, OcrPayload
from voa.storage.sqlite import SQLiteStore

FIXTURE = Path(__file__).resolve().parents[4] / "contracts" / "fixtures" / "risk_event.json"
NOW = datetime(2026, 11, 10, 3, tzinfo=UTC)


@pytest.fixture
def decision() -> RiskDecision:
    return RiskDecision.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteStore]:
    with SQLiteStore(tmp_path / "metadata.sqlite3") as metadata_store:
        yield metadata_store


def test_schema_has_only_spec_tables_columns_and_no_blobs(tmp_path: Path) -> None:
    path = tmp_path / "metadata.sqlite3"
    with SQLiteStore(path), sqlite3.connect(path) as connection:
        expected = {
            "sessions": "session_id device_id started_at_utc ended_at_utc",
            "observations_meta": (
                "observation_id session_id stream_id source_kind seq admission_status "
                "received_at_utc quality_flags_json model_id model_revision"
            ),
            "decisions": (
                "id session_id risk_event_id scenario_id previous_state state "
                "reason_codes_json evidence_ids_json policy_version decided_at_utc"
            ),
            "alerts": (
                "alert_id session_id risk_event_id scenario_id alert_seq delivery_state "
                "valid_until_utc updated_at_utc"
            ),
        }
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        assert {row[0] for row in tables} == set(expected)
        for table, columns in expected.items():
            info = connection.execute(f"PRAGMA table_info({table})").fetchall()
            assert [row[1] for row in info] == columns.split()
            assert {row[2] for row in info} <= {"TEXT", "INTEGER"}
            assert info[0][5] == 1
        info = connection.execute("PRAGMA table_info(decisions)").fetchall()
        assert info[0][2] == "INTEGER"


def test_session_create_read_update_delete(store: SQLiteStore) -> None:
    store.save_session(session_id="sess_1", device_id="dev_1", started_at_utc=NOW)
    assert store.get_session("sess_1") == {
        "session_id": "sess_1",
        "device_id": "dev_1",
        "started_at_utc": "2026-11-10T03:00:00.000000Z",
        "ended_at_utc": None,
    }
    store.save_session(
        session_id="sess_1",
        device_id="dev_1",
        started_at_utc=NOW,
        ended_at_utc=NOW + timedelta(seconds=1),
    )
    row = store.get_session("sess_1")
    assert row is not None
    assert row["ended_at_utc"] == "2026-11-10T03:00:01.000000Z"
    assert store.delete_session("sess_1")
    assert store.get_session("sess_1") is None
    assert not store.delete_session("sess_1")


def test_observation_create_read_update_delete(store: SQLiteStore) -> None:
    observation = build_observation(
        model=ModelRef(id="mock-detector", revision="0"),
        quality_flags=frozenset({"occluded", "frame_dropped"}),
    )
    store.save_observation(observation, admission_status="accepted")
    row = store.get_observation(observation.observation_id)
    assert row is not None
    assert row["source_kind"] == "object_detection"
    assert row["seq"] == 0
    assert row["model_id"] == "mock-detector"
    assert row["model_revision"] == "0"
    assert row["quality_flags_json"] == '["frame_dropped","occluded"]'
    store.save_observation(observation, admission_status="duplicate")
    row = store.get_observation(observation.observation_id)
    assert row is not None
    assert row["admission_status"] == "duplicate"
    assert store.delete_observation(observation.observation_id)
    assert store.get_observation(observation.observation_id) is None


def test_observation_without_model_has_null_model_fields(store: SQLiteStore) -> None:
    observation = build_observation()
    store.save_observation(observation, admission_status="stale_on_arrival")
    row = store.get_observation(observation.observation_id)
    assert row is not None
    assert row["model_id"] is None
    assert row["model_revision"] is None
    assert row["quality_flags_json"] == "[]"


def test_decision_create_read_update_delete(store: SQLiteStore, decision: RiskDecision) -> None:
    decision_id = store.save_decision(decision)
    second_id = store.save_decision(decision)
    assert decision_id != second_id
    row = store.get_decision(decision_id)
    assert row is not None
    assert row["state"] == "warning"
    assert row["previous_state"] == "candidate"
    assert row["evidence_ids_json"] == json.dumps(decision.evidence_ids, separators=(",", ":"))
    assert row["reason_codes_json"] == '["temporal_confirmation_passed"]'
    revised = decision.model_copy(update={"policy_version": "indoor_research_v0@2"})
    assert store.save_decision(revised, decision_id=decision_id) == decision_id
    row = store.get_decision(decision_id)
    assert row is not None
    assert row["policy_version"] == "indoor_research_v0@2"
    assert store.delete_decision(decision_id)
    assert store.get_decision(decision_id) is None
    assert store.get_decision(second_id) is not None


def test_decision_with_null_previous_state(store: SQLiteStore, decision: RiskDecision) -> None:
    decision_id = store.save_decision(decision.model_copy(update={"previous_state": None}))
    row = store.get_decision(decision_id)
    assert row is not None
    assert row["previous_state"] is None


def test_alert_delivery_update_leaves_original_decision_unchanged(
    store: SQLiteStore, decision: RiskDecision
) -> None:
    assert decision.alert is not None
    decision_id = store.save_decision(decision)
    original_row = store.get_decision(decision_id)
    for state in ("queued", "sent", "received", "played"):
        alert = Alert.model_validate({**decision.alert.model_dump(), "delivery_state": state})
        store.save_alert(
            alert,
            session_id=decision.session_id,
            risk_event_id=decision.risk_event_id,
            scenario_id=decision.scenario_id,
            alert_seq=1,
            updated_at_utc=NOW,
        )
        row = store.get_alert(alert.alert_id)
        assert row is not None
        assert row["delivery_state"] == state
        assert row["alert_seq"] == 1
    assert decision.alert.delivery_state == "queued"
    assert store.get_decision(decision_id) == original_row
    assert store.delete_alert(decision.alert.alert_id)
    assert store.get_alert(decision.alert.alert_id) is None


def test_alert_deadline_can_be_null(store: SQLiteStore, decision: RiskDecision) -> None:
    assert decision.alert is not None
    store.save_alert(
        decision.alert.model_copy(update={"valid_until_utc": None}),
        session_id=decision.session_id,
        risk_event_id=decision.risk_event_id,
        scenario_id=decision.scenario_id,
        alert_seq=1,
        updated_at_utc=NOW,
    )
    row = store.get_alert(decision.alert.alert_id)
    assert row is not None
    assert row["valid_until_utc"] is None


def test_missing_rows_are_none_and_deletes_are_false(store: SQLiteStore) -> None:
    assert store.get_session("missing") is None
    assert store.get_observation("missing") is None
    assert store.get_decision(123) is None
    assert store.get_alert("missing") is None
    assert not store.delete_session("missing")
    assert not store.delete_observation("missing")
    assert not store.delete_decision(123)
    assert not store.delete_alert("missing")


def test_values_are_bound_and_committed_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "metadata.sqlite3"
    quoted_id = "sess_'; DROP TABLE sessions; --"
    with SQLiteStore(path) as store:
        store.save_session(session_id=quoted_id, device_id="dev_1", started_at_utc=NOW)
        assert store.get_session("' OR 1 = 1 --") is None
    with SQLiteStore(path) as reopened:
        assert reopened.get_session(quoted_id) is not None
        reopened.save_session(session_id="sess_2", device_id="dev_1", started_at_utc=NOW)


@pytest.mark.parametrize("kind", ["asr", "ocr"])
def test_payload_and_alert_text_are_absent_from_database(
    tmp_path: Path, decision: RiskDecision, kind: str
) -> None:
    secret = "private-payload-do-not-persist"
    payload = (
        AsrPayload(
            kind="asr_transcript",
            utterance_id="utterance_1",
            revision=0,
            is_final=True,
            text=secret,
            speaker_role="untrusted_environment",
            authentication_basis="none",
        )
        if kind == "asr"
        else OcrPayload(
            kind="ocr_text",
            frame_id="frame_1",
            text=secret,
            bbox=(0.0, 0.0, 1.0, 1.0),
            language="ko",
            score=1.0,
        )
    )
    path = tmp_path / "metadata.sqlite3"
    assert decision.alert is not None
    with SQLiteStore(path) as store:
        store.save_observation(build_observation(payload=payload), admission_status="accepted")
        store.save_decision(decision)
        store.save_alert(
            decision.alert,
            session_id=decision.session_id,
            risk_event_id=decision.risk_event_id,
            scenario_id=decision.scenario_id,
            alert_seq=1,
            updated_at_utc=NOW,
        )
    with sqlite3.connect(path) as connection:
        dump = "\n".join(connection.iterdump())
    assert secret not in dump
    assert decision.alert.text not in dump
    assert secret.encode() not in path.read_bytes()
    assert decision.alert.text.encode() not in path.read_bytes()


def test_retention_deletes_only_strictly_older_rows_for_every_table(
    store: SQLiteStore, decision: RiskDecision
) -> None:
    assert decision.alert is not None
    cutoff = NOW - timedelta(days=7)
    decision_ids: dict[str, int] = {}
    for suffix, timestamp in (
        ("old", cutoff - timedelta(microseconds=1)),
        ("boundary", cutoff),
        ("recent", cutoff + timedelta(microseconds=1)),
    ):
        session_id = f"sess_{suffix}"
        store.save_session(
            session_id=session_id,
            device_id="dev_1",
            started_at_utc=timestamp,
            ended_at_utc=NOW,
        )
        observation = build_observation(observation_id=f"obs_{suffix}", session_id=session_id)
        store.save_observation(
            observation.model_copy(update={"received_at_utc": timestamp}),
            admission_status="accepted",
        )
        decision_ids[suffix] = store.save_decision(
            decision.model_copy(update={"session_id": session_id, "decided_at_utc": timestamp})
        )
        store.save_alert(
            decision.alert.model_copy(update={"alert_id": f"alert_{suffix}"}),
            session_id=session_id,
            risk_event_id=decision.risk_event_id,
            scenario_id=decision.scenario_id,
            alert_seq=1,
            updated_at_utc=timestamp,
        )
    assert store.purge_expired(now_utc=NOW, retention_days=7) == {
        "sessions": 1,
        "observations_meta": 1,
        "decisions": 1,
        "alerts": 1,
    }
    assert store.get_session("sess_old") is None
    assert store.get_observation("obs_old") is None
    assert store.get_decision(decision_ids["old"]) is None
    assert store.get_alert("alert_old") is None
    for suffix in ("boundary", "recent"):
        assert store.get_session(f"sess_{suffix}") is not None
        assert store.get_observation(f"obs_{suffix}") is not None
        assert store.get_decision(decision_ids[suffix]) is not None
        assert store.get_alert(f"alert_{suffix}") is not None
    assert set(store.purge_expired(now_utc=NOW, retention_days=7).values()) == {0}


def test_retention_uses_each_tables_time_independently(
    store: SQLiteStore, decision: RiskDecision
) -> None:
    assert decision.alert is not None
    store.save_session(
        session_id=decision.session_id,
        device_id=decision.device_id,
        started_at_utc=NOW - timedelta(days=8),
    )
    # Capture time is old, but received_at_utc governs late observation retention.
    observation = build_observation(session_id=decision.session_id)
    store.save_observation(
        observation.model_copy(update={"received_at_utc": NOW}), admission_status="stale_on_arrival"
    )
    decision_id = store.save_decision(decision.model_copy(update={"decided_at_utc": NOW}))
    store.save_alert(
        decision.alert,
        session_id=decision.session_id,
        risk_event_id=decision.risk_event_id,
        scenario_id=decision.scenario_id,
        alert_seq=1,
        updated_at_utc=NOW,
    )
    assert store.purge_expired(now_utc=NOW, retention_days=7) == {
        "sessions": 1,
        "observations_meta": 0,
        "decisions": 0,
        "alerts": 0,
    }
    assert store.get_observation(observation.observation_id) is not None
    assert store.get_decision(decision_id) is not None
    assert store.get_alert(decision.alert.alert_id) is not None


@pytest.mark.parametrize("days", [0, -1])
def test_invalid_retention_does_not_delete_rows(store: SQLiteStore, days: int) -> None:
    store.save_session(session_id="sess_1", device_id="dev_1", started_at_utc=NOW)
    with pytest.raises(ValueError, match="retention_days"):
        store.purge_expired(now_utc=NOW, retention_days=days)
    assert store.get_session("sess_1") is not None


@pytest.mark.parametrize(
    "timestamp",
    [datetime(2026, 11, 10), datetime(2026, 11, 10, tzinfo=timezone(timedelta(hours=9)))],
)
def test_non_utc_times_are_rejected(store: SQLiteStore, timestamp: datetime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        store.save_session(session_id="sess_1", device_id="dev_1", started_at_utc=timestamp)
    with pytest.raises(ValueError, match="UTC"):
        store.purge_expired(now_utc=timestamp, retention_days=7)
    assert store.get_session("sess_1") is None


def test_failed_write_rolls_back_and_connection_remains_usable(store: SQLiteStore) -> None:
    store.save_session(session_id="sess_1", device_id="dev_1", started_at_utc=NOW)
    with pytest.raises(sqlite3.IntegrityError):
        store.save_session(session_id="sess_1", device_id=cast(str, None), started_at_utc=NOW)
    row = store.get_session("sess_1")
    assert row is not None
    assert row["device_id"] == "dev_1"
    store.save_session(session_id="sess_2", device_id="dev_1", started_at_utc=NOW)
    assert store.get_session("sess_2") is not None


def test_raw_bytes_are_rejected_before_sqlite_can_store_a_blob(store: SQLiteStore) -> None:
    secret = b"private-raw-media"
    with pytest.raises(TypeError, match="metadata values") as error:
        store.save_session(session_id="sess_1", device_id=cast(str, secret), started_at_utc=NOW)
    assert secret.decode() not in str(error.value)
    assert store.get_session("sess_1") is None


def test_context_manager_closes_connection_on_failure(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "metadata.sqlite3")
    with pytest.raises(RuntimeError), store:
        raise RuntimeError("synthetic failure")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        store.get_session("missing")
