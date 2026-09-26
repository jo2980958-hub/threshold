# Threshold

A neutral, timestamped log of front-door arrivals and departures, shown next to a
household's home-care visit schedule. Built for the Ring track of the Amazon
Developer Hackathon.

Full detail, including the design rationale and what this deliberately does not do,
is in [`SPEC.md`](./SPEC.md).

## The one rule

A doorbell sees a door, not a visit. Threshold never says a carer did or did not
come. It says whether front-door activity was recorded inside a scheduled window,
and nothing more. That rule is enforced in code, not just in the copy: every string
the app can show for a schedule outcome is drawn from a fixed set in
`threshold/phrasing.py`, and `tests/test_phrasing.py` asserts the forbidden
accusatory phrases can never appear in anything the app produces.

## What is real and what is replayed

This app is built against the real, documented Ring API: the webhook envelope and
its HMAC-SHA256 signing scheme, the event-history endpoint, the JSON:API response
shapes, the OAuth bearer auth. `threshold/ring_client.py` is a working client
against those shapes.

**It never calls a live Ring host.** Ring's own documentation does not mention a
simulator, mock, virtual device, or sandbox anywhere (checked twice, independently,
against the full API reference and the develop/get-started/certify/publish/MCP
pages). Real testing needs a US-located Ring device on an active Ring Protection
subscription. So the demo replays HMAC-signed fixture webhooks
(`fixtures/*.json`) through the exact same verification and handling path a live
Ring webhook would hit (`threshold/webhook.py`, `threshold/app.py`). That boundary
is explicit: `ring_client.py`'s `base_url` is only ever exercised by a test that
patches `urllib.request.urlopen`; nothing in the app or the demo calls it for real.

## Running it

Requires Python 3.11 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Run the tests
.venv/bin/python -m pytest -q

# Run the demo: registers three households and their care packages, replays a
# week of fixture webhooks, and serves the one-screen view.
.venv/bin/python -m threshold.replay fixtures/care_week.json \
    --households fixtures/households.json --to-today --port 8765
```

Then open `http://127.0.0.1:8765/?household=oakfield` — the day ledger. Two more pages
are served from the same process: `/history` (the searchable event record) and `/agency`
(the roll-up across all three households).

`--households` is not optional. It is what registers Harewood Close, Millbrook
Lane and Oakfield Road, their doors and their recurring visits; without it the
server has no household to show and every read is a 404. `--to-today` shifts the
fixture forward by whole days so its last day is today, which is what makes the
silence watch and the weekly summary say something about now.

The demo day shows all three of the states the product cares about, in one
screen: a morning visit and an evening visit both show recorded front-door
activity; a midday visit shows "No arrival was recorded at the front door
during this visit," deliberately, to demonstrate the product's central honesty
constraint rather than hide it. Harewood Close is the fourth state: a household
with too little history for the silence watch to say anything, which it says.

Two more fixtures exercise specific scenarios directly:

```bash
# Reproduces Ring's own documented defect: a motorized wheelchair user is
# classified sub_type=package instead of human. The event still counts.
.venv/bin/python -m threshold.replay fixtures/wheelchair_misclassified.json

# Ring redelivers a webhook; the receiver must deduplicate on request_id.
.venv/bin/python -m threshold.replay fixtures/duplicate_delivery.json
```

## Architecture

Python standard library for everything on the critical path: `http.server` for the
webhook receiver and the read routes, `sqlite3` for persistence, `hmac`/`hashlib` for
signature verification, `dataclasses` for the event and schedule models. The only
outside dependency is `boto3`, for the weekly-narration Bedrock call (see below),
imported lazily and never required to run the app.

- `threshold/events.py`: parses Ring's webhook envelope. `sub_type` (Ring's only
  classification hint, e.g. `human`, `package`) is stored for display only. It is
  never used to include or exclude an event from the log, because Ring's own users
  have documented the classifier calling a motorized wheelchair user a package.
- `threshold/webhook.py`: HMAC-SHA256 signature verification and delivery
  deduplication on `request_id`, matching the documented webhook contract.
- `threshold/log.py`: the in-memory arrival log: a deduplicated, queryable list of
  door events.
- `threshold/schedule.py`: compares a care visit window against the log and
  produces one of exactly three outcomes, all from `phrasing.py`.
- `threshold/phrasing.py`: the single choke point for every judge-facing string
  about arrivals. Enforces the "no arrival was recorded, never an accusation" rule.
