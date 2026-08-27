# HomeScreen Platform — Requirements

**Status:** Revised 2026-08-27 after the owner answered the first round of open questions.
**Scope:** What a component is, what a provider is, what an assignment is, what the API
looks like, and the order to build them in.
**Precedence:** Below `CLAUDE.md`. Extends the
[server-driven displays design](2026-08-26-server-driven-displays-design.md) §5 and §9;
where that document defers something (rotation, vocabulary revision, provider registry),
this one decides it.

**Settled by the owner, not open (do not relitigate):**
- Large panels show a **composed dashboard** of several components, per SPEC §9's bands and
  columns. The round panel is the one-region case of the same model.
- **Secrets are set from the dashboard**, write-only, never rendered back.
- **Rotation is replaced by a schedule.** A component or a composed dashboard per calendar
  slot. There is no "cycle every N seconds".
- **The API is REST.** One noun, correct verbs, sub-resources.

This document specifies. It does not implement.

---

## 1. Product intent

A house with a few screens in it, each showing what is useful *at that hour*, changed from
a web page on the Pi and never by touching the screen.

A component — clock, weather, a ticker, the calendar, aircraft overhead — is written once
and offered to every screen it can honestly draw on. A round 240×240 LCD and an 800×480
e-paper are different glass, not different products: the same component decides what fits
on each, and the big panel composes several of them at once the way the original desk
dashboard was designed.

Configuration belongs to the screen, not to the component: two screens can show the clock
in two cities, and neither is a property of the code. What a screen shows can change with
the day — the radar in the afternoon, a clock overnight, weather on weekend mornings —
without anyone touching it.

Anything a component needs from the internet is fetched once by one daemon on the Pi, on
its own cadence, and shared by every screen that wants it. Keys are typed into the
dashboard and never come back out.

The Pi does the work so the screens don't have to. Nothing here leaves the LAN, nothing
here needs an account, and adding a component is one Python file.

---

## 2. The component contract

A component is a module in `homescreen/scenes/<name>.py` with one entry in
`scenes._registry()`. Most of this section describes what already exists; it is written
down so a new component author has one list.

### 2.1 What a module declares

```python
# homescreen/scenes/weather.py
OPTIONS   = (...)                       # §2.2
def providers(options: dict) -> tuple:  # §2.4, optional, default ()
def supports(caps: dict) -> str | None: # §2.3, optional, default None
def build(ctx: SceneContext) -> Scene:  # §2.5
```

One import and one entry in `scenes._registry()`. `registry.ASSIGNABLE_SCENES` derives from
that table, so there is no second list. A component that adds no new drawing vocabulary
adds no golden fixture.

`ROTATES` and `rotate_s` from the previous draft are **withdrawn**. Scheduling replaced
them; see §4.

### 2.2 Options — the schema is the form, the validation and the defaults

`OPTIONS` is a tuple of field specs. The dashboard renders the form from it, the registry
stores the values against the **placement** (§4.1), and `scenes.clean_options` coerces them.
Adding an option is one edit.

| Key | Required | Notes |
|---|---|---|
| `key` | yes | stored verbatim under the placement |
| `label` | yes | **Spanish** — this is UI copy |
| `type` | yes | `text` \| `int` \| `bool` \| `choice` |
| `default` | yes | what an unconfigured placement behaves as |
| `help` | no | **Spanish**, one sentence |
| `datalist` | no | a name in `web.fields.DATALISTS` (`timezones` today) |
| `choices` | for `choice` | |
| `min` / `max` | for `int` | |
| `placeholder` | no | |

Bounds, already enforced: ≤12 options per component, ≤120 chars per value. Unknown keys are
dropped; a bad value falls back to its default rather than rejecting the assignment.

**A blank option means "use the global default"** — how `clock.timezone` already resolves
against `config.yaml`'s `location:`, and the rule for every option with a server-side
default (§3.4).

**An option is never a secret.** Options are stored in `cache/devices.json`, returned by the
unauthenticated device API, and rendered into `/device/<hw>`. An API key or an ICS URL in an
option is an API key on the LAN. Secrets travel a separate write-only channel and are
referenced **by name** (§3.6). `overrides.FEED_SETTABLE_KEYS` already excludes `api_key` for
exactly this reason; that exclusion stands and is never widened.

**Option memory is per screen, per component, and now per placement.**
`registry.OPTIONS_MEMORY_FIELD` already remembers the outgoing component's settings so
trying one out and going back does not lose them. Under §4 that memory is keyed by
`(placement_id, component)`, because a composed dashboard can hold two clocks in two
regions and they are not the same screen's clock.

### 2.3 Rendering capabilities — which screens and regions this component is offered to

Availability is **derived from what `build` emits**, not asserted in a table that can lie:

| The component emits | It becomes available to |
|---|---|
| an html fragment + css | pixel-push surfaces — the Pi composes and rasterises |
| a component carrying `draw` | any device declaring `draw_list` (§6, item 1) |
| a bespoke component (`radar`) | only devices declaring that exact name |

**Every new component must emit both.** A component that emits only one is available on half
the fleet, and the operator finds out from a greyed option rather than from the code.

**A region is a geometry, not a new concept.** `ctx.caps` carries the `w`/`h`/`depth` of the
area this placement occupies — the whole 240×240 panel, or the 417×335 left column of the
e-paper. A component that already composes for two panel sizes composes for a region with no
new declaration. This is the whole reason regions cost almost nothing (§4.2).

Constraints a component inherits on each surface:

- **240×240 round.** Slots narrow towards the rim; `rim_top`/`rim_bottom` sit at 0.12/0.88
  for that reason. Realistic budget: one `xl` value, one `sm` label, one `xs` line at the
  rim.
- **800×480 1-bit e-paper.** `scenes._style.BASE_CSS` rules are invariants, not preferences:
  `#000`/`#fff` only, nothing below 10px, hierarchy by size and weight only, no CDN fonts,
  smoothing off. Inverted pills are capped at **two on screen** (SPEC §3) — a fleet-wide
  budget, so a component that wants one must be configurable without it. Never place
  graphics behind text.

`supports(caps) -> str | None` is the escape hatch: return a **Spanish** sentence when this
component genuinely cannot draw on this glass or in this region ("necesita al menos 200 px
de alto"), and the dashboard shows it as the disabled option's reason. (`_scene_options`
today emits English — `"no pixel rendering"`, `"needs radar"` — into a Spanish page. That is
a bug, fixed under item 1.)

### 2.4 Data needs

```python
def providers(options: dict) -> tuple[dict, ...]:
    return ({"provider": "openmeteo",
             "params": {"lat": 40.4168, "lon": -3.7038, "units": "metric"}},)
```

Pure, cheap, no I/O — it is called for every placement on every fetch cycle and by the
dashboard to show which jobs a screen creates. Its return value derives from **this
placement's options** merged over the global defaults, which is what makes two screens
wanting different cities two jobs and two screens wanting the same city one job (§3.2).

