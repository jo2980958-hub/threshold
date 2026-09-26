"""The export a family takes to the agency.

This is the artefact most likely to be read by somebody with no context and an
interest in a conclusion, so the phrasing rule is tested harder here than
anywhere else.
"""

from datetime import date, datetime, timezone

from threshold import export, phrasing
from threshold.events import parse_webhook_event
from threshold.household import Door, Household
from threshold.log import ArrivalLog
from threshold.schedule import ScheduleRule, parse_clock

HOUSEHOLD = Household(
    id="oakfield",
    name="Oakfield Road",
    doors=(
        Door("front_door_1", "oakfield", "Front door", is_primary=True),
        Door("back_door_1", "oakfield", "Back door"),
    ),
)
SINCE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _event(request_id, day, hour, minute=0, device="front_door_1", sub_type="human"):
    return parse_webhook_event(
        {
            "request_id": request_id,
            "device_id": device,
            "event_type": "motion_detected",
            "created_at": f"2026-09-{day:02d}T{hour:02d}:{minute:02d}:00Z",
            "attributes": {"sub_type": sub_type} if sub_type else {},
        }
    )


def _log(*events):
    log = ArrivalLog()
    for event in events:
        log.record(event)
    return log


def _rule():
    return ScheduleRule(
        label="Morning visit",
        days_of_week=(0, 1, 2, 3, 4),
        start_time=parse_clock("08:00"),
        end_time=parse_clock("08:45"),
    )


# --- CSV --------------------------------------------------------------


def test_csv_leads_with_what_the_file_does_not_claim():
    csv_text = export.events_csv(HOUSEHOLD, _log(), date(2026, 9, 14), date(2026, 9, 20))

    assert csv_text.startswith("#")
    assert "does not record who was present" in csv_text
    # The header has to come before any data, or a reader scanning rows never
    # meets it.
    assert csv_text.index("#") < csv_text.index("date,timestamp")


def test_csv_rows_carry_the_door_name_not_just_a_device_id():
    csv_text = export.events_csv(
        HOUSEHOLD,
        _log(_event("a", 14, 8, 5), _event("b", 14, 12, device="back_door_1")),
        date(2026, 9, 14),
        date(2026, 9, 14),
    )

    assert "Front door" in csv_text
    assert "Back door" in csv_text


def test_csv_carries_ring_s_own_classification_as_the_last_column_never_as_a_filter():
    csv_text = export.events_csv(
        HOUSEHOLD,
        _log(_event("a", 14, 15, sub_type="package")),
        date(2026, 9, 14),
        date(2026, 9, 14),
    )

    assert csv_text.strip().split("\n")[-1].endswith("package")
    assert "ring_sub_type" in csv_text
    # And the row is present at all, which is the point: a misclassified event
    # is still door activity.
    assert "Motion (possible delivery)" in csv_text


def test_csv_respects_the_date_range():
    csv_text = export.events_csv(
        HOUSEHOLD,
        _log(_event("in", 15, 9), _event("out", 22, 9)),
        date(2026, 9, 14),
        date(2026, 9, 20),
    )

    assert "2026-09-15" in csv_text
    assert "2026-09-22" not in csv_text


# --- the report -------------------------------------------------------


def test_the_report_opens_with_the_boundary_before_any_finding():
    text = export.visit_report(
        HOUSEHOLD, _log(), [_rule()], date(2026, 9, 14), date(2026, 9, 14), SINCE
    )

    assert phrasing.EXPORT_HEADER in text
    assert text.index(phrasing.EXPORT_HEADER) < text.index("BOOKED VISITS")


def test_the_report_closes_with_the_boundary_note_as_well():
    text = export.visit_report(
        HOUSEHOLD, _log(), [_rule()], date(2026, 9, 14), date(2026, 9, 14), SINCE
    )

    assert text.rstrip().endswith(phrasing.BOUNDARY_NOTE)


