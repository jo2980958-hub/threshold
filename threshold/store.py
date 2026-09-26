"""SQLite storage: door events, households, doors, schedule rules, summaries.

Kept separate from ArrivalLog (which is the in-memory query surface) so the
demo server can persist across restarts without every consumer needing to
know about SQL.

Schedules live here rather than in a fixture file because a care package is a
thing that changes. `schedule_rules` holds recurring bookings with an active
range, so amending a package adds a row and closes the old one instead of
rewriting what last month looked like.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from .events import DoorEvent, parse_webhook_event
from .household import Door, Household
from .log import ArrivalLog
from .schedule import ScheduleRule, parse_clock, parse_days_of_week

SCHEMA = """
CREATE TABLE IF NOT EXISTS door_events (
    request_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    sub_type TEXT,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_door_events_device_time
    ON door_events (device_id, occurred_at);

CREATE TABLE IF NOT EXISTS households (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    agency_id TEXT,
    quiet_threshold_hours INTEGER NOT NULL DEFAULT 12,
    usual_activity_by_hour INTEGER NOT NULL DEFAULT 10,
    timezone_name TEXT NOT NULL DEFAULT 'UTC'
);

CREATE TABLE IF NOT EXISTS doors (
    device_id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL,
    label TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS schedule_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id TEXT NOT NULL,
    label TEXT NOT NULL,
    days_of_week TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    active_from TEXT,
    active_until TEXT
);

CREATE TABLE IF NOT EXISTS household_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id TEXT NOT NULL,
    occurred_on TEXT NOT NULL,
    at_time TEXT,
    visit_label TEXT,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_household_day
    ON household_notes (household_id, occurred_on);

CREATE TABLE IF NOT EXISTS away_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    label TEXT
);

