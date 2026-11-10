"""TASK-06 observation admission (ENG-01, ENG-02, INV-07, T-R04)."""

from typing import Any

import pytest

from tests.builders import build_observation
from voa.contracts.observation import Observation, TrackPayload
from voa.engine.admission import Admission, AdmissionStatus
from voa.engine.profile import PolicyProfile, ProfileError


def policy(*, staleness_ms: int = 1_000) -> PolicyProfile:
    return PolicyProfile.model_validate(
        {
            "profile_id": "test",
            "profile_version": 1,
            "degraded_notice_text": "감지가 제한됩니다.",
            "params": {
                "min_score": 0.5,
                "roi_min_overlap": 0.3,
                "confirm_frames": 3,
                "clear_frames": 3,
                "staleness_ms": staleness_ms,
                "unknown_after_ms": staleness_ms,
                "warning_ttl_ms": 4_000,
                "window_ms": staleness_ms,
                "window_max_items": 256,
            },
            "scenarios": [
                {
                    "id": "obstacle",
                    "required_kinds": ["object_detection"],
                    "required_streams": ["camera_0"],
                    "labels": ["obstacle"],
                    "motion_classes": [],
                    "roi": [0.0, 0.0, 1.0, 1.0],
                    "blocking_flags": ["occluded"],
                    "warning_text": "장애물에 주의하세요.",
                    "severity": "attention",
                }
            ],
        }
    )


def admission(profile: PolicyProfile | ProfileError | None = None) -> Admission:
    return Admission(
        session_id="sess_0001",
        device_id="dev_0001",
        profile=profile if profile is not None else policy(),
    )


def assert_status(status: AdmissionStatus, expected: AdmissionStatus) -> None:
    assert status == expected


def test_first_observation_for_key_is_accepted_at_any_sequence_and_capture_time() -> None:
    gate = admission()
    obs = build_observation(seq=87, capture_ts_monotonic_ns=9_000_000_000)

    assert_status(gate.admit(obs, now_mono_ns=0), "accepted")


def test_invalid_profile_wins_before_every_observation_check() -> None:
    gate = admission(ProfileError("profile_invalid", "profile.yaml:params"))
    obs = build_observation(session_id="wrong", device_id="wrong")

    assert_status(gate.admit(obs, now_mono_ns=10**18), "config_error")


@pytest.mark.parametrize(
    "overrides",
    [
        {"session_id": "wrong"},
        {"device_id": "wrong"},
        {"session_id": "wrong", "device_id": "wrong"},
    ],
)
def test_session_or_device_mismatch_is_rejected(overrides: dict[str, Any]) -> None:
    gate = admission()

    assert_status(
        gate.admit(build_observation(**overrides), now_mono_ns=0),
        "rejected_session_mismatch",
    )


def test_session_mismatch_wins_before_duplicate() -> None:
    gate = admission()
    assert_status(gate.admit(build_observation(), now_mono_ns=0), "accepted")

    mismatch = build_observation(observation_id="obs_0001", session_id="wrong")
    assert_status(gate.admit(mismatch, now_mono_ns=0), "rejected_session_mismatch")


def test_duplicate_id_wins_before_sequence_capture_and_staleness() -> None:
    gate = admission()
    accepted = build_observation(seq=5, capture_ts_monotonic_ns=5, server_received_mono_ns=5)
    assert_status(gate.admit(accepted, now_mono_ns=5), "accepted")
    duplicate = build_observation(
        observation_id=accepted.observation_id,
        seq=0,
        capture_ts_monotonic_ns=0,
        server_received_mono_ns=0,
    )

    assert_status(gate.admit(duplicate, now_mono_ns=2_000_000_000), "duplicate")


@pytest.mark.parametrize("seq", [4, 5])
def test_sequence_must_strictly_increase_and_wins_before_later_checks(seq: int) -> None:
    gate = admission()
    assert_status(
        gate.admit(build_observation(observation_id="obs_first", seq=5), now_mono_ns=0),
        "accepted",
    )
    obs = build_observation(
        observation_id="obs_later",
        seq=seq,
        capture_ts_monotonic_ns=-1,
        server_received_mono_ns=0,
    )

    assert_status(gate.admit(obs, now_mono_ns=2_000_000_000), "out_of_order")


