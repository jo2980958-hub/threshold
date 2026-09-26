"""The HTTP surface, driven end to end against a real server on a real socket.

Uses the actual stdlib handler rather than calling ThresholdApp directly, so
routing, query parsing, status codes and content types are covered too.
"""

import json
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.server import HTTPServer
from pathlib import Path

import pytest

from threshold.app import ThresholdApp
from threshold.narrate import SOURCE_FALLBACK
from threshold.replay import (
    day_offset_to_today,
    load_fixture,
    replay_events,
    seed_households,
    shift_events_to_today,
)
from threshold.server import make_handler

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def server(monkeypatch):
    # No model call from a test, ever. The fallback path is a complete answer,
    # which is exactly what this asserts.
    monkeypatch.setenv("THRESHOLD_DISABLE_BEDROCK", "1")
    app = ThresholdApp(webhook_secret="demo-webhook-secret")
    raw = load_fixture(FIXTURES / "care_week.json")
    offset = day_offset_to_today(raw)
    seed_households(app, FIXTURES / "households.json", day_offset=offset)
    replay_events(app, shift_events_to_today(raw))

    httpd = HTTPServer(("127.0.0.1", 0), make_handler(app))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, app
    httpd.shutdown()
    httpd.server_close()


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as resp:
        return resp.status, resp.headers, resp.read().decode("utf-8")


def _get_json(base, path):
    status, _, body = _get(base, path)
    return status, json.loads(body)


def _post_json(base, path, payload):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


# --- pages -------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/history", "/agency"])
def test_every_page_is_served(server, path):
    base, _ = server
    status, headers, body = _get(base, path)

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "Threshold" in body


@pytest.mark.parametrize(
    "path,content_type",
    [("/threshold.css", "text/css"), ("/threshold.js", "application/javascript")],
)
def test_the_shared_assets_are_served_with_the_right_type(server, path, content_type):
    base, _ = server
    status, headers, _ = _get(base, path)

    assert status == 200
    assert headers["Content-Type"].startswith(content_type)


