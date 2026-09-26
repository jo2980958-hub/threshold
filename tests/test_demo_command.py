"""The command in the README has to be the command that works.

It was not. `README.md` said

    python -m threshold.replay fixtures/normal_day.json --schedule fixtures/schedule.json

which registers no household at all, so the four reads the page makes all 404
and the screen sits on "Checking this household's door record..." and
"LOADING..." forever. Nothing said anything was wrong, because no read in
`web/` had a `catch`.

Two things are tested here, because the fix was two things: the command works,
and a read that fails says so.
"""

import re
from pathlib import Path

from threshold.app import ThresholdApp
from threshold.replay import load_fixture, replay_events, seed_households

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "threshold" / "web"


def _readme() -> str:
    return (ROOT / "README.md").read_text()


def _readme_demo_command() -> str:
    """The first replay command in the README, with its line continuations joined."""
    joined = _readme().replace("\\\n", " ")
    match = re.search(r"python -m threshold\.replay[^\n`]*", joined)
    assert match, "the README no longer contains a threshold.replay command"
    return match.group(0)


def test_the_readme_demo_command_registers_households():
    command = _readme_demo_command()
    args = command.split()[3:]
    fixture = args[0]
    households = args[args.index("--households") + 1] if "--households" in args else None

    assert households, (
        "the README's demo command has no --households, so it registers nothing "
        "and every read on the page 404s"
    )

    app = ThresholdApp(webhook_secret="demo-webhook-secret")
    seeded = seed_households(app, ROOT / households)
    results = replay_events(app, load_fixture(ROOT / fixture))

    assert len(seeded) == 3
    assert app.default_household() is not None
    assert all(status == 200 for status, _ in results)


def test_every_fixture_the_readme_names_exists():
    for path in re.findall(r"fixtures/[\w.]+\.json", _readme()):
        assert (ROOT / path).exists(), path


def test_the_day_the_readme_opens_on_has_something_on_it():
    """`?household=oakfield`, not the first household alphabetically.

    Harewood Close is deliberately thin — it is the "not enough history to say"
    state — and landing a judge on it reads as a page that failed to load.
    """
    args = _readme_demo_command().split()[3:]
    app = ThresholdApp(webhook_secret="demo-webhook-secret")
    seed_households(app, ROOT / args[args.index("--households") + 1])

    opened = re.search(r"127\.0\.0\.1:8765/\?household=(\w+)", _readme())
    assert opened, "the README no longer names the household it opens on"
    assert app.household(opened.group(1)) is not None


def test_no_read_in_the_ui_goes_out_without_a_failure_branch():
    for name in ("index.html", "history.html", "agency.html", "threshold.js"):
        source = (WEB / name).read_text()
        # `fetch` may only appear inside TH.readJson itself, and inside the note
        # POST, which has its own `res.ok` branch.
        stray = [
            line
            for line in source.splitlines()
            if "fetch(" in line
            and "TH.readJson" not in line
            and "res = await fetch(url)" not in line
            and '"/api/notes"' not in line
            and not line.lstrip().startswith(("*", "/*", "//"))
            and "`" not in line
        ]
        assert stray == [], f"{name} reads without going through TH.readJson: {stray}"


def test_the_shared_reader_refuses_a_non_ok_response():
    source = (WEB / "threshold.js").read_text()

    assert "if (!res.ok)" in source
    assert "TH.ReadFailed" in source
    assert "data.error" in source


def test_every_page_carries_the_banner_a_failure_is_written_into():
    for name in ("index.html", "history.html", "agency.html"):
        source = (WEB / name).read_text()
        assert 'id="read-failed" role="alert"' in source, name
        assert "TH.sayReadFailed" in source, name


def test_the_household_picker_refuses_an_id_the_server_does_not_know():
    """The dropdown used to show the first household's name while every fetch
    asked for the id in the URL, and 404ed."""
    source = (WEB / "threshold.js").read_text()

    assert "There is no household with the id" in source
    assert "No household is registered" in source


def test_a_failed_day_does_not_then_ask_for_the_week():
    source = (WEB / "index.html").read_text()

    assert "if (await loadDay()) await loadWeekly(false);" in source


def test_the_placeholders_a_failure_replaces_are_the_ones_on_screen():
    """A spinner that survives a failure is the whole defect, so the ids the
    failure branch writes into have to be the ids the page actually holds."""
    page = (WEB / "index.html").read_text()
    replaced = set(re.findall(r'"([\w-]+)": "[^"]*could not be read', page))
    replaced |= {"day-label", "silence-message", "weekly-text"}

    for element_id in replaced:
        assert f'id="{element_id}"' in page, element_id
