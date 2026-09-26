# Friction log: Threshold (Ring track)

Started at the first SDK-adjacent decision, not reconstructed afterward. Entries 11 to 14
were added at the end of the build, during an audit pass that read the rendered page and
ran the guard at a terminal rather than trusting the test count. They are the four most
useful entries in the file and they arrived last, which is itself the finding.

---

### 1. No Ring simulator exists, contradicting the hackathon page

- **Task attempted:** Find the Ring simulator the hackathon Resources page implies,
  before writing a client against it.
- **Steps taken:** Two independent research passes grepped `developer.ring.com` and
  `developer.amazon.com/docs/ring` for `simulator`, `mock`, `virtual device`, `emulat`
  and `sandbox`. Zero matches for a documented one in either pass. The word "sandbox"
  appears in Ring's glossary and FAQ; the Test section of the same reference says to use
  your personal Ring account and devices.
- **Expected:** A hosted simulator or virtual device to send synthetic `motion_detected`
  and `ding` events to, since the hackathon page states a physical Ring device is not
  required and lists simulators under Resources.
- **Actual:** Nothing in Ring's own documentation supports that. Real testing needs a
  US-located Ring device on an active Ring Protection subscription, with staging capped
  at ten users. The hackathon page and the developer documentation say different things,
  and the developer documentation is the one that governs what a request returns.
- **Severity:** High. It decides the architecture, because the demo cannot touch a real
  Ring account.
- **Workaround:** Built `RingClient` against the documented request and response shapes —
  base URL, OAuth bearer headers, JSON:API envelopes, endpoint paths — and never pointed
  it at a live host in tests or the demo. Everything is driven by HMAC-signed fixture
  webhooks replayed through the same verification path a real Ring webhook would hit. The
  boundary is a constant, `RingClient.BASE_URL`, never called outside a `live_smoke`
  marker that is skipped by default.
- **Suggestion:** Reconcile the two pages. Either ship the simulator the Resources page
  implies, or say what the developer docs say: bring your own subscribed device, or replay
  signed webhooks. The second answer is fine. Being told the first and finding the second
  is what costs a day.
- **Against ourselves:** the Resources page also says a free Ring account gets you API
  access. Nobody here created one, so we do not actually know what an authenticated
  session returns with no device attached. That is our gap, and it is the first thing the
  next team should try.

### 2. Clip retrieval is plan-gated, which is why this product logs instead of showing

- **Task attempted:** Decide whether an arrival record should carry footage.
- **Steps taken:** Read `POST /v1/devices/{id}/media/video/download` in the reference.
- **Expected:** A clip for any timestamp inside the device's retention window.
- **Actual:** *"If the camera was not recording at the requested timestamp, you will
  receive a 416 TIMESTAMP_NOT_FOUND error."* Continuous recording is a paid tier;
  event-only recording is what a device without it does, and the window this product
  cares about — the minutes around a booked visit — is exactly the window an event-only
  device has nothing for.
- **Severity:** Medium here, and it would be High for a product built on clips. It is the
  reason Threshold's record is a text ledger: a household on the free tier would get a
  product with an empty box in it half the time, and an empty box beside a carer's name
  reads as an accusation all by itself.
- **Workaround:** No clip path at all. `ring_client.py`'s `request_clip_download()` posts
  to the endpoint with the right shape and is exercised by one test that checks the
  request body — nothing in the app or the demo ever calls it. The design decision is
  written into `SPEC.md` rather than discovered later.
- **Suggestion:** Put the plan-tier requirement in the endpoint's own description. It is
  documented once, in passing, in a different section from the endpoint it governs, and
  the shape of the failure — a `416` on a valid request — reads like a bug rather than a
  billing boundary.

### 3. Event history has no backfill, which changes what day one can demo

- **Task attempted:** Design the family log to show a first day of history.
- **Steps taken:** Re-read the event history section. `GET /v1/history/devices/{id}/events`
  is time-gated to the moment consent was granted; a fresh account link returns an empty
  list.