- `threshold/store.py`: SQLite persistence so the log survives a restart.
- `threshold/household.py`: registers each household's doors and recurring care
  visits from `--households`; what makes `/agency` a roll-up across more than one.
- `threshold/matching.py`: matches a logged event against the scheduled window it
  falls inside, feeding `schedule.py`'s three-outcome comparison.
- `threshold/silence.py`: the silence watch — when a household has too little
  history to say anything, and when a quiet stretch is long enough to flag.
- `threshold/digest.py`: builds the weekly digest of a household's front-door
  activity that `narrate.py` turns into prose (or that ships as rules-written text
  on its own).
- `threshold/narrate.py`: the Bedrock boundary — turns a week's digest into a plain-
  language summary, or falls back to deterministic rules-written text. See "Amazon
  Bedrock" below.
- `threshold/provenance.py` / `threshold/grounding.py`: keep the narrated summary
  traceable to the digest numbers that produced it, so a model-written sentence
  can be checked against what actually happened.
- `threshold/export.py`: `GET /export.csv` and `GET /export.txt`, the record and
  the plain-language visit report a family can take to the agency.
- `threshold/app.py`: the stateful application logic (webhook handling, schedule
  comparison), kept free of sockets so it is directly unit-testable.
- `threshold/server.py`: a thin stdlib HTTP server wrapping `app.py`. Serves three
  pages (`/`, `/history`, `/agency`) and their JSON routes (`/log`, `/schedule`,
  `/silence`, `/weekly`, `/api/households`, `/api/history`, `/api/agency`), plus
  `POST /webhooks/ring`, `POST /api/schedule-rules`, `POST /api/settings`, and the
  two export routes above.
- `threshold/ring_client.py`: the real Ring API client shapes; never called live
  (see above).
- `threshold/replay.py`: signs and replays a fixture file through the real webhook
  handling path, then serves the demo.
- `threshold/web/`: the three pages above — `index.html` is a vertical time-axis
  ledger with the care schedule on the left and the door log on the right, both
  positioned by time of day, with the boundary note built into the layout rather
  than left as a footnote; `history.html` and `agency.html` are the searchable
  record and the cross-household roll-up.

## Amazon Bedrock

The weekly household summary (`GET /weekly`) is written by `threshold/narrate.py`,
cached per household per week because it costs a Bedrock call. It is optional at
every step: with no AWS credentials, or with `boto3` not installed, or with the
model unreachable, the summary falls back to deterministic rules-written text
covering the same numbers, and the response says which source produced it
(`"source": "bedrock"` or `"source": "rules"`, with a named `rejected_reason` when
the model was not used).

| Variable | Default | Effect |
|---|---|---|
| `THRESHOLD_DISABLE_BEDROCK` | unset | any truthy value runs the whole app with no AWS account |
| `THRESHOLD_BEDROCK_MODEL` | unset | pins one model id |
| `AWS_REGION` | `us-east-1` | region Bedrock is called in |

## Design

Ground is graphite dusk, a true neutral charcoal. The one accent is lamplight
brass, a muted warm gold. Log text sits in paper white. Full rationale, including
why this palette and shell were chosen against the hackathon's existing UI
inventory, is in `SPEC.md`'s Design direction section. The "no arrival was
recorded" state deliberately never uses red or a cross mark; it uses the same
brass-on-graphite system as every other state, because both are answers to the
same question, not a verdict on a person.

## Tests

483 tests and 18 skipped, `tests/`, run with `pytest`. Covers: webhook signature
verification and rejection, delivery deduplication and TTL eviction, event parsing
including the wheelchair-misclassification case, the arrival log's windowing and
dedup, every schedule-comparison outcome (including insufficient-history and the
grace period), the phrasing choke point's forbidden-phrase list, the full
webhook-to-log integration path including SQLite persistence across restarts, all
three fixture files replayed end to end, and the Ring API client's request shapes
(method, path, headers, body) without ever touching the network.

## What this does not do

See "What it deliberately does not do" in `SPEC.md`. In short: no clip retrieval
(needs a paid continuous-recording plan this app cannot assume), no history
backfill (Ring's API does not support it), no identity claim beyond "activity was
recorded," and no push notifications (out of scope for this build).

## Licence

MIT. Third-party font credits: see [`ATTRIBUTION.md`](ATTRIBUTION.md).
