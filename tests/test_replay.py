from pathlib import Path

from threshold.app import ThresholdApp
from threshold.replay import load_fixture, load_schedule_fixture, replay_events
from threshold.schedule import compare_visit

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_normal_day_fixture_replays_cleanly_and_signature_verifies():
    app = ThresholdApp(webhook_secret="demo-webhook-secret")
    events = load_fixture(FIXTURES / "normal_day.json")

    results = replay_events(app, events)

    assert all(status == 200 for status, _ in results)
    assert len(app.log) == len(events)


def test_normal_day_fixture_shows_both_outcomes_side_by_side():
    """The point of this fixture: a morning and an evening visit both show
    front-door activity, and the midday visit -- deliberately left silent to
    represent a key-safe or back-door arrival -- shows 'no arrival was
    recorded,' never an accusation."""
    app = ThresholdApp(webhook_secret="demo-webhook-secret")
    events = load_fixture(FIXTURES / "normal_day.json")
    replay_events(app, events)

    visits = load_schedule_fixture(FIXTURES / "schedule.json")
    history_since = min(e["created_at"] for e in events)
    from datetime import datetime

    since_dt = datetime.fromisoformat(history_since.replace("Z", "+00:00"))

    results = {v.label: compare_visit(v, app.log, since_dt) for v in visits}

    assert results["Morning visit"].activity_recorded is True
    assert results["Evening visit"].activity_recorded is True
    assert results["Midday check-in"].activity_recorded is False


def test_wheelchair_misclassified_fixture_still_records_as_activity():
    app = ThresholdApp(webhook_secret="demo-webhook-secret")
    fixture_path = FIXTURES / "wheelchair_misclassified.json"
    events = load_fixture(fixture_path)
    replay_events(app, events)

    visits = load_schedule_fixture(fixture_path)
    from datetime import datetime, timezone

    result = compare_visit(visits[0], app.log, datetime(2020, 1, 1, tzinfo=timezone.utc))

    assert result.activity_recorded is True
    recorded_event = app.log.all_events()[0]
    assert recorded_event.sub_type == "package"  # the misclassification is preserved for display
    assert recorded_event.is_door_activity is True  # but never used to exclude it


def test_duplicate_delivery_fixture_is_deduplicated():
    app = ThresholdApp(webhook_secret="demo-webhook-secret")
    events = load_fixture(FIXTURES / "duplicate_delivery.json")

    results = replay_events(app, events)

    statuses = [body["status"] for _, body in results]
    assert statuses == ["recorded", "duplicate, ignored", "recorded"]
    assert len(app.log) == 2
