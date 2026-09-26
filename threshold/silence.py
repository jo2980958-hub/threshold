"""The silence watch: the surface for the person who never wears the pendant.

The schedule comparison answers a question somebody asked. This answers one
nobody asked, which is the harder and more useful half of the product. Someone
lives alone, refuses the pendant, and has no care package at all. The only
signal available is the absence of any door activity for longer than this
household's own days usually go.

Three things this module is careful about:

- It measures silence across *every* door the household has, not just the front
  one. A back-door-only day is not a silent day.
- It never fires on a household that has not been observed long enough to have a
  pattern. Ring does not back-fill history, so a freshly linked household knows
  nothing about itself, and inventing a rhythm from one day of data is how you
  build something that cries wolf on day two and gets muted on day three.
- Its output is not alarmed. "No front-door activity recorded for 14 hours" is
  what the sensor knows. Whether that is a fall or a lie-in is not a doorbell's
  call, and the phrasing never pretends otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from . import phrasing
from .log import ArrivalLog

# Days of observed history before the watch is willing to say anything about a
# household's pattern. Two full days is the minimum that can show a repeat.
MIN_HISTORY_DAYS = 2

# States, in the order the UI ranks them.
STATE_ALERT = "alert"
STATE_WITHIN_PATTERN = "within_pattern"
STATE_NOT_ENOUGH_HISTORY = "not_enough_history"
STATE_AWAY = "away"


@dataclass(frozen=True)
class SilenceState:
    state: str
    message: str
    hours_since_last_activity: float | None
    threshold_hours: int
    last_activity_at: datetime | None

    @property
    def is_alert(self) -> bool:
        return self.state == STATE_ALERT

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "message": self.message,
            # Whole hours, matching the message exactly. A tenth of an hour
            # means nothing to a family member, and "50.7" beside a sentence
            # saying "51 hours" reads as a bug in a product whose whole claim is
            # that the record is careful.
            "hours_since_last_activity": (
                round(self.hours_since_last_activity)
                if self.hours_since_last_activity is not None
                else None
            ),
            "threshold_hours": self.threshold_hours,
            "last_activity_at": (
                self.last_activity_at.isoformat() if self.last_activity_at else None
            ),
        }


def evaluate(
    log: ArrivalLog,
    now: datetime,
    threshold_hours: int,
    history_available_since: datetime | None,
    usual_activity_by_hour: int | None = None,
    away: bool = False,
) -> SilenceState:
    """Decide whether this household's door has been quiet longer than it should.

    An away period switches the watch off rather than explaining a week of
    alerts after the fact. A watch that cries wolf gets muted, and a muted watch
    is not a watch.

    Otherwise there are two triggers, and the first one that fires wins:

    1. Hours since the last event anywhere in the household exceed
       `threshold_hours`. This is the trigger that works for a household with no
       care package and no routine worth learning.
    2. Nothing yet today, and it is already past the hour by which this household
       usually sees something. This is narrower but catches a quiet morning
       sooner than a 12-hour count ever could, because the overnight gap has
       already used most of the threshold up.
    """
    if away:
        return SilenceState(
            state=STATE_AWAY,
            message=phrasing.assert_safe(phrasing.SILENCE_AWAY),
            hours_since_last_activity=None,
            threshold_hours=threshold_hours,
            last_activity_at=None,
        )

    if history_available_since is None:
        return SilenceState(
            state=STATE_NOT_ENOUGH_HISTORY,
            message=phrasing.assert_safe(phrasing.SILENCE_NOT_ENOUGH_HISTORY),
            hours_since_last_activity=None,
            threshold_hours=threshold_hours,
            last_activity_at=None,
        )

    observed = now - history_available_since
    if observed < timedelta(days=MIN_HISTORY_DAYS):
        return SilenceState(
            state=STATE_NOT_ENOUGH_HISTORY,
            message=phrasing.assert_safe(phrasing.SILENCE_NOT_ENOUGH_HISTORY),
            hours_since_last_activity=None,
            threshold_hours=threshold_hours,
            last_activity_at=None,
        )

    last = log.last_event_before(now)
    if last is None:
        return SilenceState(
            state=STATE_NOT_ENOUGH_HISTORY,
            message=phrasing.assert_safe(phrasing.SILENCE_NOT_ENOUGH_HISTORY),
            hours_since_last_activity=None,
            threshold_hours=threshold_hours,
            last_activity_at=None,
        )

    hours_quiet = (now - last.occurred_at).total_seconds() / 3600.0

    if hours_quiet >= threshold_hours:
        # round, not int: the same number is shown beside this message as
        # hours_since_last_activity, and "50 hours" next to "51.0" reads as a
        # bug in a product whose whole claim is that the record is careful.
        message = phrasing.QUIET_FOR_HOURS.format(
            hours=round(hours_quiet), threshold=threshold_hours
        )
        return SilenceState(
            state=STATE_ALERT,
            message=phrasing.assert_safe(message),
            hours_since_last_activity=hours_quiet,
            threshold_hours=threshold_hours,
            last_activity_at=last.occurred_at,
        )

    if usual_activity_by_hour is not None and now.hour >= usual_activity_by_hour:
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if not log.events_between(midnight, now):
            return SilenceState(
                state=STATE_ALERT,
                message=phrasing.assert_safe(phrasing.NO_MORNING_ACTIVITY_YET),
                hours_since_last_activity=hours_quiet,
                threshold_hours=threshold_hours,
                last_activity_at=last.occurred_at,
            )

    return SilenceState(
        state=STATE_WITHIN_PATTERN,
        message=phrasing.assert_safe(phrasing.SILENCE_WITHIN_PATTERN),
        hours_since_last_activity=hours_quiet,
        threshold_hours=threshold_hours,
        last_activity_at=last.occurred_at,
    )
