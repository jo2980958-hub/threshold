"""Export: the file a family takes to the agency.

This is the surface where the phrasing rule matters most, because it is the one
artefact that leaves the app and gets read by someone with no context and an
interest in a conclusion. A CSV of door events handed to a care manager will be
read as an attendance record unless the file itself says it is not one. So both
formats carry `phrasing.EXPORT_HEADER` before any row, the CSV as comment lines
and the report as its first paragraph, and the visit column holds the same three
sentences `phrasing.py` allows anywhere else. There is no "status: missed".
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timezone

from . import phrasing
from .household import Household
from .log import ArrivalLog
from .schedule import ScheduleRule, compare_visit, visits_for_date

CSV_COLUMNS = (
    "date",
    "timestamp",
    "provenance",
    "door",
    "device_id",
    "recorded_as",
    "label_provenance",
    "ring_sub_type",
)


APP_VERSION = "1.0.0"
FORM_ID = "TH-VR Rev. 2026-09"


def record_number(household: Household, start_day: date, end_day: date) -> str:
    """A stable identifier for one household's record over one range.

    Derived, not generated. A care manager who asks for the same range again
    gets the same number, so two copies of this file can be recognised as the
    same record rather than as two records that happen to agree.
    """
    return (
        f"TH-{household.id.upper()}-"
        f"{start_day.isoformat().replace('-', '')}-{end_day.isoformat().replace('-', '')}"
    )


def _provenance_lines(household: Household, history_available_since: datetime | None) -> list[str]:
    """The provenance block, as flat label/value lines.

    Every line is something this app can actually state. Where it cannot — the
    clock drift, which Ring publishes no reference for — it says that rather
    than leaving the field out, because a record with no gaps in it reads as
    generated, and this one has real gaps.
    """
    doors = ", ".join(f"{d.label} ({d.device_id})" for d in household.doors) or "none registered"
    since = (
        phrasing.stamp(history_available_since, household.tz)
        if history_available_since
        else "not recorded"
    )
    pairs = [
        ("Source devices", doors),
        ("Coverage", "Each device sees its own doorway as installed. This app has no way "
                     "to inspect a camera's field of view and does not assert what it shows."),
        ("Blind spots", "A key safe, a back or side entrance, an arrival with a family "
                        "member, and anything outside a doorway. None of these appear here."),
        ("Event source", "Ring webhook. HMAC-SHA256 signature over the raw request body, "
                         "checked at receipt; redeliveries dropped on meta.request_id."),
        ("Clock", "Device clock, as reported by Ring in each event. Drift not measured: "
                  "Ring publishes no clock reference for a doorbell and this app does not "
                  "invent one. Times are not corrected for drift."),
        ("Record begins", f"{since}. Ring does not back-fill events from before a household "
                          "connected its account, so nothing before this time exists to report."),
        ("Software prose", "No prose in this file was written by a model. Every sentence "
                           "comes from a fixed set in threshold/phrasing.py with this "
                           "record's own values substituted."),
        ("Produced by", f"Threshold {APP_VERSION}, report renderer {APP_VERSION}."),
    ]
    out: list[str] = []
    for label, value in pairs:
        out.append(f"  {label:<16} {_wrap(value, 42)[0]}")
        out += [f"  {'':<16} {line}" for line in _wrap(value, 42)[1:]]
    return out


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines or [""]


def _day_bounds(day: date, tz=timezone.utc) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day, time.min, tzinfo=tz),
        datetime.combine(day, time.max, tzinfo=tz),
    )


def events_csv(
    household: Household,
    log: ArrivalLog,
    start_day: date,
    end_day: date,
    notes: list[dict] | None = None,
) -> str:
    """Every recorded door event in a date range, one row each."""
    out = io.StringIO()
    for line in phrasing.EXPORT_HEADER.split(". "):
        text = line.strip().rstrip(".")
        if text:
            out.write(f"# {text}.\n")
    out.write(f"# Household: {household.name}\n")
    out.write(f"# Timezone: {household.timezone_name}. Every timestamp carries its own offset.\n")
    out.write(f"# Range: {start_day.isoformat()} to {end_day.isoformat()}\n")
    for marker, explanation in phrasing.PROVENANCE_LEGEND.items():
        out.write(f"# {marker}: {explanation}\n")

    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    window_start, _ = _day_bounds(start_day, household.tz)
    _, window_end = _day_bounds(end_day, household.tz)
    for event in log.events_between(window_start, window_end):
        local = event.occurred_at.astimezone(household.tz)
        writer.writerow(
            [
                local.date().isoformat(),
                phrasing.stamp(event.occurred_at, household.tz),
                phrasing.PROVENANCE_RECORDED,
                household.door_label(event.device_id),
                event.device_id,
                event.display_label,
                # Ring's own classification, carried through so a reader can see
                # what the device thought. Marked REPORTED because that is what
                # it is: the device's opinion, from a classifier documented
                # calling a motorised wheelchair user a package. Never used to
                # include or exclude a row.
                phrasing.PROVENANCE_REPORTED if event.sub_type else phrasing.PROVENANCE_RECORDED,
                event.sub_type or "",
            ]
        )
    for note in notes or []:
        writer.writerow(
            [
                note["occurred_on"],
                note.get("at_time") or "",
                phrasing.PROVENANCE_REPORTED,
                "",
                "",
                note["text"],
                phrasing.PROVENANCE_REPORTED,
                f"written by {note['author']}",
            ]
        )
    return out.getvalue()


def visit_report(
    household: Household,
    log: ArrivalLog,
    rules: list[ScheduleRule],
    start_day: date,
    end_day: date,
    history_available_since: datetime | None,
    notes: list[dict] | None = None,
) -> str:
    """A plain-text report of booked visits against the door record.

    Plain text rather than PDF on purpose: it can be pasted into an email, read
    on a phone, and printed, and none of those steps can reformat a sentence
    into something it does not say.

    Every row is marked RECORDED, REPORTED or INFERRED in a printed word rather
    than a colour, so the distinction survives a photocopier, a colour-blind
    reader and a fax. Practice Direction 32 paragraph 18.2 has required filers to
    separate their own knowledge from information and belief since 1999, and this
    is the same distinction for the same reason.
    """
    notes = notes or []
    record_no = record_number(household, start_day, end_day)
    lines = [
        "THRESHOLD FRONT-DOOR RECORD",
        "=" * 60,
        "",
        phrasing.EXPORT_HEADER,
        "",
        f"Household: {household.name}",
        f"Doors recording: {len(household.doors)}",
        f"Timezone: {household.timezone_name}",
        f"Range: {start_day.isoformat()} to {end_day.isoformat()}",
        # The identity block. A care manager who needs to quote this file needs
        # something to quote it by, and a record with no number cannot be
        # referred to in a phone call, filed against a case, or asked for again.
        # The number is derived from the household and the range rather than
        # from the clock, so asking for the same range twice gets the same
        # number rather than a new document.
        f"Record no.: {record_no}",
        f"Form: {FORM_ID}",
        f"Produced: {phrasing.stamp(datetime.now(timezone.utc), household.tz)}",
        "",
        # FRE 901(b)(9): a record is not authenticated by looking official, it
        # is authenticated by describing the process that produced it in enough
        # detail that somebody could check. Everything here is a fact this app
        # actually holds; where it does not hold one, it says so.
        "HOW THIS RECORD WAS PRODUCED",
        "-" * 60,
    ]
    lines += _provenance_lines(household, history_available_since)
    lines += [
        "",
        "HOW TO READ THIS",
        "-" * 60,
    ]
    for marker, explanation in phrasing.PROVENANCE_LEGEND.items():
        lines.append(f"  {marker:<9} {explanation}")
    lines += [
        "",
        "  Every time below carries the offset that was in force on that date,",
        "  so a reading six months later cannot drift across a clock change.",
        "",
        "-" * 60,
        "BOOKED VISITS",
        "-" * 60,
    ]

    day = start_day
    total = 0
    while day <= end_day:
        visits = visits_for_date(rules, day, household.tz)
        if visits:
            lines.append("")
            lines.append(day.strftime("%A %d %B %Y"))
            for visit in visits:
                total += 1
                comparison = compare_visit(visit, log, history_available_since)
                window = (
                    f"{visit.scheduled_start.strftime('%H:%M')}"
                    f"-{visit.scheduled_end.strftime('%H:%M')}"
                )
                lines.append(f"  {window}  {visit.label}")
                lines.append(f"    [{phrasing.PROVENANCE_INFERRED}] {comparison.status_text}")
                # An inferred finding has to name the rule it applied, the
                # window it applied it to, and what the doors could see during
                # that window. All three, every time. The absence case is the
                # one that matters: "no arrival was recorded" one careless
                # clause away from being an accusation, and a coverage
                # statement set smaller than the claim it qualifies, or left at
                # the foot of the page, is one the author hoped nobody reads.
                lines.append(
                    f"    {'':<9}  Rule applied: a door event with a timestamp inside "
                    f"{phrasing.stamp(visit.scheduled_start, household.tz)} to "
                    f"{phrasing.stamp(visit.scheduled_end, household.tz)}."
                )
                lines.append(
                    f"    {'':<9}  Doors checked: "
                    f"{', '.join(d.label for d in household.doors) or 'none registered'}. "
                    f"A carer using a key safe, a back or side entrance, or arriving with a "
                    f"family member would not appear in this window."
                )
                if comparison.door_event_count:
                    span = comparison.minutes_with_activity
                    lines.append(
                        f"    [{phrasing.PROVENANCE_RECORDED}] "
                        f"{comparison.door_event_count} door events recorded in the "
                        f"window, spanning {span} minutes."
                    )
                # The household's own account of this visit, on the same page,
                # quoted and attributed, never merged into the sentence above.
                for note in notes:
                    if note["occurred_on"] == day.isoformat() and note.get("visit_label") == visit.label:
                        lines.append(
                            f"    [{phrasing.PROVENANCE_REPORTED}] "
                            f"{note['author']} wrote: \"{note['text']}\""
                        )
        day = date.fromordinal(day.toordinal() + 1)

    if total == 0:
        lines.append("")
        lines.append("  No care visits were booked in this range.")

    window_start, _ = _day_bounds(start_day, household.tz)
    _, window_end = _day_bounds(end_day, household.tz)
    events = log.events_between(window_start, window_end)
    lines += [
        "",
        "-" * 60,
        f"FULL DOOR RECORD ({len(events)} events)",
        "-" * 60,
    ]
    current: date | None = None
    for event in events:
        local = event.occurred_at.astimezone(household.tz)
        if local.date() != current:
            current = local.date()
            lines.append("")
            lines.append(current.strftime("%A %d %B %Y"))
        marker = (
            phrasing.PROVENANCE_REPORTED if event.sub_type else phrasing.PROVENANCE_RECORDED
        )
        lines.append(
            f"  {phrasing.stamp(event.occurred_at, household.tz)}  "
            f"[{marker}]  {household.door_label(event.device_id)}  {event.display_label}"
        )
    if not events:
        lines.append("")
        lines.append("  No door activity was recorded in this range.")

    if notes:
        lines += [
            "",
            "-" * 60,
            f"WHAT THE HOUSEHOLD WROTE ({len(notes)} notes)",
            "-" * 60,
            "",
            "  The doorbell cannot see a key safe, a side entrance, or somebody",
            "  arriving with a family member. These are the people who live there",
            "  writing in what it could not see. Every line is theirs, not this",
            f"  app's, and is marked [{phrasing.PROVENANCE_REPORTED}] for that reason.",
        ]
        for note in notes:
            when = f" at {note['at_time']}" if note.get("at_time") else ""
            lines.append("")
            lines.append(f"  {note['occurred_on']}{when}")
            lines.append(
                f"    [{phrasing.PROVENANCE_REPORTED}] {note['author']} wrote: "
                f"\"{note['text']}\""
            )

    lines += ["", "-" * 60, phrasing.BOUNDARY_NOTE, ""]
    report = "\n".join(lines)
    # assert_safe runs over everything this app wrote, which is every line except
    # the quotations. A household's own words are theirs and are not censored;
    # what is guaranteed is that they arrive quoted, attributed and marked
    # REPORTED, which is what the rendering above does.
    phrasing.assert_safe("\n".join(_app_written_lines(lines, notes)))
    return report


def _app_written_lines(lines: list[str], notes: list[dict]) -> list[str]:
    quoted = {note["text"] for note in notes}
    return [line for line in lines if not any(text in line for text in quoted)]
