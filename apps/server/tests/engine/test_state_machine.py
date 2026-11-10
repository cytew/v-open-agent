"""TASK-09 ordered transitions and episode lifetime (T-R01, T-R03)."""

from pathlib import Path

import pytest

from voa.config.profile_loader import load_profile
from voa.contracts.decision import ReasonCode
from voa.contracts.enums import RiskState, Verdict
from voa.engine.evaluator import ConditionReason, ConditionResult
from voa.engine.ports import SequentialIdFactory
from voa.engine.profile import PolicyProfile
from voa.engine.state_machine import ScenarioStateMachine, StateTransition


@pytest.fixture
def profile() -> PolicyProfile:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "profiles" / "valid.yaml"
    result = load_profile(path)
    assert isinstance(result, PolicyProfile)
    return result


def machine(
    profile: PolicyProfile, *, confirm_frames: int = 3, clear_frames: int = 3
) -> ScenarioStateMachine:
    params = profile.params.model_copy(
        update={"confirm_frames": confirm_frames, "clear_frames": clear_frames}
    )
    return ScenarioStateMachine(profile.scenarios[0], params, SequentialIdFactory())


def condition(verdict: Verdict, *, reason: ConditionReason | None = None) -> ConditionResult:
    reasons: dict[Verdict, ConditionReason] = {
        "met": "condition_observed",
        "absent": "condition_absent",
        "indeterminate": "quality_blocked",
    }
    return ConditionResult(
        verdict, ("obs_frame",), reason if reason is not None else reasons[verdict]
    )


def feed(
    subject: ScenarioStateMachine, verdict: Verdict, *, now: int = 0
) -> StateTransition | None:
    return subject.advance(now_mono_ns=now, condition=condition(verdict))


def reach(subject: ScenarioStateMachine, state: RiskState) -> None:
    """Reach a table row through public inputs rather than seeding private state."""
    if state == "clear":
        for _ in range(subject.params.clear_frames):
            feed(subject, "absent")
    elif state == "candidate":
        feed(subject, "met")
    elif state in ("warning", "resolved"):
        for _ in range(max(2, subject.params.confirm_frames)):
            feed(subject, "met")
        if state == "resolved":
            for _ in range(subject.params.clear_frames):
                feed(subject, "absent")
    assert subject.state == state


@pytest.mark.parametrize(
    ("row", "start", "pre", "verdict", "next_state", "reason", "event_id", "ended"),
    [
        (1, "candidate", None, None, "unknown", "sensor_down", "risk_0001", False),
        (2, "warning", None, None, "unknown", "input_stale", "risk_0001", False),
        (3, "resolved", None, "met", "candidate", "condition_observed", "risk_0002", False),
        (4, "resolved", None, None, "clear", "episode_closed", "risk_0001", True),
        (5, "clear", None, "met", "candidate", "condition_observed", "risk_0001", False),
        (
            6,
            "candidate",
            None,
            "met",
            "warning",
            "temporal_confirmation_passed",
            "risk_0001",
            False,
        ),
        (
            7,
            "candidate",
            "absent",
            "absent",
            "clear",
            "condition_absent_confirmed",
            "risk_0001",
            True,
        ),
        (
            8,
            "warning",
            "absent",
            "absent",
            "resolved",
            "condition_absent_confirmed",
            "risk_0001",
            False,
        ),
        (9, "unknown", None, "met", "candidate", "fresh_evidence_returned", "risk_0001", False),
        (10, "unknown", "absent", "absent", "clear", "condition_absent_confirmed", None, True),
    ],
)
def test_each_transition_table_row(
    profile: PolicyProfile,
    row: int,
    start: RiskState,
    pre: Verdict | None,
    verdict: Verdict | None,
    next_state: RiskState,
    reason: ReasonCode,
    event_id: str | None,
    ended: bool,
) -> None:
    subject = machine(profile, confirm_frames=2, clear_frames=2)
    reach(subject, start)
    if pre is not None:
        assert feed(subject, pre) is None
    now = subject.params.unknown_after_ms * 1_000_000 + 1 if row == 2 else 0

    transition = subject.advance(
        now_mono_ns=now,
        condition=condition(verdict) if verdict is not None else None,
        sensor_down={"camera_0": True} if row == 1 else None,
    )

    assert transition == StateTransition(start, next_state, (reason,), event_id)
    assert subject.state == next_state
    assert subject.risk_event_id == (None if ended else event_id)