CREATE TABLE IF NOT EXISTS weekly_summaries (
    household_id TEXT NOT NULL,
    week_start TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    source TEXT NOT NULL,
    model_id TEXT,
    rejected_reason TEXT,
    text TEXT NOT NULL,
    PRIMARY KEY (household_id, week_start)
);
"""


class EventStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        # check_same_thread=False because the demo server runs serve_forever on
        # its own thread while the replayer seeds on the main one. It stays safe
        # because server.run is single-threaded: exactly one request touches
        # this connection at a time, which is all a demo needs.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # --- door events -----------------------------------------------------

    def save(self, event: DoorEvent) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO door_events "
            "(request_id, device_id, event_type, occurred_at, sub_type, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.request_id,
                event.device_id,
                event.event_type,
                event.occurred_at.isoformat(),
                event.sub_type,
                json.dumps(event.raw),
            ),
        )
        self._conn.commit()

    def load_into(self, log: ArrivalLog, device_id: str | None = None) -> int:
        """Rehydrate an ArrivalLog from disk. Returns the count loaded."""
        query = "SELECT raw_json FROM door_events"
        params: tuple = ()
        if device_id:
            query += " WHERE device_id = ?"
            params = (device_id,)
        loaded = 0
        for row in self._conn.execute(query, params).fetchall():
            event = parse_webhook_event(json.loads(row["raw_json"]))
            if log.record(event):
                loaded += 1
        return loaded

    def earliest_event_at(self, device_id: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT MIN(occurred_at) AS first FROM door_events WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if not row or row["first"] is None:
            return None
        return datetime.fromisoformat(row["first"])

    def earliest_event_for_devices(self, device_ids: list[str]) -> datetime | None:
        """When this household's record starts, across every door it has.

        Ring does not back-fill, so this is a hard floor on what any comparison
        can honestly say. A visit booked before it is reported as not comparable,
        never as a missing arrival.
        """
        if not device_ids:
            return None
        placeholders = ",".join("?" for _ in device_ids)
        row = self._conn.execute(
            f"SELECT MIN(occurred_at) AS first FROM door_events WHERE device_id IN ({placeholders})",
            tuple(device_ids),
        ).fetchone()
        if not row or row["first"] is None:
            return None
        return datetime.fromisoformat(row["first"])

    def latest_event_date_for_devices(self, device_ids: list[str]) -> date | None:
        """The most recent day this household recorded anything.

        The day view opens here rather than on the wall-clock date, so a record
        that stops (which is the exact case the silence watch exists for) shows
        the last day that has something in it instead of an empty ledger the
        viewer has to work out how to leave.
        """
        if not device_ids:
            return None
        placeholders = ",".join("?" for _ in device_ids)
        row = self._conn.execute(
            f"SELECT MAX(occurred_at) AS last FROM door_events WHERE device_id IN ({placeholders})",
            tuple(device_ids),
        ).fetchone()
        if not row or row["last"] is None:
            return None
        return datetime.fromisoformat(row["last"]).date()

    # --- households and doors --------------------------------------------

    def upsert_household(self, household: Household) -> None:
        self._conn.execute(
            "INSERT INTO households (id, name, agency_id, quiet_threshold_hours, "
            "usual_activity_by_hour, timezone_name) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, agency_id=excluded.agency_id, "
            "quiet_threshold_hours=excluded.quiet_threshold_hours, "
            "usual_activity_by_hour=excluded.usual_activity_by_hour, "
            "timezone_name=excluded.timezone_name",
            (
                household.id,
                household.name,
                household.agency_id,
                household.quiet_threshold_hours,
                household.usual_activity_by_hour,
                household.timezone_name,
            ),
        )
        for door in household.doors:
            self.upsert_door(door)
        self._conn.commit()

    def upsert_door(self, door: Door) -> None:
        self._conn.execute(
            "INSERT INTO doors (device_id, household_id, label, is_primary) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(device_id) DO UPDATE SET household_id=excluded.household_id, "
            "label=excluded.label, is_primary=excluded.is_primary",
            (door.device_id, door.household_id, door.label, int(door.is_primary)),
        )
        self._conn.commit()

    def _doors_for(self, household_id: str) -> tuple[Door, ...]:
        rows = self._conn.execute(
            "SELECT device_id, household_id, label, is_primary FROM doors "
            "WHERE household_id = ? ORDER BY is_primary DESC, label",
            (household_id,),
        ).fetchall()
        return tuple(
            Door(
                device_id=r["device_id"],
                household_id=r["household_id"],
                label=r["label"],
                is_primary=bool(r["is_primary"]),
            )
            for r in rows
        )

    def _household_from_row(self, row: sqlite3.Row) -> Household:
        return Household(
            id=row["id"],
            name=row["name"],
            agency_id=row["agency_id"],
            quiet_threshold_hours=row["quiet_threshold_hours"],
            usual_activity_by_hour=row["usual_activity_by_hour"],
            timezone_name=row["timezone_name"],
            doors=self._doors_for(row["id"]),
        )

    def get_household(self, household_id: str) -> Household | None:
        row = self._conn.execute(
            "SELECT * FROM households WHERE id = ?", (household_id,)
        ).fetchone()
        return self._household_from_row(row) if row else None

    def all_households(self, agency_id: str | None = None) -> list[Household]:
        if agency_id:
            rows = self._conn.execute(
                "SELECT * FROM households WHERE agency_id = ? ORDER BY name", (agency_id,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM households ORDER BY name").fetchall()
        return [self._household_from_row(r) for r in rows]

    def household_for_device(self, device_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT household_id FROM doors WHERE device_id = ?", (device_id,)
        ).fetchone()
        return row["household_id"] if row else None

    # --- schedule rules ---------------------------------------------------

    def add_schedule_rule(self, household_id: str, rule: ScheduleRule) -> int:
        cursor = self._conn.execute(
            "INSERT INTO schedule_rules (household_id, label, days_of_week, start_time, "
            "end_time, active_from, active_until) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                household_id,
                rule.label,
                ",".join(str(d) for d in rule.days_of_week),
                rule.start_time.isoformat(),
                rule.end_time.isoformat(),
                rule.active_from.isoformat() if rule.active_from else None,
                rule.active_until.isoformat() if rule.active_until else None,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def schedule_rules(self, household_id: str) -> list[ScheduleRule]:
        rows = self._conn.execute(
            "SELECT * FROM schedule_rules WHERE household_id = ? ORDER BY start_time",
            (household_id,),
        ).fetchall()
        return [
            ScheduleRule(
                rule_id=r["id"],
                label=r["label"],
                days_of_week=parse_days_of_week(r["days_of_week"]),
                start_time=parse_clock(r["start_time"]),
                end_time=parse_clock(r["end_time"]),
                active_from=date.fromisoformat(r["active_from"]) if r["active_from"] else None,
                active_until=date.fromisoformat(r["active_until"]) if r["active_until"] else None,
            )
            for r in rows
        ]

    def end_schedule_rule(self, household_id: str, rule_id: int, last_day: date) -> bool:
        """Close a rule on `last_day`. Never deletes it.

        A care package that ends is not a care package that never existed. The
        row stays so that days before `last_day` still expand against it, which
        is the difference between a record and a rewrite.
        """
        cursor = self._conn.execute(
            "UPDATE schedule_rules SET active_until = ? WHERE household_id = ? AND id = ?",
            (last_day.isoformat(), household_id, rule_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def delete_unstarted_schedule_rule(
        self, household_id: str, rule_id: int, today: date
    ) -> bool:
        """Delete a rule only if it has not taken effect yet.

        A rule booked for next Monday and cancelled on Friday never governed a
        day, so removing it destroys no history. Anything that has already been
        in force is closed with `end_schedule_rule` instead, and this refuses.
        """
        cursor = self._conn.execute(
            "DELETE FROM schedule_rules WHERE household_id = ? AND id = ? "
            "AND active_from IS NOT NULL AND active_from > ?",
            (household_id, rule_id, today.isoformat()),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    # --- household notes and away periods --------------------------------

    def add_note(
        self,
        household_id: str,
        occurred_on: date,
        text: str,
        author: str,
        at_time: str | None = None,
        visit_label: str | None = None,
        created_at: datetime | None = None,
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO household_notes (household_id, occurred_on, at_time, visit_label, "
            "author, text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                household_id,
                occurred_on.isoformat(),
                at_time,
                visit_label,
                author,
                text,
                (created_at or datetime.now(timezone.utc)).isoformat(),
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def notes_for_day(self, household_id: str, day: date) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM household_notes WHERE household_id = ? AND occurred_on = ? "
            "ORDER BY COALESCE(at_time, '99:99'), created_at",
            (household_id, day.isoformat()),
        ).fetchall()
        return [dict(r) for r in rows]

    def notes_between(self, household_id: str, start: date, end: date) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM household_notes WHERE household_id = ? "
            "AND occurred_on BETWEEN ? AND ? ORDER BY occurred_on, created_at",
            (household_id, start.isoformat(), end.isoformat()),
        ).fetchall()
        return [dict(r) for r in rows]

    def add_away_period(
        self, household_id: str, start_day: date, end_day: date, label: str | None = None
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO away_periods (household_id, start_date, end_date, label) "
            "VALUES (?, ?, ?, ?)",
            (household_id, start_day.isoformat(), end_day.isoformat(), label),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def is_away_on(self, household_id: str, day: date) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM away_periods WHERE household_id = ? AND ? BETWEEN start_date "
            "AND end_date LIMIT 1",
            (household_id, day.isoformat()),
        ).fetchone()
        return row is not None

    def away_periods(self, household_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM away_periods WHERE household_id = ? ORDER BY start_date",
            (household_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- cached weekly summaries -----------------------------------------

    def save_summary(
        self,
        household_id: str,
        week_start: date,
        text: str,
        source: str,
        model_id: str | None,
        rejected_reason: str | None,
        generated_at: datetime,
    ) -> None:
        self._conn.execute(
            "INSERT INTO weekly_summaries (household_id, week_start, generated_at, source, "
            "model_id, rejected_reason, text) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(household_id, week_start) DO UPDATE SET generated_at=excluded.generated_at, "
            "source=excluded.source, model_id=excluded.model_id, "
            "rejected_reason=excluded.rejected_reason, text=excluded.text",
            (
                household_id,
                week_start.isoformat(),
                generated_at.isoformat(),
                source,
                model_id,
                rejected_reason,
                text,
            ),
        )
        self._conn.commit()

    def get_summary(self, household_id: str, week_start: date) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM weekly_summaries WHERE household_id = ? AND week_start = ?",
            (household_id, week_start.isoformat()),
        ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        self._conn.close()
