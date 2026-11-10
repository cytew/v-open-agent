"""TASK-08 scenario verdicts and filtering (T-R02, T-R12, T-R15)."""

from pathlib import Path

import pytest

from tests.builders import build_frame
from voa.config.profile_loader import load_profile
from voa.contracts.enums import MotionClass, QualityFlag, TrackQuality, Verdict
from voa.contracts.observation import Detection, Observation, Track
from voa.contracts.types import BBox
from voa.engine.evaluator import ConditionReason, ConditionResult, ScenarioEvaluator, overlap
from voa.engine.profile import PolicyProfile
from voa.engine.window import FrameAssembler, ObservationWindow


@pytest.fixture
def profile() -> PolicyProfile:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "profiles" / "valid.yaml"
    result = load_profile(path)
    assert isinstance(result, PolicyProfile)
    return result


def evaluator(profile: PolicyProfile, *, tracked: bool) -> ScenarioEvaluator:
    scenario = profile.scenarios[0 if tracked else 1].model_copy(
        update={"roi": (0.25, 0.0, 0.75, 1.0)}
    )
    params = profile.params.model_copy(update={"roi_min_overlap": 0.5})
    return ScenarioEvaluator(scenario, params)


def target(
    tracked: bool,
    *,
    label: str | None = None,
    score: float = 0.8,
    bbox: BBox = (0.25, 0.0, 0.75, 1.0),
    quality: TrackQuality = "good",
    motion_class: MotionClass = "moving",
    track_id: str = "track_1",
) -> Detection | Track:
    if tracked:
        return Track(
            track_id=track_id,
            label=label if label is not None else "person",
            bbox=bbox,
            score=score,
            quality=quality,
            motion_class=motion_class,
            age_frames=1,
        )
    return Detection(label=label if label is not None else "obstacle", bbox=bbox, score=score)


def frame_for(
    tracked: bool,
    *targets: Detection | Track,
    flags: frozenset[QualityFlag] = frozenset(),
) -> tuple[Observation, ...]:
    detection, track = build_frame(
        detections=[item for item in targets if isinstance(item, Detection)],
        tracks=[item for item in targets if isinstance(item, Track)],
        quality_flags=flags,
    )
    return (detection, track) if tracked else (detection,)


@pytest.mark.parametrize(
    ("bbox", "roi", "expected"),
    [
        ((0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 1.0, 1.0), 1.0),
        ((0.25, 0.25, 0.75, 0.75), (0.0, 0.0, 1.0, 1.0), 1.0),
        ((0.0, 0.0, 1.0, 1.0), (0.25, 0.25, 0.75, 0.75), 0.25),
        ((0.0, 0.0, 0.5, 0.5), (0.25, 0.25, 0.75, 0.75), 0.25),
        ((0.0, 0.0, 0.25, 0.25), (0.5, 0.5, 1.0, 1.0), 0.0),
        ((0.0, 0.0, 0.5, 1.0), (0.5, 0.0, 1.0, 1.0), 0.0),
    ],
)
def test_overlap_is_intersection_divided_by_target_area(
    bbox: BBox, roi: BBox, expected: float
) -> None:
    assert overlap(bbox, roi) == pytest.approx(expected)


@pytest.mark.parametrize("tracked", [False, True])
@pytest.mark.parametrize(
    ("verdict", "reason"),
    [
        ("met", "condition_observed"),
        ("absent", "condition_absent"),
        ("indeterminate", "quality_blocked"),
    ],
)
def test_both_scenarios_produce_all_three_verdicts(
    profile: PolicyProfile, tracked: bool, verdict: Verdict, reason: ConditionReason
) -> None:
    candidates = [target(tracked)] if verdict == "met" else []
    flags: frozenset[QualityFlag] = (
        frozenset({"occluded"}) if verdict == "indeterminate" else frozenset()
    )
    frame = frame_for(tracked, *candidates, flags=flags)

    assert evaluator(profile, tracked=tracked).evaluate(frame) == ConditionResult(
        verdict, tuple(item.observation_id for item in frame), reason
    )


