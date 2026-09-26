"""Parsing and classification of Ring webhook events.

Shapes match Ring's documented webhook envelope: HMAC-signed, JSON body, with
`event_type`, `device_id`, `request_id` (used for dedup), `created_at`, and an
`attributes` object that carries `sub_type` on `motion_detected` events.

The one rule this module exists to enforce: `sub_type` is stored for display
only. It is never used to decide whether an event counts as front-door
activity. Ring's own users have documented the classifier calling a motorized
wheelchair user a package instead of a person, so gating on `sub_type: human`
would silently erase that household from its own log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# Event types this app acts on. Ring's webhook also sends device_added,
# device_removed, device_online/offline, and subscription lifecycle events;
# this app ignores those for the front-door log.
DOOR_ACTIVITY_EVENT_TYPES = frozenset({"motion_detected", "button_press"})


class MalformedEvent(ValueError):
    """Raised when a webhook payload does not match the documented shape."""


@dataclass(frozen=True)
class DoorEvent:
    request_id: str
    device_id: str
    event_type: str
    occurred_at: datetime
    sub_type: str | None  # display hint only, see module docstring
    raw: dict

    @property
    def is_door_activity(self) -> bool:
        return self.event_type in DOOR_ACTIVITY_EVENT_TYPES

    @property
    def display_label(self) -> str:
        """A human label for the UI. Informational, never a filter."""
        if self.event_type == "button_press":
            return "Doorbell pressed"
        if self.event_type == "motion_detected":
            if self.sub_type == "human":
                return "Motion (person-shaped)"
            if self.sub_type == "package":
                return "Motion (possible delivery)"
            if self.sub_type == "vehicle":
                return "Motion (vehicle)"
            if self.sub_type == "animal":
                return "Motion (animal)"
            return "Motion"
        return self.event_type


def parse_webhook_event(payload: dict) -> DoorEvent:
    """Parse one Ring webhook payload into a DoorEvent.

    Matches the documented envelope:
        {
          "request_id": "...",
          "device_id": "...",
          "event_type": "motion_detected" | "button_press" | ...,
          "created_at": "2026-09-22T08:14:03Z",
          "attributes": {"sub_type": "human", ...}
        }
    """
    try:
        request_id = payload["request_id"]
        device_id = payload["device_id"]
        event_type = payload["event_type"]
        created_at_raw = payload["created_at"]
    except KeyError as exc:
        raise MalformedEvent(f"missing required field: {exc}") from exc

    occurred_at = _parse_timestamp(created_at_raw)
    attributes = payload.get("attributes") or {}
    sub_type = attributes.get("sub_type")

    return DoorEvent(
        request_id=request_id,
        device_id=device_id,
        event_type=event_type,
        occurred_at=occurred_at,
        sub_type=sub_type,
        raw=payload,
    )


def _parse_timestamp(raw: str) -> datetime:
    # Ring timestamps are ISO 8601 UTC with a trailing Z.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