- **Expected:** Some grace window, or a way to request a short backfill.
- **Actual:** Neither is documented. A newly linked household has zero history until
  events start arriving after linking.
- **Severity:** Medium. A real deployment has to wait a day before "the usual rhythm"
  means anything.
- **Workaround:** `schedule.py` treats an empty or short history as "not enough data yet"
  rather than inferring a rhythm from nothing, and the fixtures carry a multi-day replay
  so the demo can show a baseline forming.
- **Suggestion:** A documented backfill window, even 24 to 48 hours, would remove a real
  day-one gap for every integrator on this platform, not only for this product.

### 4. `sub_type: human` cannot be trusted as a gate

- **Task attempted:** Decide whether to filter the arrival log to `motion.human` events,
  which is what the API's composite event-type filter syntax invites.
- **Steps taken:** Cross-checked against the r/Ring reporting in the research file: Ring's
  classifier has been documented labelling a motorised wheelchair user a package rather
  than a person, for months.
- **Expected:** Not a bug report. A design decision forced by evidence.
- **Actual:** Filtering on `sub_type: human` would silently drop a wheelchair user from
  their own front-door log.
- **Severity:** High for the product's honesty, not for build time.
- **Workaround:** `events.py` ingests every `motion_detected` event regardless of
  `sub_type`. The sub_type is stored as a display hint — "possible delivery" against
  "motion" — and never used to include or exclude an event from the log or the schedule
  comparison. `test_events.py` feeds a `motion_detected` event with `sub_type: package`
  and asserts it still counts as front-door activity.
- **Suggestion:** Publish a confidence score alongside `sub_type` so integrators can pick
  their own threshold, instead of trusting a binary label that is known to misclassify
  mobility-aid users. Ring also gives no identity beyond `sub_type: human`, which is the
  ceiling on this whole product category rather than a defect: an arrival log can say an
  arrival happened and can never say who arrived. Everything Threshold does about
  phrasing follows from that one absence.

### 5. A PEP 668 stumble, noted only because other tracks hit it harder

- **Steps taken:** `python3 -m pip install pytest` failed on the externally-managed
  environment guard. Created a local venv (`.venv`) instead.
- **Severity:** Low. Clean once scoped to a venv.
- **Suggestion:** None. A machine quirk, not a platform problem.

### 6. A thread-safety bug in the demo server, caught by loading the page

- **Task attempted:** Screenshot the running demo in a browser to check the design
  against the shared UI inventory, rather than trusting the code to render.
- **Steps taken:** Ran `threshold.replay` against `ThreadingHTTPServer`, loaded the page
  in a real browser, and it 500'd on `/schedule`.
- **Expected:** A rendered ledger.
- **Actual:** `sqlite3.ProgrammingError: SQLite objects created in a thread can only be
  used in that same thread.` The webhook replay opened the connection on the main thread;
  each HTTP handler ran on its own.
- **Severity:** Medium. It would have shipped a demo that crashes on the one read route
  that matters, if I had trusted `curl` output and never loaded the page.
- **Workaround:** A plain single-threaded `HTTPServer`. A hackathon demo behind one
  SQLite connection does not need concurrent handling.
- **A second bug, same screenshot:** two door events 51 seconds apart rendered with
  overlapping text on the time-axis ledger, because entries were positioned purely by
  timestamp with no minimum-gap rule. Fixed with a declutter pass that enforces a minimum
  pixel gap between adjacent entries in both columns. Recorded because it is the kind of
  thing that only appears once you look at the rendered page.
- **Suggestion:** None for Ring. Left in because the brief asks for what happened, not
  only for what the platform did.

### 7. `list-foundation-models` advertises models the account cannot invoke

- **Task attempted:** Call Bedrock for the weekly plain-language summary, using
  `anthropic.claude-sonnet-5` as the build brief specified.
