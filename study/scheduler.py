from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from study.config import StudyConfig


@dataclass(frozen=True)
class ScheduleDecision:
    scheduler_name: str
    prior_box: int
    new_box: int
    previous_interval_days: int | None
    new_interval_days: int
    next_review_at: str
    reason_codes: tuple[str, ...]
    reason_summary: str


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def to_iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


def initial_card_state(config: StudyConfig, *, now: datetime | None = None) -> ScheduleDecision:
    reviewed_at = now or utc_now()
    return ScheduleDecision(
        scheduler_name=config.scheduler,
        prior_box=1,
        new_box=1,
        previous_interval_days=None,
        new_interval_days=0,
        next_review_at=to_iso(reviewed_at),
        reason_codes=("new_card", "due_now"),
        reason_summary="New cards are due immediately so they can enter the next review session.",
    )


def fallback_schedule(
    config: StudyConfig,
    *,
    prior_box: int,
    result: str,
    now: datetime | None = None,
) -> ScheduleDecision:
    if result not in {"pass", "fail", "incomplete"}:
        raise ValueError("result must be `pass`, `fail`, or `incomplete`")

    reviewed_at = now or utc_now()
    previous_interval = config.box_intervals[prior_box]

    if result == "pass":
        new_box = min(5, prior_box + 1)
        new_interval = config.box_intervals[new_box]
        reason_codes = ("result_pass", f"promote_box_{new_box}")
        reason_summary = (
            f"Passed review promoted the card from box {prior_box} to box {new_box}, "
            f"so the next review is in {new_interval} day(s)."
        )
    elif result == "fail":
        new_box = 1
        new_interval = config.box_intervals[new_box]
        reason_codes = ("result_fail", "reset_to_box_1")
        reason_summary = (
            f"Failed review reset the card to box 1, so the next review is in {new_interval} day(s)."
        )
    else:
        new_box = prior_box
        # Incomplete reviews should come back quickly without being scored as failures.
        new_interval = 1
        reason_codes = ("result_incomplete", "keep_box", "reschedule_soon")
        reason_summary = (
            f"Incomplete review kept the card in box {new_box} and scheduled it again in {new_interval} day(s)."
        )

    next_review_at = reviewed_at + timedelta(days=new_interval)
    return ScheduleDecision(
        scheduler_name=config.scheduler,
        prior_box=prior_box,
        new_box=new_box,
        previous_interval_days=previous_interval,
        new_interval_days=new_interval,
        next_review_at=to_iso(next_review_at),
        reason_codes=reason_codes,
        reason_summary=reason_summary,
    )
