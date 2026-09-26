"""Households, the doors inside them, and the settings that vary per household.

The first build assumed one household with one door and a schedule loaded from a
fixture. Both assumptions are wrong in the field. A bungalow with a front and a
back door produces two event streams that have to be read together, and an agency
looking after forty clients needs the same product pointed at forty households.

Nothing in here decides anything about a visit. These are the identifiers and the
configured numbers; the rules that use them live in `schedule.py` and
`silence.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Hours of no front-door activity before the silence watch says so. Twelve is a
# starting value, not a clinical threshold: it is long enough that an ordinary
# quiet evening does not trip it and short enough that a whole day of nothing
# does. Every household can change it, which is the point of it being here.
DEFAULT_QUIET_THRESHOLD_HOURS = 12

# The hour by which this household's door usually sees something. Used only when
# there is enough history to have learned one; see silence.py.
DEFAULT_USUAL_ACTIVITY_BY_HOUR = 10

# A household's own wall clock. Booked visit times are local times, so this is
# what a clock change moves and what the comparison has to expand against.
DEFAULT_TIMEZONE = "UTC"


@dataclass(frozen=True)
class Door:
    """One Ring device on one household's property."""

    device_id: str
    household_id: str
    label: str
    is_primary: bool = False


@dataclass(frozen=True)
class Household:
    id: str
    name: str
    quiet_threshold_hours: int = DEFAULT_QUIET_THRESHOLD_HOURS
    usual_activity_by_hour: int = DEFAULT_USUAL_ACTIVITY_BY_HOUR
    # Set when the household is under an agency's care. An agency view rolls up
    # every household carrying its id; a family-only household carries None and
    # never appears in one.
    agency_id: str | None = None
    # IANA zone name. Booked visit times are wall-clock times in this zone.
    timezone_name: str = DEFAULT_TIMEZONE
    doors: tuple[Door, ...] = field(default_factory=tuple)

    @property
    def tz(self) -> tzinfo:
        """The household's zone, falling back to UTC rather than failing.

        A missing tzdata package or a zone name that has been retired should
        degrade the timestamps, never take the log away from the family reading
        it.
        """
        try:
            return ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            return timezone.utc

    @property
    def device_ids(self) -> list[str]:
        return [d.device_id for d in self.doors]

    @property
    def primary_door(self) -> Door | None:
        for door in self.doors:
            if door.is_primary:
                return door
        return self.doors[0] if self.doors else None

    def door_label(self, device_id: str) -> str:
        for door in self.doors:
            if door.device_id == device_id:
                return door.label
        # A device that sent us events but is not in the household's door list.
        # Shown, never dropped: an unrecognised door is still door activity, and
        # silently discarding it would be the same mistake as gating on
        # sub_type.
        return "Unrecognised door"


def validate_quiet_threshold(hours: object) -> int:
    """Coerce a submitted quiet threshold, or raise ValueError.

    One hour is the floor because anything shorter fires on a normal afternoon.
    Seventy-two is the ceiling because past that the setting is not a watch, it
    is an off switch, and an off switch should be an off switch.
    """
    try:
        value = int(hours)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"quiet threshold must be a whole number of hours: {hours!r}") from exc
    if not 1 <= value <= 72:
        raise ValueError(f"quiet threshold must be between 1 and 72 hours, got {value}")
    return value


def validate_timezone(name: object) -> str:
    """Accept an IANA zone name this machine can actually resolve."""
    text = str(name or "").strip()
    if not text:
        raise ValueError("a timezone name cannot be empty")
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise ValueError(f"unknown timezone: {text!r}") from exc
    return text


def validate_hour(hour: object) -> int:
    try:
        value = int(hour)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"hour must be a whole number: {hour!r}") from exc
    if not 0 <= value <= 23:
        raise ValueError(f"hour must be between 0 and 23, got {value}")
    return value
