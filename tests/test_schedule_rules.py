"""Recurring schedule rules: the configurable replacement for the fixture."""

from datetime import date, time

import pytest

from threshold.schedule import (
    ScheduleRule,
    parse_clock,
    parse_days_of_week,
    visits_for_date,
)

WEEKDAYS = (0, 1, 2, 3, 4)


def _rule(**kwargs):
    defaults = dict(
        label="Morning visit",
        days_of_week=WEEKDAYS,
        start_time=time(8, 0),
        end_time=time(8, 45),
    )
    defaults.update(kwargs)
    return ScheduleRule(**defaults)


def test_rule_applies_on_its_own_weekdays_only():
    rule = _rule()
    assert rule.applies_on(date(2026, 9, 21)) is True   # Monday
    assert rule.applies_on(date(2026, 9, 19)) is False  # Saturday


def test_rule_expands_to_a_visit_at_the_right_time_on_the_right_day():
    visit = _rule().visit_for(date(2026, 9, 21))

    assert visit.label == "Morning visit"
    assert visit.scheduled_start.isoformat() == "2026-09-21T08:00:00+00:00"
    assert visit.scheduled_end.isoformat() == "2026-09-21T08:45:00+00:00"


def test_a_window_crossing_midnight_lands_on_the_next_day_not_a_negative_span():
    rule = _rule(label="Night call", start_time=time(22, 30), end_time=time(0, 15))

    visit = rule.visit_for(date(2026, 9, 21))

    assert visit.scheduled_end > visit.scheduled_start
    assert visit.scheduled_end.date() == date(2026, 9, 22)


def test_active_from_means_a_package_change_does_not_rewrite_history():
    rule = _rule(active_from=date(2026, 9, 21))

    assert rule.applies_on(date(2026, 9, 14)) is False  # before the package started
    assert rule.applies_on(date(2026, 9, 21)) is True


def test_active_until_closes_a_package_without_deleting_it():
    rule = _rule(active_until=date(2026, 9, 18))

    assert rule.applies_on(date(2026, 9, 18)) is True
    assert rule.applies_on(date(2026, 9, 21)) is False


def test_visits_for_date_returns_every_matching_rule_in_time_order():
    rules = [
        _rule(label="Evening visit", start_time=time(17, 0), end_time=time(17, 45)),
        _rule(label="Morning visit"),
        _rule(label="Midday check-in", start_time=time(12, 0), end_time=time(12, 30)),
    ]

    visits = visits_for_date(rules, date(2026, 9, 21))

    assert [v.label for v in visits] == ["Morning visit", "Midday check-in", "Evening visit"]


def test_visits_for_date_returns_nothing_on_a_day_no_rule_covers():
    assert visits_for_date([_rule()], date(2026, 9, 19)) == []


@pytest.mark.parametrize("raw,expected", [("0,1,2", (0, 1, 2)), ([4, 0, 4], (0, 4))])
def test_parse_days_of_week_accepts_strings_and_lists_and_deduplicates(raw, expected):
    assert parse_days_of_week(raw) == expected


@pytest.mark.parametrize("raw", ["", "7", "-1", "Monday", []])
def test_parse_days_of_week_rejects_nonsense(raw):
    with pytest.raises(ValueError):
        parse_days_of_week(raw)


def test_parse_clock_accepts_hh_mm():
    assert parse_clock("08:00") == time(8, 0)


@pytest.mark.parametrize("raw", ["8am", "25:00", "", None])
def test_parse_clock_rejects_nonsense(raw):
    with pytest.raises(ValueError):
        parse_clock(raw)


def test_days_label_reads_as_a_person_would_write_it():
    assert _rule(days_of_week=(0, 2, 4)).days_label == "Mon, Wed, Fri"