**A placement in a schedule slot that is not currently active still declares its providers.**
The night-time weather is fetched during the day, or the panel shows an hour-old first frame
at the moment the slot opens. A job is dropped only when nothing on any screen's schedule
references it.

A component with no `providers` needs no data. `clock` and `status` stay that way.

### 2.5 Build

`build(ctx) -> Scene`, where `ctx` carries `cfg`, `cache_dir`, `caps` (this region's
geometry), `now`, `device` and this placement's validated `options`.

- Reads data **only** from the provider cache envelopes its `providers()` named. It never
  performs network I/O — `serve.py` must remain import-clean of anything that fetches.
- Returns `components` (data push) and/or `html` + `css` (pixel push), `layout="fill"`,
  `poll_s`, `poll_max_s`.
- **`Scene.html` becomes a FRAGMENT, not a document.** A composed dashboard puts several
  fragments on one page, so the `<!doctype>`, the `<style>` and the page box move to the
  surface template. `scenes._style.page()` keeps producing the identical document for the
  one-placement case — pinned by a golden test asserting the 800×480 clock frame is
  byte-identical before and after the change.
- **Must never raise.** `safe_build` catches, but a caught exception is a lost screen. In a
  composed dashboard one placement that fails collapses to a short Spanish note in its
  region; it never takes the other regions down.
- **Must render with `ok: false`.** Last good data, marked stale (a tertiary `·` past one
  hour, SPEC §11). Empty sections collapse; never an empty rectangle sitting on glass for six
  hours. With no envelope at all it renders a Spanish "sin datos", not a blank.
- All third-party strings are HTML-escaped on the pixel path, as `planes.py` already does
  because callsigns come from adsb.fi.

### 2.6 Cadence

- `poll_s` — when to come back. **Aim at the next change**, never a fixed grid: the clock
  asks for `60 - now % 60`, a weather component asks for the next provider refresh. One
  request per change beats twelve per minute that still land late.
- `poll_max_s` — the stable ceiling liveness is judged against.
- Precedence, unchanged: **operator setting > component > hardware floor.** `poll_floor`
  exists because a 1-bit panel's own refresh is ~3s and there are two Chromium render slots
  for the whole fleet.
- The server then folds in the **schedule boundary** (§4.5). A component never computes that
  itself and never needs to know a schedule exists.
- SPEC §6's `refresh.quiet_hours` is a **platform** stretch applied over a component's
  answer, not something each component reimplements.

### 2.7 Preview

Falls out of §2.3 with no per-component work: the preview executes the same instruction list
the device executes. It is not the frame — fonts and antialiasing are the panel's — but
nothing about the layout is guessed.

Requirements a component inherits:
- Previewable **per region** in a composed dashboard, and as the composed whole.
- Previewable **at a chosen time**, because a schedule means "what does this show" has no
  single answer (§4.6).
- Reflects the option values **currently in the form**, before saving.
- A component that emits no instruction list (radar) says "sin vista previa" rather than
  showing a different program's guess.

---

## 3. The provider contract

### 3.1 What a provider is

A module in `homescreen/sources/<name>.py` that turns parameters into one cache envelope.

```python
NAME               = "openmeteo"
PARAMS             = (...)      # field specs, same shape as OPTIONS
DEFAULT_INTERVAL_S = 600        # what this data is worth refreshing at
MIN_INTERVAL_S     = 60         # upstream's floor; an operator cannot go below it
MIN_SPACING_S      = 0.0        # inter-request spacing across all jobs of this provider
SECRETS            = ()         # e.g. ("api_key",) — names, never values

def fetch(params: dict, secrets: dict, session) -> dict:
    """Return the envelope's `data`. May raise; the runner records the failure."""
```

**Metadata must be importable without importing `requests`.** `serve.py` reads
`NAME`/`PARAMS`/`SECRETS` to render the settings page, and the VALIDATION C7 guard forbids
the fetching machinery from entering its import graph. `adsb.py` already does
`import requests` inside the function; that pattern becomes the rule.

### 3.2 Jobs — how work is keyed and deduped

A fetch job is **(provider, params)** and nothing else. Not a device, not a screen, not a
slot.

```
key       = sha256(json.dumps({"provider": p, "params": params},
                              sort_keys=True, separators=(",", ":")))[:16]
cache     = cache/feed/<provider>/<key>.json
envelope  = { fetched_at, ok, error, data }        # SPEC §7, no exceptions to the shape
```

Every cycle the daemon walks every **approved** screen's whole schedule — default view and
every slot — calls each placement's `providers(options)`, and unions the keys. Two screens
showing Madrid weather are one job. Two screens showing different tickers are two jobs. A
screen that is revoked, removed, or whose schedule stops referencing a job stops creating it
on the next cycle, with no restart.

This is the lesson `config.feed_data_path` already records: keying the cache by device
produced an empty sky forever, because the subscription is per-**location**, not per-screen.
The job key generalises that to every provider.

### 3.3 Cadence and rate limits

Per job: `max(provider.MIN_INTERVAL_S, operator_override or provider.DEFAULT_INTERVAL_S)`.
Same precedence shape as polling — **operator > provider > floor** — and an operator value
below the floor is **clamped, not ignored**, for the same reason a scene's cadence is: the
failure mode must be "fetches oddly", never "stopped fetching".

Per provider: `MIN_SPACING_S` is enforced as **spacing between requests, not an average**.
adsb.fi's 1 req/s is the existing case and the reason `check_cadence` rejects a second radar
at the shipped timeouts. A configuration that would exceed a documented free tier is refused
**at startup with the arithmetic in the message**, in the style of `check_cadence`, rather
than discovered as a 429 at 14:00.

### 3.4 Global defaults vs per-placement

| Lives globally | Lives on the placement |
|---|---|
| provider endpoint, interval override | which city, which symbols, which calendar |
| API keys and secret URLs (§3.6) | units, how many rows |
| the house's `location:` and `secondary_clock:` | everything else the component asks for |

A blank placement option resolves to the global default — `clock.timezone` today, unchanged.

The endpoint/interval half of this **already ships**: `overrides.FEEDS_KEY = "@feeds"`,
`FEED_SETTABLE_KEYS = ("endpoint", "fetch_seconds")`, `set_feed()` and `POST /settings`
landed in `634de6a`. Generalising it from one hardcoded `"adsb"` to every registered
provider is part of item 4, not new machinery.

> **One disagreement, stated once.** The owner asked for feed config to be "global but
> changeable per scene". For *credentials and endpoints* that is right and it is what §3.4
> says. For *content parameters* — which city, which tickers — a global default every screen
> can override is a layer a three-screen house never cashes in: "why is this screen showing
> Madrid" becomes two lookups instead of one. My recommendation is that content parameters
> live only on the placement. I have planned for what was asked anyway, because
> blank-means-default costs nothing and the disagreement is about which fields get a global
> default, not about the mechanism.

### 3.5 Failure

Unchanged from CLAUDE.md §6 and SPEC §11, now applied per job:

- No fetcher may raise into the render path. On failure keep the previous cache and set
  `ok: false`; recording the failure is itself guarded, because a read-only SD card makes
  every write throw.
