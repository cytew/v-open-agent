"""Deterministic observation admission (SPEC 4.2, INV-07)."""

from dataclasses import dataclass, field
from typing import Literal

from voa.contracts.enums import SourceKind
from voa.contracts.observation import Observation
from voa.engine.profile import PolicyProfile, ProfileError

AdmissionStatus = Literal[
    "accepted",
    "rejected_session_mismatch",
    "duplicate",
    "out_of_order",
    "stale_on_arrival",
    "config_error",
]

AdmissionKey = tuple[str, SourceKind]


@dataclass(frozen=True)
class Admission:
    """Apply ordered admission checks and remember accepted observations only."""

    session_id: str
    device_id: str
    profile: PolicyProfile | ProfileError
    _observation_ids: set[str] = field(default_factory=set, init=False, repr=False)
    _last_seq: dict[AdmissionKey, int] = field(default_factory=dict, init=False, repr=False)
    _last_capture: dict[AdmissionKey, int] = field(default_factory=dict, init=False, repr=False)

    def admit(self, observation: Observation, *, now_mono_ns: int) -> AdmissionStatus:
        """Return the first failing status, updating history only when accepted."""
        if isinstance(self.profile, ProfileError):
            return "config_error"
        if observation.session_id != self.session_id or observation.device_id != self.device_id:
            return "rejected_session_mismatch"
        if observation.observation_id in self._observation_ids:
            return "duplicate"

        key = (observation.stream_id, observation.source_kind)
        last_seq = self._last_seq.get(key)
        if last_seq is not None and observation.seq <= last_seq:
            return "out_of_order"
        last_capture = self._last_capture.get(key)
        if last_capture is not None and observation.capture_ts_monotonic_ns < last_capture:
            return "out_of_order"

        staleness_ns = self.profile.params.staleness_ms * 1_000_000
        if now_mono_ns - observation.server_received_mono_ns > staleness_ns:
            return "stale_on_arrival"

        self._observation_ids.add(observation.observation_id)
        self._last_seq[key] = observation.seq
        self._last_capture[key] = observation.capture_ts_monotonic_ns
        return "accepted"

    def forget_observation(self, observation_id: str) -> None:
        """Drop an evicted window ID while retaining stream ordering history."""
        self._observation_ids.discard(observation_id)
