/* Shared front-end helpers.

   Kept deliberately small and framework-free, matching the standard-library
   choice on the server side. Nothing here decides anything: every sentence
   about an arrival arrives from the API already written by threshold/phrasing.py,
   and this file only places it on the page. If you find yourself about to build
   a sentence here, it belongs in phrasing.py instead. */

const TH = {};

TH.escape = function (value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
};

TH.fmtTime = function (iso) {
  const d = new Date(iso);
  let h = d.getUTCHours();
  const m = String(d.getUTCMinutes()).padStart(2, "0");
  const ampm = h >= 12 ? "pm" : "am";
  h = h % 12;
  if (h === 0) h = 12;
  return h + ":" + m + ampm;
};

TH.fmtDate = function (isoDate) {
  // Built from the date string itself rather than the browser's clock, so a
  // replayed older day is labelled with its own date and not with today's.
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(undefined, {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
};

/* Every read in this app goes through here.

   It exists because none of them used to. `loadDay`, `loadWeekly`, the history
   page and the agency page all did `await (await fetch(url)).json()` with no
   catch, so a 404 left "Loading..." and "Checking this household's door
   record..." on screen forever while the console filled with
   `Cannot read properties of undefined`. A demo that silently shows spinners
   is worse than one that says what went wrong: a reader who cannot tell a slow
   page from a broken one is being asked to trust a screen that is lying to
   them, and that is the opposite of what this app is for.

   The server's own `{"error": "..."}` is shown when there is one, because it
   names the household or the date the reader got wrong. */
TH.ReadFailed = function (message) {
  this.name = "ReadFailed";
  this.message = message;
};
TH.ReadFailed.prototype = Object.create(Error.prototype);

TH.readJson = async function (url) {
  let res;
  try {
    res = await fetch(url);
  } catch (err) {
    throw new TH.ReadFailed(
      "The page could not reach the server. It may have been stopped; the " +
        "terminal it was started from will say."
    );
  }
  let data = null;
  try {
    data = await res.json();
  } catch (err) {
    data = null;
  }
  if (!res.ok) {
    throw new TH.ReadFailed(
      (data && data.error) || "The server answered with " + res.status + "."
    );
  }
  if (data === null) {
    throw new TH.ReadFailed("The server's answer was not readable as JSON.");
  }
  return data;
};

/* Says so, in the banner every page carries, and returns nothing.

   `replacing` is a map of element id to the sentence that should stand where a
   "Loading..." placeholder is, so no spinner survives a failure. */
TH.sayReadFailed = function (err, replacing) {
  const message =
    err && err.name === "ReadFailed"
      ? err.message
      : "Something went wrong while reading this page. " + (err && err.message ? err.message : "");
  const banner = document.getElementById("read-failed");
  if (banner) {
    banner.textContent = "This page could not be filled in. " + message;
    banner.hidden = false;
  }
  Object.keys(replacing || {}).forEach(function (id) {
    const el = document.getElementById(id);
    if (el) el.textContent = replacing[id];
  });
  if (!banner) throw err;
};

TH.params = function () {
  return new URLSearchParams(location.search);
};

TH.query = function (extra) {
  const p = TH.params();
  Object.keys(extra || {}).forEach((k) => {
    if (extra[k] === null || extra[k] === undefined || extra[k] === "") p.delete(k);
    else p.set(k, extra[k]);
  });
  return p.toString();
};

TH.go = function (extra) {
  location.search = TH.query(extra);
};

/* Fills the household picker in the masthead and returns the selected id. */
TH.mountHouseholds = async function (selectId) {
  const select = document.getElementById(selectId);
  const data = await TH.readJson("/api/households");
  const asked = TH.params().get("household");
  if (!data.households.length) {
    throw new TH.ReadFailed(
      "No household is registered, so there is nothing to show. The demo " +
        "registers three: re-run it with --households fixtures/households.json."
    );
  }
  const known = data.households.some(function (h) { return h.id === asked; });
  if (asked && !known) {
    // The dropdown would otherwise show the first household's name while every
    // fetch asked for the one in the URL, and 404ed.
    throw new TH.ReadFailed(
      "There is no household with the id \u201c" + asked + "\u201d. The " +
        "households this server knows are: " +
        data.households.map(function (h) { return h.id; }).join(", ") + "."
    );
  }
  const selected = asked || data.selected || data.households[0].id;
  select.innerHTML = data.households
    .map(
      (h) =>
        `<option value="${TH.escape(h.id)}"${h.id === selected ? " selected" : ""}>` +
        `${TH.escape(h.name)}</option>`
    )
    .join("");
  select.addEventListener("change", () => TH.go({ household: select.value, device_id: null }));
  return { selected: selected, households: data.households };
};

/* Pushes overlapping rows apart on the time axis.

   Takes each row's real rendered height rather than a fixed pixel gap, because
   a status line that wraps to four lines on a narrow window is four times
   taller than one that does not, and a constant gap lets wrapped text collide.
   Runs on a list already sorted by time. */
TH.declutter = function (positions, heights, minPad) {
  const out = positions.slice();
  for (let i = 1; i < out.length; i++) {
    const minY = out[i - 1] + heights[i - 1] + minPad;
    if (out[i] < minY) out[i] = minY;
  }
  return out;
};

TH.WIDE = "(min-width: 821px)";
TH.isWide = function () {
  return window.matchMedia(TH.WIDE).matches;
};

window.TH = TH;