- An identical repeated failure is **not rewritten** — 28,800 fsyncs a day onto the microSD
  during an outage. `cache.write_failure` already does this.
- One job's failure never stops the cycle.
- The settings page shows per-job health: provider, params summary with secrets redacted,
  age, last error, in Spanish. A source with no feed of its own is **listed and labelled**,
  not skipped — `634de6a` fixed exactly that omission and the job list must not reintroduce
  it.
- The daemon exits **78 (EX_CONFIG)** only for faults that will not fix themselves;
  `RestartPreventExitStatus=78` is already wired in both units.

### 3.6 Secrets — write-only, set from the dashboard

Settled: the dashboard writes them. Open-Meteo needs no key, which is why weather ships
first; stocks, sport and the calendar do.

**Storage.**
- `cache/secrets.json`, created mode **`0600`**, written by `serve.py`, read by the fetch
  daemon. Both run as `pi`. `cache/` is already gitignored.
- Written atomically with `fsync` on the file **and the directory**, like `overrides.save` —
  a mains Pi on a microSD with no UPS classically yields a zero-length file otherwise, and a
  silently reverted API key looks like an upstream outage.
- Shape: `{"<provider>": {"<secret>": {"value": "...", "updated_at": "<iso>"}}}`.

**The structural guarantee is separation, not an allowlist.** Secrets are **never merged
into `cfg`**. `overrides.apply()` does not touch them, so `/api/status`, `_device_summary`
and every other reader of the config dict cannot render one even by accident. A named-key
allowlist would be a rule that can drift; a file the render path never reads cannot.

**Two functions, one of which the serve path may call.**
- `secrets.state(provider) -> {name: {"set": bool, "updated_at": str}}` — presence only.
  This is what the dashboard and the API read.
- `secrets.get(provider, name) -> str | None` — the fetch runner only.

**The enforcing test is a sentinel, not a convention.** With a secret set to a known nonsense
string, that string appears in **no** response body: `/`, `/device/<hw>`, `/settings`, and
every JSON route. Asserted by iterating the app's URL map so a route added later is covered
without anyone remembering to add it.

**Setting, replacing, clearing.**
- The form field is empty on every render and carries a state pill: `configurada` /
  `sin configurar`, plus `actualizada <cuándo>`.
- Submitting it **blank leaves the stored value untouched** — so saving an unrelated setting
  cannot wipe a key, which is the failure a write-only field invites.
- An explicit `borrar` checkbox clears it.

**Read-back attempts.** `GET` on a secret returns `200` with `{"name": "api_key", "set":
true, "updated_at": "..."}` and **no `value` key at all** — absent, not `null`, so nothing
downstream can log an empty string as though it were the secret. It is 200 rather than 403
because the resource's *state* is legitimately readable; only its value is not. There is no
route, parameter or debug flag that returns the value, and no `?include=value`.

**The accepted exposure.** The LAN is unauthenticated by the owner's decision, so anything
on it can *overwrite* a key — the same exposure already accepted for reassigning every
screen in the house. Reading one back is not accepted, which is why the store is write-only
over HTTP.

### 3.7 What ADS-B already proves, and what generalises

`sources/adsb.py` is the reference implementation of most of this and should not be
rewritten to fit the shape — it should *be* the shape:

| Already right, generalise as-is | Needs to change |
|---|---|
| own daemon, own cadence, never on a device request (C7) | targets come from `config.yaml devices:`, not from placements |
| envelope `{fetched_at, ok, error, data}` (SPEC §7) | cache is `cache/feed/<feed>.json`, not job-keyed |
| never raises, guarded down to the failure write | params live in `config.yaml` + `overrides.json` |
| dedup by cache path so N screens are one request | `check_cadence` is hardcoded to one provider's arithmetic |
| loop, not a timer — interval in config, no SD churn | `check_config` knows what an aircraft is |
| startup validation that fails loudly (EX_CONFIG) | |
| an operator-settable endpoint and interval (`@feeds`) | it is hardcoded to the name `"adsb"` |

The 12-second firmware extrapolation horizon and the 1 req/s limit are ADS-B's own numbers
and stay ADS-B's, expressed as `MIN_INTERVAL_S`/`MIN_SPACING_S` plus a provider-specific
startup check.

---

## 4. The assignment model: placements, regions, views, schedules

This section replaces "a screen shows one component" and replaces rotation.

### 4.1 Four nouns

| Noun | Is | Identity |
|---|---|---|
| **placement** | one component in one region with its own options | `id`, stable |
| **view** | an ordered list of placements — a composed dashboard | name, per screen |
| **slot** | a recurring wall-clock window pointing at a view | `id`, ordered |
| **schedule** | a default view plus a list of slots | one per screen |

A round 240×240 screen is the degenerate case all the way down: one region, one placement,
one view, zero slots. **Nothing about it is special-cased** — it is the same record with
smaller numbers, which is exactly why the record is being shaped now rather than migrated
later.

### 4.2 Regions are geometries

A **surface** (a class of glass) declares its regions. That is the only new table, it lives
server-side, and it is derived from SPEC §9's measured geometry rather than invented:

| Surface | Region | Rect (x,y,w,h) | Holds | Stacks |
|---|---|---|---|---|
| `round_240` | `full` | 0,0,240,240 | 1 | — |
| `epaper_800x480` | `masthead` | 0,0,800,53 | 1 | — |
| | `main_left` | 18,63,417,335 | 4 | vertically |
| | `main_right` | 461,63,321,335 | 3 | vertically |
| | `markets` | 18,406,764,62 | 6 | horizontally (FX + 5 tickers) |

- A region's capacity is a **hard cap with a stated reason**. `markets` is 6 because SPEC §9
  measures the band at 764px and says five tickers is the ceiling — the sixth truncates
  symbols. The dashboard refuses the seventh with a Spanish notice; it does not silently
  render a truncated row.
- Placements in a stacking region **flow and collapse**: an empty one takes no height (SPEC
  §11), which is the existing "sections collapse" invariant expressed as a layout rule.
- A component receives its region's rect as `ctx.caps` and composes for it exactly as it
  already composes for 240×240 versus 800×480. **No component learns what a region is.**
- The clock's partial-refresh window `(16,72)–(280,140)` is a property of the *masthead+left
  column* composition, not a global constant (PLAN S2). A view declares which placement, if
  any, owns the partial window; a view with no clock in the top-left has no partial window
  and refreshes fully.

**CSS isolation.** The server wraps each fragment in `<div id="rg-{placement_id}">` and
prefixes every selector in that component's CSS with `#rg-{id} ` at compose time — a small,
testable string transform. Components keep writing `.big{...}` as `clock.py` does today and
change nothing. Acceptance: two placements of the same component with different options
render different values and neither's CSS leaks into the other.

### 4.3 The schedule — shape

A slot is **weekday set + wall-clock window**. Nothing more.

