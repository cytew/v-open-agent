"""Pure three-valued scenario evaluation of assembled frames (SPEC 4.5)."""

from dataclasses import dataclass
from typing import Literal

from voa.contracts.enums import SourceKind, Verdict
from voa.contracts.observation import Detection, DetectionPayload, Observation, Track, TrackPayload
from voa.contracts.types import BBox
from voa.engine.profile import PolicyParams, ScenarioProfile

ConditionReason = Literal["quality_blocked", "track_lost", "condition_observed", "condition_absent"]


@dataclass(frozen=True)
class ConditionResult:
    verdict: Verdict
    evidence_ids: tuple[str, ...]
    reason: ConditionReason


def overlap(bbox: BBox, roi: BBox) -> float:
    """Return the fraction of the validated target box covered by the ROI."""
    width = max(0.0, min(bbox[2], roi[2]) - max(bbox[0], roi[0]))
    height = max(0.0, min(bbox[3], roi[3]) - max(bbox[1], roi[1]))
    return width * height / ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))


@dataclass(frozen=True)
class ScenarioEvaluator:
    """Evaluate one scenario without changing observations, policy or engine state.

    FrameAssembler supplies complete, fresh frames. Missing or malformed frames
    are caller errors, never evidence of absence. No clock or freshness I/O is
    performed here.
    """

    scenario: ScenarioProfile
    params: PolicyParams

    def evaluate(self, observations: tuple[Observation, ...]) -> ConditionResult:
        frame = self._ordered_frame(observations)
        evidence_ids = tuple(observation.observation_id for observation in frame)
        if any(
            observation.quality_flags.intersection(self.scenario.blocking_flags)
            for observation in frame
        ):
            return ConditionResult("indeterminate", evidence_ids, "quality_blocked")

        # Required kinds are detection only, or detection followed by track (4.9).
        payload = frame[-1].payload
        targets: list[Detection] | list[Track]
        if isinstance(payload, TrackPayload):
            targets = payload.tracks
        elif isinstance(payload, DetectionPayload):
            targets = payload.detections
        else:
            raise ValueError("Expected a complete vision frame")

        qualifying = any(self._qualifies(target) for target in targets)
        if not qualifying and any(
            isinstance(target, Track) and target.quality == "lost" and self._in_scope(target)
            for target in targets
        ):
            return ConditionResult("indeterminate", evidence_ids, "track_lost")
        if qualifying:
            return ConditionResult("met", evidence_ids, "condition_observed")
        return ConditionResult("absent", evidence_ids, "condition_absent")

    def _in_scope(self, target: Detection | Track) -> bool:
        return (
            target.label in self.scenario.labels
            and overlap(target.bbox, self.scenario.roi) >= self.params.roi_min_overlap
        )

    def _qualifies(self, target: Detection | Track) -> bool:
        return (
            self._in_scope(target)
            and target.score >= self.params.min_score
            and (
                not isinstance(target, Track)
                or (
                    target.quality != "lost" and target.motion_class in self.scenario.motion_classes
                )
            )
        )

    def _ordered_frame(self, observations: tuple[Observation, ...]) -> tuple[Observation, ...]:
        by_kind: dict[SourceKind, Observation] = {}
        identity: tuple[str, str, str] | None = None
        for observation in observations:
            payload = observation.payload
            if (
                not isinstance(payload, DetectionPayload | TrackPayload)
                or observation.source_kind not in self.scenario.required_kinds
                or observation.stream_id not in self.scenario.required_streams
                or observation.source_kind in by_kind
            ):
                raise ValueError("Expected one observation per required vision kind")
            current = (observation.session_id, observation.device_id, payload.frame_id)
            if identity is not None and current != identity:
                raise ValueError("Expected observations from one session, device and frame")
            identity = current
            by_kind[observation.source_kind] = observation
        if set(by_kind) != set(self.scenario.required_kinds):
            raise ValueError("Expected a complete vision frame")
        return tuple(by_kind[kind] for kind in self.scenario.required_kinds)
