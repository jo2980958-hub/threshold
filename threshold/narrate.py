"""Amazon Bedrock writes the weekly summary. It does not decide anything.

Where the model earns its place
-------------------------------
Threshold's argument is that it reports rather than judges, so a model must
never decide whether a visit happened. Everything a judge would call a decision
has already been made before this module runs: `schedule.compare_visit` decided
whether door activity fell inside a booked window, `silence.evaluate` decided
whether a household has been quiet too long, and `phrasing.py` decided the words.
`digest.build` collected those outcomes into numbers.

What is left is a writing job, and it is a real one. The family member three
hours away is not going to read a table of seven day-counts and four visit
tallies. They will read four sentences. Turning the former into the latter, in
plain language, with the boundary intact, is the step this product genuinely
needed a model for and the only step where one is used.

How the boundary is enforced, not just asserted
-----------------------------------------------
1. The model is shown `digest.facts_block()` and nothing else. It never sees a
   raw event, so it has nothing to classify.
2. Every sentence it returns goes through `phrasing.assert_model_output_safe`,
   the same choke point every other judge-facing string goes through, with a
   stricter word list and a numeric grounding check. A number the rules did not
   compute is a rejection.
3. Rejection is not an error path shown to a user. It falls back to
   `digest.deterministic_summary`, which is a complete answer written by rules.
   The same fallback covers a missing boto3, missing credentials, a timeout, or
   Bedrock being unreachable.

`tests/test_narrate.py` stubs the client and proves all of it, including the case
where the model tries to write an accusation.

What happens when Bedrock is not there
--------------------------------------
The family gets the log without the prose, and nothing else changes. This is the
clearest statement of the model's place in the product: the weekly narration is
the only thing in Threshold a model touches, so an unreachable Bedrock costs a
paragraph and costs nothing else. The door log, the schedule comparison, the
silence watch, the export and every sentence in them are produced by rules and
are unaffected. A demo with the network unplugged is the same demo with one
panel labelled differently.

Model and region
----------------
`bedrock-runtime` in `us-east-1`, through the Converse API, against the first id
in `MODEL_PREFERENCE` this account can actually invoke. Region comes from the
environment; no account identifier appears anywhere in this repository. A
bare model id is not enough on this account -- see `MODEL_PREFERENCE` below.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import digest as digest_mod
from . import phrasing
from .digest import WeeklyDigest

# Tried in order, first one that answers wins, and the winner is remembered for
# the life of the process. A chain rather than one id because what an account
# may invoke is not what `list-foundation-models` advertises: the bare ids
# `anthropic.claude-sonnet-5` and `anthropic.claude-opus-5` are both listed here
# and both return AccessDeniedException on Converse. Only inference profiles are
# callable.
MODEL_PREFERENCE = (
    "us.anthropic.claude-sonnet-4-6",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
)
DEFAULT_MODEL_ID = MODEL_PREFERENCE[0]
# Region only. No account id belongs in this repository: credentials come from
# the environment, and the code needs a region and nothing else.
DEFAULT_REGION = "us-east-1"
# Short on purpose. A page load waits on this, and the fallback is a complete
# answer rather than a degraded one, so waiting is worth less than responding.
DEFAULT_TIMEOUT_SECONDS = 8.0
MAX_TOKENS = 400
# One retry when the gate refuses a draft, and no more. The model is told which
# rule it broke and asked again. In practice the first draft usually fails on
# something small and legitimate-sounding, "12 of them" being the one this was
# built for: "them" is banned because it is how a sentence starts referring to
# people, and a model that meant "12 of the visits" can simply say so. Bounded
# at two attempts because a third would be a model arguing with a rule, and the
# rule wins by construction.
MAX_ATTEMPTS = 2

SOURCE_MODEL = "bedrock"
SOURCE_FALLBACK = "rules"

SYSTEM_PROMPT = """You write one short weekly summary for a family member whose \
relative has a Ring doorbell logged by an app called Threshold.

You are given facts that were already computed from sensor data by rules. Your \
only job is to write them as plain English. You are not deciding anything.

