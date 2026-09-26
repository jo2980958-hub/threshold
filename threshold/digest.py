"""The weekly digest: the facts, computed by rules, before anything writes prose.

This module is the reason a model is safe to use in this product at all. Every
number a family member reads in the weekly summary is computed here, by code
anybody can read, from the log and the schedule. `narrate.py` then asks Amazon
Bedrock to turn these facts into a paragraph. The model describes; the rules
decide. A model cannot make a visit into a non-visit, because by the time it is
called, the comparison has already been made, already been phrased by
`phrasing.py`, and is being handed over as a finished sentence.

`allowed_numbers` is the machinery that keeps it that way: the set of every
number these facts contain, which `phrasing.assert_model_output_safe` checks the
model's prose against. A summary that contains a number the rules never computed
is rejected and the deterministic fallback is used instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo

from . import phrasing
from .log import ArrivalLog
from .schedule import ScheduleRule, VisitComparison, compare_visit, visits_for_date


def _clock(hour: int) -> str:
    """"17" is a number a model will happily write as "17 hours". "5pm" is not.

    Handing the model a readable clock time rather than a bare hour is the
    difference between a sentence a family member reads and one they parse.
    """
    suffix = "am" if hour < 12 else "pm"
    display = hour % 12 or 12
    return f"{display}{suffix}"


@dataclass(frozen=True)
class DayFacts:
    day: date
    event_count: int
    first_activity: datetime | None
    last_activity: datetime | None

    @property
    def is_quiet(self) -> bool:
        return self.event_count == 0


@dataclass(frozen=True)
class WeeklyDigest:
    """One household's week, entirely in numbers the rules produced."""

    household_name: str
    week_start: date
    week_end: date
    door_count: int
    total_events: int
    days: tuple[DayFacts, ...]
    visits_scheduled: int
    visits_with_activity: int
    visits_without_recorded_arrival: int
    visits_not_comparable: int
    quiet_days: tuple[date, ...]
    busiest_day: date | None
    earliest_activity_hour: int | None
    latest_activity_hour: int | None
    comparisons: tuple[VisitComparison, ...] = field(default=())

    @property
    def days_covered(self) -> int:
        return len(self.days)

    def facts_block(self) -> str:
        """The digest as flat lines. This is exactly what the model is shown.

        Written as plain statements rather than JSON because the model's job is
        rewriting, and handing it prose to rewrite keeps it from treating the
        structure as something to reason about.
        """
        return "\n".join([f"Household name: {self.household_name}", *self._computed_lines()])

    def _computed_lines(self) -> list[str]:
        """The facts block minus the household's own name.

        Split out because these two halves have different provenance and only
        one of them may widen the grounding allowlist. Everything here was
        computed by rules in this repository from the log and the schedule. The
        household name was typed by a person.
        """
        lines = [
            f"Week covered: {self.week_start.isoformat()} to {self.week_end.isoformat()}",
            # Stated rather than left implicit, so that a sentence like "2 of
            # the 7 days" is grounded in a figure the rules computed. Before,
            # 7 was allowed because it was small, which is not a reason.
            f"Days covered by this record: {(self.week_end - self.week_start).days + 1}",
            f"Doors recording in this household: {self.door_count}",
            f"Total front-door events recorded in the week: {self.total_events}",
            f"Days in the week with no front-door activity at all: {len(self.quiet_days)}",
            f"Care visits booked in the week: {self.visits_scheduled}",
            f"Booked visits where front-door activity was recorded in the window: "
            f"{self.visits_with_activity}",
            f"Booked visits where no arrival was recorded at the front door: "
            f"{self.visits_without_recorded_arrival}",
            f"Booked visits that could not be compared for lack of door history: "
            f"{self.visits_not_comparable}",
        ]
        if self.earliest_activity_hour is not None:
            lines.append(
                f"Earliest time of day any door activity was recorded: "
                f"{_clock(self.earliest_activity_hour)}"
            )
        if self.latest_activity_hour is not None:
            lines.append(
                f"Latest time of day any door activity was recorded: "
                f"{_clock(self.latest_activity_hour)}"
            )
        for day in self.days:
            lines.append(
                f"On {day.day.isoformat()} the doors recorded {day.event_count} events."
            )
        return lines

    def allowed_numbers(self) -> set[str]:
        """Every number the model is permitted to write, from these facts alone.

        Only the facts block. This used to also admit 0 to 7 unconditionally,
        on the reasoning that a small count cannot assert anything the facts do
        not already contain. That reasoning is wrong, and it was the hole in the
        anti-hallucination gate: "front-door activity was recorded during 6 of
        the visits" is a fabricated finding about a household's care, and 6 is a
        small number. Whether a figure is grounded has nothing to do with how
        large it is; it has to do with whether the rules computed it.

        The seven days of a week, and the day counts that go with them, are
        already in the facts block for any real digest, so the readable prose
        this was meant to permit still passes. What no longer passes is a count
        the rules never produced.

        Nothing user-supplied and nothing model-supplied may widen this set.
        That is why it is built from `_computed_lines()` and not from
        `facts_block()`: the block the model is shown opens with the household
        name, which is free text a person typed on the settings page, and a
        household calling itself **Flat 43** used to put `43` into the
        anti-hallucination allowlist. A sentence is grounded because the rules
        computed the figure, not because it appears somewhere in the prompt.
        """
        return phrasing.numbers_in("\n".join(self._computed_lines()))

    def as_dict(self) -> dict:
        return {
            "household_name": self.household_name,
            "week_start": self.week_start.isoformat(),
            "week_end": self.week_end.isoformat(),
            "door_count": self.door_count,
            "total_events": self.total_events,
            "days_covered": self.days_covered,
            "visits_scheduled": self.visits_scheduled,
            "visits_with_activity": self.visits_with_activity,
            "visits_without_recorded_arrival": self.visits_without_recorded_arrival,
            "visits_not_comparable": self.visits_not_comparable,
            "quiet_days": [d.isoformat() for d in self.quiet_days],
            "busiest_day": self.busiest_day.isoformat() if self.busiest_day else None,
            "per_day": [
                {"day": d.day.isoformat(), "event_count": d.event_count} for d in self.days
            ],
        }