def test_a_booked_visit_with_nothing_recorded_uses_the_allowed_sentence_only():
    text = export.visit_report(
        HOUSEHOLD, _log(), [_rule()], date(2026, 9, 14), date(2026, 9, 14), SINCE
    )

    assert phrasing.NO_ARRIVAL_RECORDED in text
    lowered = text.lower()
    assert "missed" not in lowered
    assert "no show" not in lowered
    assert "attendance" not in lowered


def test_a_booked_visit_with_activity_reports_the_count_and_the_span():
    text = export.visit_report(
        HOUSEHOLD,
        _log(_event("a", 14, 8, 5), _event("b", 14, 8, 42)),
        [_rule()],
        date(2026, 9, 14),
        date(2026, 9, 14),
        SINCE,
    )

    assert phrasing.ARRIVAL_RECORDED in text
    assert "2 door events" in text
    assert "spanning 37 minutes" in text


def test_a_visit_before_the_history_starts_is_reported_as_not_comparable():
    text = export.visit_report(
        HOUSEHOLD,
        _log(),
        [_rule()],
        date(2026, 9, 14),
        date(2026, 9, 14),
        datetime(2026, 9, 20, tzinfo=timezone.utc),
    )

    assert "Not enough door history" in text
    assert phrasing.NO_ARRIVAL_RECORDED not in text


def test_the_report_says_so_when_nothing_was_booked_in_the_range():
    text = export.visit_report(
        HOUSEHOLD, _log(), [], date(2026, 9, 19), date(2026, 9, 19), SINCE
    )

    assert "No care visits were booked in this range." in text


def test_the_report_says_so_when_the_doors_recorded_nothing():
    text = export.visit_report(
        HOUSEHOLD, _log(), [], date(2026, 9, 19), date(2026, 9, 19), SINCE
    )

    assert "No door activity was recorded in this range." in text


def test_the_whole_report_passes_the_phrasing_gate():
    text = export.visit_report(
        HOUSEHOLD,
        _log(_event("a", 14, 8, 5), _event("b", 15, 12, device="back_door_1")),
        [_rule()],
        date(2026, 9, 14),
        date(2026, 9, 20),
        SINCE,
    )

    phrasing.assert_safe(text)


# --- provenance and the household's own account -----------------------

NOTE = {
    "occurred_on": "2026-09-14",
    "at_time": "12:10",
    "visit_label": "Morning visit",
    "author": "Anna",
    "text": "The carer did not come to the front, I let her in the back.",
}


def test_the_csv_explains_its_three_provenance_words_before_any_row():
    csv_text = export.events_csv(HOUSEHOLD, _log(), date(2026, 9, 14), date(2026, 9, 20))

    for marker in ("RECORDED", "REPORTED", "INFERRED"):
        assert f"# {marker}:" in csv_text
    assert csv_text.index("# RECORDED:") < csv_text.index("date,timestamp")


def test_a_csv_timestamp_carries_its_offset_so_it_survives_a_clock_change():
    london = Household(
        id="oakfield",
        name="Oakfield Road",
        timezone_name="Europe/London",
        doors=(Door("front_door_1", "oakfield", "Front door"),),
    )

    csv_text = export.events_csv(
        london, _log(_event("a", 14, 8, 5)), date(2026, 9, 14), date(2026, 9, 14)
    )

    assert "(UTC+01:00)" in csv_text
    assert "BST" in csv_text


def test_a_door_event_is_recorded_and_ring_s_guess_about_it_is_only_reported():
    csv_text = export.events_csv(
        HOUSEHOLD, _log(_event("a", 14, 8, 5)), date(2026, 9, 14), date(2026, 9, 14)
    )
    row = csv_text.strip().split("\n")[-1]

    assert ",RECORDED," in row  # the doorbell fired
    assert ",REPORTED," in row  # what it thought the shape was