Rules you must follow exactly:
- A doorbell records a door, not a person. Never say or imply that anyone came, \
did not come, arrived, visited, attended, or missed anything. Write about what \
was recorded at the door.
- Never use the words he, she, him, her, his, they, them, their, someone, \
somebody, anyone or nobody, for any purpose at all. Say "the visits" or "the \
days", never "them". Never name a role such as carer, nurse or visitor.
- Use only the numbers you are given. Do not calculate new ones, do not round, \
and do not estimate. Write numbers as digits.
- When you mention booked visits that had no door activity, write the whole \
phrase "no arrival was recorded at the front door". Never shorten it to \
something like "1 did not".
- Do not speculate about causes. Do not suggest what the reader should do. Do \
not describe anything as concerning, urgent or reassuring.
- Four sentences at most. Calm, ordinary language. No headings, no bullet \
points, no preamble such as "Here is your summary"."""

USER_PROMPT_TEMPLATE = """Here are this week's facts for one household.

{facts}

Write the summary now, following every rule."""

RETRY_PROMPT_TEMPLATE = """That draft was refused by the check that runs on \
everything shown to a family member. The reason:

{reason}

Write the summary again, fixing only that. Keep the same facts."""


class BedrockUnavailable(RuntimeError):
    """Raised when the model could not be reached or did not answer usefully."""


@dataclass(frozen=True)
class Summary:
    text: str
    source: str
    model_id: str | None = None
    # Set when a model answered but its answer was refused by the phrasing gate.
    # Surfaced in the API response and shown in the UI, because "the model tried
    # to say something it was not allowed to say and was stopped" is the single
    # most interesting thing this integration does.
    rejected_reason: str | None = None

    @property
    def from_model(self) -> bool:
        return self.source == SOURCE_MODEL

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "source": self.source,
            "model_id": self.model_id,
            "rejected_reason": self.rejected_reason,
        }


def _default_client(region: str, timeout: float):
    """Build a bedrock-runtime client, or raise BedrockUnavailable.

    boto3 is an optional dependency on purpose. The app is standard library
    everywhere else, the tests run without it, and a checkout with no AWS
    credentials still has a working weekly summary.
    """
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - exercised by absence, not by test
        raise BedrockUnavailable("boto3 is not installed") from exc

    config = Config(
        connect_timeout=timeout,
        read_timeout=timeout,
        retries={"max_attempts": 1, "mode": "standard"},
    )
    return boto3.client("bedrock-runtime", region_name=region, config=config)


def _invoke(client, model_id: str, messages: list[dict]) -> str:
    response = client.converse(
        modelId=model_id,
        system=[{"text": SYSTEM_PROMPT}],
        messages=messages,
        inferenceConfig={"maxTokens": MAX_TOKENS, "temperature": 0.0},
    )
    blocks = response["output"]["message"]["content"]
    text = "".join(block.get("text", "") for block in blocks).strip()
    if not text:
        raise BedrockUnavailable("model returned an empty summary")
    return text


def _turn(role: str, text: str) -> dict:
    return {"role": role, "content": [{"text": text}]}


# Remembered across calls so a household that is not entitled to the first model
# does not pay for a failed call on every page load.
_working_model: str | None = None


def _models_to_try(requested: str | None) -> tuple[str, ...]:
    if requested:
        return (requested,)
    if _working_model:
        return (_working_model,)
    return MODEL_PREFERENCE


def _invoke_with_preference(client, models: tuple[str, ...], messages: list[dict]):
    """Try each model in turn, stepping past the ones this account cannot call.

    Returns (text, model_id). Only an access or validation failure moves to the
    next model. A timeout or a connection failure is not a reason to try three
    models in a row while a page waits, so it propagates.
    """
    global _working_model
    last: Exception | None = None
    for model_id in models:
        try:
            text = _invoke(client, model_id, messages)
        except BedrockUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - botocore's error types vary
            name = type(exc).__name__
            if name in ("AccessDeniedException", "ValidationException", "ClientError") or (
                "AccessDenied" in str(exc) or "ValidationException" in str(exc)
            ):
                last = exc
                continue
            raise
        _working_model = model_id
        return text, model_id
    raise last or BedrockUnavailable("no model in the preference chain could be called")


def summarise(
    digest: WeeklyDigest,
    client=None,
    model_id: str | None = None,
    region: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Summary:
    """Return the weekly summary, from the model when it is allowed to stand.

    Never raises, and that is a contract rather than an observation. A guard
    that raises out of a function documented not to raise hands the caller a
    500 and, in this app, put the developer message -- which quotes the
    offending text -- in front of a family member. So the two places that could
    still throw are caught here: composing the fallback, which interpolates a
    household name somebody typed, and the gate on the assembled text.
    """
    try:
        fallback_text = digest_mod.deterministic_summary(digest)
    except phrasing.PhrasingViolation as exc:
        # `exc.rule`, never `str(exc)`: the message quotes the sentence, and
        # this string is rendered. Losing the week's prose is the right cost
        # for a household whose name trips a rule about what the app says.
        return Summary(text=phrasing.BOUNDARY_NOTE, source=SOURCE_FALLBACK, rejected_reason=exc.rule)
    model_id = model_id or os.environ.get("THRESHOLD_BEDROCK_MODEL") or None
    models = _models_to_try(model_id)
    region = region or os.environ.get("AWS_REGION", DEFAULT_REGION)
    used_model = models[0]

    if os.environ.get("THRESHOLD_DISABLE_BEDROCK"):
        return Summary(text=fallback_text, source=SOURCE_FALLBACK)

    allowed = digest.allowed_numbers()
    messages = [_turn("user", USER_PROMPT_TEMPLATE.format(facts=digest.facts_block()))]
    refusals: list[str] = []

    for attempt in range(MAX_ATTEMPTS):
        try:
            if client is None:
                client = _default_client(region, timeout)
            raw, used_model = _invoke_with_preference(client, models, messages)
        except BedrockUnavailable as exc:
            return Summary(text=fallback_text, source=SOURCE_FALLBACK, rejected_reason=str(exc))
        except Exception as exc:  # noqa: BLE001 - any client/network/credential failure
            # Deliberately broad. botocore raises a dozen distinct exception
            # types for "the call did not work", and every one of them means the
            # same thing here: use the summary the rules wrote.
            # The exception's own message is not repeated here. botocore puts
            # request ids, ARNs and occasionally the account number into it,
            # and this string is persisted, cached and rendered to a family
            # member. The type name says which kind of failure it was, which
            # is the part they and a developer both need.
            return Summary(
                text=fallback_text,
                source=SOURCE_FALLBACK,
                rejected_reason=f"Amazon Bedrock could not be reached ({type(exc).__name__}).",
            )

        cleaned = _strip_preamble(raw)
        try:
            phrasing.assert_model_output_safe(cleaned, allowed)
        except phrasing.PhrasingViolation as exc:
            # `exc.rule`, never `str(exc)`. The full message quotes the draft,
            # and `refusals` ends up in `rejected_reason`, which is persisted,
            # re-served from cache and written onto the household's page. A
            # refusal that republishes what it refused is a second output path
            # around the gate. The retry below still gets the full message,
            # because the model already wrote it.
            refusals.append(exc.rule)
            if attempt + 1 < MAX_ATTEMPTS:
                messages += [
                    _turn("assistant", cleaned),
                    _turn("user", RETRY_PROMPT_TEMPLATE.format(reason=exc)),
                ]
                continue
            return Summary(
                text=fallback_text,
                source=SOURCE_FALLBACK,
                model_id=used_model,
                rejected_reason=" Then again: ".join(refusals),
            )

        # The boundary note is not the model's to write, paraphrase or leave
        # out, so it is appended by us after the gate rather than asked for in
        # the prompt.
        text = f"{cleaned} {phrasing.BOUNDARY_NOTE}"
        try:
            guarded = phrasing.assert_safe(text)
        except phrasing.PhrasingViolation as exc:
            # Unreachable while the gate above holds, which is exactly why it
            # is caught: "cannot happen" is not a reason to let an exception
            # out of a function whose contract is that none do.
            return Summary(
                text=fallback_text,
                source=SOURCE_FALLBACK,
                model_id=used_model,
                rejected_reason=exc.rule,
            )
        return Summary(
            text=guarded,
            source=SOURCE_MODEL,
            model_id=used_model,
            # Kept even on success: a draft that was refused and then corrected
            # is the most informative thing this integration can show, and
            # hiding it would be hiding the gate working.
            rejected_reason=refusals[0] if refusals else None,
        )

    return Summary(text=fallback_text, source=SOURCE_FALLBACK, model_id=used_model)


def _strip_preamble(text: str) -> str:
    """Drop a leading "Here is..." line if the model adds one anyway."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and lines[0].rstrip(":").lower().startswith(("here is", "here's", "summary")):
        lines = lines[1:]
    return " ".join(lines).strip()
