"""The arrival log: a neutral, timestamped list of front-door activity.

No names, no verdicts, no filtering on `sub_type`. This is Surface Two from
the spec: a household-side record next to the care schedule, and it is also
the raw material Surface One (the silence watch) reads from.

A log holds one household's doors. `for_devices` returns a view scoped to a
subset of them, which is how the day view narrows to a single door without any
other module having to know how the events are stored.
"""

from __future__ import annotations

from datetime import datetime

from .events import DoorEvent


class ArrivalLog:
    """An append-only, deduplicated list of DoorEvents."""

    def __init__(self, events: dict[str, DoorEvent] | None = None) -> None:
        self._events: dict[str, DoorEvent] = dict(events or {})

    def record(self, event: DoorEvent) -> bool:
        """Add `event` if it is front-door activity and not already seen.

        Returns True if it was newly recorded, False if it was a duplicate
        delivery or an event type this log does not track.
        """
        if not event.is_door_activity:
            return False
        if event.request_id in self._events:
            return False
        self._events[event.request_id] = event
        return True

    def for_devices(self, device_ids: list[str] | None) -> "ArrivalLog":
        """A view of this log scoped to some doors. Empty list means no doors.

        `None` means every door, which is what an unscoped call gets. The
        distinction matters: a household with zero registered doors must see an
        empty log, not everybody's.
        """
        if device_ids is None:
            return self
        wanted = set(device_ids)
        return ArrivalLog(
            {rid: e for rid, e in self._events.items() if e.device_id in wanted}
        )

    def events_between(self, start: datetime, end: datetime) -> list[DoorEvent]:
        matches = [e for e in self._events.values() if start <= e.occurred_at <= end]
        return sorted(matches, key=lambda e: e.occurred_at)

    def all_events(self) -> list[DoorEvent]:
        return sorted(self._events.values(), key=lambda e: e.occurred_at)

    def last_event_before(self, when: datetime) -> DoorEvent | None:
        prior = [e for e in self._events.values() if e.occurred_at <= when]
        if not prior:
            return None
        return max(prior, key=lambda e: e.occurred_at)

    def search(
        self,
        query: str = "",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[DoorEvent]:
        """Free-text search over the log, newest first.

        The text matches the display label, the event type and the device id.
        It deliberately does *not* match `sub_type` as a filter term in the
        sense of narrowing what the log contains: `sub_type` is part of the
        display label and so is searchable the way any other displayed word is,
        but nothing here removes an event from the record on the strength of it.
        """
        results = self.all_events()
        if start is not None:
            results = [e for e in results if e.occurred_at >= start]
        if end is not None:
            results = [e for e in results if e.occurred_at <= end]
        needle = query.strip().lower()
        if needle:
            results = [
                e
                for e in results
                if needle in e.display_label.lower()
                or needle in e.event_type.lower()
                or needle in e.device_id.lower()
            ]
        return list(reversed(results))

    def __len__(self) -> int:
        return len(self._events)
