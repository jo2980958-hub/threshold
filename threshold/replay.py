"""Replay HMAC-signed fixture webhooks into a ThresholdApp.

This is the demo's data source. Because no Ring simulator is documented
anywhere (see SPEC.md), the fixtures are Ring's
documented webhook envelope, signed with `webhook.sign` the same way Ring
would sign a live delivery, then delivered through `ThresholdApp.handle_webhook`,
the same code path a real Ring webhook would hit. The boundary this app never
crosses is calling `ring_client.RingClient` against a live host; this module
never imports it.

`--to-today` shifts every fixture timestamp forward by a whole number of days so
the fixture's last day lands on today. Whole days, never hours, because the
schedule rules are day-of-week rules and a partial-day shift would slide a
Wednesday booking onto a Thursday. It is a presentation shift and the README
says so; the events themselves are unchanged.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .app import ThresholdApp
from .household import Door, Household
from .schedule import CareVisit, ScheduleRule, parse_clock, parse_days_of_week


def load_fixture(path: str | Path) -> list[dict]:
    with open(path) as f:
        data = json.load(f)
    return data["events"]


def load_schedule_fixture(path: str | Path) -> list[CareVisit]:
    """Legacy one-day schedule fixture, kept for the original single-day demo."""
    with open(path) as f:
        data = json.load(f)
    return [
        CareVisit(
            label=v["label"],
            scheduled_start=datetime.fromisoformat(v["scheduled_start"]),
            scheduled_end=datetime.fromisoformat(v["scheduled_end"]),
        )
        for v in data["visits"]
    ]


def seed_households(app: ThresholdApp, path: str | Path, day_offset: int = 0) -> list[str]:
    """Register households, their doors, their care packages, notes and away days.

    `day_offset` moves every date by the same whole number of days the events
    were moved by. Shifting the events without shifting the schedule would put
    the package amendment on the wrong side of the days it governs, which is the
    exact class of bug the versioning is there to prevent.
    """
    shift = timedelta(days=day_offset)

    def moved(raw: str | None) -> date | None:
        return date.fromisoformat(raw) + shift if raw else None

    with open(path) as f:
        data = json.load(f)
    seeded = []
    for entry in data["households"]:
        household = Household(
            id=entry["id"],
            name=entry["name"],
            agency_id=entry.get("agency_id"),
            quiet_threshold_hours=entry.get("quiet_threshold_hours", 12),
            usual_activity_by_hour=entry.get("usual_activity_by_hour", 10),
            timezone_name=entry.get("timezone", "UTC"),
            doors=tuple(
                Door(
                    device_id=d["device_id"],
                    household_id=entry["id"],
                    label=d["label"],
                    is_primary=d.get("is_primary", False),
                )
                for d in entry.get("doors", [])
            ),
        )
        app.store.upsert_household(household)
        for rule in entry.get("schedule_rules", []):
            app.store.add_schedule_rule(
                household.id,
                ScheduleRule(
                    label=rule["label"],
                    days_of_week=parse_days_of_week(rule["days_of_week"]),
                    start_time=parse_clock(rule["start_time"]),
                    end_time=parse_clock(rule["end_time"]),
                    active_from=moved(rule.get("active_from")),
                    active_until=moved(rule.get("active_until")),
                ),
            )
        for note in entry.get("notes", []):
            app.store.add_note(
                household_id=household.id,
                occurred_on=moved(note["occurred_on"]),
                text=note["text"],
                author=note["author"],
                at_time=note.get("at_time"),
                visit_label=note.get("visit_label"),
            )
        for away in entry.get("away", []):
            app.store.add_away_period(
                household.id,
                moved(away["start_date"]),
                moved(away["end_date"]),
                away.get("label"),
            )
        seeded.append(household.id)
    return seeded


def day_offset_to_today(events: list[dict], today: date | None = None) -> int:
    """How many whole days the fixture has to move so its last day is today."""
    if not events:
        return 0
    today = today or datetime.now().date()
    latest = max(
        datetime.fromisoformat(e["created_at"].replace("Z", "+00:00")).date() for e in events
    )
    return (today - latest).days


def shift_events_to_today(events: list[dict], today: date | None = None) -> list[dict]:
    """Move every event forward by whole days so the last one lands today."""
    if not events:
        return events
    offset = timedelta(days=day_offset_to_today(events, today))
    if offset == timedelta(0):
        return events
    shifted = []
    for event in events:
        moved = dict(event)
        when = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")) + offset
        moved["created_at"] = when.isoformat()
        # The request_id has to move too, or a re-run against the same SQLite
        # file would be deduplicated as a redelivery of the unshifted event.
        moved["request_id"] = f"{event['request_id']}+{offset.days}d"
        shifted.append(moved)
    return shifted


def replay_events(app: ThresholdApp, events: list[dict]) -> list[tuple[int, dict]]:
    """Sign and deliver each event through the real webhook handler.

    Returns the (status, response) pairs in delivery order, so a caller (or a
    test) can assert every fixture event was accepted.
    """
    from .webhook import sign

    results = []
    for event in events:
        raw_body = json.dumps(event).encode("utf-8")
        signature = sign(app.webhook_secret, raw_body)
        results.append(app.handle_webhook(raw_body, signature))
    return results


def main() -> None:  # pragma: no cover - thin CLI wrapper
    import argparse

    parser = argparse.ArgumentParser(
        description="Replay a Threshold fixture and run the demo server."
    )
    parser.add_argument("fixture", help="Path to fixtures/<name>.json (events)")
    parser.add_argument(
        "--households",
        default=None,
        help="Path to fixtures/households.json (households, doors, care packages)",
    )
    parser.add_argument("--schedule", help="Legacy single-day schedule fixture", default=None)
    parser.add_argument(
        "--to-today",
        action="store_true",
        help="Shift fixture timestamps forward by whole days so the last day is today",
    )
    parser.add_argument("--secret", default="demo-webhook-secret")
    parser.add_argument("--db", default=":memory:")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    from . import server

    app = ThresholdApp(webhook_secret=args.secret, db_path=args.db)

    events = load_fixture(args.fixture)
    offset = day_offset_to_today(events) if args.to_today else 0

    if args.households:
        seeded = seed_households(app, args.households, day_offset=offset)
        print(f"Registered {len(seeded)} households: {', '.join(seeded)}")

    if offset:
        events = shift_events_to_today(events)
        print(f"Shifted the fixture forward by {offset} whole days so its last day is today.")
    results = replay_events(app, events)
    accepted = sum(1 for status, _ in results if status == 200)
    print(f"Replayed {len(events)} fixture events, {accepted} accepted (200).")

    if args.schedule:
        visits = load_schedule_fixture(args.schedule)
        print(f"Loaded {len(visits)} single-day visits from the legacy schedule fixture.")

    print(f"Serving on http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    server.run(app, port=args.port)


if __name__ == "__main__":  # pragma: no cover
    main()
