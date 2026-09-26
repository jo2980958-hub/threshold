"""The one place judge-facing strings about arrivals get written.

The whole product argument is: a doorbell sees a door, not a visit. Ring hands us
`sub_type: human` on a motion event and nothing else, and that classifier has been
documented calling a motorized wheelchair user a package. So this module is the
single choke point every other module must go through to describe what the door
log shows. Nothing outside this file is allowed to say a person did or did not
come. `tests/test_phrasing.py` asserts the forbidden phrases never appear in
anything `schedule.py`, `silence.py`, `digest.py` or `log.py` can produce.

This applies to model output too. `narrate.py` asks Amazon Bedrock to write the
weekly family summary, and every sentence that comes back goes through
`assert_model_output_safe` in this file before anything else may see it. A model
is a writer here, never a decider: it gets facts the rules already computed and
already phrased, and if what it returns fails the gate the caller falls back to a
deterministic sentence. `tests/test_narrate.py` proves that by feeding the gate a
stubbed model that tries to accuse someone.
"""

from __future__ import annotations

from .grounding import numbers_in  # noqa: F401  (re-exported for callers and tests)
from .matching import normalise, prepare  # noqa: F401  (re-exported)

# Every string this app can show for a schedule comparison outcome. Nothing else
# is permitted; schedule.py must return one of these keys, never build its own
# sentence.
ARRIVAL_RECORDED = "Front-door activity recorded during this visit."
NO_ARRIVAL_RECORDED = "No arrival was recorded at the front door during this visit."
NOT_ENOUGH_HISTORY = (
    "Not enough door history yet to compare against this visit. "
    "Ring does not back-fill events from before this household connected its account."
)

# The permanent context line. It ships with every schedule view, not as a
# disclaimer appended once, but as part of the screen.
BOUNDARY_NOTE = (
    "This is a record of activity at the front door, not a record of who provided "
    "care. A carer using a key safe, a back or side entrance, or arriving with a "
    "family member will not appear here. Treat a missing entry as missing "
    "information, not as evidence."
)

# The short form, for the ledger gutter where the full note will not fit. Says
# the same thing; a viewer scrolling the log cannot get past it.
BOUNDARY_NOTE_SHORT = (
    "A record of the front door, not of who provided care. "
    "Missing means missing information."
)

# The silence-alert lines (surface one, family use). Deliberately not alarmed:
# absence of a doorbell event is information, not an emergency.
NO_MORNING_ACTIVITY_YET = (
    "No front-door activity recorded yet today, later than the household's usual pattern."
)
QUIET_FOR_HOURS = (
    "No front-door activity recorded for {hours} hours, longer than this household's "
    "configured quiet threshold of {threshold} hours."
)
SILENCE_NOT_ENOUGH_HISTORY = (
    "Not enough door history yet to say whether today is quiet for this household."
)
SILENCE_WITHIN_PATTERN = "Front-door activity recorded within the household's usual pattern."
SILENCE_AWAY = (
    "The household recorded itself as away on this date, so the silence watch is not "
    "running."
)

# --- what the household knows, as opposed to what the door saw ---------
#
# The door cannot see a key safe, a side entrance, or a carer who arrived with
# a family member. That gap is the product's largest weakness and the sentence
# in BOUNDARY_NOTE apologises for it. A household note is the household writing
# the missing information onto the same day, so the record becomes what the door
# saw plus what the people there know.
#
# It is the one place a sentence on this page was not written by the rules, and
# so it is the one place the forbidden-phrase list does not apply. A daughter
# who writes "the carer didn't come" is entitled to say that about her own
# mother's house; an app that refused to let her would be censoring its user,
# not protecting a worker. What the rules guarantee instead is containment:
#
# 1. A note is always rendered as a quotation, attributed to the person who
#    wrote it and to the minute they wrote it. The app never says it.
# 2. The app never restates, summarises or paraphrases a note in its own voice.
# 3. A note never changes a visit's status. `compare_visit` does not see notes.
# 4. A note never reaches the Bedrock prompt. Letting a model read one would
#    launder a household's accusation into the app's own voice, which is exactly
#    the harm the rest of this file exists to prevent.
#
# `tests/test_notes.py` asserts all four.
NOTE_SOURCE_DOOR = "the doorbell"
NOTE_SOURCE_PERSON = "a person"

