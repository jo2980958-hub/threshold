"""The weekly panel is not allowed to contradict itself about who wrote it.

The defect this file exists for: `narrate.summarise` returns
`source=SOURCE_MODEL` with `rejected_reason` still populated when the first draft
was refused and the retry passed the gate. The panel read `rejected_reason` on
its own and printed "The summary above is the one the rules wrote instead" two
lines under "Written by Amazon Bedrock".
"""

from datetime import date, datetime, timezone

from threshold import digest as digest_mod
from threshold import phrasing, provenance
from threshold.app import ThresholdApp
from threshold.events import parse_webhook_event
from threshold.household import Door, Household
from threshold.narrate import SOURCE_FALLBACK, SOURCE_MODEL

DISCARDED = "the summary above is the one the rules wrote instead"


def test_no_notice_at_all_when_nothing_was_refused():
    assert provenance.gate_notice(SOURCE_MODEL, None) is None
    assert provenance.gate_notice(SOURCE_FALLBACK, None) is None
    assert provenance.gate_notice(SOURCE_MODEL, "") is None


def test_a_corrected_draft_is_never_described_as_discarded():
    notice = provenance.gate_notice(
        SOURCE_MODEL, "It contained a number the rules did not compute."
    )

    assert notice is not None
    assert DISCARDED not in notice.lower()
    assert "was not shown" not in notice
    assert "first draft was refused" in notice
    assert "passed the same check" in notice


def test_a_discarded_draft_still_says_the_rules_wrote_the_summary():
    notice = provenance.gate_notice(
        SOURCE_FALLBACK, "It contained a number the rules did not compute."
    )

    assert notice is not None
    assert DISCARDED in notice.lower()
    assert "was not shown" in notice


def test_the_reason_is_punctuated_so_the_sentences_do_not_run_together():
    notice = provenance.gate_notice(SOURCE_MODEL, "It named a person")

    assert "It named a person. It was asked again" in notice


class _RefusesOnceThenComplies:
    """First draft breaks the gate; second one does not. The real retry path."""

    def __init__(self, good_text):
        self.good_text = good_text
        self.calls = 0

    def converse(self, **kwargs):
        self.calls += 1
        text = "The carer did not turn up on Tuesday." if self.calls == 1 else self.good_text
        return {"output": {"message": {"content": [{"text": text}]}}}


def _app_with_one_week_of_events():
    app = ThresholdApp(webhook_secret="secret")
    app.store.upsert_household(
        Household(
            id="oakfield",
            name="Oakfield Road",
            doors=(Door("front_door_1", "oakfield", "Front door", is_primary=True),),
        )
    )
    for day in (21, 22, 23):
        event = parse_webhook_event(
            {
                "request_id": f"evt-{day}",
                "device_id": "front_door_1",
                "event_type": "motion_detected",
                "created_at": f"2026-09-{day}T08:05:00Z",
                "attributes": {"sub_type": "human"},
            }
        )
        app.log.record(event)
        app.store.save(event)
    return app


def _stubbed(app):
    """The app, plus a client whose second draft is the rules' own summary."""
    digest = app._digest_for(app.household("oakfield"), date(2026, 9, 21))
    # The rules' own summary minus the boundary note, which `narrate` appends
    # after the gate and which the gate refuses on its way in.
    written = digest_mod.deterministic_summary(digest)
    app.bedrock_client = _RefusesOnceThenComplies(
        written.replace(phrasing.BOUNDARY_NOTE, "").strip()
    )
    return app


def test_weekly_payload_carries_a_notice_that_agrees_with_its_own_source():
    app = _stubbed(_app_with_one_week_of_events())

    payload = app.weekly_summary("oakfield", date(2026, 9, 23))

    # The precondition the panel got wrong: a model-sourced summary that still
    # carries a refusal.
    assert payload["source"] == SOURCE_MODEL
    assert payload["rejected_reason"]
    assert DISCARDED not in payload["gate_notice"].lower()


def test_the_cached_read_of_the_same_week_says_the_same_thing():
    app = _stubbed(_app_with_one_week_of_events())

    first = app.weekly_summary("oakfield", date(2026, 9, 23))
    cached = app.weekly_summary("oakfield", date(2026, 9, 23))

    assert cached["cached"] is True
    assert cached["gate_notice"] == first["gate_notice"]


def test_the_panel_prints_the_server_s_notice_rather_than_composing_one():
    """`web/index.html` must not rebuild the sentence from `rejected_reason`."""
    page = (
        __import__("pathlib").Path(__file__).resolve().parent.parent
        / "threshold"
        / "web"
        / "index.html"
    ).read_text()

    body = page[page.index("async function loadWeekly") : page.index("async function loadDay")]
    code = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("//")
    )

    assert "data.gate_notice" in code
    assert "rules wrote instead" not in code
    assert "data.rejected_reason" not in code