def test_a_button_press_reports_nothing_second_hand():
    press = parse_webhook_event(
        {
            "request_id": "b",
            "device_id": "front_door_1",
            "event_type": "button_press",
            "created_at": "2026-09-14T08:05:00Z",
            "attributes": {},
        }
    )
    csv_text = export.events_csv(HOUSEHOLD, _log(press), date(2026, 9, 14), date(2026, 9, 14))

    assert "REPORTED" not in csv_text.strip().split("\n")[-1]


def test_a_household_note_travels_in_the_csv_marked_as_reported():
    csv_text = export.events_csv(
        HOUSEHOLD, _log(), date(2026, 9, 14), date(2026, 9, 14), notes=[NOTE]
    )
    row = csv_text.strip().split("\n")[-1]

    assert "REPORTED" in row
    assert "written by Anna" in row
    assert "RECORDED" not in row


def test_the_report_marks_a_comparison_as_inferred_not_as_something_it_saw():
    text = export.visit_report(
        HOUSEHOLD, _log(), [_rule()], date(2026, 9, 14), date(2026, 9, 14), SINCE
    )

    assert f"[INFERRED] {phrasing.NO_ARRIVAL_RECORDED}" in text


def test_the_report_explains_the_three_words_before_it_uses_them():
    text = export.visit_report(
        HOUSEHOLD, _log(), [_rule()], date(2026, 9, 14), date(2026, 9, 14), SINCE
    )

    assert "HOW TO READ THIS" in text
    assert text.index("HOW TO READ THIS") < text.index("BOOKED VISITS")


def test_a_household_note_sits_beside_the_visit_it_is_about_quoted_and_attributed():
    """The two-sided day, in the artefact that leaves the building."""
    text = export.visit_report(
        HOUSEHOLD, _log(), [_rule()], date(2026, 9, 14), date(2026, 9, 14), SINCE, notes=[NOTE]
    )

    assert '[REPORTED] Anna wrote: "' in text
    # And the machine's own answer is still there, unchanged, next to it.
    assert f"[INFERRED] {phrasing.NO_ARRIVAL_RECORDED}" in text


def test_a_household_note_is_quoted_verbatim_even_when_it_accuses_somebody():
    """Anna is entitled to say this about her own mother's house. What the app
    guarantees is that it arrives as her sentence, in quotation marks, marked
    REPORTED, and never as the app's own finding."""
    text = export.visit_report(
        HOUSEHOLD, _log(), [_rule()], date(2026, 9, 14), date(2026, 9, 14), SINCE, notes=[NOTE]
    )

    assert NOTE["text"] in text
    for line in text.split("\n"):
        if NOTE["text"] in line:
            assert "REPORTED" in line
            assert "Anna wrote:" in line


def test_the_app_s_own_lines_still_pass_the_phrasing_gate_when_a_note_accuses():
    """The document contains "did not come" because Anna wrote it. Nothing the
    app wrote does, and that is what is checked."""
    text = export.visit_report(
        HOUSEHOLD, _log(), [_rule()], date(2026, 9, 14), date(2026, 9, 14), SINCE, notes=[NOTE]
    )

    app_written = [line for line in text.split("\n") if NOTE["text"] not in line]
    phrasing.assert_safe("\n".join(app_written))


def test_the_report_says_whose_words_the_notes_section_holds():
    text = export.visit_report(
        HOUSEHOLD, _log(), [_rule()], date(2026, 9, 14), date(2026, 9, 14), SINCE, notes=[NOTE]
    )

    assert "WHAT THE HOUSEHOLD WROTE" in text
    assert "writing in what it could not see" in text


def test_the_report_states_the_household_timezone():
    london = Household(id="h", name="Oakfield Road", timezone_name="Europe/London")

    text = export.visit_report(london, _log(), [], date(2026, 9, 14), date(2026, 9, 14), SINCE)

    assert "Timezone: Europe/London" in text