def test_new_machine_starts_unknown_without_episode_or_transition(profile: PolicyProfile) -> None:
    subject = machine(profile)

    assert subject.state == "unknown"
    assert subject.risk_event_id is None
    assert (subject.met_streak, subject.absent_streak) == (0, 0)
    assert subject.last_determinate_mono_ns is None
    assert subject.last_indeterminate_reason is None
    assert subject.advance(now_mono_ns=10**18) is None
    transition = feed(subject, "met", now=10**18)
    assert transition is not None and transition.risk_event_id == "risk_0001"


@pytest.mark.parametrize("confirm_frames", [1, 2, 3])
def test_confirmation_requires_consecutive_frames_and_never_skips_candidate(
    profile: PolicyProfile, confirm_frames: int
) -> None:
    subject = machine(profile, confirm_frames=confirm_frames)

    first = feed(subject, "met")
    assert first is not None and first.state == "candidate"
    assert subject.risk_event_id == "risk_0001"
    for index in range(2, max(2, confirm_frames) + 1):
        transition = feed(subject, "met", now=index)
        if index < confirm_frames:
            assert transition is None and subject.state == "candidate"
        else:
            assert transition is not None and transition.state == "warning"
    assert feed(subject, "met", now=10) is None
    assert subject.state == "warning"
    assert subject.risk_event_id == "risk_0001"


@pytest.mark.parametrize("start", ["clear", "resolved"])
def test_confirmation_of_one_still_allows_only_one_transition_per_call(
    profile: PolicyProfile, start: RiskState
) -> None:
    subject = machine(profile, confirm_frames=1)
    reach(subject, start)

    transition = feed(subject, "met")

    assert transition is not None and transition.state == "candidate"
    next_transition = feed(subject, "met")
    assert next_transition is not None and next_transition.state == "warning"


@pytest.mark.parametrize("start", ["candidate", "warning", "unknown"])
@pytest.mark.parametrize("clear_frames", [1, 3])
def test_clear_or_resolution_requires_confirmed_absence_not_missing_frames(
    profile: PolicyProfile, start: RiskState, clear_frames: int
) -> None:
    subject = machine(profile, clear_frames=clear_frames)
    reach(subject, start)
    for index in range(clear_frames):
        transition = feed(subject, "absent", now=index * 2)
        if index < clear_frames - 1:
            assert transition is None
            assert subject.advance(now_mono_ns=index * 2 + 1) is None
            assert subject.absent_streak == index + 1
            assert subject.state == start
        else:
            assert transition is not None
            assert transition.state == ("resolved" if start == "warning" else "clear")


@pytest.mark.parametrize("verdict", ["met", "absent"])
@pytest.mark.parametrize("reason", ["quality_blocked", "track_lost"])
def test_indeterminate_breaks_both_streaks_without_refreshing_determinate_time(
    profile: PolicyProfile, verdict: Verdict, reason: ConditionReason
) -> None:
    subject = machine(profile)
    feed(subject, verdict, now=100)
    feed(subject, verdict, now=101)

    assert (
        subject.advance(now_mono_ns=102, condition=condition("indeterminate", reason=reason))
        is None
    )
    assert (subject.met_streak, subject.absent_streak) == (0, 0)
    assert subject.last_determinate_mono_ns == 101
    assert subject.last_indeterminate_reason == reason
    assert feed(subject, verdict, now=103) is None
    assert (subject.met_streak, subject.absent_streak) == ((1, 0) if verdict == "met" else (0, 1))
    assert subject.last_determinate_mono_ns == 103
    assert subject.last_indeterminate_reason is None