- **Steps taken:** `aws bedrock list-foundation-models --region us-east-1`, grepped for
  claude. `anthropic.claude-sonnet-5`, `anthropic.claude-opus-5`,
  `anthropic.claude-sonnet-4-6` and a dozen others all come back. Called `bedrock-runtime`
  `Converse` with the first.
- **Expected:** A model the control plane lists to be a model the data plane runs.
- **Actual:** `AccessDeniedException: anthropic.claude-sonnet-5 is not available for this
  account.` Same for `anthropic.claude-opus-5`, and for the `us.` inference profile forms
  of both. `us.anthropic.claude-sonnet-4-6` and `us.anthropic.claude-sonnet-4-5-20250929-v1:0`
  both work, at roughly 1.2 to 1.6 seconds for a 400-token summary.
- **Severity:** Medium. Fifteen minutes, and it would have been a live demo failure rather
  than a build failure if the fallback had not already existed.
- **Workaround:** `narrate.MODEL_PREFERENCE` is an ordered tuple tried in turn.
  `AccessDeniedException` and `ValidationException` step to the next entry; anything else,
  a timeout or a connection failure, does not, because walking three models at eight
  seconds each is a page that never loads. The first id that answers is cached for the
  process. `tests/test_narrate.py` covers all four paths with a stub.
- **Suggestion:** `ListFoundationModels` should either filter to what the caller can
  invoke or carry an explicit per-model entitlement field. A listing that is not a
  capability statement is worse than no listing, because it reads like one.

### 8. Converse is the right shape, and the retry is the interesting part

- **Task attempted:** Get a model to write four sentences about a week of door events
  without ever asserting that a person came or did not come.
- **Steps taken:** `Converse` with a system prompt, `temperature: 0`, `maxTokens: 400`.
  Every returned sentence goes through `phrasing.assert_model_output_safe`.
- **Expected:** A clean first draft, given how explicit the prompt is.
- **Actual:** The first real draft was refused for one word. It wrote "the front door
  recorded activity within the expected window for 12 of them", and "them" is on the
  banned list because it is how a sentence starts referring to people. The refusal was
  correct and the sentence was harmless, which is the interesting case: a gate strict
  enough to be worth having refuses things a person would have allowed.
- **Severity:** Low as a defect, high as a design lesson.
- **Workaround:** One bounded retry. The refused draft and the reason go back as a second
  turn, and the model fixed it to "12 of the booked visits". Two attempts maximum, then
  the rules-written summary is used instead — a third would be a model arguing with a
  rule, and the rule wins by construction.
- **Suggestion:** Nothing for Amazon. `Converse`'s multi-turn message shape made the retry
  three lines of code, which is the right outcome. Recorded because the obvious
  alternative, loosening the gate until the model passes, is the wrong move and is the one
  you reach for under time pressure.

### 9. Daylight saving is a false-accusation bug in this product, not a formatting bug

- **Task attempted:** Reconcile a booked visit window against the door log.
- **Steps taken:** The first implementation combined a rule's `start_time` with
  `timezone.utc`. A booked "08:00" became 08:00 UTC.
- **Expected:** Correct, since the events are UTC too.
- **Actual:** Wrong for half the year anywhere that observes summer time. A carer arriving
  at 08:05 on the Monday after the spring change is at 07:05 UTC, an hour outside an
  08:00–08:45 UTC window, and the app reports "No arrival was recorded at the front door"
  for a visit that happened exactly on time.
- **Severity:** High. In most products an hour of drift is cosmetic. Here it is an
  unfounded suggestion that a named, low-paid person did not turn up, produced twice a
  year, with nothing on screen to show it came from arithmetic.
- **Workaround:** Households carry an IANA zone. `ScheduleRule.visit_for(day, tz)` builds
  the window in that zone, and `digest.build` counts a household's days in its own local
  day rather than Greenwich's. Every printed timestamp carries its offset
  (`2026-03-30 08:05:00 BST (UTC+01:00)`), with RFC 3339's `-00:00` where the offset is
  genuinely unknown. `tests/test_timestamps.py` asserts the correct result and
  demonstrates the wrong one, so the regression cannot return quietly.
