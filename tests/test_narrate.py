"""The Bedrock integration, and the gate that stops it deciding anything.

Every test here runs with a stubbed client. Nothing in this file touches the
network, so the suite passes with no AWS credentials, no boto3, and no
connection, which is the same property the demo needs.
"""

from datetime import date, datetime, timezone

import pytest

from threshold import digest as digest_mod
from threshold import narrate, phrasing
from threshold.events import parse_webhook_event
from threshold.log import ArrivalLog
from threshold.schedule import ScheduleRule, parse_clock


class StubClient:
    """Stands in for boto3's bedrock-runtime client."""

    def __init__(self, text=None, error=None):
        self.text = text
        self.error = error
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"output": {"message": {"content": [{"text": self.text}]}}}


def _log():
    log = ArrivalLog()
    for day in (14, 15, 16, 17, 18):
        for hour, minute in ((8, 5), (8, 42), (12, 3)):
            log.record(
                parse_webhook_event(
                    {
                        "request_id": f"evt-{day}-{hour}-{minute}",
                        "device_id": "front_door_1",
                        "event_type": "motion_detected",
                        "created_at": f"2026-09-{day}T{hour:02d}:{minute:02d}:00Z",
                        "attributes": {"sub_type": "human"},
                    }
                )
            )
    return log


def _rules():
    return [
        ScheduleRule(
            label="Morning visit",
            days_of_week=(0, 1, 2, 3, 4),
            start_time=parse_clock("08:00"),
            end_time=parse_clock("08:45"),
        )
    ]


def _digest():
    return digest_mod.build(
        household_name="Oakfield Road",
        log=_log(),
        rules=_rules(),
        week_start=date(2026, 9, 14),
        history_available_since=datetime(2026, 9, 1, tzinfo=timezone.utc),
        door_count=2,
    )


# --- the happy path ---------------------------------------------------


def test_a_clean_model_summary_is_used_and_labelled_as_the_model_s():
    digest = _digest()
    client = StubClient(
        "The doors recorded 15 events between 2026-09-14 and 2026-09-20. "
        "2 of the 7 days recorded nothing at the front door."
    )

    summary = narrate.summarise(digest, client=client)

    assert summary.source == narrate.SOURCE_MODEL
    assert summary.from_model is True
    assert "15 events" in summary.text
    assert summary.rejected_reason is None


def test_the_boundary_note_is_appended_by_us_and_never_left_to_the_model():
    client = StubClient("The doors recorded 15 events in the week.")

    summary = narrate.summarise(_digest(), client=client)

    assert summary.text.endswith(phrasing.BOUNDARY_NOTE)


def test_the_model_only_ever_sees_facts_the_rules_computed():
    digest = _digest()
    client = StubClient("The doors recorded 15 events in the week.")

    narrate.summarise(digest, client=client)

    sent = client.calls[0]["messages"][0]["content"][0]["text"]
    assert digest.facts_block() in sent
    # No raw event ever reaches the prompt, so there is nothing for the model
    # to classify even if it wanted to.
    assert "request_id" not in sent
    assert "sub_type" not in sent


def test_the_call_is_made_with_a_deterministic_temperature_and_a_token_cap():
    client = StubClient("The doors recorded 15 events in the week.")

    narrate.summarise(_digest(), client=client)

    config = client.calls[0]["inferenceConfig"]
    assert config["temperature"] == 0.0
    assert config["maxTokens"] == narrate.MAX_TOKENS


# --- the gate ---------------------------------------------------------


def test_a_model_that_accuses_somebody_is_rejected_and_the_rules_answer_instead():
    """The assertion this whole integration exists to be able to make."""
    digest = _digest()
    client = StubClient("The carer did not come on Wednesday.")

    summary = narrate.summarise(digest, client=client)

    assert summary.source == narrate.SOURCE_FALLBACK
    assert "did not come" not in summary.text
    assert summary.text == digest_mod.deterministic_summary(digest)
    # The reason names the rule and quotes none of the draft. Putting the
    # refused sentence inside the explanation that it was refused delivers the
    # accusation to the family member anyway, which is the one thing this app
    # exists to prevent.
    assert "did not come" not in summary.rejected_reason
    assert "carer" not in summary.rejected_reason.lower()
    assert "refused-language list" in summary.rejected_reason


