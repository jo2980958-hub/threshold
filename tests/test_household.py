"""Households, doors, and the settings that are per household rather than global."""

import pytest

from threshold.household import (
    Door,
    Household,
    validate_hour,
    validate_quiet_threshold,
)


def _household(**kwargs):
    defaults = dict(
        id="oakfield",
        name="Oakfield Road",
        doors=(
            Door("front_door_1", "oakfield", "Front door", is_primary=True),
            Door("back_door_1", "oakfield", "Back door"),
        ),
    )
    defaults.update(kwargs)
    return Household(**defaults)


def test_device_ids_lists_every_door_not_just_the_primary_one():
    assert _household().device_ids == ["front_door_1", "back_door_1"]


def test_primary_door_is_the_one_marked_primary():
    assert _household().primary_door.device_id == "front_door_1"


def test_primary_door_falls_back_to_the_first_door_when_none_is_marked():
    household = _household(doors=(Door("back_door_1", "oakfield", "Back door"),))

    assert household.primary_door.device_id == "back_door_1"


def test_a_household_with_no_doors_has_no_primary_door_rather_than_an_error():
    assert _household(doors=()).primary_door is None


def test_door_label_names_the_door_an_event_came_from():
    assert _household().door_label("back_door_1") == "Back door"


def test_an_unregistered_device_is_labelled_not_dropped():
    """Silently discarding an event from a door nobody registered would be the
    same mistake as gating on sub_type: it removes real activity from a
    household's own record on the strength of a configuration gap."""
    assert _household().door_label("shed_cam_9") == "Unrecognised door"


@pytest.mark.parametrize("value,expected", [(1, 1), ("12", 12), (72, 72)])
def test_quiet_threshold_accepts_a_sensible_range(value, expected):
    assert validate_quiet_threshold(value) == expected


@pytest.mark.parametrize("value", [0, -1, 73, 1000, "soon", None, ""])
def test_quiet_threshold_rejects_values_that_would_make_the_watch_useless(value):
    with pytest.raises(ValueError):
        validate_quiet_threshold(value)


@pytest.mark.parametrize("value,expected", [(0, 0), ("10", 10), (23, 23)])
def test_validate_hour_accepts_a_clock_hour(value, expected):
    assert validate_hour(value) == expected


@pytest.mark.parametrize("value", [-1, 24, "morning", None])
def test_validate_hour_rejects_anything_that_is_not_one(value):
    with pytest.raises(ValueError):
        validate_hour(value)


def test_a_household_with_no_agency_is_not_part_of_an_agency_roll_up():
    assert _household().agency_id is None