- **Suggestion:** Ring's webhook envelope is UTC-only and carries no device timezone. A
  `device_timezone` field on the device record would let integrators reconcile against
  local schedules without asking the user to configure a zone a second time. Every
  scheduling product on this platform will hit this, and the ones that hit it silently
  will produce exactly the accusation described above.

### 10. An editable schedule manufactures accusations if it is not versioned

- **Task attempted:** Let a household change its care package instead of shipping a
  fixture.
- **Steps taken:** Added `schedule_rules` with `active_from` and `active_until`, then
  tried the obvious thing: edit a rule in place.
- **Expected:** A schedule change affects the schedule.
- **Actual:** It rewrites history. Move Tuesday's visit from 08:00 to 10:00 today and
  every past Tuesday is re-compared against a 10:00 window that was never booked on any of
  them, so each reports "no arrival was recorded". Nobody typed an accusation and no
  phrasing rule was broken. The harm arrives as a data bug.
- **Severity:** High, and worth naming because it is invisible in testing: every
  individual function is correct.
- **Workaround:** `active_from` defaults to today rather than to "always", so a new booking
  cannot reach backwards; backdating is possible but has to be asked for.
  `amend_schedule_rule` closes the old rule the day before the change and opens a new one,
  so no past day moves and nothing is deleted. `end_schedule_rule` sets `active_until`
  instead of deleting, and a rule can only be deleted outright if it never governed a day.
  `tests/test_schedule_versioning.py` asserts the false row cannot appear.
- **Suggestion:** Not a Ring issue. Recorded because "make the schedule editable" is a
  one-line feature request with this inside it.

### 11. The gate catches the accusation and the page prints it anyway

- **When:** audit pass, reading the rendered family page rather than the tests.
- **Task attempted:** Confirm the one promise this product makes — that it can never say
  a carer did not come — holds on every path, not only the accepted one.
- **Steps taken:** Fed the gate a model that tries to accuse somebody, which
  `tests/test_narrate.py` already does, then followed the refusal instead of the pass.
- **Expected:** The refusal is metadata. The family sees a rules-written summary and a
  note that the draft was rejected.
- **Actual:** `PhrasingViolation`'s message embeds the full offending text:

  ```python
  raise PhrasingViolation(f"phrasing violation in model output: {phrase!r} in {text!r}")
  ```

  `narrate.py` collects `str(exc)` into `refusals` and into `rejected_reason`; `app.py`
  returns it; `store.py` persists it and the app re-serves it from cache; `index.html`
  writes it to the page. So the model writes "the carer did not come", the gate catches it
  exactly as designed, and the family member reads it anyway, inside a sentence explaining
  that it was blocked. Rendering is via `textContent`, so there is no injection — the
  problem is the content, and the content is the accusation the entire app exists to
  prevent.
- **Severity:** High. The failure is in the audit trail built to prove the guarantee.
- **Workaround:** Give `PhrasingViolation` a `rule` attribute alongside its full message.
  Keep the full message for logs and for the existing tests; set `rejected_reason` from
  `exc.rule` only. Name the rule that fired and the field it fired on, and keep the text
  where the developer is and the family is not.
- **Why it happened, which generalises:** showing the guard working is the right instinct
  and the module's own comment says so — "the model tried to say something it was not
  allowed to say and was stopped" is the most interesting thing the integration does. But
  showing the guard working means rendering what it caught, and that render is a second
  output path around the guard. It's a pattern worth checking for in any guarded-output
  build: the good instinct is to show the guard working, and nobody notices that a
  rejection is model output with a frame around it.

### 12. Two holes in the best-built gate in the repository, both found by running it

- **When:** same audit pass, at a terminal, with the guard ported out and fed real
  sentences.