NOTE_LEAD_IN = "Added by {author} at {when}:"


def attribute_note(text: str, author: str, when: str) -> dict:
    """Render a household note as an attributed quotation, never as our voice.

    Returns the parts separately rather than one string so no caller can
    accidentally concatenate the quotation into a sentence the app appears to
    be making itself.
    """
    author = (author or "").strip() or "someone in the household"
    return {
        "lead_in": NOTE_LEAD_IN.format(author=author, when=when),
        "quote": text.strip(),
        "written_by": NOTE_SOURCE_PERSON,
        "author": author,
    }

# The header the export carries. A family member takes this to the agency, so
# the first thing on the page has to be what it does not claim.
EXPORT_HEADER = (
    "Threshold front-door record. This is a record of activity at one door, produced "
    "from doorbell sensor events. It does not record who was present, and it cannot "
    "see a key safe, a back entrance, or a side entrance. Absence of an entry means "
    "the doorbell recorded nothing, not that nobody came."
)

# Substrings that must never appear in any string this app generates for a
# person to read. Checked case-insensitively. If a change to schedule.py or
# log.py ever produces one of these, test_phrasing.py fails the build.
FORBIDDEN_SUBSTRINGS = (
    "did not come",
    "didn't come",
    "no show",
    "no-show",
    "failed to show",
    "missed the visit",
    "missed their visit",
    "skipped the visit",
    "carer did not",
    "carer didn't",
    "did not show up",
    "wasn't here",
    "was not here",
    "faked",
    "fraud",
    "lied about",
)

# Extra phrases banned from anything a model wrote. Broader than the list above
# on purpose: the rules produce a fixed vocabulary we have read, a model
# produces sentences nobody has read, so the gate on model output is stricter
# than the gate on our own constants.
MODEL_FORBIDDEN_SUBSTRINGS = FORBIDDEN_SUBSTRINGS + (
    "should be investigated",
    "you should contact",
    "we recommend",
    "appears to have",
    "seems to have",
    "suggests that",
    "likely that",
    "probably",
    "may indicate",
    "cause for concern",
    "raises questions",
    # Inference, as a class rather than as the three phrasings somebody thought
    # of. The system prompt says "do not speculate about causes", and until this
    # block existed nothing checked it: "the booked window passed with nothing
    # at the door, which speaks for itself" contains no banned word, no person
    # and no ungrounded number, and it is the whole accusation.
    "speaks for itself",
    "can only mean",
    "no other explanation",
    "draw your own",
    "clearly shows",
    "demonstrates that",
    "which means",
    "this means",
)

# Whole words banned from model output. Matched on word boundaries, because a
# blunt substring test on "her" also fires on "other" and would make the gate
# look broken rather than strict. These fall into three groups: words that put
# a person at the door, words that assert certainty the sensor cannot deliver,
# and words that escalate.
MODEL_FORBIDDEN_WORDS = (
    # someone at the door
    "he",
    "she",
    "him",
    "her",
    "his",
    "hers",
    "they",
    "them",
    "their",
    "someone",
    "somebody",
    "nobody",
    "anyone",
    "carer",
    "carers",
    "caregiver",
    "caregivers",
    "nurse",
    "nurses",
    "cleaner",
    "visitor",
    "visitors",
    "worker",
    "staff",
    "attendant",
    # The class, not the four job titles above it. A model that cannot write
    # "carer" writes "the individual", "a person" or "the courier" instead, and
    # every one of those puts somebody at a door this app can only say fired a
    # sensor. Match the class, not the
    # token somebody happened to imagine.
    "individual",
    "individuals",
    "person",
    "persons",
    "people",
    "man",
    "men",
    "woman",
    "women",
    "child",
    "children",
    "adult",
    "adults",
    "driver",
    "courier",
    "clerk",
    "resident",
    "relative",
    "relatives",
    "neighbour",
    "neighbor",
    # certainty the sensor cannot deliver
    "proof",
    "evidence",
    "proves",
    "confirms",
    "confirmed",
    "verified",
    "attended",
    "visited",
    "arrived",
    "skipped",
    "neglected",
    "abandoned",
    # escalation
    "emergency",
    "urgent",
    "alarming",
    "danger",
    "dangerous",
    "suspicious",
    "concerning",
    "worrying",
)


