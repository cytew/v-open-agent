"""Per-scenario transition table and episode lifetime (SPEC 4.6)."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from voa.contracts.decision import ReasonCode
from voa.contracts.enums import RiskState
from voa.engine.evaluator import ConditionResult
from voa.engine.ports import IdFactory
from voa.engine.profile import PolicyParams, ScenarioProfile


@dataclass(frozen=True)
class StateTransition:
    """Internal transition; closing transitions retain the episode's final ID.

    DecisionEngine adds evidence, timestamps and alerts in stage 6 (TASK-10).
    """

    previous_state: RiskState
    state: RiskState
    reason_codes: tuple[ReasonCode, ...]
    risk_event_id: str | None


@dataclass
class ScenarioStateMachine:
    """Apply at most one transition using injected time and IDs only."""

    scenario: ScenarioProfile
    params: PolicyParams
    ids: IdFactory
    state: RiskState = field(default="unknown", init=False)
    risk_event_id: str | None = field(default=None, init=False)
    met_streak: int = field(default=0, init=False)
    absent_streak: int = field(default=0, init=False)
    last_determinate_mono_ns: int | None = field(default=None, init=False)
    last_indeterminate_reason: ReasonCode | None = field(default=None, init=False)

    def advance(
        self,
        *,
        now_mono_ns: int,
        condition: ConditionResult | None = None,
        sensor_down: Mapping[str, bool] | None = None,
    ) -> StateTransition | None:
        """A frame updates counters first; no frame applies only rows 1/2/4.

        The engine enforces monotonic time and supplies fresh frame results.
        Absence of a frame never counts as an absent condition.
        """
        if condition is not None:
            self._update_counters(condition, now_mono_ns=now_mono_ns)
        down = sensor_down is not None and any(
            sensor_down.get(stream, False) for stream in self.scenario.required_streams
        )

        if self.state != "unknown":
            # Rows 1 and 2 take precedence over every frame-based transition.
            if down:
                return self._transition(
                    "unknown", ("sensor_down",), end_episode=self.state == "resolved"
                )
            if (
                self.last_determinate_mono_ns is not None
                and now_mono_ns - self.last_determinate_mono_ns
                > self.params.unknown_after_ms * 1_000_000
            ):
                reasons: tuple[ReasonCode, ...] = ("input_stale",)
                if self.last_indeterminate_reason is not None:
                    reasons += (self.last_indeterminate_reason,)
                return self._transition("unknown", reasons, end_episode=self.state == "resolved")

        if self.state == "resolved":
            if condition is not None and self.met_streak >= 1:  # Row 3.
                return self._transition("candidate", ("condition_observed",), start_episode=True)
            return self._transition("clear", ("episode_closed",), end_episode=True)  # Row 4.

        if condition is None:
            return None
        if self.state == "clear" and self.met_streak >= 1:  # Row 5.
            return self._transition("candidate", ("condition_observed",), start_episode=True)
        if self.state == "candidate":
            if self.met_streak >= self.params.confirm_frames:  # Row 6.
                return self._transition("warning", ("temporal_confirmation_passed",))
            if self.absent_streak >= self.params.clear_frames:  # Row 7.
                return self._transition("clear", ("condition_absent_confirmed",), end_episode=True)
        elif self.state == "warning" and self.absent_streak >= self.params.clear_frames:  # Row 8.
            return self._transition("resolved", ("condition_absent_confirmed",))
        elif self.state == "unknown" and not down:
            if self.met_streak >= 1:  # Row 9.
                return self._transition(
                    "candidate",
                    ("fresh_evidence_returned",),
                    start_episode=self.risk_event_id is None,
                )
            if self.absent_streak >= self.params.clear_frames:  # Row 10.
                return self._transition("clear", ("condition_absent_confirmed",), end_episode=True)
        return None

    def _update_counters(self, condition: ConditionResult, *, now_mono_ns: int) -> None:
        if condition.verdict == "met":
            self.met_streak += 1
            self.absent_streak = 0
            self.last_determinate_mono_ns = now_mono_ns
            self.last_indeterminate_reason = None
        elif condition.verdict == "absent":
            self.absent_streak += 1
            self.met_streak = 0
            self.last_determinate_mono_ns = now_mono_ns
            self.last_indeterminate_reason = None
        else:
            if condition.reason != "quality_blocked" and condition.reason != "track_lost":
                raise ValueError("Expected an indeterminate frame reason")
            self.met_streak = self.absent_streak = 0
            self.last_indeterminate_reason = condition.reason

    def _transition(
        self,
        state: RiskState,
        reason_codes: tuple[ReasonCode, ...],
        *,
        start_episode: bool = False,
        end_episode: bool = False,
    ) -> StateTransition:
        previous_state = self.state
        if start_episode:
            self.risk_event_id = self.ids.new("risk")
        event_id = self.risk_event_id
        if end_episode:
            self.risk_event_id = None
        self.state = state
        if state == "unknown":
            self.met_streak = self.absent_streak = 0
        return StateTransition(previous_state, state, reason_codes, event_id)