- **Actual:** two defects, neither visible in 3,575 lines of test.

  ```
  PASSES GATE -> She's not recorded at the front door at 8.
  PASSES GATE -> The carer's booked time shows nothing at the door.
  PASSES GATE -> Front-door activity was recorded for 5 of the booked visits.
  blocked     -> The carer did not come on Wednesday. | PhrasingViolation
  ```

  **The apostrophe.** The tokeniser is `re.findall(r"[a-z']+", lowered)`, which puts the
  apostrophe inside the character class, so `she's` tokenises as one token and `she` is
  never seen. Same for `carer's`, `they're`, `he'd`, `nurse's`. The module docstring
  promises the opposite: "so pronouns and job titles cannot reappear in a paraphrase." Not
  one test line contains an apostrophe inside a banned word. The typographic `’` splits
  correctly and is caught, so the plain ASCII form is the hole — which is the form a model
  writes.

  **The blanket.** `digest.py` does `allowed |= {str(n) for n in range(0, 8)}`, so every
  integer from 0 to 7 is grounded unconditionally. Every number this product argues about
  lives in that range: visits scheduled, visits with no arrival, quiet days, door count,
  days covered. The anti-hallucination check is inoperative for exactly the figures an
  accusation rides on. The test that proves the check works uses `43`, a number from
  safely outside the blanket, and another test asserts `"999" not in allowed_numbers()`,
  which is true and proves nothing.
- **Severity:** High. Both are one-line fixes and neither would have been found by adding
  tests in the style of the existing ones, because the existing ones were written from the
  same mental model as the code.
- **Suggestion to anyone building a phrasing gate:** port the guard out into a scratch
  script and feed it sentences a person would write, including contractions, before
  trusting it. And when a grounding check needs an allow-list of numbers, check what range
  the product's own claims live in before whitelisting one.

### 13. Threading a timezone through a fixture is where the demo breaks, not the code

- **Task attempted:** Give the demo households a real zone (`Europe/London`) so the
  daylight-saving handling is visible rather than only tested.
- **Steps taken:** Set the zone, restarted the replay, looked at the ledger.
- **Expected:** Same demo, better timestamps.
- **Actual:** Every booked window moved an hour relative to the fixture, because the
  fixture's `created_at` values were authored as UTC wall times chosen to match the
  schedule. The Friday morning visit still read "activity recorded", but for the wrong
  reason: the household's own 07:14 movement had drifted into the window while the three
  actual doorbell events had drifted out. The status was right by accident, which is the
  worst way for a demo to be right.
- **Severity:** Medium. Forty minutes, and it would have survived a screenshot review.
- **Workaround:** The fixture generator now writes local wall-clock times and converts to
  the UTC instant Ring would carry, and the quiet-Saturday filter matches on the local
  date rather than a UTC string prefix. `replay.py --to-today` shifts schedule active
  ranges, notes and away periods by the same whole-day offset as the events, or the
  package amendment lands on the wrong side of the days it governs.
- **Suggestion:** For anyone building a replay harness against a UTC-only webhook: author
  fixtures in the local zone of the thing being simulated and convert on the way out.
  Authoring in UTC silently couples the fixture to one time of year.

### 14. The README's own demo command left every household unregistered, and nothing crashed to say so

- **When:** audit pass, following the "Running it" block exactly as a judge would, rather
  than the invocation already memorised from writing it.
- **Task attempted:** Confirm a judge pasting the documented command sees the three-state
  ledger described two paragraphs above it.
- **Steps taken:** Ran the form the README carried at the time:
  ```
  .venv/bin/python -m threshold.replay fixtures/normal_day.json \
      --schedule fixtures/schedule.json --port 8765
  ```
  then opened `http://127.0.0.1:8765/?device_id=front_door_1`.
- **Expected:** A morning and an evening visit showing recorded front-door activity, a
  midday visit showing "No arrival was recorded."
- **Actual:** The page shell returned 200 and rendered. Every data call behind it returned
  the same shape:
  ```
  GET /day?device_id=front_door_1       {"error": "unknown household: ''"}
  GET /log?device_id=front_door_1       {"error": "unknown household: ''"}
  GET /schedule?device_id=front_door_1  {"error": "unknown household: ''"}
  GET /weekly?device_id=front_door_1    {"error": "unknown household: ''"}
  ```
  and the weekly panel sat on "Loading this week's summary…" forever. Nothing crashed, so
  nothing forced a second look. The failure mode was silence, not an error page.