# --- provenance, printed as a word rather than shown as a colour -------
#
# Practice Direction 32 paragraph 18.2 has required filers to separate what a
# witness knows themselves from what they were told since 1999, and this product
# turns out to need the same distinction for the same reason. Every row on a
# judge-facing surface carries one of these three words, spelled out, never
# encoded in a colour a printer or a colour-blind reader would lose:
#
#   RECORDED  A sensor fired. The doorbell's own event, and the strongest thing
#             this app has. It says a thing happened at a door, and nothing
#             about who or why.
#   REPORTED  Somebody said so. Ring's classifier calling a shape "human" is
#             reported, not recorded, because that classifier has been
#             documented calling a motorised wheelchair user a package. A
#             household note is reported too, by a named person. Both are
#             second-hand, and they sit on the same timeline as RECORDED rows
#             without either pretending to be the other.
#   INFERRED  The rules worked it out. A schedule comparison and a silence state
#             are conclusions drawn from RECORDED rows by code in this repo, and
#             are only ever as good as the interval arithmetic behind them.
#
# Nothing a model wrote is ever any of these three. Model output is prose about
# rows that already carry their own marks, which is why `narrate.py` can use one
# at all.
PROVENANCE_RECORDED = "RECORDED"
PROVENANCE_REPORTED = "REPORTED"
PROVENANCE_INFERRED = "INFERRED"

PROVENANCE_LEGEND = {
    PROVENANCE_RECORDED: "A sensor at the door fired. Says nothing about who.",
    PROVENANCE_REPORTED: (
        "Somebody said so: Ring's own classification, or a note written by the "
        "household. Second-hand either way."
    ),
    PROVENANCE_INFERRED: "Worked out by this app's rules from the recorded events.",
}

# RFC 3339 spells an unknown offset -00:00, distinct from +00:00 which asserts
# UTC. Used when a timestamp reaches us with no zone at all.
UNKNOWN_OFFSET = "-00:00"