def test_capture_time_may_repeat_but_must_not_move_backwards() -> None:
    gate = admission()
    assert_status(
        gate.admit(
            build_observation(observation_id="obs_first", seq=1, capture_ts_monotonic_ns=100),
            now_mono_ns=0,
        ),
        "accepted",
    )
    assert_status(
        gate.admit(
            build_observation(observation_id="obs_equal", seq=2, capture_ts_monotonic_ns=100),
            now_mono_ns=0,
        ),
        "accepted",
    )
    assert_status(
        gate.admit(
            build_observation(observation_id="obs_older", seq=3, capture_ts_monotonic_ns=99),
            now_mono_ns=2_000_000_000,
        ),
        "out_of_order",
    )


def test_staleness_boundary_is_inclusive_then_rejects_one_nanosecond_later() -> None:
    gate = admission(policy(staleness_ms=1))
    boundary = build_observation(observation_id="obs_boundary", server_received_mono_ns=10)
    stale = build_observation(observation_id="obs_stale", seq=1, server_received_mono_ns=10)

    assert_status(gate.admit(boundary, now_mono_ns=1_000_010), "accepted")
    assert_status(gate.admit(stale, now_mono_ns=1_000_011), "stale_on_arrival")


@pytest.mark.parametrize(
    ("rejected", "expected"),
    [
        (
            build_observation(observation_id="bad_session", session_id="wrong", seq=99),
            "rejected_session_mismatch",
        ),
        (
            build_observation(observation_id="stale", seq=99, server_received_mono_ns=0),
            "stale_on_arrival",
        ),
    ],
)
def test_rejected_observation_does_not_advance_sequence_state(
    rejected: Observation, expected: AdmissionStatus
) -> None:
    gate = admission(policy(staleness_ms=1))
    assert_status(gate.admit(rejected, now_mono_ns=2_000_000), expected)

    assert_status(
        gate.admit(
            build_observation(
                observation_id=rejected.observation_id,
                seq=1,
                server_received_mono_ns=2_000_000,
            ),
            now_mono_ns=2_000_000,
        ),
        "accepted",
    )


def test_out_of_order_rejection_does_not_advance_capture_state() -> None:
    gate = admission()
    assert_status(
        gate.admit(
            build_observation(observation_id="first", seq=5, capture_ts_monotonic_ns=100),
            now_mono_ns=0,
        ),
        "accepted",
    )
    assert_status(
        gate.admit(
            build_observation(observation_id="bad", seq=4, capture_ts_monotonic_ns=10_000),
            now_mono_ns=0,
        ),
        "out_of_order",
    )
    assert_status(
        gate.admit(
            build_observation(observation_id="good", seq=6, capture_ts_monotonic_ns=101),
            now_mono_ns=0,
        ),
        "accepted",
    )


def test_sequence_history_is_isolated_by_stream_and_source_kind() -> None:
    gate = admission()
    detection = build_observation(observation_id="detection", seq=3, stream_id="camera_0")
    other_stream = build_observation(observation_id="other_stream", seq=0, stream_id="camera_1")
    track = build_observation(
        observation_id="track",
        seq=0,
        stream_id="camera_0",
        payload=TrackPayload(kind="track_state", frame_id="frame_0001", tracks=[]),
    )

    assert_status(gate.admit(detection, now_mono_ns=0), "accepted")
    assert_status(gate.admit(other_stream, now_mono_ns=0), "accepted")
    assert_status(gate.admit(track, now_mono_ns=0), "accepted")


def test_forgotten_observation_id_is_no_longer_a_duplicate() -> None:
    gate = admission()
    first = build_observation(observation_id="reused", seq=0)
    assert_status(gate.admit(first, now_mono_ns=0), "accepted")
    gate.forget_observation("reused")

    reused = build_observation(observation_id="reused", seq=1)
    assert_status(gate.admit(reused, now_mono_ns=0), "accepted")
