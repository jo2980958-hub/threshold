"""ThresholdApp: the webhook and query logic, kept free of sockets so it can be
unit tested directly. `server.py` wraps this in a stdlib HTTP handler.

Every read here is scoped to a household, because a care agency runs one
deployment across many of them. The scoping is done by door: a household's log
is the union of the doors registered to it, and an event from a device nobody
has registered is still stored, still counted, and shown as an unrecognised
door rather than dropped.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from . import digest as digest_mod
from . import export as export_mod
from . import narrate, phrasing, provenance, silence
from .events import MalformedEvent, parse_webhook_event
from .household import Household, validate_hour, validate_quiet_threshold
from .log import ArrivalLog
from .schedule import (
    ScheduleRule,
    compare_visit,
    parse_clock,
    parse_days_of_week,
    visits_for_date,
)
from .store import EventStore
from .webhook import DeliveryDeduplicator, InvalidSignature, verify_signature


class UnknownHousehold(KeyError):
    """Raised when a request names a household that is not registered."""


class ThresholdApp:
    """Holds the state the HTTP handler reads and writes."""

    def __init__(self, webhook_secret: str, db_path: str = ":memory:") -> None:
        self.webhook_secret = webhook_secret
        self.store = EventStore(db_path)
        self.log = ArrivalLog()
        self.dedup = DeliveryDeduplicator()
        self.store.load_into(self.log)
        # Injected in tests and in the offline demo. None means narrate.py
        # builds its own boto3 client, and falls back to the rules if it cannot.
        self.bedrock_client = None

    # --- ingest -----------------------------------------------------------

    def handle_webhook(self, raw_body: bytes, signature: str) -> tuple[int, dict]:
        try:
            verify_signature(self.webhook_secret, raw_body, signature)
        except InvalidSignature:
            return 401, {"error": "invalid signature"}

        try:
            payload = json.loads(raw_body)
            event = parse_webhook_event(payload)
        except (json.JSONDecodeError, MalformedEvent) as exc:
            return 400, {"error": f"malformed payload: {exc}"}

        if self.dedup.seen_before(event.request_id):
            return 200, {"status": "duplicate, ignored"}

        self.dedup.mark_seen(event.request_id)
        recorded = self.log.record(event)
        if recorded:
            self.store.save(event)
        return 200, {"status": "recorded" if recorded else "ignored (not door activity)"}

    # --- household scoping ------------------------------------------------

    def household(self, household_id: str) -> Household:
        found = self.store.get_household(household_id)
        if found is None:
            raise UnknownHousehold(household_id)
        return found

    def default_household(self) -> Household | None:
        households = self.store.all_households()
        return households[0] if households else None

    def log_for(self, household: Household, device_id: str | None = None) -> ArrivalLog:
        device_ids = household.device_ids
        if device_id:
            # A door the household does not own yields an empty log rather than
            # silently widening to every door.
            device_ids = [d for d in device_ids if d == device_id]
        return self.log.for_devices(device_ids)

    def history_since(self, household: Household) -> datetime | None:
        return self.store.earliest_event_for_devices(household.device_ids)

    # --- read surfaces ----------------------------------------------------

    def focus_date(self, household: Household, fallback: date) -> date:
        """The day the ledger opens on: the last day with anything in it."""
        latest = self.store.latest_event_date_for_devices(household.device_ids)
        return latest or fallback

    def day_log(
        self, household_id: str, day: date | None = None, device_id: str | None = None
    ) -> list[dict]:
        household = self.household(household_id)
        log = self.log_for(household, device_id)
        events = log.all_events()
        if day is not None:
            events = [e for e in events if e.occurred_at.date() == day]
        return [self._event_row(household, e) for e in events]

    def _event_row(self, household: Household, event) -> dict:
        """One door event, with where each part of it came from spelled out.

        The doorbell firing is RECORDED: a sensor did a thing. What the doorbell
        thought it saw is REPORTED, because Ring's classifier said so and that
        classifier has been documented calling a motorised wheelchair user a
        package. The two travel together on the same row and neither is allowed
        to pass for the other.
        """
        return {
            "event_type": event.event_type,
            "occurred_at": event.occurred_at.isoformat(),
            "stamp": phrasing.stamp(event.occurred_at, household.tz),
            "label": event.display_label,
            "device_id": event.device_id,
            "door": household.door_label(event.device_id),
            "sub_type": event.sub_type,
            "provenance": phrasing.PROVENANCE_RECORDED,
            # Only set when Ring's guess is doing work in the label. A plain
            # button press has nothing reported about it.
            "label_provenance": (
                phrasing.PROVENANCE_REPORTED if event.sub_type else phrasing.PROVENANCE_RECORDED
            ),
        }

    def schedule_comparisons(
        self, household_id: str, day: date, device_id: str | None = None
    ) -> list[dict]:
        household = self.household(household_id)
        log = self.log_for(household, device_id)
        history = self.history_since(household)
        rules = self.store.schedule_rules(household_id)
        results = []
        for visit in visits_for_date(rules, day, household.tz):
            comparison = compare_visit(visit, log, history)
            results.append(
                {
                    "label": comparison.visit.label,
                    "scheduled_start": comparison.visit.scheduled_start.isoformat(),
                    "scheduled_end": comparison.visit.scheduled_end.isoformat(),
                    "status": comparison.status_text,
                    "door_event_count": comparison.door_event_count,
                    "minutes_with_activity": comparison.minutes_with_activity,
                    "boundary_note": comparison.boundary_note,
                    # A comparison is a conclusion this app's rules drew from
                    # recorded events, and is only ever as good as the interval
                    # arithmetic behind it. It says so.
                    "provenance": phrasing.PROVENANCE_INFERRED,
                }
            )
        return results

    def silence_state(self, household_id: str, now: datetime) -> dict:
        household = self.household(household_id)
        state = silence.evaluate(
            log=self.log_for(household),
            now=now,
            threshold_hours=household.quiet_threshold_hours,
            history_available_since=self.history_since(household),
            usual_activity_by_hour=household.usual_activity_by_hour,
            away=self.store.is_away_on(household_id, now.date()),
        )
        return {**state.as_dict(), "provenance": phrasing.PROVENANCE_INFERRED}

    def notes_for_day(self, household_id: str, day: date) -> list[dict]:
        """The household's own account of a day, as attributed quotations.

        Never merged into anything the app says. See the long comment in
        phrasing.py for why these four rules are the way this feature stays
        inside the product's argument instead of breaking it.
        """
        self.household(household_id)
        rendered = []
        for row in self.store.notes_for_day(household_id, day):
            when = row["at_time"] or row["created_at"][11:16]
            attributed = phrasing.attribute_note(row["text"], row["author"], when)
            rendered.append(
                {
                    "note_id": row["id"],
                    "at_time": row["at_time"],
                    "visit_label": row["visit_label"],
                    "created_at": row["created_at"],
                    "provenance": phrasing.PROVENANCE_REPORTED,
                    **attributed,
                }
            )
        return rendered

    def add_note(self, household_id: str, payload: dict) -> dict:
        household = self.household(household_id)
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("a note needs something written in it")
        if len(text) > 2000:
            raise ValueError("a note is limited to 2000 characters")
        author = str(payload.get("author") or "").strip()
        if not author:
            raise ValueError("a note has to say who wrote it")
        at_time = payload.get("at_time") or None
        if at_time:
            parse_clock(str(at_time))  # raises ValueError on anything that is not a clock
        note_id = self.store.add_note(
            household_id=household.id,
            occurred_on=date.fromisoformat(payload["occurred_on"]),
            text=text,
            author=author,
            at_time=str(at_time) if at_time else None,
            visit_label=(str(payload["visit_label"]) if payload.get("visit_label") else None),
        )
        return {"note_id": note_id, "author": author}

    def add_away_period(self, household_id: str, payload: dict) -> dict:
        household = self.household(household_id)
        start_day = date.fromisoformat(payload["start_date"])
        end_day = date.fromisoformat(payload["end_date"])
        if end_day < start_day:
            raise ValueError("an away period has to end on or after the day it starts")
        away_id = self.store.add_away_period(
            household.id, start_day, end_day, str(payload.get("label") or "") or None
        )
        return {
            "away_id": away_id,
            "start_date": start_day.isoformat(),
            "end_date": end_day.isoformat(),
        }

    def history(
        self,
        household_id: str,
        query: str = "",
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        household = self.household(household_id)
        matches = self.log_for(household).search(query=query, start=start, end=end)
        page = matches[offset : offset + limit]
        return {
            "total": len(matches),
            "limit": limit,
            "offset": offset,
            "events": [self._event_row(household, e) for e in page],
        }

    def weekly_summary(
        self, household_id: str, week_of: date, refresh: bool = False
    ) -> dict:
        """The plain-language week, as a family member reads it.

        Cached per household per week because it costs a Bedrock call, and a
        week that has already happened does not change. `refresh=True` is the
        button in the UI, and the only way to spend a second call.
        """
        household = self.household(household_id)
        week_start = digest_mod.week_start_for(week_of)

        if not refresh:
            cached = self.store.get_summary(household_id, week_start)
            if cached:
                return {
                    "week_start": week_start.isoformat(),
                    "text": cached["text"],
                    "source": cached["source"],
                    "model_id": cached["model_id"],
                    "rejected_reason": cached["rejected_reason"],
                    "gate_notice": provenance.gate_notice(
                        cached["source"], cached["rejected_reason"]
                    ),
                    "generated_at": cached["generated_at"],
                    "cached": True,
                    "facts": self._digest_for(household, week_start).as_dict(),
                }

        digest = self._digest_for(household, week_start)
        summary = narrate.summarise(digest, client=self.bedrock_client)
        generated_at = datetime.now(timezone.utc)
        self.store.save_summary(
            household_id=household_id,
            week_start=week_start,
            text=summary.text,
            source=summary.source,
            model_id=summary.model_id,
            rejected_reason=summary.rejected_reason,
            generated_at=generated_at,
        )
        return {
            "week_start": week_start.isoformat(),
            "text": summary.text,
            "source": summary.source,
            "model_id": summary.model_id,
            "rejected_reason": summary.rejected_reason,
            "gate_notice": provenance.gate_notice(
                summary.source, summary.rejected_reason
            ),
            "generated_at": generated_at.isoformat(),
            "cached": False,
            "facts": digest.as_dict(),
        }

    def _digest_for(self, household: Household, week_start: date):
        return digest_mod.build(
            household_name=household.name,
            log=self.log_for(household),
            rules=self.store.schedule_rules(household.id),
            week_start=week_start,
            history_available_since=self.history_since(household),
            door_count=len(household.doors),
            tz=household.tz,
        )

    def agency_overview(self, agency_id: str | None, now: datetime) -> dict:
        """One row per household under an agency's care.

        Ordered by silence state, not by name: the household whose door has been
        quiet longest is the one a coordinator needs to see first. The row says
        what the door recorded and nothing else; there is no per-carer column,
        no compliance score and no ranking of households against each other,
        because none of those are things a doorbell can support.
        """
        rows = []
        for household in self.store.all_households(agency_id):
            log = self.log_for(household)
            history = self.history_since(household)
            state = silence.evaluate(
                log=log,
                now=now,
                threshold_hours=household.quiet_threshold_hours,
                history_available_since=history,
                usual_activity_by_hour=household.usual_activity_by_hour,
            )
            rules = self.store.schedule_rules(household.id)
            today = [
                compare_visit(visit, log, history)
                for visit in visits_for_date(rules, now.date(), household.tz)
            ]
            rows.append(
                {
                    "household_id": household.id,
                    "name": household.name,
                    "doors": len(household.doors),
                    "events_recorded": len(log),
                    "silence": state.as_dict(),
                    "visits_today": len(today),
                    "visits_today_with_activity": sum(
                        1 for c in today if c.status_text == phrasing.ARRIVAL_RECORDED
                    ),
                    "visits_today_without_recorded_arrival": sum(
                        1 for c in today if c.status_text == phrasing.NO_ARRIVAL_RECORDED
                    ),
                }
            )
        rank = {silence.STATE_ALERT: 0, silence.STATE_NOT_ENOUGH_HISTORY: 1}
        rows.sort(key=lambda r: (rank.get(r["silence"]["state"], 2), r["name"]))
        return {"agency_id": agency_id, "households": rows, "boundary_note": phrasing.BOUNDARY_NOTE}

    # --- writes -----------------------------------------------------------

    def add_schedule_rule(
        self, household_id: str, payload: dict, today: date | None = None
    ) -> dict:
        """Add a booked visit, in force from a given day and never before it.

        `active_from` defaults to today rather than to "always". This is not a
        convenience default, it is the safety property of the whole schedule
        feature. A rule with no start date applies backwards through every day
        the household has ever recorded, so booking a new Tuesday visit would
        silently manufacture a "no arrival was recorded" row against every past
        Tuesday, for a visit that was never booked on any of them. That is a
        false accusation against a named, low-paid person arriving as a data
        bug, which is the exact harm the phrasing rules exist to prevent.

        Backdating is still possible, but only by asking for it explicitly.
        """
        household = self.household(household_id)
        today = today or datetime.now(timezone.utc).date()
        active_from = (
            date.fromisoformat(payload["active_from"]) if payload.get("active_from") else today
        )
        active_until = (
            date.fromisoformat(payload["active_until"]) if payload.get("active_until") else None
        )
        if active_until and active_until < active_from:
            raise ValueError("a schedule rule cannot end before it starts")
        rule = ScheduleRule(
            label=str(payload["label"]).strip(),
            days_of_week=parse_days_of_week(payload["days_of_week"]),
            start_time=parse_clock(payload["start_time"]),
            end_time=parse_clock(payload["end_time"]),
            active_from=active_from,
            active_until=active_until,
        )
        if not rule.label:
            raise ValueError("a schedule rule needs a label")
        rule_id = self.store.add_schedule_rule(household.id, rule)
        return {
            "rule_id": rule_id,
            "label": rule.label,
            "days": rule.days_label,
            "active_from": active_from.isoformat(),
        }

    def amend_schedule_rule(
        self, household_id: str, payload: dict, today: date | None = None
    ) -> dict:
        """Change a booked visit from a given day, leaving the old one in place.

        A care package that changes on the 12th was a different package on the
        11th, and the 11th still has to compare against what was booked then.
        So this closes the existing rule the day before the change and opens a
        new one from the change onwards. Nothing is deleted and no past day
        moves.
        """
        household = self.household(household_id)
        today = today or datetime.now(timezone.utc).date()
        effective_from = (
            date.fromisoformat(payload["effective_from"])
            if payload.get("effective_from")
            else today
        )
        rule_id = int(payload["rule_id"])
        existing = {r.rule_id: r for r in self.store.schedule_rules(household.id)}
        if rule_id not in existing:
            raise ValueError(f"no schedule rule {rule_id} for this household")
        old = existing[rule_id]

        if old.active_from and old.active_from >= effective_from:
            # The rule never governed a day before the change, so there is no
            # history to protect and amending in place is honest.
            self.store.delete_unstarted_schedule_rule(household.id, rule_id, today - timedelta(days=1))

        self.store.end_schedule_rule(household.id, rule_id, effective_from - timedelta(days=1))
        created = self.add_schedule_rule(
            household.id,
            {
                "label": payload.get("label", old.label),
                "days_of_week": payload.get("days_of_week", list(old.days_of_week)),
                "start_time": payload.get("start_time", old.start_time.isoformat()),
                "end_time": payload.get("end_time", old.end_time.isoformat()),
                "active_from": effective_from.isoformat(),
            },
            today=today,
        )
        return {
            "replaced_rule_id": rule_id,
            "previous_rule_ends": (effective_from - timedelta(days=1)).isoformat(),
            **created,
        }

    def end_schedule_rule(
        self, household_id: str, payload: dict, today: date | None = None
    ) -> dict:
        """Stop a booked visit from a given day. Never deletes the history."""
        household = self.household(household_id)
        today = today or datetime.now(timezone.utc).date()
        last_day = (
            date.fromisoformat(payload["last_day"]) if payload.get("last_day") else today
        )
        rule_id = int(payload["rule_id"])
        if not self.store.end_schedule_rule(household.id, rule_id, last_day):
            raise ValueError(f"no schedule rule {rule_id} for this household")
        return {"rule_id": rule_id, "active_until": last_day.isoformat()}

    def update_settings(self, household_id: str, payload: dict) -> dict:
        household = self.household(household_id)
        quiet = household.quiet_threshold_hours
        usual = household.usual_activity_by_hour
        if "quiet_threshold_hours" in payload:
            quiet = validate_quiet_threshold(payload["quiet_threshold_hours"])
        if "usual_activity_by_hour" in payload:
            usual = validate_hour(payload["usual_activity_by_hour"])
        updated = Household(
            id=household.id,
            name=str(payload.get("name") or household.name),
            agency_id=household.agency_id,
            quiet_threshold_hours=quiet,
            usual_activity_by_hour=usual,
            doors=household.doors,
        )
        self.store.upsert_household(updated)
        return {
            "household_id": updated.id,
            "name": updated.name,
            "quiet_threshold_hours": updated.quiet_threshold_hours,
            "usual_activity_by_hour": updated.usual_activity_by_hour,
        }

    # --- export -----------------------------------------------------------

    def export_csv(self, household_id: str, start_day: date, end_day: date) -> str:
        household = self.household(household_id)
        return export_mod.events_csv(
            household,
            self.log_for(household),
            start_day,
            end_day,
            notes=self.store.notes_between(household_id, start_day, end_day),
        )

    def export_report(self, household_id: str, start_day: date, end_day: date) -> str:
        household = self.household(household_id)
        return export_mod.visit_report(
            household=household,
            log=self.log_for(household),
            rules=self.store.schedule_rules(household_id),
            start_day=start_day,
            end_day=end_day,
            history_available_since=self.history_since(household),
            notes=self.store.notes_between(household_id, start_day, end_day),
        )