@pytest.mark.parametrize(
    "draft",
    [
        "The carer did not come on Wednesday.",
        "She was out all morning and nobody attended.",
        "The nurse missed the visit; the doors recorded 43 events.",
        "They skipped Tuesday entirely.",
    ],
)
def test_the_refusal_never_republishes_the_draft_it_refused(draft):
    """The rejection path is a second output channel around the gate.

    `rejected_reason` is persisted by store.py, re-served from cache by
    app.py and written onto the household's page by index.html. Whatever
    the model wrote must not survive the trip, however it is framed.
    """
    summary = narrate.summarise(_digest(), client=StubClient(draft))
    reason = summary.rejected_reason or ""

    assert reason, "a refusal should still say that the gate fired"
    for word in ("carer", "nurse", "she", "they", "nobody", "missed", "skipped", "43"):
        assert word not in reason.lower(), f"{word!r} from the draft reached the page"
    # And the draft is still readable where a developer is and a family is not.
    with pytest.raises(phrasing.PhrasingViolation) as excinfo:
        phrasing.assert_model_output_safe(draft, _digest().allowed_numbers())
    assert draft in str(excinfo.value)


@pytest.mark.parametrize(
    "draft",
    [
        "She was out all morning.",
        "The carer arrived at 8.",
        "Nobody visited the house this week.",
        "They recorded nothing on Friday.",
        "The nurse attended 5 times.",
    ],
)
def test_a_model_that_puts_a_person_at_the_door_is_rejected(draft):
    summary = narrate.summarise(_digest(), client=StubClient(draft))

    assert summary.source == narrate.SOURCE_FALLBACK


@pytest.mark.parametrize(
    "draft",
    [
        "This is cause for concern.",
        "The pattern suggests that something changed.",
        "We recommend calling the agency.",
        "The record proves the booked window was empty.",
    ],
)
def test_a_model_that_draws_a_conclusion_is_rejected(draft):
    summary = narrate.summarise(_digest(), client=StubClient(draft))

    assert summary.source == narrate.SOURCE_FALLBACK


def test_a_model_that_invents_a_number_is_rejected():
    """The anti-hallucination gate. 43 is not a figure the rules computed, so
    the sentence cannot be shown however plausible it reads."""
    summary = narrate.summarise(
        _digest(), client=StubClient("The doors recorded 43 events in the week.")
    )

    assert summary.source == narrate.SOURCE_FALLBACK
    # 43 is the hallucination. Printing it beside "this was rejected" puts an
    # invented figure on the page regardless of the frame around it.
    assert "43" not in summary.rejected_reason
    assert "did not compute" in summary.rejected_reason


def test_numbers_the_rules_did_compute_are_allowed_through():
    digest = _digest()
    assert str(digest.total_events) in digest.allowed_numbers()

    summary = narrate.summarise(
        digest,
        client=StubClient(f"The doors recorded {digest.total_events} events in the week."),
    )

    assert summary.source == narrate.SOURCE_MODEL


def test_an_empty_model_answer_falls_back():
    summary = narrate.summarise(_digest(), client=StubClient("   "))

    assert summary.source == narrate.SOURCE_FALLBACK


def test_a_leading_here_is_your_summary_line_is_stripped_rather_than_shown():
    client = StubClient("Here is your summary:\nThe doors recorded 15 events in the week.")

    summary = narrate.summarise(_digest(), client=client)

    assert summary.source == narrate.SOURCE_MODEL
    assert not summary.text.lower().startswith("here is")


# --- unreachable Bedrock ---------------------------------------------


def test_a_network_failure_falls_back_to_the_rules_and_never_raises():
    digest = _digest()
    client = StubClient(error=ConnectionError("could not connect to bedrock-runtime"))

    summary = narrate.summarise(digest, client=client)

    assert summary.source == narrate.SOURCE_FALLBACK
    assert summary.text == digest_mod.deterministic_summary(digest)
    assert "ConnectionError" in summary.rejected_reason


def test_a_credentials_failure_falls_back_too():
    client = StubClient(error=RuntimeError("Unable to locate credentials"))

    summary = narrate.summarise(_digest(), client=client)

    assert summary.source == narrate.SOURCE_FALLBACK


