"""SQLite metadata boundary (DAT-01, SPEC 8.1).

Only explicitly selected metadata is written. Payloads, alert text, credentials
and engine state restoration are not part of this repository. The server owns
startup/hourly calls to ``purge_expired`` and supplies the current UTC time.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Literal, Self

from voa.contracts.decision import Alert, RiskDecision
from voa.contracts.observation import Observation

_Value = str | int | None
MetadataRow = dict[str, _Value]
_Table = Literal["sessions", "observations_meta", "decisions", "alerts"]

_COLUMNS: dict[_Table, tuple[str, ...]] = {
    "sessions": ("session_id", "device_id", "started_at_utc", "ended_at_utc"),
    "observations_meta": (
        "observation_id",
        "session_id",
        "stream_id",
        "source_kind",
        "seq",
        "admission_status",
        "received_at_utc",
        "quality_flags_json",
        "model_id",
        "model_revision",
    ),
    "decisions": (
        "id",
        "session_id",
        "risk_event_id",
        "scenario_id",
        "previous_state",
        "state",
        "reason_codes_json",
        "evidence_ids_json",
        "policy_version",
        "decided_at_utc",
    ),
    "alerts": (
        "alert_id",
        "session_id",
        "risk_event_id",
        "scenario_id",
        "alert_seq",
        "delivery_state",
        "valid_until_utc",
        "updated_at_utc",
    ),
}

_RETENTION_COLUMNS: dict[_Table, str] = {
    "sessions": "started_at_utc",
    "observations_meta": "received_at_utc",
    "decisions": "decided_at_utc",
    "alerts": "updated_at_utc",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY NOT NULL,
    device_id TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    ended_at_utc TEXT
);
CREATE TABLE IF NOT EXISTS observations_meta (
    observation_id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    seq INTEGER NOT NULL,
    admission_status TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    quality_flags_json TEXT NOT NULL,
    model_id TEXT,
    model_revision TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    risk_event_id TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    previous_state TEXT,
    state TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    decided_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL,
    risk_event_id TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    alert_seq INTEGER NOT NULL,
    delivery_state TEXT NOT NULL,
    valid_until_utc TEXT,
    updated_at_utc TEXT NOT NULL
);
"""


def _utc_text(value: datetime) -> str:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    # Fixed precision makes SQLite text comparisons match UTC time ordering.
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


class SQLiteStore:
    """Synchronous CRUD for the four metadata tables, with no engine dependency.

    Save methods upsert by primary key. A decision without ``decision_id`` gets
    a new integer ID. Updating delivery state uses ``save_alert`` separately
    from the original RiskDecision. Missing reads return None; deletes report
    whether a row existed. Connection use belongs to one server thread.
    """

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(path))
        self._connection.row_factory = sqlite3.Row
        try:
            self._connection.executescript(_SCHEMA)
        except sqlite3.Error:
            self._connection.close()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def save_session(
        self,
        *,
        session_id: str,
        device_id: str,
        started_at_utc: datetime,
        ended_at_utc: datetime | None = None,
    ) -> None:
        self._save(
            "sessions",
            (
                session_id,
                device_id,
                _utc_text(started_at_utc),
                _utc_text(ended_at_utc) if ended_at_utc is not None else None,
            ),
        )

    def get_session(self, session_id: str) -> MetadataRow | None:
        return self._get("sessions", session_id)

    def delete_session(self, session_id: str) -> bool:
        return self._delete("sessions", session_id)

    def save_observation(self, observation: Observation, *, admission_status: str) -> None:
        model = observation.model
        self._save(
            "observations_meta",
            (
                observation.observation_id,
                observation.session_id,
                observation.stream_id,
                observation.source_kind,
                observation.seq,
                admission_status,
                _utc_text(observation.received_at_utc),
                json.dumps(sorted(observation.quality_flags), separators=(",", ":")),
                model.id if model is not None else None,
                model.revision if model is not None else None,
            ),
        )

    def get_observation(self, observation_id: str) -> MetadataRow | None:
        return self._get("observations_meta", observation_id)

    def delete_observation(self, observation_id: str) -> bool:
        return self._delete("observations_meta", observation_id)

    def save_decision(self, decision: RiskDecision, *, decision_id: int | None = None) -> int:
        row_id = self._save(
            "decisions",
            (
                decision_id,
                decision.session_id,
                decision.risk_event_id,
                decision.scenario_id,
                decision.previous_state,
                decision.state,
                json.dumps(decision.reason_codes, separators=(",", ":")),
                json.dumps(decision.evidence_ids, separators=(",", ":")),
                decision.policy_version,
                _utc_text(decision.decided_at_utc),
            ),
        )
        return decision_id if decision_id is not None else row_id

    def get_decision(self, decision_id: int) -> MetadataRow | None:
        return self._get("decisions", decision_id)

    def delete_decision(self, decision_id: int) -> bool:
        return self._delete("decisions", decision_id)

    def save_alert(
        self,
        alert: Alert,
        *,
        session_id: str,
        risk_event_id: str,
        scenario_id: str,
        alert_seq: int,
        updated_at_utc: datetime,
    ) -> None:
        self._save(
            "alerts",
            (
                alert.alert_id,
                session_id,
                risk_event_id,
                scenario_id,
                alert_seq,
                alert.delivery_state,
                _utc_text(alert.valid_until_utc) if alert.valid_until_utc is not None else None,
                _utc_text(updated_at_utc),
            ),
        )

    def get_alert(self, alert_id: str) -> MetadataRow | None:
        return self._get("alerts", alert_id)

    def delete_alert(self, alert_id: str) -> bool:
        return self._delete("alerts", alert_id)

    def purge_expired(self, *, now_utc: datetime, retention_days: int) -> dict[str, int]:
        """Delete each table's rows strictly older than its own retention cutoff.

        There is no session cascade: observations, decisions and alerts have
        independent retention timestamps, as specified in SPEC 8.1.
        """
        if retention_days < 1:
            raise ValueError("retention_days must be >= 1")
        cutoff = _utc_text(now_utc - timedelta(days=retention_days))
        deleted: dict[str, int] = {}
        with self._connection:
            for table, column in _RETENTION_COLUMNS.items():
                cursor = self._connection.execute(
                    f"DELETE FROM {table} WHERE {column} < ?", (cutoff,)
                )
                deleted[table] = cursor.rowcount
        return deleted

    def _save(self, table: _Table, values: tuple[_Value, ...]) -> int:
        # Do not let SQLite's permissive TEXT affinity store bytes as BLOBs.
        if any(value is not None and type(value) not in (str, int) for value in values):
            raise TypeError("metadata values must be text, integers or null")
        columns = _COLUMNS[table]
        updates = ", ".join(f"{column} = excluded.{column}" for column in columns[1:])
        with self._connection:
            cursor = self._connection.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)}) "
                f"ON CONFLICT ({columns[0]}) DO UPDATE SET {updates}",
                values,
            )
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    def _get(self, table: _Table, key: str | int) -> MetadataRow | None:
        columns = _COLUMNS[table]
        row = self._connection.execute(
            f"SELECT {', '.join(columns)} FROM {table} WHERE {columns[0]} = ?", (key,)
        ).fetchone()
        return dict(row) if row is not None else None

    def _delete(self, table: _Table, key: str | int) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                f"DELETE FROM {table} WHERE {_COLUMNS[table][0]} = ?", (key,)
            )
            return cursor.rowcount > 0
