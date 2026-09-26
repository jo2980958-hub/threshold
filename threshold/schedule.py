"""Care visit schedules, and their comparison against the arrival log.

This is where "no arrival was recorded" versus "the carer did not come" gets
decided in code, not just in prose. Every outcome string comes from
`phrasing.py`'s fixed set and is checked with `phrasing.assert_safe` before
it can leave this module.

Schedules are recurring rules, not a fixture. A home-care package is booked as
"weekdays at 08:00 for 45 minutes", runs for months, and changes when the
package changes. `ScheduleRule` holds that, `visits_for_date` expands it into
the concrete windows for one day, and `active_from` / `active_until` mean a
package change does not rewrite history: last month's days still expand against
last month's rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo

from . import phrasing
from .log import ArrivalLog

# How much slack either side of a scheduled window still counts as "during"
# the visit. Carers do not arrive on the second.
DEFAULT_GRACE = timedelta(minutes=5)

MONDAY_FIRST_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class CareVisit:
    label: str
    scheduled_start: datetime
    scheduled_end: datetime


@dataclass(frozen=True)
class ScheduleRule:
    """A recurring booked visit, as an agency would actually write it down."""

    label: str
    # Monday is 0, matching datetime.weekday().
    days_of_week: tuple[int, ...]
    start_time: time
    end_time: time
    active_from: date | None = None
    active_until: date | None = None
    rule_id: int | None = None

    def applies_on(self, day: date) -> bool:
        if day.weekday() not in self.days_of_week:
            return False
        if self.active_from and day < self.active_from:
            return False
        if self.active_until and day > self.active_until:
            return False
        return True

    def visit_for(self, day: date, tz: tzinfo = timezone.utc) -> CareVisit:
        """Expand to a concrete window, in the household's own wall clock.

        `start_time` is a local time, because "the morning call is at eight" is
        what an agency books and what a clock change moves. Combining it with
        UTC instead would put every booked window an hour out for half the year
        in any zone that observes summer time, and an hour of drift against a
        45-minute window is a "no arrival was recorded" row for a visit that
        happened exactly on time. That row is a false accusation against a
        named, low-paid person, arriving as a timezone bug.
        """
        start = datetime.combine(day, self.start_time, tzinfo=tz)
        end = datetime.combine(day, self.end_time, tzinfo=tz)
        if end <= start:
            # A window written as 22:30 to 00:15 runs past midnight. Land it on
            # the next day rather than producing a negative-length visit that
            # would silently never match any event.
            end += timedelta(days=1)
        return CareVisit(label=self.label, scheduled_start=start, scheduled_end=end)

    @property
    def days_label(self) -> str:
        return ", ".join(MONDAY_FIRST_DAY_NAMES[d] for d in sorted(self.days_of_week))


def visits_for_date(
    rules: list[ScheduleRule], day: date, tz: tzinfo = timezone.utc
) -> list[CareVisit]:
    """Expand recurring rules into the concrete visits booked for one day.

    Only the rules in force on `day` are expanded. A rule closed last week does
    not reach forward, and a rule added today does not reach back, which is what
    stops a schedule change manufacturing missing arrivals in the past.
    """
    visits = [rule.visit_for(day, tz) for rule in rules if rule.applies_on(day)]
    return sorted(visits, key=lambda v: v.scheduled_start)


def parse_days_of_week(raw: str | list) -> tuple[int, ...]:
    """Accept "0,1,2,3,4" or [0,1,2,3,4]. Raise on anything else."""
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
    else:
        parts = list(raw)
    if not parts:
        raise ValueError("a schedule rule needs at least one day of the week")
    days = []
    for part in parts:
        try:
            day = int(part)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"day of week must be 0-6 (Monday is 0), got {part!r}") from exc
        if not 0 <= day <= 6:
            raise ValueError(f"day of week must be 0-6 (Monday is 0), got {day}")
        days.append(day)
    return tuple(sorted(set(days)))


def parse_clock(raw: str) -> time:
    """Parse "08:00" or "08:00:00" into a time. Raise on anything else."""
    try:
        return time.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"time must look like 08:00, got {raw!r}") from exc


@dataclass(frozen=True)
class VisitComparison:
    visit: CareVisit
    status_text: str
    door_event_count: int
    first_activity: datetime | None
    last_activity: datetime | None
    boundary_note: str = field(default=phrasing.BOUNDARY_NOTE)

    @property
    def activity_recorded(self) -> bool:
        return self.status_text == phrasing.ARRIVAL_RECORDED

    @property
    def minutes_with_activity(self) -> int | None:
        """Minutes between the first and last door event inside the window.

        This is the number the person paying for a 45-minute visit wants, and it
        is also the number most easily misread, so it is deliberately not called
        "visit length". It is the span of door activity, which for a single
        arrival with no departure event is zero.
        """
        if self.first_activity is None or self.last_activity is None:
            return None
        return int((self.last_activity - self.first_activity).total_seconds() // 60)


def compare_visit(
    visit: CareVisit,
    log: ArrivalLog,
    history_available_since: datetime | None,
    grace: timedelta = DEFAULT_GRACE,
) -> VisitComparison:
    """Compare one scheduled visit window against the door's activity log."""
    if history_available_since is None or history_available_since > visit.scheduled_start:
        return VisitComparison(
            visit=visit,
            status_text=phrasing.assert_safe(phrasing.NOT_ENOUGH_HISTORY),
            door_event_count=0,
            first_activity=None,
            last_activity=None,
        )

    window_start = visit.scheduled_start - grace
    window_end = visit.scheduled_end + grace
    events = log.events_between(window_start, window_end)

    if events:
        status = phrasing.ARRIVAL_RECORDED
        first_activity = events[0].occurred_at
        last_activity = events[-1].occurred_at
    else:
        status = phrasing.NO_ARRIVAL_RECORDED
        first_activity = None
        last_activity = None

    return VisitComparison(
        visit=visit,
        status_text=phrasing.assert_safe(status),
        door_event_count=len(events),
        first_activity=first_activity,
        last_activity=last_activity,
    )
