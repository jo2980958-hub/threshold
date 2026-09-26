"""The sentence the weekly panel prints about where its prose came from.

This is copy, not policy. `narrate.summarise` has already decided what happened
and recorded it on `Summary`; nothing here may change that record, and nothing
here may soften it.

It lives in Python rather than in `web/index.html` because the panel used to get
it wrong in a way no browser test would have caught. `narrate.py` returns
`source=SOURCE_MODEL` with `rejected_reason` still set when a first draft was
refused and the retry passed the gate — deliberately, because a gate that worked
is the most informative thing this integration can show. The panel read
`rejected_reason` on its own and printed "The summary above is the one the rules
wrote instead" directly beneath "Written by Amazon Bedrock". Both sentences on
one screen, in the one app whose whole argument is that it can say where every
sentence came from.

The rule `gate_notice` encodes: **a refusal notice may only claim the
model's text was discarded when it actually was**, which is exactly when the
source is the rules. When the source is the model, a refusal means the first
draft was refused and the second one passed the same check.
"""

from __future__ import annotations

from .narrate import SOURCE_MODEL


def gate_notice(source: str, rejected_reason: str | None) -> str | None:
    """What the phrasing gate did, or `None` when it never refused anything.

    Two cases, and the difference between them is the whole point:

    - the source is the rules, so the refused draft is not on screen;
    - the source is the model, so the draft on screen is the corrected one and
      saying it "was not shown" would be false.
    """
    if not rejected_reason:
        return None
    reason = rejected_reason.strip()
    if not reason.endswith((".", "!", "?")):
        reason += "."
    if source == SOURCE_MODEL:
        return (
            "The model's first draft was refused. " + reason +
            " It was asked again, and the summary above is the second draft, "
            "which passed the same check."
        )
    return (
        "The model's draft was not shown. " + reason +
        " The summary above is the one the rules wrote instead."
    )