def test_a_malformed_bedrock_response_falls_back_rather_than_crashing_the_page():
    class Broken:
        def converse(self, **kwargs):
            return {"output": {}}

    summary = narrate.summarise(_digest(), client=Broken())

    assert summary.source == narrate.SOURCE_FALLBACK


def test_bedrock_can_be_switched_off_by_environment_without_touching_a_client(monkeypatch):
    monkeypatch.setenv("THRESHOLD_DISABLE_BEDROCK", "1")
    client = StubClient("The doors recorded 15 events in the week.")

    summary = narrate.summarise(_digest(), client=client)

    assert summary.source == narrate.SOURCE_FALLBACK
    assert client.calls == []


def test_the_fallback_summary_is_a_complete_answer_not_an_error_message():
    digest = _digest()

    text = digest_mod.deterministic_summary(digest)

    assert str(digest.total_events) in text
    assert "error" not in text.lower()
    assert "unavailable" not in text.lower()
    assert text.endswith(phrasing.BOUNDARY_NOTE)


def test_the_default_model_id_is_an_inference_profile_that_this_account_can_call():
    # Bare model ids such as anthropic.claude-sonnet-5 return AccessDenied on
    # this account even though list-foundation-models advertises them.
    assert narrate.DEFAULT_MODEL_ID.startswith("us.anthropic.")


def test_the_client_is_built_with_a_timeout_so_a_page_load_cannot_hang(monkeypatch):
    captured = {}

    class FakeConfig:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeBoto:
        @staticmethod
        def client(name, region_name=None, config=None):
            captured["name"] = name
            captured["region"] = region_name
            raise RuntimeError("stop here, the client is all we wanted to inspect")

    import sys
    import types

    fake_botocore = types.ModuleType("botocore")
    fake_config_mod = types.ModuleType("botocore.config")
    fake_config_mod.Config = FakeConfig
    fake_botocore.config = fake_config_mod
    monkeypatch.setitem(sys.modules, "boto3", FakeBoto)
    monkeypatch.setitem(sys.modules, "botocore", fake_botocore)
    monkeypatch.setitem(sys.modules, "botocore.config", fake_config_mod)

    summary = narrate.summarise(_digest(), timeout=3.0)

    assert captured["connect_timeout"] == 3.0
    assert captured["read_timeout"] == 3.0
    assert captured["name"] == "bedrock-runtime"
    assert summary.source == narrate.SOURCE_FALLBACK


# --- the bounded retry ------------------------------------------------


class ScriptedClient:
    """Returns a different draft on each call, so the retry path is visible."""

    def __init__(self, *drafts):
        self.drafts = list(drafts)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        text = self.drafts[min(len(self.calls) - 1, len(self.drafts) - 1)]
        return {"output": {"message": {"content": [{"text": text}]}}}


def test_a_refused_draft_is_sent_back_with_the_reason_and_the_corrected_one_is_used():
    """The case this was built for: "12 of them" is refused because "them" is
    how a sentence starts referring to people, and the model can simply say
    "12 of the visits" instead."""
    client = ScriptedClient(
        "Front-door activity was recorded for 12 of them.",
        "Front-door activity was recorded for 12 of the booked visits.",
    )

    summary = narrate.summarise(_digest(), client=client)

    assert summary.source == narrate.SOURCE_MODEL
    assert "12 of the booked visits" in summary.text
    assert len(client.calls) == 2


def test_the_retry_turn_tells_the_model_which_rule_it_broke():
    client = ScriptedClient(
        "Front-door activity was recorded for 12 of them.",
        "Front-door activity was recorded for 12 of the booked visits.",
    )

    narrate.summarise(_digest(), client=client)

    retry = client.calls[1]["messages"]
    assert retry[1]["role"] == "assistant"
    assert retry[2]["role"] == "user"
    assert "them" in retry[2]["content"][0]["text"]


def test_a_corrected_summary_still_reports_the_draft_that_was_refused():
    """Hiding the refusal would be hiding the gate working, which is the most
    informative thing this integration does."""
    client = ScriptedClient(
        "Front-door activity was recorded for 12 of them.",
        "Front-door activity was recorded for 12 of the booked visits.",
    )

    summary = narrate.summarise(_digest(), client=client)

    assert summary.source == narrate.SOURCE_MODEL
    assert summary.rejected_reason is not None
    assert "do not allow about people" in summary.rejected_reason
    assert "for 12 of them" not in summary.rejected_reason