def stamp(moment, tz=None) -> str:
    """Format a moment as "2026-03-14 19:07:32 PDT (UTC-07:00)".

    The offset travels with every printed time. Without it, a record read six
    months later cannot be reconciled across a clock change, and in this product
    an hour of drift against a booked window is a false "no arrival was
    recorded" row against a named, low-paid person. That is a bug worth
    preventing rather than discovering in March.
    """
    if tz is not None:
        moment = moment.astimezone(tz)
    offset = moment.utcoffset()
    if offset is None:
        return f"{moment.strftime('%Y-%m-%d %H:%M:%S')} (UTC{UNKNOWN_OFFSET})"
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    hours, minutes = divmod(abs(total) // 60, 60)
    # "UTC (UTC+00:00)" says the same thing twice; so does "+00:00 (UTC+00:00)".
    name = moment.tzname() or ""
    redundant = name == "UTC" or name.startswith(("UTC+", "UTC-", "+", "-"))
    label = "" if redundant else f"{name} "
    return (
        f"{moment.strftime('%Y-%m-%d %H:%M:%S')} "
        f"{label}(UTC{sign}{hours:02d}:{minutes:02d})".replace("  ", " ")
    )


class PhrasingViolation(ValueError):
    """Raised when text that is about to be shown breaks the phrasing rule.

    Two payloads, and the difference between them is load-bearing.

    ``str(exc)`` is the developer's version. It quotes the offending text in
    full, because a refusal you cannot read is a refusal you cannot debug, and
    it is what the tests and the retry prompt use.

    ``exc.rule`` is the household's version. It names the rule that fired and
    carries none of the refused text.

    They are separate because a rejection is model output with a frame around
    it, not metadata. The gate here exists so that "the carer did not come"
    never reaches a family member; quoting it back inside the sentence that
    explains it was blocked delivers the accusation anyway, one paragraph
    lower, with a label on it. Anything shown to a person reads ``rule``.
    """

    #: What a person is told. Never contains any of the refused text.
    GENERIC = "It broke one of the phrasing rules."

    def __init__(self, message: str, rule: str | None = None) -> None:
        super().__init__(message)
        self.rule = rule or self.GENERIC


#: Stands in for a span the household supplied. One token, no letters the word
#: or phrase lists contain, so masking can never create or destroy a match.
_USER_SPAN = " ␣ "


def _mask(text: str, spans: tuple[str, ...]) -> str:
    """Blank out spans of `text` that the household typed rather than we wrote.

    A household name is the household's own words, sitting inside a sentence
    this app composed. The guard has to run on the sentence and not on the
    name: a house on **No-Show Lane** is not this app accusing anybody, and a
    guard that refused it would either censor a real address or -- which is
    what it actually did -- raise a `PhrasingViolation` out of
    `narrate.summarise`, a function whose contract is that it never raises.
    """
    masked = text
    for span in spans:
        span = (span or "").strip()
        if span:
            masked = masked.replace(span, _USER_SPAN)
    return masked


def assert_safe(text: str, *, user_spans: tuple[str, ...] = ()) -> str:
    """Raise if `text` contains a forbidden accusation phrase.

    Used both by tests and, defensively, at the point schedule.py assembles a
    result, so a bad string cannot reach the log or the HTTP response.

    `user_spans` names substrings the household supplied. They are masked before
    the comparison and are never themselves checked, because this rule is about
    what the app says, not about what a household may call its own house.
    """
    lowered = normalise(_mask(text, user_spans))
    for phrase in FORBIDDEN_SUBSTRINGS:
        if phrase in lowered:
            raise PhrasingViolation(
                f"phrasing violation: {phrase!r} found in judge-facing text: {text!r}",
                rule="It used a phrase from the refused-language list.",
            )
    return text


def assert_model_output_safe(text: str, allowed_numbers: set[str]) -> str:
    """Raise unless `text` is something a model is allowed to have written.

    Four gates, in order of how much they matter:

    0. Script. After folding, any letter outside `a-z` is either a language this
       gate was never written for or somebody probing it. A fold table can never
       be finished -- `dıd not`, with a dotless Turkish ı, walks through any
       version of it -- so the allowlist is the check and a foreign letter is a
       refusal. See GUARD-STANDARD.md §2.
    1. Everything `assert_safe` rejects, plus the wider `MODEL_*` lists. A model
       that writes "the carer did not come" is stopped by the same check that
       stops us writing it.
    2. Whole-word matches on `MODEL_FORBIDDEN_WORDS`, so pronouns and job titles
       cannot reappear in a paraphrase. Checked against both folded forms, so
       `care-r` and `she's` and `carer` are one comparison.
    3. Numeric grounding. Every number in the sentence must be one the rules put
       in the digest, in any spelling: `five`, `fifth`, `5`, `05:00` and `2.50`
       all land on the same figure. This is the anti-hallucination gate: a model
       may rearrange the facts into prose, it may not invent a count, an hour or
       a date. It is the concrete reason the model cannot change an outcome.
    """
    assert_safe(text)
    folded = prepare(text)

    foreign = folded.foreign_letters()
    if foreign:
        raise PhrasingViolation(
            f"unexpected script in model output: {foreign} in {text!r}",
            rule="It used letters outside the alphabet the rules are written in.",
        )

    for phrase in MODEL_FORBIDDEN_SUBSTRINGS:
        if folded.has_phrase(phrase):
            raise PhrasingViolation(
                f"phrasing violation in model output: {phrase!r} in {text!r}",
                rule="It used a phrase from the refused-language list.",
            )

    banned = folded.tokens & set(MODEL_FORBIDDEN_WORDS)
    if banned:
        raise PhrasingViolation(
            f"phrasing violation in model output: banned word(s) {sorted(banned)} in {text!r}",
            rule="It used a word the rules do not allow about people.",
        )

    ungrounded = numbers_in(text) - allowed_numbers
    if ungrounded:
        # The ungrounded numbers are themselves model output, and a made-up
        # count printed beside "this was rejected" is still a made-up count on
        # the page. They stay in the developer message only.
        raise PhrasingViolation(
            "ungrounded number(s) in model output: "
            f"{sorted(ungrounded)} not among the facts the rules computed; text={text!r}",
            rule="It contained a number the rules did not compute.",
        )

    return text