```json
{
  "schedule": {
    "tz": "Europe/Madrid",
    "default": "reposo",
    "views": {
      "reposo": {
        "placements": [
          {"id": "p1", "region": "full", "component": "clock",
           "options": {"timezone": "", "show_seconds": false}}
        ]
      },
      "vuelos": {
        "placements": [
          {"id": "p2", "region": "full", "component": "planes",
           "options": {"radius_km": 60, "show_ground": false}}
        ]
      },
      "finde": {
        "placements": [
          {"id": "p3", "region": "full", "component": "weather",
           "options": {"lat": "", "lon": "", "units": "metric"}}
        ]
      }
    },
    "slots": [
      {"id": "s1", "view": "vuelos", "days": [1,2,3,4,5,6,7],
       "from": "09:00", "to": "23:00"},
      {"id": "s2", "view": "finde", "days": [6,7],
       "from": "08:00", "to": "12:00"}
    ]
  }
}
```

The 800×480 case is the same record with more placements in one view:

```
{"id": "e1", "region": "masthead",   "component": "masthead", "options": {}},
{"id": "e2", "region": "main_left",  "component": "clock",    "options": {"timezone": ""}},
{"id": "e3", "region": "main_left",  "component": "calendar", "options": {"source": "casa"}},
{"id": "e4", "region": "main_right", "component": "weather",  "options": {"units": "metric"}},
{"id": "e5", "region": "markets",    "component": "quotes",   "options": {"symbols": "VWCE.DE,AAPL"}}
```

Field rules:

- `days` — ISO weekday numbers, Monday = 1. A set; order is ignored.
- `from` / `to` — `HH:MM`, **wall clock in `schedule.tz`**, minute precision.
- `to <= from` **wraps midnight**: `"from": "23:00", "to": "09:00"` is the night. The slot
  belongs to the day its `from` falls on, so a Friday-night slot runs into Saturday morning.
  This is the one non-obvious rule and it exists because "clock at night" is the owner's own
  example.
- `view` — a key in `views`. A slot pointing at a missing view is a **validation error on
  write**, not a blank panel at 03:00.
- `default` — required. A schedule without one is rejected.

**Named views rather than inline ones**, because Q6's answer makes a view a thing worth
naming: an 800×480 composition is five placements someone arranged, and duplicating it
across a weekday and a weekend slot is how one copy gets edited and the other does not. The
cost is one indirection and one rule: **a view referenced by nothing is kept, not
garbage-collected.** Deleting a view is an explicit action, and deleting one a slot still
references is refused.

**What this deliberately cannot express**, and will not grow to:

- specific dates — "the countdown on 31 December"
- nth-weekday-of-month, alternating weeks, holidays
- sunrise/sunset-relative times
- one-off overrides or exceptions to a recurring slot
- seconds precision
- different schedules per region (see Q9)

Anything on that list is a second scheduling language. The three examples the owner gave —
radar during the day, clock at night, weather on weekend mornings — are all weekday-set plus
window, and stopping there is what keeps the editor a table with five columns.

### 4.4 Resolution: which view is showing