def test_the_retry_is_bounded_at_two_attempts_and_then_the_rules_answer():
    client = ScriptedClient("The carer did not come.", "She did not come either.")

    summary = narrate.summarise(_digest(), client=client)

    assert summary.source == narrate.SOURCE_FALLBACK
    assert len(client.calls) == narrate.MAX_ATTEMPTS == 2
    assert "Then again:" in summary.rejected_reason


def test_a_clean_first_draft_costs_exactly_one_call():
    client = ScriptedClient("The doors recorded 15 events in the week.")

    narrate.summarise(_digest(), client=client)

    assert len(client.calls) == 1


# --- the model preference chain ---------------------------------------


class AccessDeniedException(Exception):
    """Named to match botocore's generated exception class."""


class ChainClient:
    """Denies every model until one of them is `allowed`."""

    def __init__(self, allowed):
        self.allowed = allowed
        self.tried = []

    def converse(self, **kwargs):
        self.tried.append(kwargs["modelId"])
        if kwargs["modelId"] != self.allowed:
            raise AccessDeniedException(
                f"{kwargs['modelId']} is not available for this account"
            )
        return {
            "output": {
                "message": {"content": [{"text": "The doors recorded 15 events in the week."}]}
            }
        }


def _reset_chain():
    narrate._working_model = None


def test_a_model_this_account_cannot_call_steps_to_the_next_one():
    """list-foundation-models advertises ids that Converse refuses, so the id
    that works has to be discovered rather than assumed."""
    _reset_chain()
    client = ChainClient(allowed=narrate.MODEL_PREFERENCE[1])

    summary = narrate.summarise(_digest(), client=client)

    assert summary.source == narrate.SOURCE_MODEL
    assert summary.model_id == narrate.MODEL_PREFERENCE[1]
    assert client.tried[0] == narrate.MODEL_PREFERENCE[0]


def test_the_working_model_is_remembered_so_the_next_page_load_does_not_re_probe():
    _reset_chain()
    first = ChainClient(allowed=narrate.MODEL_PREFERENCE[1])
    narrate.summarise(_digest(), client=first)

    second = ChainClient(allowed=narrate.MODEL_PREFERENCE[1])
    narrate.summarise(_digest(), client=second)

    assert second.tried == [narrate.MODEL_PREFERENCE[1]]
    _reset_chain()


def test_the_chain_running_out_falls_back_to_the_rules():
    _reset_chain()
    client = ChainClient(allowed="some-model-nobody-has")

    summary = narrate.summarise(_digest(), client=client)

    assert summary.source == narrate.SOURCE_FALLBACK
    assert client.tried == list(narrate.MODEL_PREFERENCE)
    _reset_chain()


def test_an_explicit_model_id_is_used_alone_and_never_silently_swapped():
    _reset_chain()
    client = ChainClient(allowed="only-this-one")

    narrate.summarise(_digest(), client=client, model_id="only-this-one")

    assert client.tried == ["only-this-one"]
    _reset_chain()


def test_a_timeout_does_not_walk_the_whole_chain_while_a_page_waits():
    """Three models in a row at eight seconds each is a page that never loads.
    A connection failure means Bedrock is unreachable, not that this model is."""
    _reset_chain()

    class Timeout:
        def __init__(self):
            self.tried = []

        def converse(self, **kwargs):
            self.tried.append(kwargs["modelId"])
            raise ConnectionError("read timeout on bedrock-runtime")

    client = Timeout()
    summary = narrate.summarise(_digest(), client=client)

    assert summary.source == narrate.SOURCE_FALLBACK
    assert len(client.tried) == 1
    _reset_chain()


def test_no_aws_account_identifier_appears_anywhere_in_the_source():
    """This directory becomes a public repository. Credentials come from the
    environment; the code needs a region and nothing else."""
    import re
    from pathlib import Path

    root = Path(__file__).parent.parent
    for path in list(root.rglob("*.py")) + list(root.rglob("*.md")) + list(root.rglob("*.json")):
        if ".venv" in path.parts or ".pytest_cache" in path.parts:
            continue
        for match in re.findall(r"\b\d{12}\b", path.read_text()):
            raise AssertionError(f"12-digit number that looks like an AWS account in {path}: {match}")
