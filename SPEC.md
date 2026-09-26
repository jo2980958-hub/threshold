# Threshold: SPEC

Ring track. Python. Directory: `apps/threshold/`.

## What it does

Threshold turns a Ring doorbell's raw event stream into two things that sit on the
same page:

1. **An arrival log.** A neutral, timestamped list of front-door activity: motion and
   button presses. No names, no verdicts.
2. **A visit comparison.** The same log laid next to a household's care-visit
   schedule, showing whether front-door activity fell inside each scheduled window,
   and for how long.

That is the whole product. It does not identify who came. It does not decide whether
a carer did their job. It records whether the one sensor at the one door the API can
see picked up activity in a time window, and it says so in exactly those words.

## Who for

Two people looking at the same door for different reasons:

- **The family member three hours away**, wondering if their parent is up and about
  and whether the pendant they refuse to wear even matters. Surface: a silence alert
  when the household's usual morning door activity has not happened by the usual
  hour.
- **Whoever is paying for home care**, comparing a 45-minute booked visit against
  however many minutes of door activity actually happened. Surface: the schedule
  comparison log.

## The one screen that carries the demo

A single day view: the household's care schedule for today down the left (visit
blocks with start/end/label), and the door's activity log down the right, time
aligned. Each schedule block is annotated with exactly one of two states, and only
these two:

- **"Front-door activity recorded during this visit."**
- **"No arrival was recorded at the front door during this visit."**

Never "the carer did not come," never "missed visit," never "no show." The interface
and the code enforce this at the same choke point (`threshold/phrasing.py`): every
judge-facing string the schedule comparison can produce is drawn from one allowed
set, and a test asserts the forbidden phrases never appear in any output the logic
can generate.

Below the schedule, a short, permanent line of context that is part of the product,
not a disclaimer bolted onto marketing copy:

> "This is a record of activity at the front door, not a record of who provided care.
> A carer using a key safe, a back or side entrance, or arriving with a family member
> will not appear here. Treat a missing entry as missing information, not as
> evidence."

## What it deliberately does not do

- **It does not name or accuse a person.** Ring's API gives no identity beyond
  `sub_type: human` on a motion event, and that classifier has been documented
  calling a motorized wheelchair user a package. Threshold never filters or gates on
  `sub_type`; it is stored only as a display hint, never as a basis for inclusion,
  exclusion, or a claim about who was at the door.
- **It does not claim a live Ring simulator exists.** One does not (checked by a
  full grep of Ring's docs for `simulator`, `mock`, `virtual device`, `emulat`,
  `sandbox`, which returns nothing). The demo replays HMAC-signed fixture webhooks, built to
  the real Ring webhook envelope and verified with the real signing scheme, into the
  same receiver a live Ring integration would call. This boundary is explicit in
  `threshold/ring_client.py` (the real request shapes, never invoked against a live
  host in tests or the demo) and in this README.
- **It does not retrieve video clips.** Clip retrieval needs a paid
  continuous-recording plan Ring's docs say may not be present; the schedule
  comparison and the arrival log work from event metadata (`motion_detected`,
  `button_press`) alone, which every Ring device on any plan produces.
- **It does not backfill history.** Event history is time-gated to the moment
  consent was granted. A freshly linked household starts with an empty log, and
  `schedule.py` says so ("not enough history yet") rather than inventing a rhythm
  from zero days of data.
- **It does not alert the family before they ask**, in the sense of pushing a
  notification through a channel this app does not have. It computes and exposes the
  silence state; wiring it to SMS/push is out of scope for the hackathon build.
- **It is not a fraud detector.** It is a log. The OIG's own findings say these cases
  get proven by disproving the carer's paperwork with independent evidence, which is
  a neutral record, not an accusation engine, so that is what this ships as.

## Design direction

Checked against this hackathon's existing UI inventory before picking anything,
since fifteen other apps in this hackathon already claim a palette and a shell.

**Ground: graphite dusk**, a near-black warm-neutral charcoal, with a slightly
lighter graphite for panels. Every other dark ground already taken in the inventory
is hue-shifted toward a specific colour (dusk purple, plum-black); graphite dusk is
deliberately a true neutral, close to no colour at all, because the app's argument
is that the record itself should read as neutral. **Accent: lamplight brass**, a
muted, slightly desaturated warm gold, closer to an old porch bulb than to a
highlighter. Three warm accents are already claimed in the inventory (a bright
yellow, an apricot, and a saturated orange); brass sits well clear of all three by
being duller and more metallic rather than brighter. **Text and log entries sit in
paper white**, a warm off-white ink colour, not a full paper-coloured ground (paper
creams and sands are already claimed twice in the inventory), so the paper quality
shows up in the typography and the ledger lines, not in a background wash.

The shell is a **vertical time-axis ledger**: a single thin spine running down the
centre of the screen representing the day, with the care schedule's visit blocks
anchored to it on the left and the door's activity log anchored to it on the right,
both positioned at their real time of day so a viewer can see at a glance whether a
visit block on the left has any log entries beside it on the right. This is not a
left sidebar, not a top nav with a metrics strip, not a centred hero with cards, and
not a left rail, the four shells already in use elsewhere in this hackathon; it is
closer to a two-column diary than a dashboard, which fits a product whose entire
design argument is "this is a record, not a verdict."

Typography is a humanist serif for the log entries and the visit labels (read like a
household diary, not a spreadsheet) and a plain grotesk for the spine's timestamps
and the boundary note. Generous vertical rhythm suggests a day passing rather than a
grid of widgets.

**The ethical argument is built into the components, not left to a caption.** The
"no arrival was recorded" state never uses red, never uses a cross or an X mark,
and never sits directly beside a name; it renders in the same brass-on-graphite
system as every other state, at the same visual weight as "front-door activity
recorded," with a small clock glyph rather than any pass/fail icon, because both
states are answers to the same question ("was there door activity here") and only
one of two possible answers, not a verdict on a person. The permanent boundary
sentence sits inside the ledger between the two columns, not as a footnote below
them, so a viewer cannot read the log without also reading what the log does not
claim.

## Stack

Python 3, standard library only for the app (`http.server`, `hmac`, `hashlib`,
`json`, `sqlite3`, `dataclasses`, `datetime`). `pytest` for tests, installed in a
local venv (`apps/threshold/.venv`) so nothing leaks into the system interpreter.
No web framework dependency; the webhook receiver and the read endpoints are a small
stdlib HTTP server, matching the "Python, since the Ring starter is Python" stack
note without pulling in anything the demo does not need.