1. Evaluate `now` in `schedule.tz` → `(weekday, HH:MM)`.
2. Collect every slot whose `days` contains the weekday **and** whose window contains the
   time (wrapping slots are tested against the previous day's `from` as well).
3. **The last matching slot in the list wins.** One sentence, no specificity ranking to
   argue about. The dashboard renders slots in order, lets them be reordered, and marks the
   one that is `activo ahora` so precedence is never a guess.
4. No match → the `default` view. **Never blank.** A screen showing nothing looks broken and
   there is no way to tell it from a dead one at a glance.

**Membership testing, not edge triggering.** The server asks "which slot contains this
instant", never "did a boundary just pass". That choice is what makes DST a non-event:

- **Spring forward** (02:00→03:00 does not exist): a boundary inside the skipped hour never
  needs to fire. At the next evaluated instant the slot is simply already active or already
  over.
- **Fall back** (02:00 happens twice): a `01:30–02:30` slot is active twice. Harmless.
- A device that was asleep across a transition asks "what now?" and gets the right answer.

### 4.5 Cadence — the schedule boundary is a known future time

This is the part that makes scheduling cost almost nothing, and it reuses the mechanism the
clock already proved.

```
next_change = min(scene.poll_s, seconds_until_next_slot_boundary)
X-Poll-Seconds = registry.poll_seconds(rec, scene_poll_s=next_change)
                 # = operator override, else max(next_change, poll_floor(caps))
poll_max_s     = scene.poll_max_s        # UNCHANGED
```

- **`min`**, because the picture changes at whichever comes first — the component's own next
  change, or the moment the slot flips.
- **`poll_max_s` deliberately does not shrink to the boundary.** Liveness must be judged
  against the stable ceiling; that is the lesson from the clock's countdown, where advertising
  1s and judging silence at 3s would call a healthy panel dead.
- **A slot boundary cannot drive an e-paper below `poll_floor`.** No new mechanism is
  needed: `registry.poll_seconds` already applies `max(n, poll_floor(caps))`, so feeding it
  a smaller number is bounded by the same 30s floor. The consequence is stated plainly — on
  1-bit glass a transition lands up to 30s late, on a panel whose own refresh is ~3s.
- **`scenes.POLL_MAX_S` is 600**, so a device never sleeps more than ten minutes regardless.
  A boundary six hours away is already clamped to 600 and re-evaluated on arrival. This is
  why the DST arithmetic can never do real damage: the worst case for a mis-computed
  boundary is **one 10-minute cycle late**, never a stuck panel. Compute the next boundary in
  local wall time, clamp, and let the next poll correct it.

### 4.6 Preview — "what will this show, and when"

A schedule means "what does this screen show" has no single answer, so the preview grows a
time.

- A **week strip** on the screen's page: 7 rows × 24 columns, each cell coloured by the view
  that wins there, with `ahora` marked. This is how overlap precedence is *seen* rather than
  reasoned about.
- Clicking a cell, or typing a time, re-renders the preview **at that instant**: the
  preview accepts an `at` parameter and resolves the schedule against it instead of `now`.
- For a composed surface, the preview renders **the whole view** with region outlines, and
  each region individually.
- Unsaved form values are reflected before saving.

---

## 5. The API

The current surface has four names for one thing — `/api/device/<hw>/scene`,
`/api/devices/<hw>`, `/api/display/<name>/data`, `/api/config/devices/<id>` — plus
`POST /home/device` and `POST /api/devices/<hw>/approval` for what is a sub-resource's state.

### 5.1 One noun

**`/api/devices/{hw}`.** Not a fifth name. `devices` is already plural, already the admin
surface, and already what the registry calls the thing; renaming it to `screens` would be
churn for a synonym. The Spanish UI says *pantalla*; the English API says *device*; that
split already exists everywhere in this codebase and is correct.

### 5.2 The map

| Method | Path | Returns | Codes |
|---|---|---|---|
| GET | `/api/devices` | `{devices:[…]}`; `?state=pending\|approved` | 200 |
| GET | `/api/devices/{hw}` | one device | 200, 404 |
| PATCH | `/api/devices/{hw}` | name, `poll_seconds` only | 200, 400, 404, 503 |
| DELETE | `/api/devices/{hw}` | — | 204, 404, 503 |
| GET/PUT | `/api/devices/{hw}/membership` | `{"approved": bool}` | 200, 400, 404 |
| GET/PUT | `/api/devices/{hw}/schedule` | the whole schedule (§4.3) | 200, 400, 404 |
| GET | `/api/devices/{hw}/scene` | **device-facing**; caps in the query | 200, 304, 400, 404 |
| GET | `/api/devices/{hw}/frame` | **device-facing**; `?w=&h=` required | 200, 304, 400, 404, 503 |
| GET | `/api/devices/{hw}/preview.svg` | `?view=&region=&at=` | 200, 404 |
| GET | `/api/components` | catalog: name, options schema, providers, surfaces | 200 |
| GET | `/api/components/{name}` | one | 200, 404 |
| GET | `/api/providers` | catalog: name, params schema, secret **names** | 200 |
| GET/PUT | `/api/providers/{name}/settings` | endpoint, interval | 200, 400, 404 |
| GET | `/api/providers/{name}/secrets/{s}` | `{name, set, updated_at}` — **no value** | 200, 404 |
| PUT | `/api/providers/{name}/secrets/{s}` | `{"value": "…"}` | 204, 400, 404 |
| DELETE | `/api/providers/{name}/secrets/{s}` | — | 204, 404 |
| GET | `/api/jobs` | live fetch jobs and their health | 200 |
| GET | `/api/status` | service status | 200 |

Headline decisions:

- **`PUT /membership`, not `POST /approval`.** Membership is a state, not an event; PUT is
  idempotent and says so. `approved` stays **mandatory with no default** — `634de6a` fixed a
  bodyless POST that admitted a device to the fleet by defaulting the missing field to the
  privileged value, and the REST rewrite must not reintroduce it.
- **`PUT /schedule` replaces the whole schedule.** A schedule is small, and partial edits of
  an ordered list are where lost updates live. One atomic write, validated as a whole:
  slot→view references resolved, `default` present, region capacities respected.
- **The device-facing routes stay `GET` with caps in the query.** Capabilities ride on every
  call rather than a separate handshake, so a server restart cannot lose them — that is a
  deliberate existing decision and REST does not change it. `/frame` keeps reading geometry
  from **this** request and never from storage, because the body's length is the one thing a
  device cannot check.
- **`/api/display/{name}/data` retires.** It was keyed by a `config.yaml` id rather than a
  hardware id, and under the provider model a feed is a job: `/api/jobs` covers debugging.
- **`/api/config/*` retires** into `PATCH /api/devices/{hw}` and
  `PUT /api/providers/{n}/settings` (item 8).
- **`POST /home/device` retires.** It is the dashboard form's endpoint and belongs under
  `/device/<hw>` with the rest of the HTML.

### 5.3 The HTML dashboard is not part of this

`/`, `/device/<hw>` and `/settings` stay **GET plus form POST plus redirect**. Browsers
cannot PUT or DELETE from a form; making them do so requires JavaScript, and the dashboard
must render and function with scripting off — it is how you debug the network, and the one
script it has (swapping option groups) already degrades to "the saved component's settings
are the ones on the page".

**Do not RESTify the dashboard.** Stated here so nobody does it in the name of consistency.
The HTML pages and the JSON API are two interfaces to the same handlers, with different
constraints, and that is correct.

### 5.4 Migration — one flash, two changes

Two things need a reflash: the `draw_list` capability (item 1) and the scene path. **They
ship as one firmware release**, so the fleet is flashed once.

The server serves both surfaces during the window:

- Legacy paths are registered as **aliases on the same handler**, not copies — the codebase
  already does this (`@app.get("/")` + `@app.get("/home")`). One handler, one behaviour, no
  drift.
- Legacy responses carry `Deprecation: true` and `Sunset: <date>` (RFC 8594).
- A legacy hit is logged **once per device per day**, not per poll — 17k requests/device/day
  onto a microSD journal is the wear this project already refused a systemd timer to avoid.
- Each device record gains `api: "legacy" | "current"`, derived from the path it last used,
  and the fleet page shows **who still needs flashing**. The evidence is already arriving:
  every device sends `fw=` on every poll and the registry stores it.

**Removal criteria — all three, not any:**

1. Every `approved` record reports `fw >= <release>`.
2. No legacy path hit for 30 consecutive days.
3. The pending tray holds nothing on old firmware.

**One legacy route outlives the sunset: `GET /api/device/{hw}/scene`.** After removal it
does not 404 — a 404 to a device is a blank panel and no explanation. It returns a **status
scene, in Spanish, saying the firmware is out of date**, on the device's own glass. This is
the same reasoning that gives a pending device an explanation instead of content: a board
that spent a year in a drawer must be able to tell you why it is not working, from the
screen you are looking at.

---

## 6. Prioritised backlog

Ordered so each item lands something on glass or removes a blocker the next would hit.
Sizes: **S** one focused session, **M** a few, **L** a week or more.

**What moved since the first draft, and why**

| Change | Reason |
|---|---|
| Item 1 absorbs the API path change | Both need a reflash; one release, one flash |
| **NEW** item 2 — the REST surface | The owner asked for it, and item 1's firmware must have a path to point at |
| **NEW** item 3 — the record becomes a layout | Q6 settled composed dashboards; shaping the record now is what avoids a migration later, and schedules cannot be built on `scene` + `options` |
| Old item 4 "settings that save" → **item 7, shrunk** | `POST /settings`, `set_feed()` and `@feeds` shipped in `634de6a`. Only the secret store remains |
| Old item 6 "rotation" → **deleted** | Replaced by item 6, schedules |
| Loop-under-test moved up to 5 | Schedules change what a device is told to wake for; do not build that on an untested loop. Landed as `88a8565` while this was being written; item 5 is now the remainder |
| Old item 12 "scene builder" **split** | The record shape (item 3) lands now; the e-paper compositor (item 13) still waits on hardware |

---

### 1. One firmware release: `draw_list` and the canonical path — **S/M** — DO THIS FIRST

**Goal.** Adding a component to the Pi makes it available on every round screen immediately,
with no firmware release — and the fleet is on the canonical API path.

`config::kDeclaredComponents` is `"radar,clock"` and the server drops any component a device
did not declare, while `componentKindFromName` already returns `kDrawList` for any unknown
name that ships an instruction list. The declaration is the only thing between us and
`weather`. Five backlog items are blocked behind a one-word edit.

**Acceptance criteria**
- Firmware declares `radar,draw_list` and polls `/api/devices/{hw}/scene`; host tests assert
  both strings in the URL the client builds.
- `GET /api/devices/{hw}/scene?…&components=radar,draw_list` for a component named `weather`
  returns it in `components` with **no** `unsupported` entry.
- A device declaring only `radar` still gets `unsupported: ["clock"]` — the widening is
  opt-in, not a hole.
- A device on the old firmware (`radar,clock`, legacy path) keeps working unchanged; assert
  both firmwares against both paths — four combinations, four tests.
- `_scene_options` offers every instruction-list component to a `draw_list` device and still
  disables `planes` where `radar` was not declared, with the reason shown.
- Every reason string in the component picker is **Spanish**; a test asserts the picker
  contains no English.
- `componentKindFromName("weather")` returns `kDrawList` with a draw list present and
  `kUnknown` without one; an empty list shows a status screen, not a hole.
- The firmware version constant is bumped and reaches the registry, because item 2's removal
  criteria depend on it.

**Dependencies.** Item 2 must have defined the path, but can land in the same session.

**Why first.** It is the smallest item on the list, it unblocks five others, and it is the
only one requiring physically reflashing hardware — the longest lead time on the board. It
should start while the server work that needs it is still being written.

---

### 2. The REST surface and the deprecation window — **M**

**Goal.** One noun, correct verbs, and old devices keep working until they are flashed.

**Acceptance criteria**
- Every path in §5.2 exists and returns the stated codes; one test per row.
- Legacy paths are **aliases on the same handler** — a test asserts the legacy and canonical
  responses are byte-identical apart from the deprecation headers.
- `Deprecation: true` and `Sunset:` on every legacy response.
- A legacy hit is logged at most once per device per day; assert with an injected clock that
  1,000 polls produce one log line.
- `/api/devices/{hw}` carries `api: "legacy"|"current"` and the fleet page lists devices
  needing a flash.
- `PUT /membership` with a body missing `approved` returns **400**, not an approval.
- `/api/status` continues to expose named keys only, never the whole feed dict.
- The HTML pages are untouched — a test asserts `/`, `/device/<hw>` and `/settings` still
  work with forms only, and that no dashboard page requires scripting to save.

**Dependencies.** None.

---

### 3. The assignment record becomes a layout — **S/M**

**Goal.** No behaviour change today; no migration later.

`scene` + `options` becomes one view with one placement in region `full`. Everything on
screen looks and behaves identically. This is the cheapest moment this will ever happen.

**Acceptance criteria**
- The record carries `schedule.views` and `schedule.default`; a record written before this
  reads as a one-placement default view, with no migration script (the
  treat-old-records-as-valid pattern `APPROVAL_FIELD` already uses).
- `registry.OPTIONS_MEMORY_FIELD` is keyed by `(placement_id, component)`; a screen with two
  clocks in two regions keeps two sets of options.
- Placement ids are stable across edits: renaming a view or reordering placements does not
  change an id, so option memory survives.
- A round device's served scene is **byte-identical** before and after — pinned by the
  existing wire fixture.
- `Scene.html` becomes a fragment plus `css`; `_style.page()` composes. The 800×480 clock
  frame is byte-identical before and after, pinned by a golden test.
- Region capacity is enforced on write: a seventh `markets` placement is a 400 with a
  Spanish reason.
- A view referenced by a slot cannot be deleted; an unreferenced one is kept.

**Dependencies.** None. Blocks items 6 and 13.

---

### 4. Providers and jobs — **L**

**Goal.** One daemon fetches everything every screen needs, deduped, each on its own
cadence, with no per-component wiring.

**Acceptance criteria**
- `sources.registry()` maps provider name → module; `sources.jobs(schedules)` returns deduped
  `Job(provider, params, key, interval_s, cache_path)`.
- Jobs are collected from **every** placement in **every** view of **every** approved screen's
  schedule, not only the active one (§2.4).
- Two placements with identical params produce **one** job; differing params produce two.
- `key` is stable across restarts and dict ordering:
  `job({"lat":1,"lon":2}).key == job({"lon":2,"lat":1}).key`.
- Cache path is `cache/feed/<provider>/<key>.json`; the envelope is byte-compatible with
  today's `read_cache`.
- A provider whose `fetch` raises records `ok: false`, keeps the previous `data`, and the
  runner continues — assert a 3-job cycle where the middle one raises completes all three.
- Two jobs on one provider with `MIN_SPACING_S = 1.0` are issued ≥1.0s apart, on an injected
  clock with no real sleeping.
- Cadence precedence: operator > provider default > `MIN_INTERVAL_S`, with a below-floor
  operator value **clamped**; one test per rung.
- Schedules are re-read every cycle: mutating `devices.json` between injected cycles starts
  the new job without a restart.
- Startup prunes `cache/feed/<provider>/*.json` with no live job and mtime older than 7 days.
- Every provider module imports without `requests`; the C7 import-graph guard on `serve.py`
  still passes.
- `adsb` is registered as a provider and the radar keeps working unchanged.
- `@feeds` generalises from the hardcoded `"adsb"` to any registered provider, and existing
  stored `@feeds.adsb` settings keep applying.

**Dependencies.** None.

---

### 5. The render loop under test — **M** — MOSTLY DONE (`88a8565`)

**Goal.** The three bugs that shipped in a row cannot ship again, before schedules give the
loop a new reason to wake.

`4eb4624`, `bd55df2` and `84b7462` all reached hardware because host tests called
`renderScene()` directly while the device reaches it through `loop()`'s render policy, which
no test exercised.

**Shipped in `88a8565`** — seven tests that drive `loop()` itself and assert what reached the
glass, with no production restructure needed (better than this item originally asked for: the
extraction turned out to be unnecessary). Frames are counted by the screen clear rather than
by `pushSprite`, because only the radar composites through a sprite and an instruction list
draws straight to the panel — the first version of the metric read zero for every clock and
proved nothing. Two mutations in opposite directions each fail two tests. 266 firmware tests.

**Remaining before item 6**
- A composited-but-**not blitted** frame is retried and not recorded as painted. This is the
  half of `4eb4624`'s family the seven tests do not cover, and it is the one a schedule
  boundary can hit: a frame requested at a transition while the aircraft list is locked.
- The portal-settings-changed branch (`wifiConsumeSettingsChanged` ⇒ `requestRedraw`).
- A schedule boundary requests exactly one frame, not one per loop pass — assert against the
  `kRenderIntervalMs` rate limit once item 6 exists.
- `pio test -e native` passes; the count reported in the commit, per project custom.

**Dependencies.** None. The remainder is small and belongs with item 6.

---

### 6. Schedules — **L**

**Goal.** A screen shows the radar in the afternoon, a clock overnight, and weather on
weekend mornings, without anyone touching it.

**Acceptance criteria**
- The record accepts §4.3's shape; `PUT /api/devices/{hw}/schedule` validates it whole:
  `default` present, every slot's `view` resolvable, `days` ⊆ 1..7, `HH:MM` well-formed,
  region capacities respected. Invalid ⇒ 400 with a Spanish reason and **nothing written**.
- Resolution: last matching slot wins; no match ⇒ default. One test per rule, plus a table
  test over a whole week for the owner's three examples.
- A wrapping window (`23:00`→`09:00`) is active at 02:00 on the following day and inactive at
  22:00 on the same day.
- Timezone: slots evaluate in `schedule.tz`, defaulting to `config.yaml location.timezone`.
- **DST:** a slot boundary inside the skipped spring-forward hour never fires, and the
  correct view is served at the first instant after it; a slot spanning the repeated
  fall-back hour is active in both passes. Assert with a frozen clock at Europe/Madrid's
  actual transition instants.
- **Cadence:** `X-Poll-Seconds` is `min(component next change, seconds to boundary)` after
  the existing precedence; `poll_max_s` is unchanged by the schedule. Assert both directly.
- **The floor holds:** a boundary 3s away on a `depth: 1` device still advertises 30s.
  Assert it.
- A boundary further out than `POLL_MAX_S` clamps to 600 and re-evaluates; assert a boundary
  six hours away advertises 600, not 21,600.
- The week strip renders 7×24 with the winning view per cell and `ahora` marked; the preview
  accepts `at` and resolves against it.
- Removing the last slot leaves the default showing — never a blank panel.
- A schedule change takes effect on the device within one advertised poll; assert
  `_last_cold` is invalidated as a scene change already does.

**Dependencies.** Items 3 and 5.

---

### 7. The secret store — **S/M**

**Goal.** Type an API key into the dashboard and never see it again.

The feed-settings half of the old item 4 shipped in `634de6a`; this is what remains.

**Acceptance criteria**
- `cache/secrets.json` created **0600**, written atomically with file **and directory**
  fsync.
- `secrets.state()` and `secrets.get()` split per §3.6; `overrides.apply()` does not touch
  secrets and a test asserts no secret key appears in the merged `cfg`.
- **Sentinel test:** a secret set to a known nonsense string appears in no response body, on
  any route, asserted by iterating the app's URL map so later routes are covered
  automatically.
- `GET` on a secret returns `{name, set, updated_at}` with **no `value` key present at all**.
- Blank submit leaves the stored value untouched; `borrar` clears it; the pill reads
  `configurada` / `sin configurar` with `actualizada <cuándo>`.
- `api_key` is still absent from `FEED_SETTABLE_KEYS` — secrets never travel `overrides.json`,
  which `/api/config` dumps.
- Removing a secret makes its jobs fail with `ok: false` and a Spanish reason on the settings
  page; no traceback, no blank panel.

**Dependencies.** Item 4.

---

### 8. Weather — **M**

**Goal.** A screen shows the temperature and today's conditions for a city set on that
screen's page.

Open-Meteo needs **no API key**, which makes it the honest first proof of the provider
contract — the whole chain works before secrets exist. (It is placed after the secret store
only because item 7 is short; it does not depend on it.)

**Acceptance criteria**
- Provider `openmeteo` calls SPEC §7.1's documented URL; `fetch` is unit-tested against
  `tests/fixtures/openmeteo_sample.json` with no network.
- `DEFAULT_INTERVAL_S = 600`, `MIN_INTERVAL_S = 600`.
- Options: `lat`, `lon` (blank ⇒ `config.yaml location:`), `units` (`choice`), 
  `show_forecast` (`bool`).
- 240×240: `xl` temperature at `center`, `sm` condition at `below`, `xs` máx/mín at
  `rim_bottom`. `main_right` on the e-paper: current conditions, hourly strip, 5-day list per
  SPEC §9.
- WMO code → **Spanish** condition text per SPEC §7.1's table. No icons (Q2).
- `poll_s` aims at the next provider refresh; `poll_max_s == 600`.
- `ok: false` with a previous envelope ⇒ last good temperature plus a staleness mark; no
  envelope ⇒ "sin datos". Neither raises.
- Preview returns 200 SVG with the temperature string present.
- Offered on both a `draw_list` device and a pixel-push surface.

**Dependencies.** Items 1 and 4.

---

### 9. Stocks and currency — **M**

**Goal.** A screen shows a ticker large with its change below; the e-paper's markets band
shows up to five.

**Acceptance criteria**
- Component `quotes`; options `symbols` (comma-separated), `provider` (`choice`:
  twelvedata | stooq | coingecko), `currency` (`choice`).
- One job per (provider, symbol set); two screens with the same symbols dedupe to one, a
  different set is a second. Batch into one upstream call where supported.
- **Five is the hard ceiling on the pixel path** (SPEC §9: 764px band, ~117px cells, six
  truncates). The sixth is refused with a Spanish notice, never a truncated row.
- Round 240×240 shows **one** symbol per placement (see Q10).
- Outside market hours an equity renders `cerrado`, not a 0,0% arrow (SPEC §7.4); crypto
  exempt.
- FX arrows compare against the **previous cached value persisted in the envelope** (SPEC
  §7.3), never an official rate.
- A configuration exceeding the documented free tier is refused **at startup with the
  arithmetic in the message**.
- Missing key ⇒ job `ok: false` with a Spanish reason; component renders "sin clave"; no
  traceback.
- Tone `bad` on 1-bit glass resolves to an inverted block, not a missing glyph.

**Dependencies.** Items 4, 7.

---

### 10. Range and radar parameters become placement configuration — **M**

**Goal.** Change what the radar shows from the dashboard, not from a button on the back.

**Acceptance criteria**
- `planes` gains `radius_km` (`choice` over the existing presets), `units` (km | mi),
  `show_ground`, `max_aircraft`.
- The served `radar` component's `radius_km` follows the placement; the device uses the
  served value and `rangeCurrent()` follows it.
- Changing it changes the ring labels within one poll.
- NVS keeps only what a device needs with no server (WiFi, server URL). The range key is
  removed; an existing stored value is ignored, not migrated into a conflict.
- The ADS-B job's params come from the placement; the horizon invariant
  (`connect + read + interval < 12s`) is re-asserted **per job**.
- `overrides.py`'s device-scoped `SETTABLE_KEYS`, `config.yaml devices:` and `/api/config`
  are retired for the radar — two places holding the same fact is the bug that produced the
  empty-sky mismatch.
- The BOOT button's short press does whatever Q11 settles; whatever that is, it is **one
  meaning fleet-wide** and documented on the device page.

**Accepted trade.** The design spec chose polar coordinates specifically so changing range
stayed "instant and local" rather than a network round-trip. Moving it to the dashboard makes
it a round-trip, bounded by one poll (~5s on the radar's cadence). That is the right trade at
the owner's instruction, and the projection code that made polar coordinates worth keeping is
untouched either way.

**Dependencies.** Items 3, 4.

---

### 11. Calendar — **M**

**Goal.** Today's events on a screen, from a secret ICS address.

**Acceptance criteria**
- The ICS URL is a **secret**, never an option. The option is a `choice` over the *names* of
  configured calendar secrets.
- `recurring-ical-events` expands RRULEs (plain `icalendar` does not — CLAUDE.md §6).
- Events for today, sorted by start, capped at `max_events`, with a `+N más` tertiary row on
  overflow. An empty day **collapses**.
- 240×240: next event only — `sm` time at `above`, `md` title truncated at `center`, `xs`
  countdown at `rim_bottom`. `main_left` on the e-paper: the agenda block per SPEC §9.
- The ICS URL appears in no response body (§3.6's sentinel covers it).
- A malformed calendar yields an empty list and `ok: false`, never an exception.

**Dependencies.** Items 4, 7.

---

### 12. Sport events — **M**

**Goal.** The next few fixtures, text only.

**Acceptance criteria**
- Providers `footballdata` and `thesportsdb` per SPEC §7.5; merged, sorted by kickoff, capped
  at `max_fixtures`.
- Options: `competitions`, `max_fixtures` (`int`).
- Text only — no crests, no logos, no remote images.
- Times in the placement's timezone, defaulting to `config.yaml location:`.
- One provider failing still renders the other's fixtures with `ok: false` noted.

**Dependencies.** Items 4, 7.

---

### 13. The e-paper compositor — **L**

**Goal.** The 800×480 panel shows the composed dashboard SPEC §9 designs.

Item 3 already shaped the record; this builds the renderer. It waits on hardware: the panel
is **not purchased** (CLAUDE.md §2) and PLAN B2's legibility gate may change the type scale,
which changes what a region is worth.

**Acceptance criteria**
- The surface region table of §4.2 exists with SPEC §9's measured rects.
- Fragments compose into one document; per-placement CSS is prefixed and does not leak —
  two placements of one component with different options render differently.
- Placements in a stacking region flow and **collapse when empty** (SPEC §11).
- The partial-refresh window is a **property of the view**, not a constant (PLAN S2); a view
  with no clock in the top-left declares none and refreshes fully.
- Render stays inside the two Chromium slots: a composed frame is one render, not one per
  placement. Assert the invocation count.
- The compiled CSS contains only `#000`/`#fff`; no `font-size` below 10px. Both already have
  audits; run them over the composed output.
- Polarity vector still holds: black at (0,0) and (7,1) in an 8×2 serialises to `0x80 0x01`.

**Dependencies.** Items 3, 5; panel hardware; PLAN B2.

---

### 14. Claude usage — **S/M**

Contingent on Q5's earlier answer, which is still open.

- Provider reads a usage/cost figure with an admin key from the secret store,
  `DEFAULT_INTERVAL_S = 3600`.
- 240×240: `xl` figure at `center`, `sm` period at `below`, `xs` "actualizado hh:mm" at
  `rim_bottom`; tone `bad` past a configurable threshold.
- The key never reaches a response body.
- A 401/403 renders "sin acceso" and marks the job failed, rather than a zero that reads as
  "nothing spent".

**Dependencies.** Items 4, 7, and an answer to Q5.

---

### Not scheduled

**Deliveries (SPEC §7.6).** Gmail parsing of Amazon shipment mail. Stays last per SPEC §14
and PLAN Phase D: most fragile, least essential, and the only provider whose upstream changes
shape without warning a few times a year. It is a component like any other under §2 and needs
no platform work.

---

## 7. Open questions

### Still open from the first round

**Q2 — Does the draw vocabulary grow before weather and stocks ship?**
Text-only, or add `icon` / `spark` / `arrow` from the design spec's §5.2 table?
→ **Recommend text-only for v1.** Every addition costs two executor implementations, a golden
fixture, and for icons a font on both sides. The design spec already predicts one revision
after the first non-radar scene renders on hardware. A `▲`/`▼` from the existing face covers
the delta case free.

**Q5 — What is the source for "Claude usage"?**
The plausible path is the Anthropic Admin API's usage/cost report, which means an admin key on
the Pi and an org-wide rather than personal figure.
→ **Recommend that path, with the endpoint and key type verified before item 14 is
scheduled** — I have not checked it against current docs. If an admin key on a LAN box is not
acceptable, drop the component rather than fake it.

### New, created by these decisions

**Q9 — Does a schedule apply to a whole screen, or per region?**
"Weather in the right column in the morning, the calendar there in the evening" is a
different feature from "the whole panel changes at 21:00". Per-region schedules multiply the
config surface by the region count and make "what is showing" a per-region answer.
→ **Recommend whole-screen views.** All three of the owner's examples are whole-screen, and
a per-region schedule is expressible today by making two views that differ in one placement —
more clicks, but no new concept and no new failure mode. Revisit only if the e-paper is in
daily use and two views differing by one region becomes a chore.

**Q10 — Rotation was the answer for multiple tickers. What replaces it?**
The owner's original example was "one ticker on the round screen, and if more are added it
rotates every x seconds". A schedule is coarse — minutes and hours — so it does not serve
that: nobody wants AAPL from 09:00 and NVDA from 09:05. With rotation withdrawn, a round
screen shows **one** symbol. The options are (a) one symbol per screen, more symbols means
more screens; (b) a component-internal cycle that is not the platform's business — the
component asks for a short `poll_s` and returns a different symbol each time; (c) revive a
narrow rotation just for within-component pages.
→ **Recommend (b).** It needs no platform feature at all: a component that wants to cycle
already has the mechanism, because `poll_s` lets it ask to be woken whenever it likes and it
decides what to draw when it is. `poll_floor` still protects the e-paper, and the schedule
stays the only *platform* notion of time. If that feels like rotation smuggled back in — it
is, but confined to one component's own code rather than a field in every schema.

**Q11 — What does the BOOT short press mean now?**
"Next page now" is meaningless without pages, and range is moving to the dashboard (item 10).
Long press stays the WiFi portal. Candidates: nothing, "poll now", or keep range cycling.
→ **Recommend "poll now" — fetch the scene immediately and redraw.** It is useful on every
component, it is one meaning fleet-wide, and it is exactly what you press after changing
something on the dashboard rather than waiting out a 30s e-paper cadence. It also gives the
button a purpose on a screen whose component has no notion of pages or range, which "keep
range cycling" does not.

---

## 8. Non-goals

- **Not a general dashboard builder.** Regions are a fixed, measured table per surface
  (§4.2), not a canvas. No layout DSL, no drag-and-drop, no user-authored components.
- **Not a second scheduling language.** §4.3's exclusion list is a commitment: no dates, no
  cron, no holidays, no sunrise-relative slots, no exceptions.
- **Not authenticated, and not exposed.** Plain HTTP on the LAN is the owner's decision.
  Nothing here is a step toward a login, a token, a reverse proxy or a public URL. The secret
  store is write-only, which is a different property from access control.
- **Not a cloud service.** No account, no telemetry leaving the house, no remote config.
- **Not a browser kiosk.** Devices are HTTP clients that draw; Chromium is a rasteriser
  invoked per frame, not a runtime, and there is no per-device JavaScript.
- **The dashboard is not a single-page app.** Forms and redirects. It must work with
  scripting off, because it is how you debug the network (§5.3).
- **Not a historian.** No time-series database, no retention, no charts over months. The
  cache holds the latest envelope per job and a 7-day prune. The SD card is 15 GB and
  unmitigated for wear.
- **Not animated.** The e-paper is 1-bit with a ~3s refresh and two render slots for the
  whole fleet.
- **Not a home-automation controller.** Screens display; they do not actuate.
- **Not a fleet product.** 64 devices, one household, one operator, no roles, no audit log,
  no multi-tenancy.
- **No build step and no CDN.** No bundler, no npm, no webfont fetched at boot. The dashboard
  must render when the network is the thing being debugged.