@pytest.mark.parametrize("tracked", [False, True])
@pytest.mark.parametrize(
    ("score", "verdict"), [(0.499999, "absent"), (0.5, "met"), (0.500001, "met")]
)
def test_min_score_boundary_is_inclusive(
    profile: PolicyProfile, tracked: bool, score: float, verdict: Verdict
) -> None:
    frame = frame_for(tracked, target(tracked, score=score))
    assert evaluator(profile, tracked=tracked).evaluate(frame).verdict == verdict


@pytest.mark.parametrize("tracked", [False, True])
@pytest.mark.parametrize(
    ("right", "verdict"), [(0.499999, "absent"), (0.5, "met"), (0.500001, "met")]
)
def test_roi_overlap_boundary_is_inclusive(
    profile: PolicyProfile, tracked: bool, right: float, verdict: Verdict
) -> None:
    frame = frame_for(tracked, target(tracked, bbox=(0.0, 0.0, right, 1.0)))
    assert evaluator(profile, tracked=tracked).evaluate(frame).verdict == verdict


@pytest.mark.parametrize("tracked", [False, True])
def test_unlisted_label_is_absent(profile: PolicyProfile, tracked: bool) -> None:
    frame = frame_for(tracked, target(tracked, label="unlisted"))
    assert evaluator(profile, tracked=tracked).evaluate(frame).verdict == "absent"


@pytest.mark.parametrize(
    ("motion", "verdict"), [("moving", "met"), ("static", "absent"), ("unknown", "absent")]
)
def test_track_motion_filter(profile: PolicyProfile, motion: MotionClass, verdict: Verdict) -> None:
    frame = frame_for(True, target(True, motion_class=motion))
    assert evaluator(profile, tracked=True).evaluate(frame).verdict == verdict


@pytest.mark.parametrize(
    ("quality", "verdict"), [("good", "met"), ("degraded", "met"), ("lost", "indeterminate")]
)
def test_track_quality_filter(
    profile: PolicyProfile, quality: TrackQuality, verdict: Verdict
) -> None:
    frame = frame_for(True, target(True, quality=quality))
    assert evaluator(profile, tracked=True).evaluate(frame).verdict == verdict


@pytest.mark.parametrize(
    ("right", "verdict", "reason"),
    [
        (0.499999, "absent", "condition_absent"),
        (0.5, "indeterminate", "track_lost"),
        (0.500001, "indeterminate", "track_lost"),
    ],
)
def test_lost_track_uses_label_and_overlap_without_score_or_motion_filter(
    profile: PolicyProfile,
    right: float,
    verdict: Verdict,
    reason: ConditionReason,
) -> None:
    frame = frame_for(
        True,
        target(
            True,
            bbox=(0.0, 0.0, right, 1.0),
            score=0.01,
            quality="lost",
            motion_class="unknown",
        ),
    )
    result = evaluator(profile, tracked=True).evaluate(frame)
    assert (result.verdict, result.reason) == (verdict, reason)


def test_unlisted_lost_track_does_not_block_absence(profile: PolicyProfile) -> None:
    frame = frame_for(True, target(True, label="unlisted", quality="lost"))
    result = evaluator(profile, tracked=True).evaluate(frame)
    assert (result.verdict, result.reason) == ("absent", "condition_absent")


@pytest.mark.parametrize("has_qualifying", [False, True])
def test_lost_track_blocks_only_when_no_qualifying_track_exists(
    profile: PolicyProfile, has_qualifying: bool
) -> None:
    frame = frame_for(
        True,
        target(True, quality="lost"),
        target(True, score=0.8 if has_qualifying else 0.1, track_id="track_2"),
    )
    result = evaluator(profile, tracked=True).evaluate(frame)
    assert (result.verdict, result.reason) == (
        ("met", "condition_observed") if has_qualifying else ("indeterminate", "track_lost")
    )