def test_an_unknown_route_is_a_404_in_json_not_a_stack_trace(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(base, "/nope")

    assert exc.value.code == 404


# --- the ledger --------------------------------------------------------


def test_day_returns_everything_the_ledger_needs_in_one_round_trip(server):
    base, _ = server
    status, body = _get_json(base, "/day?household=oakfield")

    assert status == 200
    assert set(body) >= {
        "date",
        "events",
        "comparisons",
        "silence",
        "household",
        "boundary_note",
        "boundary_note_short",
        "rules",
    }
    assert body["household"]["name"] == "Oakfield Road"
    assert len(body["household"]["doors"]) == 2


def test_the_ledger_opens_on_the_last_day_with_something_in_it(server):
    base, _ = server
    _, body = _get_json(base, "/day?household=oakfield")

    assert body["date"] == date.today().isoformat()


def test_a_household_that_stopped_recording_opens_on_its_last_day_not_an_empty_today(server):
    """millbrook stops two days before the fixture's today. Opening on an empty
    ledger would leave the viewer to work out how to get back to the data."""
    base, _ = server
    _, body = _get_json(base, "/day?household=millbrook")

    assert body["date"] < date.today().isoformat()
    assert body["events"]


def test_the_boundary_note_is_part_of_the_payload_not_a_string_in_the_page(server):
    base, _ = server
    _, body = _get_json(base, "/day?household=oakfield")

    assert "not a record of who provided care" in body["boundary_note"]
    assert body["boundary_note_short"]


def test_asking_for_a_specific_day_returns_that_day(server):
    base, _ = server
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    _, body = _get_json(base, f"/day?household=oakfield&date={yesterday}")

    assert body["date"] == yesterday
    assert all(e["occurred_at"].startswith(yesterday) for e in body["events"])


def test_filtering_to_one_door_narrows_the_log_to_that_door(server):
    base, _ = server
    _, all_doors = _get_json(base, "/log?household=oakfield")
    _, front_only = _get_json(base, "/log?household=oakfield&device_id=front_door_1")

    assert len(front_only["events"]) <= len(all_doors["events"])
    assert all(e["device_id"] == "front_door_1" for e in front_only["events"])


def test_a_door_that_belongs_to_another_household_yields_nothing_not_everything(server):
    base, _ = server
    _, body = _get_json(base, "/log?household=oakfield&device_id=front_door_2")

    assert body["events"] == []


def test_a_bad_date_is_a_400_not_a_500(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(base, "/day?household=oakfield&date=not-a-date")

    assert exc.value.code == 400


def test_an_unknown_household_is_a_404_with_a_readable_message(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(base, "/day?household=nowhere")

    assert exc.value.code == 404
    assert "unknown household" in exc.value.read().decode("utf-8")


# --- the silence watch -------------------------------------------------


def test_the_silence_route_exists_and_answers(server):
    base, _ = server
    status, body = _get_json(base, "/silence?household=millbrook")

    assert status == 200
    assert set(body) == {
        "state",
        "message",
        "hours_since_last_activity",
        "threshold_hours",
        "last_activity_at",
        "provenance",
    }
    assert body["provenance"] == "INFERRED"


def test_a_household_that_stopped_recording_raises_the_silence_alert(server):
    base, _ = server
    _, body = _get_json(base, "/silence?household=millbrook")

    assert body["state"] == "alert"
    assert "No front-door activity recorded" in body["message"]


def test_a_household_still_recording_is_within_its_pattern(server):
    base, _ = server
    _, body = _get_json(base, "/silence?household=oakfield")

    assert body["state"] in ("within_pattern", "alert")
    assert body["threshold_hours"] == 14


def test_a_freshly_linked_household_is_not_told_it_is_quiet(server):
    base, _ = server
    _, body = _get_json(base, "/silence?household=harewood")

    assert body["state"] == "not_enough_history"


# --- history and search ------------------------------------------------


def test_history_pages_through_the_record(server):
    base, _ = server
    _, first = _get_json(base, "/api/history?household=oakfield&limit=5&offset=0")
    _, second = _get_json(base, "/api/history?household=oakfield&limit=5&offset=5")

    assert first["total"] == second["total"] > 5
    assert len(first["events"]) == 5
    assert first["events"][0] != second["events"][0]


def test_history_is_newest_first(server):
    base, _ = server
    _, body = _get_json(base, "/api/history?household=oakfield&limit=10")

    stamps = [e["occurred_at"] for e in body["events"]]
    assert stamps == sorted(stamps, reverse=True)


def test_search_narrows_the_page_without_changing_the_record(server):
    base, _ = server
    _, everything = _get_json(base, "/api/history?household=oakfield&limit=500")
    _, bells = _get_json(base, "/api/history?household=oakfield&limit=500&q=doorbell")

    assert bells["total"] < everything["total"]
    assert all("Doorbell" in e["label"] for e in bells["events"])


def test_search_matches_a_door_name(server):
    base, _ = server
    _, body = _get_json(base, "/api/history?household=oakfield&limit=500&q=back_door")

    assert body["total"] > 0
    assert all(e["device_id"] == "back_door_1" for e in body["events"])


def test_a_date_range_narrows_the_history(server):
    base, _ = server
    today = date.today().isoformat()
    _, body = _get_json(
        base, f"/api/history?household=oakfield&from={today}T00:00:00Z&to={today}T23:59:59Z"
    )

    assert all(e["occurred_at"].startswith(today) for e in body["events"])


def test_a_search_that_matches_nothing_returns_an_empty_page_not_an_error(server):
    base, _ = server
    status, body = _get_json(base, "/api/history?household=oakfield&q=zzzzz")

    assert status == 200
    assert body["total"] == 0
    assert body["events"] == []


# --- the agency view ---------------------------------------------------


def test_the_agency_view_lists_every_household(server):
    base, _ = server
    status, body = _get_json(base, "/api/agency")

    assert status == 200
    assert {h["household_id"] for h in body["households"]} == {
        "oakfield",
        "millbrook",
        "harewood",
    }


def test_the_agency_view_puts_the_quiet_household_first(server):
    base, _ = server
    _, body = _get_json(base, "/api/agency")

    assert body["households"][0]["household_id"] == "millbrook"
    assert body["households"][0]["silence"]["state"] == "alert"


def test_the_agency_view_carries_the_boundary_note(server):
    base, _ = server
    _, body = _get_json(base, "/api/agency")

    assert "not a record of who provided care" in body["boundary_note"]


def test_the_agency_view_has_no_per_person_column_of_any_kind(server):
    """A doorbell cannot support a compliance score for a named worker, so the
    payload must not contain one for a UI to render by accident."""
    base, _ = server
    _, body = _get_json(base, "/api/agency")

    # The boundary note is the one place the word "carer" is allowed, and it is
    # allowed there precisely because it is saying a carer may not appear.
    rows = json.dumps(body["households"]).lower()
    for word in ("carer", "score", "compliance", "rating", "missed", "no-show", "performance"):
        assert word not in rows


def test_filtering_by_agency_returns_only_that_agency_s_households(server):
    base, _ = server
    _, mine = _get_json(base, "/api/agency?agency=northgate-care")
    _, theirs = _get_json(base, "/api/agency?agency=someone-else")

    assert len(mine["households"]) == 3
    assert theirs["households"] == []


# --- the weekly summary ------------------------------------------------


def test_the_weekly_summary_answers_without_a_model_when_bedrock_is_switched_off(server):
    base, _ = server
    status, body = _get_json(base, "/weekly?household=oakfield")

    assert status == 200
    assert body["source"] == SOURCE_FALLBACK
    assert body["text"]
    assert body["facts"]["days_covered"] == 7


def test_the_weekly_summary_is_cached_after_the_first_call(server):
    base, _ = server
    _, first = _get_json(base, "/weekly?household=oakfield")
    _, second = _get_json(base, "/weekly?household=oakfield")

    assert first["cached"] is False
    assert second["cached"] is True
    assert first["text"] == second["text"]


def test_refresh_bypasses_the_cache(server):
    base, _ = server
    _get_json(base, "/weekly?household=oakfield")
    _, refreshed = _get_json(base, "/weekly?household=oakfield&refresh=1")

    assert refreshed["cached"] is False


def test_the_weekly_summary_snaps_any_day_to_its_week(server):
    base, _ = server
    _, body = _get_json(base, "/weekly?household=oakfield&week_of=2026-09-17")

    assert body["week_start"] == "2026-09-14"


# --- export ------------------------------------------------------------


def test_the_csv_export_downloads_with_a_filename(server):
    base, _ = server
    today = date.today().isoformat()
    status, headers, body = _get(base, f"/export.csv?household=oakfield&from={today}&to={today}")

    assert status == 200
    assert headers["Content-Type"].startswith("text/csv")
    assert "attachment" in headers["Content-Disposition"]
    assert "oakfield" in headers["Content-Disposition"]
    assert body.startswith("#")


def test_the_report_export_is_plain_text_and_leads_with_the_boundary(server):
    base, _ = server
    today = date.today().isoformat()
    status, headers, body = _get(base, f"/export.txt?household=oakfield&from={today}&to={today}")

    assert status == 200
    assert headers["Content-Type"].startswith("text/plain")
    assert "does not record who was present" in body
    assert body.index("does not record who was present") < body.index("BOOKED VISITS")


# --- configuration -----------------------------------------------------


def test_a_schedule_rule_can_be_added_over_http_and_shows_up_on_the_day(server):
    base, _ = server
    weekday = date.today().weekday()
    status, created = _post_json(
        base,
        "/api/schedule-rules",
        {
            "household": "millbrook",
            "label": "New evening call",
            "days_of_week": [weekday],
            "start_time": "19:00",
            "end_time": "19:30",
        },
    )

    assert status == 201
    assert created["rule_id"] > 0

    _, day = _get_json(base, f"/day?household=millbrook&date={date.today().isoformat()}")
    assert [c["label"] for c in day["comparisons"]] == ["New evening call"]


def test_a_nonsense_schedule_rule_is_rejected_with_a_readable_message(server):
    base, _ = server
    status, body = _post_json(
        base,
        "/api/schedule-rules",
        {"household": "millbrook", "label": "Bad", "days_of_week": [9], "start_time": "x", "end_time": "y"},
    )

    assert status == 400
    assert "day of week" in body["error"]


def test_the_quiet_threshold_is_configurable_over_http(server):
    base, _ = server
    status, body = _post_json(
        base, "/api/settings", {"household": "millbrook", "quiet_threshold_hours": 48}
    )

    assert status == 200
    assert body["quiet_threshold_hours"] == 48

    _, silence = _get_json(base, "/silence?household=millbrook")
    assert silence["threshold_hours"] == 48


def test_raising_the_threshold_stops_the_hour_count_alert(server):
    """Deliberately asserts the message rather than the state. The usual-hour
    trigger is a second, independent reason this household can still be flagged,
    and whether it fires depends on the wall-clock hour the suite runs at; the
    hour-count trigger going quiet is the part the setting controls."""
    base, _ = server
    _, before = _get_json(base, "/silence?household=millbrook")
    assert before["state"] == "alert"
    assert "configured quiet threshold" in before["message"]

    _post_json(base, "/api/settings", {"household": "millbrook", "quiet_threshold_hours": 72})

    _, after = _get_json(base, "/silence?household=millbrook")
    assert after["threshold_hours"] == 72
    assert "configured quiet threshold" not in after["message"]


def test_an_out_of_range_threshold_is_rejected(server):
    base, _ = server
    status, body = _post_json(
        base, "/api/settings", {"household": "millbrook", "quiet_threshold_hours": 500}
    )

    assert status == 400
    assert "between 1 and 72" in body["error"]


def test_settings_for_an_unknown_household_are_a_404(server):
    base, _ = server
    status, _ = _post_json(base, "/api/settings", {"household": "nowhere", "name": "x"})

    assert status == 404


# --- the webhook still works -------------------------------------------


def test_the_webhook_route_still_accepts_a_signed_delivery(server):
    from threshold.webhook import sign

    base, app = server
    payload = {
        "request_id": "evt-live-1",
        "device_id": "front_door_1",
        "event_type": "button_press",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "attributes": {},
    }
    raw = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + "/webhooks/ring",
        data=raw,
        headers={"X-Signature": sign(app.webhook_secret, raw)},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    assert body["status"] == "recorded"


def test_the_webhook_route_still_rejects_an_unsigned_delivery(server):
    base, _ = server
    req = urllib.request.Request(
        base + "/webhooks/ring", data=b"{}", headers={"X-Signature": "wrong"}, method="POST"
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)

    assert exc.value.code == 401