def test_met_and_absent_reset_the_opposite_streak(profile: PolicyProfile) -> None:
    subject = machine(profile)
    feed(subject, "met")
    feed(subject, "met")
    assert feed(subject, "absent") is None
    assert (subject.met_streak, subject.absent_streak) == (0, 1)
    assert feed(subject, "met") is None
    assert (subject.met_streak, subject.absent_streak) == (1, 0)
    assert subject.state == "candidate"


@pytest.mark.parametrize("start", ["unknown", "clear", "candidate", "warning", "resolved"])
def test_tick_does_not_update_frame_counters_or_determinate_metadata(
    profile: PolicyProfile, start: RiskState
) -> None:
    subject = machine(profile)
    reach(subject, start)
    before = (
        subject.met_streak,
        subject.absent_streak,
        subject.last_determinate_mono_ns,
        subject.last_indeterminate_reason,
    )

    transition = subject.advance(now_mono_ns=1)

    assert (
        subject.met_streak,
        subject.absent_streak,
        subject.last_determinate_mono_ns,
        subject.last_indeterminate_reason,
    ) == before
    if start == "resolved":
        assert transition is not None and transition.state == "clear"
    else:
        assert transition is None and subject.state == start


@pytest.mark.parametrize("start", ["candidate", "warning", "clear"])
def test_input_timeout_uses_strict_boundary_and_never_resolves(
    profile: PolicyProfile, start: RiskState
) -> None:
    subject = machine(profile)
    reach(subject, start)
    event_id = subject.risk_event_id
    boundary = subject.params.unknown_after_ms * 1_000_000

    assert subject.advance(now_mono_ns=boundary) is None
    transition = subject.advance(now_mono_ns=boundary + 1)

    assert transition == StateTransition(start, "unknown", ("input_stale",), event_id)
    assert (subject.met_streak, subject.absent_streak) == (0, 0)
    assert subject.risk_event_id == event_id


@pytest.mark.parametrize("reason", ["quality_blocked", "track_lost"])
def test_indeterminate_timeout_preserves_the_last_frame_reason(
    profile: PolicyProfile, reason: ConditionReason
) -> None:
    subject = machine(profile)
    reach(subject, "warning")
    boundary = subject.params.unknown_after_ms * 1_000_000
    prior: ConditionReason = "track_lost" if reason == "quality_blocked" else "quality_blocked"
    assert (
        subject.advance(now_mono_ns=1, condition=condition("indeterminate", reason=prior)) is None
    )
    assert (
        subject.advance(now_mono_ns=boundary, condition=condition("indeterminate", reason=reason))
        is None
    )
    assert subject.state == "warning"
    transition = subject.advance(now_mono_ns=boundary + 1)

    assert transition is not None
    assert transition.state == "unknown"
    assert transition.reason_codes == ("input_stale", reason)
    assert subject.last_determinate_mono_ns == 0


def test_sensor_down_takes_priority_over_staleness(profile: PolicyProfile) -> None:
    subject = machine(profile)
    reach(subject, "warning")

    transition = subject.advance(
        now_mono_ns=subject.params.unknown_after_ms * 1_000_000 + 1,
        sensor_down={"camera_0": True},
    )

    assert transition is not None
    assert transition.reason_codes == ("sensor_down",)
    assert transition.state == "unknown"
    assert (subject.met_streak, subject.absent_streak) == (0, 0)