def week_start_for(day: date) -> date:
    """The Monday of `day`'s week."""
    return day - timedelta(days=day.weekday())


def build(
    household_name: str,
    log: ArrivalLog,
    rules: list[ScheduleRule],
    week_start: date,
    history_available_since: datetime | None,
    door_count: int = 1,
    tz: tzinfo = timezone.utc,
) -> WeeklyDigest:
    """Compute one household's week. No model is involved at any point here."""
    week_end = week_start + timedelta(days=6)

    days: list[DayFacts] = []
    quiet: list[date] = []
    comparisons: list[VisitComparison] = []
    total = 0

    for offset in range(7):
        day = week_start + timedelta(days=offset)
        # A household's day is its own day, not UTC's. In a zone west of
        # Greenwich the two disagree by hours, which would put a late evening
        # event on tomorrow's count and a "quiet day" where there was not one.
        day_start = datetime.combine(day, time.min, tzinfo=tz)
        day_end = datetime.combine(day, time.max, tzinfo=tz)
        events = log.events_between(day_start, day_end)
        total += len(events)
        days.append(
            DayFacts(
                day=day,
                event_count=len(events),
                first_activity=events[0].occurred_at if events else None,
                last_activity=events[-1].occurred_at if events else None,
            )
        )
        if not events:
            quiet.append(day)
        for visit in visits_for_date(rules, day, tz):
            comparisons.append(compare_visit(visit, log, history_available_since))

    with_activity = sum(1 for c in comparisons if c.status_text == phrasing.ARRIVAL_RECORDED)
    without = sum(1 for c in comparisons if c.status_text == phrasing.NO_ARRIVAL_RECORDED)
    not_comparable = sum(
        1 for c in comparisons if c.status_text == phrasing.NOT_ENOUGH_HISTORY
    )

    active_days = [d for d in days if d.event_count > 0]
    busiest = max(active_days, key=lambda d: d.event_count).day if active_days else None
    earliest = min((d.first_activity.hour for d in active_days), default=None)
    latest = max((d.last_activity.hour for d in active_days), default=None)

    return WeeklyDigest(
        household_name=household_name,
        week_start=week_start,
        week_end=week_end,
        door_count=door_count,
        total_events=total,
        days=tuple(days),
        visits_scheduled=len(comparisons),
        visits_with_activity=with_activity,
        visits_without_recorded_arrival=without,
        visits_not_comparable=not_comparable,
        quiet_days=tuple(quiet),
        busiest_day=busiest,
        earliest_activity_hour=earliest,
        latest_activity_hour=latest,
        comparisons=tuple(comparisons),
    )


def deterministic_summary(digest: WeeklyDigest) -> str:
    """The summary written without a model. Always available, always correct.

    This is what a family member reads when Bedrock is unreachable, slow, or
    returns something that fails the phrasing gate. It is deliberately a
    complete answer rather than an error message: the product's value does not
    depend on the model, and a demo that dies without network is a demo that
    dies in front of a judge.
    """
    parts = [
        f"Between {digest.week_start.isoformat()} and {digest.week_end.isoformat()}, "
        f"the doors at {digest.household_name} recorded {digest.total_events} events."
    ]
    if digest.quiet_days:
        parts.append(
            f"{len(digest.quiet_days)} of the 7 days recorded no front-door activity at all."
        )
    else:
        parts.append("Every day in the week recorded some front-door activity.")

    if digest.visits_scheduled:
        parts.append(
            f"Of {digest.visits_scheduled} booked care visits, "
            f"{digest.visits_with_activity} had front-door activity recorded inside the "
            f"booked window, and {digest.visits_without_recorded_arrival} had no arrival "
            f"recorded at the front door."
        )
        if digest.visits_not_comparable:
            parts.append(
                f"{digest.visits_not_comparable} booked visits fell before this "
                f"household's door history begins and could not be compared."
            )
    else:
        parts.append("No care visits were booked in this week.")

    parts.append(phrasing.BOUNDARY_NOTE)
    # The household's own name is masked out of the check rather than checked.
    # It is the only span of this sentence this app did not write, and a real
    # address on **No-Show Lane** used to raise a PhrasingViolation out of here
    # -- through `narrate.summarise`, whose contract is that it never raises,
    # and on to the browser with the developer message and the offending text
    # in it.
    return phrasing.assert_safe(" ".join(parts), user_spans=(digest.household_name,))