- **Severity:** High as a first impression, zero as a code defect. `threshold/replay.py`
  only registers a household when given `--households`; that flag is what walks
  `fixtures/households.json` and creates Oakfield, Millbrook and Harewood before the
  server starts. `--schedule` is a different, older fixture path (the code's own
  argparse help calls it "Legacy single-day schedule fixture"), and the command above was
  documenting that superseded form, not the one that actually populates anything.
- **Workaround:** Changed the documented command to `fixtures/care_week.json
  --households fixtures/households.json --to-today`, and added the sentence now sitting
  in the README's own run block: "`--households` is not optional. It is what registers
  Harewood Close, Millbrook Lane and Oakfield Road, their doors and their recurring
  visits; without it the server has no household to show and every read is a 404." No
  code changed; both the fixture and the flag already existed and simply were not the
  ones the README pointed at.
- **Suggestion:** Nothing Amazon-specific. A page that returns 200 and renders its shell
  is not evidence a demo works. The only way this was caught was opening the URL in a
  browser and reading what came back from each panel, the same discipline that caught the
  threading bug in row 6.

---

## Product feedback answers

**Tools, APIs and SDKs used.** Ring's REST API reference and webhook documentation,
read-only: there is no published Ring Python SDK, only the REST shape and an MCP server,
neither of which this app calls live. Amazon Bedrock through `boto3`'s `bedrock-runtime`
Converse API, for the weekly plain-language summary and nothing else. Python standard
library for the rest (`http.server`, `hmac`, `hashlib`, `json`, `sqlite3`, `zoneinfo`,
`dataclasses`), plus pytest in a local venv.

**Onboarding, zero to hello world.** Reading the docs was fine and the shapes are clear.
Exercising them was not possible without hardware this project does not have, so there is
no hello world to report. Bedrock's onboarding was the opposite: ambient credentials, one
CLI Converse call, working in minutes, with the model id as the only stumble.

**What worked.** The webhook signing scheme — HMAC-SHA256 over the raw body in
`X-Signature` — is simple and easy to reproduce for fixture replay. The event-type
composite filter syntax is a sensible design even though this app deliberately does not
gate on it. On the AWS side, `Converse` is the right abstraction: a system prompt, a
messages list and `inferenceConfig` covered everything, and the multi-turn shape turned a
"your draft was refused, here is why" retry into three lines rather than a redesign.
`botocore.config.Config` exposes connect timeout, read timeout and retries in one place,
which is what a product needs when a page load is waiting on the call.

**What needs work.** No simulator despite the hackathon page. No backfill on event
history. No device timezone in the webhook envelope, which turns daylight saving into a
false-accusation bug for every scheduling integration on the platform. No identity beyond
a `sub_type` classifier that is documented as unreliable, and no confidence score beside
it. Clip retrieval plan-gated in a footnote. On the AWS side, `ListFoundationModels`
listing models the account cannot invoke.

**Amazon Devices Builder Tools.** Not used. It sits first under "Start here" on the
Resources page and we went to the API reference instead. What would have made us install
it: a statement of what it knows that the reference does not. If it can answer "does this
endpoint need a paid plan" or "what timezone is this timestamp in", that is worth an
install, and neither question is answerable from the reference today.

**Would we build with it again?** Yes for a log-shaped product like this one, because the
webhook and event-history shapes are enough to build something honest on. No for anything
that needs to name or clear a specific person, and that is a property of the platform
rather than a complaint about it.

## One thing we would tell the next team

Follow your guard's refusal path as carefully as its accept path. Ours caught the
accusation and then published it, in a field called `rejected_reason`, on the page the
accusation was about. A rejection is not metadata; it is model output with a frame around
it. Name the rule, never the text.