@pytest.mark.parametrize("verdict", ["met", "absent"])
def test_sensor_down_overrides_frame_transition_and_resets_updated_counters(
    profile: PolicyProfile, verdict: Verdict
) -> None:
    subject = machine(profile, confirm_frames=2, clear_frames=1)
    reach(subject, "candidate" if verdict == "met" else "warning")
    previous_state = subject.state

    transition = subject.advance(
        now_mono_ns=1, condition=condition(verdict), sensor_down={"camera_0": True}
    )

    assert transition == StateTransition(previous_state, "unknown", ("sensor_down",), "risk_0001")
    assert (subject.met_streak, subject.absent_streak) == (0, 0)


@pytest.mark.parametrize("verdict", ["met", "absent"])
def test_unknown_cannot_leave_while_required_sensor_is_down_or_on_recovery_tick(
    profile: PolicyProfile, verdict: Verdict
) -> None:
    subject = machine(profile)
    for index in range(4):
        assert (
            subject.advance(
                now_mono_ns=index,
                condition=condition(verdict),
                sensor_down={"camera_0": True},
            )
            is None
        )
    assert subject.state == "unknown"
    assert subject.risk_event_id is None
    assert subject.advance(now_mono_ns=4, sensor_down={"camera_0": False}) is None
    assert subject.state == "unknown"


@pytest.mark.parametrize("failed_stream", ["camera_0", "camera_1"])
def test_any_required_stream_down_is_sufficient(profile: PolicyProfile, failed_stream: str) -> None:
    subject = ScenarioStateMachine(
        profile.scenarios[0].model_copy(update={"required_streams": ["camera_0", "camera_1"]}),
        profile.params,
        SequentialIdFactory(),
    )
    reach(subject, "candidate")
    transition = subject.advance(now_mono_ns=1, sensor_down={failed_stream: True})
    assert transition is not None and transition.state == "unknown"


def test_unrelated_sensor_down_does_not_block_fresh_evidence(profile: PolicyProfile) -> None:
    subject = machine(profile)
    transition = subject.advance(
        now_mono_ns=0, condition=condition("met"), sensor_down={"other_camera": True}
    )
    assert transition is not None and transition.state == "candidate"


def test_fresh_frame_updates_determinate_time_before_applying_time_rules(
    profile: PolicyProfile,
) -> None:
    subject = machine(profile)
    reach(subject, "warning")
    now = subject.params.unknown_after_ms * 1_000_000 + 1

    assert feed(subject, "met", now=now) is None
    assert subject.state == "warning"
    assert subject.last_determinate_mono_ns == now
    assert subject.advance(now_mono_ns=now) is None


@pytest.mark.parametrize("verdict", ["met", "absent", "indeterminate", None])
def test_resolved_leaves_on_next_evaluation_in_one_transition(
    profile: PolicyProfile, verdict: Verdict | None
) -> None:
    subject = machine(profile)
    reach(subject, "resolved")
    transition = subject.advance(
        now_mono_ns=1, condition=condition(verdict) if verdict is not None else None
    )

    assert transition is not None and transition.previous_state == "resolved"
    if verdict == "met":
        assert transition.state == "candidate"
        assert transition.risk_event_id == subject.risk_event_id == "risk_0002"
    else:
        assert transition.state == "clear"
        assert transition.risk_event_id == "risk_0001"
        assert subject.risk_event_id is None


@pytest.mark.parametrize("trigger", ["sensor", "timeout"])
def test_resolved_unknown_closes_episode_before_recovery(
    profile: PolicyProfile, trigger: str
) -> None:
    subject = machine(profile)
    reach(subject, "resolved")
    now = subject.params.unknown_after_ms * 1_000_000 + 1 if trigger == "timeout" else 1
    transition = subject.advance(
        now_mono_ns=now, sensor_down={"camera_0": True} if trigger == "sensor" else None
    )

    assert transition is not None
    assert transition.state == "unknown"
    assert transition.reason_codes == (
        ("sensor_down",) if trigger == "sensor" else ("input_stale",)
    )
    assert transition.risk_event_id == "risk_0001"
    assert subject.risk_event_id is None
    recovered = feed(subject, "met", now=now + 1)
    assert recovered is not None and recovered.state == "candidate"
    assert recovered.risk_event_id == "risk_0002"


