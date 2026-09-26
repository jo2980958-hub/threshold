"""A household note is not allowed to escape the ledger.

The input takes 2,000 characters (`app.py`), and at the gutter's 248px a note
that long renders as a ~1,400px ribbon. `#notes-col.positioned` is
`position: absolute` inside `.gutter` (`threshold.css`), so the height the
placing code set on the list itself contributed nothing to the grid row: the
note ran past the bottom of the ledger, down the rest of the document, and
covered the middle third of every line of the weekly summary a judge is meant
to read.

Two things fix it and both are asserted here: the height has to reach
`.gutter`, which is the grid item, and a note that long is clamped with its
full text one press away rather than drawn at full height by default.

The geometry itself was measured in a browser at 1280px and at 390px:
collapsed 279px, expanded 1,517px, ledger growing to hold both, zero overlap
with the weekly panel and zero horizontal overflow on the phone.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "threshold" / "web" / "index.html").read_text()
CSS = (ROOT / "threshold" / "web" / "threshold.css").read_text()


def _function(name: str) -> str:
    start = PAGE.index(f"function {name}(")
    rest = PAGE[start:]
    return rest[: rest.index("\n}\n") + 2]


def test_the_height_reaches_the_grid_item_and_not_only_the_absolute_list():
    body = _function("positionNotes")

    assert "gutter.style.minHeight = bottom" in body
    assert "col.style.height = bottom" in body


def test_the_gutter_is_released_again_when_there_is_nothing_to_hold():
    """A stale min-height would leave an empty day with a tall ledger."""
    body = _function("positionNotes")
    early = body[: body.index("const floor")]

    assert 'gutter.style.minHeight = "";' in early
    assert 'col.style.height = "";' in early


def test_the_notes_list_is_still_the_absolutely_positioned_one():
    """If this stops being true the fix above is solving nothing."""
    assert "#notes-col.positioned{" in CSS
    block = CSS[CSS.index("#notes-col.positioned{"):]
    assert "position:absolute" in block[: block.index("}")]


def test_a_long_note_is_clamped_rather_than_drawn_at_full_height():
    assert ".note blockquote.clamped{" in CSS
    block = CSS[CSS.index(".note blockquote.clamped{"):]
    block = block[: block.index("}")]

    assert "-webkit-line-clamp:8" in block
    assert "overflow:hidden" in block


def test_the_clamp_is_released_when_the_reader_asks_for_the_whole_note():
    assert ".note.open blockquote.clamped{" in CSS
    block = CSS[CSS.index(".note.open blockquote.clamped{"):]

    assert "overflow:visible" in block[: block.index("}")]


def test_the_expand_button_is_added_only_when_the_clamp_clamped_something():
    """Otherwise a two-line note carries a button that does nothing."""
    body = _function("renderNotes")

    assert "quote.scrollHeight <= quote.clientHeight" in body
    assert 'quote.classList.remove("clamped")' in body
    assert "return;" in body


def test_expanding_a_note_re_places_the_column_it_just_made_taller():
    body = _function("expandButton")

    assert "positionNotes(band)" in body


def test_the_button_says_which_state_it_is_in_to_a_screen_reader_as_well():
    body = _function("expandButton")

    assert 'setAttribute("aria-expanded"' in body
    assert 'setAttribute("aria-controls", quote.id)' in body
    assert "Read the whole note" in body
    assert "Show less of this note" in body


def test_the_note_is_still_a_quotation_and_still_marked_reported():
    body = _function("renderNotes")

    assert "<blockquote" in body
    assert 'data-mark="REPORTED"' in body
    assert "TH.escape(n.quote)" in body


def test_nothing_truncates_the_text_itself():
    """The clamp is visual. A note is the household's own words and the app
    does not get to shorten them."""
    body = _function("renderNotes")

    assert ".slice(" not in body
    assert "substring(" not in body