@pytest.mark.parametrize("flagged_index", [0, 1])
@pytest.mark.parametrize("has_qualifying", [False, True])
def test_blocking_flags_on_either_kind_take_priority_over_lost_and_met(
    profile: PolicyProfile, flagged_index: int, has_qualifying: bool
) -> None:
    frame = list(frame_for(True, target(True, quality="good" if has_qualifying else "lost")))
    frame[flagged_index] = frame[flagged_index].model_copy(
        update={"quality_flags": frozenset({"occluded"})}
    )
    result = evaluator(profile, tracked=True).evaluate(tuple(frame))
    assert (result.verdict, result.reason) == ("indeterminate", "quality_blocked")


@pytest.mark.parametrize("tracked", [False, True])
def test_nonblocking_flag_does_not_suppress_a_target(profile: PolicyProfile, tracked: bool) -> None:
    frame = frame_for(tracked, target(tracked), flags=frozenset({"low_light"}))
    assert evaluator(profile, tracked=tracked).evaluate(frame).verdict == "met"


def test_camera_motion_flag_blocks_only_the_moving_scenario(profile: PolicyProfile) -> None:
    frame = frame_for(
        True, target(False), target(True), flags=frozenset({"camera_motion_uncompensated"})
    )
    moving = evaluator(profile, tracked=True)
    obstacle = evaluator(profile, tracked=False)
    before = tuple(item.model_dump() for item in frame)
    policy_before = profile.model_dump()
    blocked = moving.evaluate(frame)

    assert (blocked.verdict, blocked.reason) == ("indeterminate", "quality_blocked")
    assert obstacle.evaluate((frame[0],)).verdict == "met"
    assert moving.evaluate(frame) == blocked
    assert tuple(item.model_dump() for item in frame) == before
    assert profile.model_dump() == policy_before


@pytest.mark.parametrize("has_track", [False, True])
def test_tracking_scenario_uses_tracks_and_keeps_detection_as_evidence(
    profile: PolicyProfile, has_track: bool
) -> None:
    frame = frame_for(True, target(True) if has_track else target(False, label="person"))
    result = evaluator(profile, tracked=True).evaluate(frame)

    assert result.verdict == ("met" if has_track else "absent")
    assert result.evidence_ids == ("obs_0001", "obs_0002")


def test_input_order_does_not_change_result_or_evidence_order(profile: PolicyProfile) -> None:
    frame = frame_for(True, target(True))
    subject = evaluator(profile, tracked=True)

    assert subject.evaluate(tuple(reversed(frame))) == subject.evaluate(frame)
    assert subject.evaluate(frame).evidence_ids == ("obs_0001", "obs_0002")


@pytest.mark.parametrize("malformed", ["empty", "incomplete", "duplicate_kind", "other_session"])
def test_missing_or_malformed_frame_is_never_treated_as_absence(
    profile: PolicyProfile, malformed: str
) -> None:
    detection, track = build_frame()
    cases: dict[str, tuple[Observation, ...]] = {
        "empty": (),
        "incomplete": (detection,),
        "duplicate_kind": (detection, detection),
        "other_session": (detection, track.model_copy(update={"session_id": "other"})),
    }

    with pytest.raises(ValueError, match="Expected"):
        evaluator(profile, tracked=True).evaluate(cases[malformed])


@pytest.mark.parametrize("delayed", [False, True])
def test_assembly_supplies_only_complete_fresh_frames_for_evaluation(
    profile: PolicyProfile, delayed: bool
) -> None:
    subject = evaluator(profile, tracked=True)
    window = ObservationWindow(subject.params)
    assembler = FrameAssembler(subject.scenario, window)

    def process(observation: Observation, now: int) -> ConditionResult | None:
        window.append(observation, now_mono_ns=now)
        frame = assembler.add(observation, now_mono_ns=now)
        return subject.evaluate(frame) if frame is not None else None

    detection, _ = build_frame()
    assert process(detection, 0) is None
    now = subject.params.staleness_ms * 1_000_000 + 1 if delayed else 0
    candidate = target(True)
    assert isinstance(candidate, Track)
    _, track = build_frame(tracks=[candidate], server_received_mono_ns=now)
    result = process(track, now)
    if delayed:
        assert result is None
    else:
        assert result == ConditionResult("met", ("obs_0001", "obs_0002"), "condition_observed")