def test_unknown_recovery_requires_new_confirmation_and_retains_active_episode(
    profile: PolicyProfile,
) -> None:
    subject = machine(profile)
    reach(subject, "warning")
    now = subject.params.unknown_after_ms * 1_000_000 + 1
    subject.advance(now_mono_ns=now)
    assert (subject.met_streak, subject.absent_streak) == (0, 0)

    first = feed(subject, "met", now=now + 1)
    assert first is not None and first.state == "candidate"
    assert subject.met_streak == 1
    assert feed(subject, "met", now=now + 2) is None
    confirmed = feed(subject, "met", now=now + 3)
    assert confirmed is not None and confirmed.state == "warning"
    assert first.risk_event_id == confirmed.risk_event_id == "risk_0001"


def test_unknown_entry_discards_prior_absence_confirmations(profile: PolicyProfile) -> None:
    subject = machine(profile)
    reach(subject, "warning")
    for _ in range(subject.params.clear_frames - 1):
        assert feed(subject, "absent") is None
    now = subject.params.unknown_after_ms * 1_000_000 + 1
    subject.advance(now_mono_ns=now)
    assert subject.state == "unknown" and subject.absent_streak == 0

    for index in range(1, subject.params.clear_frames):
        assert feed(subject, "absent", now=now + index) is None
        assert subject.state == "unknown"
    closed = feed(subject, "absent", now=now + subject.params.clear_frames)
    assert closed is not None and closed.state == "clear"
    assert closed.risk_event_id == "risk_0001"
    assert subject.risk_event_id is None


def test_candidate_cancellation_ends_episode_and_next_candidate_gets_new_id(
    profile: PolicyProfile,
) -> None:
    subject = machine(profile)
    reach(subject, "candidate")
    for _ in range(subject.params.clear_frames - 1):
        assert feed(subject, "absent") is None
    closed = feed(subject, "absent")
    assert closed is not None and closed.risk_event_id == "risk_0001"
    assert subject.risk_event_id is None
    reopened = feed(subject, "met")
    assert reopened is not None and reopened.risk_event_id == "risk_0002"


def test_alert_ttl_is_not_a_warning_state_timeout(profile: PolicyProfile) -> None:
    params = profile.params.model_copy(update={"warning_ttl_ms": 1})
    subject = ScenarioStateMachine(profile.scenarios[0], params, SequentialIdFactory())
    reach(subject, "warning")

    assert subject.advance(now_mono_ns=1_000_001) is None
    assert subject.state == "warning"


def test_scenarios_keep_independent_state_with_shared_id_factory(profile: PolicyProfile) -> None:
    ids = SequentialIdFactory()
    first = ScenarioStateMachine(profile.scenarios[0], profile.params, ids)
    second = ScenarioStateMachine(profile.scenarios[1], profile.params, ids)
    reach(first, "warning")

    assert second.state == "unknown"
    assert second.met_streak == 0
    transition = feed(second, "met")
    assert transition is not None and transition.risk_event_id == "risk_0002"
    assert first.state == "warning" and first.risk_event_id == "risk_0001"


def test_same_injected_inputs_reproduce_transitions_and_episode_ids(profile: PolicyProfile) -> None:
    def replay() -> list[StateTransition | None]:
        subject = machine(profile, confirm_frames=2, clear_frames=1)
        stale = subject.params.unknown_after_ms * 1_000_000 + 1
        return [
            feed(subject, "met"),
            feed(subject, "met"),
            subject.advance(now_mono_ns=stale),
            feed(subject, "met", now=stale + 1),
            feed(subject, "met", now=stale + 2),
            feed(subject, "absent", now=stale + 3),
            subject.advance(now_mono_ns=stale + 4),
            feed(subject, "met", now=stale + 5),
        ]

    assert replay() == replay()
