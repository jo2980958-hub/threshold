import pytest

from threshold.events import MalformedEvent, parse_webhook_event


def _payload(**overrides):
    base = {
        "request_id": "evt-1",
        "device_id": "front_door_1",
        "event_type": "motion_detected",
        "created_at": "2026-09-22T08:05:02Z",
        "attributes": {"sub_type": "human"},
    }
    base.update(overrides)
    return base


def test_parses_motion_event():
    event = parse_webhook_event(_payload())
    assert event.request_id == "evt-1"
    assert event.device_id == "front_door_1"
    assert event.event_type == "motion_detected"
    assert event.sub_type == "human"
    assert event.is_door_activity is True


def test_parses_button_press_with_no_attributes():
    event = parse_webhook_event(_payload(event_type="button_press", attributes={}))
    assert event.event_type == "button_press"
    assert event.sub_type is None
    assert event.is_door_activity is True


def test_ignores_non_door_event_types_for_activity_flag():
    event = parse_webhook_event(_payload(event_type="device_online"))
    assert event.is_door_activity is False


def test_missing_required_field_raises_malformed_event():
    payload = _payload()
    del payload["request_id"]
    with pytest.raises(MalformedEvent):
        parse_webhook_event(payload)


def test_timestamp_without_z_suffix_is_treated_as_utc():
    event = parse_webhook_event(_payload(created_at="2026-09-22T08:05:02+00:00"))
    assert event.occurred_at.isoformat() == "2026-09-22T08:05:02+00:00"


def test_wheelchair_misclassified_as_package_still_counts_as_door_activity():
    """Reproduces the documented Ring defect: a motorized wheelchair user's
    motion event is tagged sub_type=package. This must never be excluded from
    the log, and sub_type must never gate is_door_activity."""
    event = parse_webhook_event(_payload(attributes={"sub_type": "package"}))
    assert event.is_door_activity is True
    assert event.sub_type == "package"


@pytest.mark.parametrize(
    "sub_type,expected_label",
    [
        ("human", "Motion (person-shaped)"),
        ("package", "Motion (possible delivery)"),
        ("vehicle", "Motion (vehicle)"),
        ("animal", "Motion (animal)"),
        (None, "Motion"),
    ],
)
def test_display_label_is_a_hint_not_a_filter(sub_type, expected_label):
    event = parse_webhook_event(_payload(attributes={"sub_type": sub_type} if sub_type else {}))
    assert event.display_label == expected_label
    # Regardless of the label shown, the event still counts as activity.
    assert event.is_door_activity is True


def test_button_press_display_label():
    event = parse_webhook_event(_payload(event_type="button_press", attributes={}))
    assert event.display_label == "Doorbell pressed"
