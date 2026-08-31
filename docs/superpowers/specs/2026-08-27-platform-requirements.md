# HomeScreen Platform — Requirements

**Status:** Revised 2026-08-27 after the owner answered the first round of open questions.
**Extended 2026-08-31** — see the addendum at §9: the 7.5" panel is physically in hand, sport
grows beyond football, coverage is audited, and "crisp and attractive" is made checkable.
Where the addendum contradicts §1–§8 it wins; it contradicts nothing today.
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
- A **schedule applies to a whole screen**, not per region.
- **Adapting to the surface is the component's job**, not the platform's: the same
  configured data is a stacked list in a large region and a cycling single value on a small
  one.
- **Buttons become a declared capability** the dashboard binds. Until that ships the BOOT
  short press does nothing. The long press opens the WiFi portal and is never bindable.

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

**And adapting to that geometry is the component's own obligation.** Declaring that you can
draw on a surface is a promise to draw *well* on it, not merely to fit. The same configured
data may need a different presentation on different glass, and choosing between them is the
component's job — the platform has no opinion and offers no mechanism:

> Five tickers are a **stacked list** in the 764×62 markets band and a **single ticker that
> cycles** on a 240×240 round panel. One `symbols` option, one component, two presentations.

This is the general rule, not a note about stocks. A calendar shows five rows in a column and
the next event alone on a watch face. A weather component shows a five-day strip where there
is room and a temperature where there is not. `ctx.caps` is what the decision is made from,
and `supports()` is for the case where no honest presentation exists at all.

**A component that chooses to cycle asks to be woken at its own rate**, which needs no new
machinery: `poll_s` already lets it name its next change (§2.6), and `poll_floor` already
stops that from turning the e-paper's two render slots into a busy loop. Cycling is therefore
a component's private business, never a field in a schema and never a platform concept.

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
- A component that **cycles its own presentation** (§2.3) names the cycle here: it asks for
  the next step and returns the next value when woken. That is the whole mechanism; there is
  no rotation feature underneath it.
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
- **different schedules per region.** Settled: a schedule applies to a whole screen. A
  per-region change is expressible today as two views differing by one placement — more
  clicks, no new concept, no new failure mode, and "what is showing" keeps one answer.

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

### 4.7 Inputs — a device declares its buttons, the dashboard binds them

Settled: the BOOT short press gets **no fixed meaning**. Instead a device declares its inputs
the way it already declares its screen, and an operator binds an action to each one.

**Declaration rides the existing query string.** Capabilities already travel on every poll —
`w`, `h`, `depth`, `max_items`, `components` — so inputs are one more list and need no
handshake, no new endpoint, and no schema change: `registry.CAP_INTS` gains nothing and
`_CAP_LISTS` gains `buttons`.

```
GET /api/devices/{hw}/scene?w=240&h=240&depth=16&max_items=40
    &components=radar,draw_list&buttons=boot.short&fw=…
```

- One entry per **bindable gesture**, formatted `<input>.<gesture>` where gesture is `short`
  or `long`. A board with two buttons declares `buttons=a.short,a.long,b.short`.
- `<input>.<gesture>` and not `<input>:short+long`, because the list separator is already a
  comma and `clean_caps` already truncates entries at 32 chars and the list at
  `MAX_CAP_LIST`. This shape needs no parsing changes at all.
- Validated as `^[a-z0-9_]{1,16}\.(short|long)$`; anything else is dropped rather than
  guessed at, exactly as `clean_caps` drops a nonsense `depth`.
- **`boot.long` is never declared.** A device does not offer for binding a gesture it does
  not own, and the portal owns that one. Its absence from the list is the enforcement.

**The action vocabulary is deliberately tiny.**

| Action | Does | Needs |
|---|---|---|
| `nada` | nothing (default for every input) | — |
| `refrescar` | drop the cached ETag and poll immediately | nothing new |
| `identificar` | show this screen's hardware id large for ~5s, then resume | nothing new |

Every v1 action is one the device performs **alone**. Devices only ever `GET` today, and
keeping it that way is what makes this extension small. Deliberately excluded: anything that
changes server state (reassigning, advancing a schedule, editing an option), anything that
actuates outside the screen, chords, double-taps, and press-and-hold ramps. See Q12.

`identificar` earns its place because three anonymous boards on a shelf is a real problem and
the status scene already knows how to draw a hardware id.

**Bindings are stored per DEVICE, not per view.** A button is a physical object a person
reaches for with a fixed expectation, and one that means different things at different hours
is worse than one that means one thing. Both v1 actions are device-level concerns anyway —
neither belongs to whatever component happens to be showing. The binding sits on the device
record next to `poll_seconds`, which is the other per-device operator setting. If a
component-specific action ever exists, that is when per-view bindings earn their complexity —
the same argument that settled Q9.

**The device learns its bindings from the scene payload it already polls.** No new endpoint,
no second request, and the existing ETag covers it, so a binding change reaches the glass on
the next poll and a 304 means nothing changed:

```json
{"hw": "…", "scene": "planes", "assigned": true, "layout": "fill",
 "components": [ … ],
 "inputs": {"boot.short": "refrescar"}}
```

An absent key, an unknown action, or an input the device never declared is a **no-op** —
never an error path on the device's only route. The server filters `inputs` down to what the
device declared, exactly as it already filters `components` (design spec §5.5), and reports
the drop in the fleet view rather than silently.

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

**Much of this shipped in `462be45`.** The table marks what exists so nobody rebuilds it.

| Method | Path | Returns | Codes | State |
|---|---|---|---|---|
| GET | `/api/devices` | `{devices:[…]}` | 200 | **exists** |
| GET | `/api/devices/{hw}` | one device | 200, 404 | **exists** |
| PATCH | `/api/devices/{hw}` | name, scene, `poll_seconds`, options | 200, 400, 404, 503 | **exists** |
| DELETE | `/api/devices/{hw}` | — | 200, 404, 503 | **exists** |
| GET/PUT | `/api/devices/{hw}/membership` | `{"approved": bool}` | 200, 400, 404 | **exists** |
| GET | `/api/devices/{hw}/scene` | **device-facing**; caps in the query | 200, 304, 400, 404 | **exists** |
| GET | `/api/devices/{hw}/frame` | **device-facing**; `?w=&h=` required | 200, 304, 400, 404, 503 | **exists** |
| GET | `/api/devices/{hw}/preview.svg` | `?view=` | 200, 404 | **exists** |
| GET | `/api/status` | service status | 200 | **exists** |
| GET | `/api/devices` `?state=` | filter `pending\|approved` | 200 | designed |
| GET/PUT | `/api/devices/{hw}/schedule` | the whole schedule (§4.3) | 200, 400, 404 | designed |
| GET/PUT | `/api/devices/{hw}/inputs` | button bindings (§4.7) | 200, 400, 404 | designed |
| GET | `/api/devices/{hw}/preview.svg` `?region=&at=` | region and instant | 200, 404 | designed |
| GET | `/api/components`, `/api/components/{name}` | catalog: options schema, providers, surfaces | 200, 404 | designed |
| GET | `/api/providers`, `/api/providers/{name}` | catalog: params schema, secret **names** | 200, 404 | designed |
| GET/PUT | `/api/providers/{name}/settings` | endpoint, interval | 200, 400, 404 | designed |
| GET | `/api/providers/{name}/secrets/{s}` | `{name, set, updated_at}` — **no value** | 200, 404 | designed |
| PUT/DELETE | `/api/providers/{name}/secrets/{s}` | `{"value": "…"}` / — | 204, 400, 404 | designed |
| GET | `/api/jobs` | live fetch jobs and their health | 200 | designed |

Still to retire: `/api/config/*` (into `PATCH /api/devices/{hw}` and
`PUT /api/providers/{n}/settings`), `/api/display/{name}/data` and `/health` (into
`/api/jobs`), and `POST /home/device`.

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

`/`, `/device/<hw>` and `/settings` stay **GET plus form POST plus redirect** — because a
form is the least machinery that does the job. A browser posts a form and follows a redirect
with no code at all; reaching the same page through `fetch` and PUT means writing, shipping
and debugging a client to accomplish exactly what the browser already does. That is the whole
argument, and it is enough.

**Do not RESTify the dashboard.** Stated here so nobody does it in the name of consistency.
The HTML pages and the JSON API are two interfaces onto the same handlers with different
constraints, and that is correct.

**JavaScript is fine.** An earlier draft justified this section partly on the dashboard
working with scripting off. That constraint was never the owner's — it was invented in an
earlier session and asserted in a test as though it were given, and `462be45` retired both
the test and the claim. What survives is smaller and true: **the dashboard ships its own
assets.** No CDN, no bundler, no webfont fetched at boot, because it is the page you open
when the network is the thing misbehaving. Scripts are welcome; fetching them from someone
else's server is not.

The **schedule editor is expected to use it.** A 7×24 week grid you paint by dragging is a far
better input than four paired time fields and a weekday checkbox row, and §4.6 already asks
for that grid as the way overlap precedence is seen rather than reasoned about. "Keep it
simple" is the constraint — inline, dependency-free, in the file it belongs to, like the
existing option-group swap.

### 5.4 Migration — one flash, three changes

Three changes need a reflash: the `draw_list` capability, the canonical scene path, and
unbinding the BOOT short press (§4.7). **They ship as one firmware release** — not because
flashing is expensive (it is one command over USB) but because there is no reason to cut
three.

The server serves both surfaces during the window. **The first half shipped in `462be45`:**

- ✅ Legacy paths are registered as **aliases on the same handler**, not copies — the codebase
  already did this (`@app.get("/")` + `@app.get("/home")`). One handler, one behaviour, no
  drift. `LEGACY_RULES` names them and an `after_request` hook marks them, so the rule cannot
  fall out of step with the routes.
- ✅ Legacy responses carry `Deprecation: true`, `Sunset: Wed, 31 Dec 2026 23:59:59 GMT`
  (RFC 8594) and `Link: </api/devices>; rel="successor-version"`.
- ⬜ A legacy hit is logged **once per device per day**, not per poll — 17k requests/device/day
  onto a microSD journal is the wear this project already refused a systemd timer to avoid.
- ⬜ Each device record gains `api: "legacy" | "current"`, derived from the path it last used,
  and the fleet page shows **who still needs flashing**. Half the evidence is already
  arriving: every device sends `fw=` on every poll and the registry stores it.

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
| Item 1 absorbs the API path change and the inert BOOT press | Three edits to one binary; batching them is bookkeeping, not scheduling |
| Item 2 — the REST surface | Largely **shipped** in `462be45`; item 2 is now the remainder |
| **NEW** item 13 — the input extension | Q11: buttons become a declared capability, bound from the dashboard |
| **NEW** item 3 — the record becomes a layout | Q6 settled composed dashboards; shaping the record now is what avoids a migration later, and schedules cannot be built on `scene` + `options` |
| Old item 4 "settings that save" → **item 7, shrunk** | `POST /settings`, `set_feed()` and `@feeds` shipped in `634de6a`. Only the secret store remains |
| Old item 6 "rotation" → **deleted** | Replaced by item 6, schedules |
| Loop-under-test moved up to 5 | Schedules change what a device is told to wake for; do not build that on an untested loop. Landed as `88a8565`; item 5 is now the remainder |
| Item 6's resolver landed as `ec2be47` | Item 6 is now the wiring, the storage and the editor |
| Old item 12 "scene builder" **split** | The record shape (item 3) lands now; the e-paper compositor (item 13) still waits on hardware |

---

### 1. One firmware release: `draw_list`, the canonical path, an inert button — **S/M** — DO THIS FIRST

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
- **The BOOT short press becomes a no-op** — `onRangeTap` unbound, range served from the
  placement (item 10), bindings arriving later (item 13). Long press still opens the WiFi
  portal, unchanged and untouched.
- The firmware version constant is bumped and reaches the registry, because §5.4's removal
  criteria depend on it.

**Dependencies.** The canonical path exists already (`462be45`).

**Why first — on merit, not on logistics.** An earlier draft argued this from reflashing lead
time. That argument is void: the board is on a USB cable and flashing is one command. The
real case is simpler and stronger.

1. **It unblocks five items.** Weather, quotes, calendar, sport and Claude usage are each one
   Python file *and one firmware release* until this lands. That is the difference between
   "add a component" being an afternoon and being a release.
2. **It is the smallest item here.** One constant, one filter rule, and the tests that pin
   both.
3. **Three changes, one binary.** The capability, the canonical path and the inert button all
   touch the same firmware. Shipping them together is bookkeeping — it is not a schedule
   argument and does not need to be one.

The honest counter-argument is that nothing visible changes when it lands. True, and it is
still right to go first: every subsequent item is smaller because of it.

---

### 2. The REST surface — the remainder — **S/M** — MOSTLY DONE (`462be45`)

**Goal.** One noun, correct verbs, and old devices keep working until they are flashed.

**Shipped in `462be45`** — the canonical `/api/devices/{hw}` noun with `scene`, `frame`,
`membership` and `preview.svg` sub-resources; every old path kept alive as an alias on the
same handler; `Deprecation`, `Sunset` and `Link: rel="successor-version"` on all four legacy
rules; `approved` still mandatory with no default. 921 tests.

**Remaining**
- A legacy hit is logged at most **once per device per day**; assert with an injected clock
  that 1,000 polls produce one log line.
- `/api/devices/{hw}` carries `api: "legacy"|"current"` from the path last used, and the
  fleet page lists which screens still need flashing.
- `?state=pending|approved` on the collection.
- Retire `/api/config/*`, `/api/display/*` and `POST /home/device` — the first two fold into
  items 4 and 10, so they retire with those rather than on their own.
- The HTML pages stay forms and redirects (§5.3); a test asserts `/`, `/device/<hw>` and
  `/settings` still save through a form POST and a redirect.

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

### 6. Schedules — **L** — RESOLUTION DONE (`ec2be47`)

**Goal.** A screen shows the radar in the afternoon, a clock overnight, and weather on
weekend mornings, without anyone touching it.

**Shipped in `ec2be47`** — `homescreen/schedule.py`: `slot_contains`, `active_view`,
`seconds_to_next_change`, `clean_schedule`, bounded at `MAX_SLOTS`/`MAX_VIEWS` because the
page that writes them is unauthenticated. Last-match-wins, midnight wrapping, and both
Madrid DST transitions pinned at real dates. `seconds_to_next_change` walks boundaries and
tests membership at each rather than doing arithmetic on the current slot — with overlapping
slots the next change is not this slot's end, and two back-to-back slots on one view have a
seam that is not a change and must not wake a panel for a frame nobody can see. Three
mutations caught. 958 tests.

**Remaining — the wiring, the storage and the editor**
- The device record carries the schedule; `GET/PUT /api/devices/{hw}/schedule` validates it
  whole through `clean_schedule` and writes nothing on a 400.
- Region capacities are enforced on write (§4.2), which `clean_schedule` does not yet know
  about because item 3 has not defined placements.
- `_scene_for` resolves through `active_view` instead of `rec["scene"]`; a screen with no
  schedule keeps behaving exactly as today.
- **Cadence:** `X-Poll-Seconds` is `min(component next change, seconds_to_next_change)` after
  the existing precedence; `poll_max_s` unchanged. Assert both directly on the route, not
  only on the helper.
- **The floor holds:** a boundary 3s away on a `depth: 1` device still advertises 30s.
- A boundary six hours out advertises 600, not 21,600 — the `POLL_MAX_S` clamp on the route.
- The week strip renders 7×24 with the winning view per cell and `ahora` marked; the preview
  accepts `at`.
- Removing the last slot leaves the default showing — never a blank panel.
- A schedule change takes effect within one advertised poll; `_last_cold` is invalidated as a
  scene change already does.

**Dependencies.** Item 3 for placements; item 5's remainder for the boundary redraw.

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
- **The component adapts to the surface** (§2.3), which is the point of this item as much as
  the prices are. One `symbols` option, two presentations, asserted separately:
  - a region ≥ 400px wide renders a **stacked list** of every configured symbol;
  - a 240×240 round panel renders **one symbol at a time and cycles**, asking for its own
    next step through `poll_s`. No `rotate_s` option, no platform rotation feature.
  - a test builds the same placement against both geometries and asserts one list and one
    cycling value, from one set of options.
- **Five is the hard ceiling on the pixel path** (SPEC §9: 764px band, ~117px cells, six
  truncates). The sixth is refused with a Spanish notice, never a truncated row. The cycling
  presentation has no such cap — it shows them in turn.
- The cycle rate is a component constant, not an option, until someone asks otherwise; on a
  `depth: 1` surface `poll_floor` clamps it and the component must still be correct when
  woken slower than it asked.
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
- The BOOT short press is already inert (item 1) and stays inert here: range is a placement
  setting, not a gesture. It becomes bindable in item 13, and never to the range.

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

### 13. Inputs — a device declares its buttons, the dashboard binds them — **S/M**

**Goal.** The button on the back does something you chose, and the choice is made on the
screen's own page.

**Acceptance criteria**
- A device declaring `buttons=boot.short` has that input listed on its page with an action
  picker; a device declaring none shows no picker at all rather than a disabled one.
- `clean_caps` accepts `^[a-z0-9_]{1,16}\.(short|long)$`, drops anything else, and still
  honours `MAX_CAP_LIST` and the 32-char entry truncation — assert `buttons=../..;rm` is
  dropped, not stored.
- A capability list is only read when the same request also declares geometry, like every
  other cap — a bare `?buttons=` fragment cannot redefine a device.
- Bindings are stored **per device**, next to `poll_seconds`; changing the assignment does not
  change them.
- `GET/PUT /api/devices/{hw}/inputs`, plus the form on the device page.
- The scene payload carries `inputs` filtered to what the device declared; a binding for an
  undeclared input is dropped and **reported in the fleet view**, exactly as an undeclared
  component already is.
- Changing a binding changes the scene ETag, so it reaches the device on the next poll and an
  unchanged binding still answers 304.
- Firmware: `refrescar` drops the cached ETag and polls immediately; `identificar` draws the
  hardware id for ~5s and then restores the scene without waiting for new content — assert
  through `loop()`, not `renderScene()` (item 5).
- An unknown or absent action is a **no-op**, never an error screen.
- **`boot.long` is not in the declared list, has no picker, and cannot be bound.** A test
  asserts a `PUT` naming it is a 400.

**Dependencies.** Items 1 and 5.

---

### 14. The e-paper compositor — **L**

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

### 15. Claude usage — **S/M**

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

### Answered, and where the answer lives

| | Question | Answer | Now in |
|---|---|---|---|
| Q1 | Where do secrets live | the dashboard writes them, write-only | §3.6, item 7 |
| Q3 | Rotation scope | replaced by a schedule | §4 |
| Q4 | Retire `config.yaml devices:` for the radar | yes, migrate | item 10 |
| Q6 | Composed dashboards on the big panel | yes, per SPEC §9 | §4, items 3 and 14 |
| Q9 | Schedule per screen or per region | **per screen** | §4.3, §4.4 |
| Q10 | What replaces rotation for multiple tickers | **the component adapts to the surface** | §2.3, item 9 |
| Q11 | What the BOOT short press means | **nothing; buttons become a capability** | §4.7, item 13 |

Q10's answer turned out to be the more general one and was folded into the component contract
rather than into the stocks item: a component declares which surfaces it draws on *and* is
responsible for expressing its data well on each. Cycling is one way of doing that, on small
glass, decided by the component.

### New, created by these answers

**Q12 — Can a button ever do something only the server can do?**
The v1 action vocabulary (§4.7) is deliberately all-local — `refrescar`, `identificar` —
because devices only ever `GET` today and keeping it that way is what makes the extension
small. But the obvious fourth action is "show me the next ticker now", and with Q10's answer
the *server* decides which ticker is showing, from the clock. So that press cannot be served
locally: it needs either a device→server write (`POST /api/devices/{hw}/events`, the first
thing a device ever writes) or the component shipping every page in the payload for the device
to cycle through — which is the rotation design that was just withdrawn.
→ **Recommend deferring, and shipping §4.7 with the two local actions.** Neither branch is
cheap, and nobody has yet wanted the button badly enough to say which. `identificar` and
`refrescar` are useful on their own, and the declaration shape (`buttons=`, per-device
bindings, `inputs` in the scene payload) is unchanged by whichever way this later goes — a
third action is a row in a table, not a redesign. If it does come up, my inclination is the
events endpoint over pages-in-payload: one device write is a smaller thing to own than a
second rotation mechanism, and it is the same shape a future "I pressed something" would take
anyway.

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
- **The dashboard is not a single-page app.** Forms and redirects, because that is the least
  machinery that does the job (§5.3). Scripts are allowed and the schedule editor is expected
  to use them; **assets are always local** — no CDN, no bundler, no webfont fetched at boot.
- **The BOOT long press is not bindable, ever.** It opens the WiFi portal and that is the only
  recovery path for a board on the wrong network — the one gesture that must work when
  nothing else does, including when the binding that would have changed it cannot be reached.
  It is not offered in `buttons=`, not listed in the dashboard, and not in the action
  vocabulary.
- **Buttons do not actuate anything off the screen.** No chords, no double-taps, no
  press-and-hold ramps, and nothing that reaches past the device it is attached to.
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

---

# Addendum, 2026-08-31 — the panel is real

**Status:** appended after the owner reported the 7.5" 800×480 1-bit panel is physically in
hand, asked for basketball alongside football, and asked that both the display and the web
app be "crisp and attractive" in a way somebody can check.

**Precedence:** same as the document it extends — below `CLAUDE.md`, above `SPEC.md`. §9–§12
are new sections; nothing in §1–§8 is withdrawn.

**What changed underneath this document.** CLAUDE.md §2's hardware table said *"e-paper
panel — not purchased yet"* and PLAN §6 was a shopping list. That line is now false and
every item that read "waits on hardware" is unblocked: backlog item 14 (the e-paper
compositor), PLAN phases B and C, and validation questions V6, V2b, V5b, V8b, V9, V10, V11.
The gate that all of them sit behind — **B2, is 10px type legible on real glass** — can be
answered this week, and until it is, nothing downstream of it should be designed, because
its answer changes the type scale and the type scale changes what a region is worth.

---

## 9. Bringing up the e-paper panel

### 9.1 The decision: PIXEL PUSH, and it is not close

The panel takes a rendered framebuffer from `GET /api/devices/{hw}/frame`. It does not
execute instruction lists for its content.

Both paths exist in the tree and both work. The case for choosing between them is not about
bytes on the wire; it is about **what this particular panel is for**.

**The draw vocabulary cannot express this panel's design, and growing it to fit would be
reinventing CSS in JSON and then implementing it twice.** `homescreen/draw.py` offers five
vertical slots (`rim_top`, `above`, `center`, `below`, `rim_bottom`), five size tokens as
fractions of the panel's *short* side, and horizontal centring with no other option. That is
a watch-face vocabulary and it is exactly right for the 240×240 round panel it was written
for. On 800×480 the short side is 480, so `xs` resolves to 26px and `md` to 53px — there is
no size token in the vocabulary that can draw 13px body text, and no slot arithmetic that can
draw a five-row agenda in the left column while a six-cell markets band runs along the
bottom. SPEC §9's design is a *typographic layout*: columns, baselines, rules, tables,
inverted pills, a 13/13/11/10px scale. Every one of those is a CSS declaration today and a
new wire primitive, a new Python resolver, a new C++ executor and a new golden fixture
tomorrow.

**CLAUDE.md's own decision rule already answers it.** *"Text layout → server renders;
geometry → device renders."* An 800×480 composed dashboard is almost entirely text layout.
The round radar is geometry, which is why it is data push and stays data push.

**The pixel path is the one that is already built, and the data path is the one that would
have to be built.** `render.py` (threshold 160, polarity, the two-slot semaphore, the
content-keyed frame cache), `compose.py` (per-placement CSS scoping, absolute region
rectangles), `layout.py`'s `dashboard` template, the `/api/devices/{hw}/frame` route with its
ETag and its cold-render budget, `mockdevice.py --kind epaper` decoding a frame back to a
PNG, and an `html` body on every one of the eight components — all of that exists, is tested,
and assumes pixel push. Choosing data push strands it *and* incurs the vocabulary work above.

**The counter-arguments, taken seriously, and why they lose:**

| Objection | Answer |
|---|---|
| Chromium is not installed on the Pi (CLAUDE.md §2) | It is one `apt install`, and it is PLAN B0 — a provisioning step, not a design constraint. `render.find_chromium()` already detects trixie's `chromium` name. The cost is real and bounded: ~200 MB and ~3 s per invocation against 1.8 GB usable, which is why `_RENDER_SLOTS` is 2 and why the frame cache is keyed on content. Both were designed for this |
| 48,000 bytes versus <1 KB | Over the house WiFi with no TLS, on a C3, this is on the order of a second — measure it at E6 rather than trusting either of us. It is not free, and 9.3's streaming requirement is the reason it is not a heap problem either; but it is not the expensive part of a 3-second refresh that happens twice a minute at most |
| Two render slots for the whole fleet | The fleet is one e-paper and one round panel. The round panel does not render. The floor already holds: `registry.poll_floor` returns 30 s for `depth == 1`, so a single panel can request at most two frames a minute, and identical HTML does not fork a browser at all |
| A 1-bit panel refreshes in ~3 s, so why send more than it can show | This argues for fewer frames, not for a different payload. It is already answered by the 30 s floor |
| Partial refresh for a ticking clock (PLAN S2) | The strongest objection, and it still loses. See 9.2 |

### 9.2 Where data push survives, and it is not a compromise

Two places, both narrow, both permanent.

**Status screens and the cold-boot fallback (PLAN S6).** A panel that cannot reach the Pi,
or is not yet approved, or is on the wrong network, must say so *on its own glass* with no
server involved. That is one or two lines of centred text — precisely what the draw
vocabulary is for. So the e-paper firmware **also** declares `draw_list` and polls `/scene`,
and uses `/frame` only for content. This is not a second rendering path for the dashboard; it
is the panel's ability to explain itself, which §5.4 already established is non-negotiable
("a board that spent a year in a drawer must be able to tell you why it is not working").

**The partial-refresh window is a cropped sub-frame, not an instruction list.** A ticking
clock through the draw vocabulary would put a second type engine on the glass, and the two
engines would disagree at the seam — different face, different metrics, a minute digit that
does not sit where the hour digit sits. The clock region must come from the same render as
the rest of the page. So partial refresh is **the same pixel payload, cropped**:

- The server knows which rectangle changed, because it holds both frames.
- A view declares which placement, if any, owns the partial window (§4.2, PLAN S2). A view
  with no clock in the top-left has none and refreshes fully.
- Window x-bounds are multiples of 8 (CLAUDE.md §6). The desk template's `(16,72)–(280,140)`
  is 264 = 33×8 and must contain **only** the numerals.
- The wire shape is decided at E5 below, with E4's measurement in hand, between
  `X-Partial-Window: x0,y0,x1,y1` alongside a full frame and a `?window=` sub-frame request.
  **The sub-frame is the recommended default** — it sends 2 KB instead of 48 KB, and a device
  that cannot check a body's length must never be asked to crop one.

### 9.3 Hardware and firmware

**Board.** The ESP32-C3 Super Mini already in hand is the default, with one measurement
gating it. V10 asks whether a 48 KB contiguous allocation survives fragmentation on a C3;
the answer is to **never make that allocation**. Stream the HTTP body in ~1–4 KB chunks
straight into the driver, band by band, and the largest block ever needed is the chunk. If
V8b comes back saying GxEPD2 will not accept a pushed window buffer through `writeImage` /
`writeImagePart` and insists on locally-drawn bitmaps, then and only then move to an
**ESP32-S3 with ≥2 MB PSRAM**, where a full framebuffer is trivial. State the rule so the
board is chosen by a measurement rather than a preference:

> Stream if the driver permits it; buy PSRAM if it does not. Do not allocate 48 KB on a C3.

**Driver library.** `GxEPD2_BW` with `GxEPD2_750_T7` (800×480, the `epd7in5_V2` silicon).
**Never Waveshare's Arduino library** — CLAUDE.md §6, it has no partial refresh and therefore
no ticking clock. Every polarity and alignment finding in `VALIDATION-01` #1 and #3 was
measured against the **Python** driver; V9 says they must be re-measured against GxEPD2, and
E6 below is where.

**Breakout.** PLAN §6 assumes the Waveshare e-Paper Driver HAT breaks the FFC out for both
the Pi and a C3. Verify that before wiring: the HAT is a 40-pin Raspberry Pi form factor, and
if it does not expose usable pins for an ESP32 the part needed is the separate Waveshare
**ESP32 e-Paper Driver Board**. This is a ten-minute check against the product page and it
blocks E6, not E2.

**Capability declaration.** Exactly the existing query string, with nothing new to build:

```
GET /api/devices/{hw}/scene?w=800&h=480&depth=1&shape=rect
    &components=draw_list&max_items=40&fw=…
GET /api/devices/{hw}/frame?w=800&h=480
```

`registry.clean_caps` already accepts `w`, `h`, `depth`, `max_items` through `CAP_INTS`,
already validates `shape` against `surface.SHAPES`, and already truncates capability lists.
`surface.describe` already turns `depth: 1` into `monochrome: True`, and `poll_floor` already
returns 30 s for it. **Nothing in the capability layer needs to change for this panel**, and
that is the payoff for having described glass rather than enumerated it.

Note what `/frame` does *not* take: `depth` and `shape` are absent from its query, by design —
§5.2 fixed the frame's length to what the caller asked for and nothing else. The scene it
builds must still know the depth. See 9.5 item 1, which is a bug.

**Tones on one ink.** `draw.TONES` has eight entries and the C3 maps each to an RGB565 pen
(`firmware/src/ui/components.cpp`). On 1-bit glass there are two inks, so a tone cannot be a
colour and must become a **treatment**. The mapping is small, it is the honest one, and it
lives in **one server-side function** so the two executors cannot drift:

| Tone | On 1-bit glass | Why |
|---|---|---|
| `normal` | black, 13px/500 | |
| `dim` | black, 11px/400 | **Never grey.** CLAUDE.md §6: greys forfeit partial refresh, and grey text at 10–13px thresholds to speckle. Hierarchy is size and weight only |
| `accent` | black, 10px/500 uppercase, `.14em` tracking — the existing `.lab` | An identity is a label, and a label is already a treatment in `BASE_CSS` |
| `good`, `cool` | **`normal`** | Untreated. Direction is carried by the `▲`/`▼` glyph already in the string, not by the ink |
| `hot` | **`normal`** | The honest answer. "Hot" is a property of the number, and 32° says it. One ink cannot say it twice, and inventing a treatment for it spends the pill budget on the least urgent tone on the panel |
| `bad`, `warn` | inverted pill (`.pill`: white on black) | The only tones that mean *look here now* |

**Two pills on a composed page, fleet-wide** (SPEC §3). A third `bad` degrades to `normal`
rather than being drawn — a page of inverted blocks has no emphasis at all. Enforced at
compose time, asserted by a test (§12, C5).

### 9.4 Staged bring-up

Seven stages. Each has one observable outcome; each is falsifiable; none begins before the
one above it has answered.

**E0 — Prove the whole pixel pipeline with no panel and no Pi.** *(do this first, it costs
nothing)*
Serve locally on the Mac, where Chrome exists. Assign a `dashboard`-template view with a
masthead, two column components and a markets band. Run
`python -m homescreen.mockdevice --kind epaper --once --out /tmp/screen.png`.
**Observable:** a PNG that is 800×480, not a negative, and recognisably SPEC §9.
**This is where the design iteration happens** — the owner's "crisp and attractive" is
answered here on a screenshot loop of seconds, not on glass at 3 s a refresh over SSH
(CLAUDE.md §5).
*Blocker to fix first:* `mockdevice.KINDS["epaper"]` declares `components: "text"` and no
`shape`. It must declare `shape: "rect"` and `components: "draw_list"` or it is not
simulating the device that was just bought.

**E1 — Pi render subset (PLAN B0).**
`apt install chromium fontconfig`, install Inter system-wide, `raspi-config nonint do_spi 0`,
reboot, confirm `/dev/spidev0.0`. Cap journald retention first — the card is a 15 GB microSD
and SPEC §16's wear risk is live.
**Observable:** `curl 'http://dashboard.local:8080/api/devices/<hw>/frame?w=800&h=480'`
returns **exactly 48,000 bytes** from the Pi, and the run reports `render.grey_fraction` of
the intermediate PNG. That number answers **V5b** (does the Pi's Chromium honour
`-webkit-font-smoothing: none`); macOS measured 0.010%, the Pi is expected worse, and the 160
threshold exists for that gap.

**E2 — Panel hello-world, wired to the Pi (PLAN B1).**
Vendored `epd7in5_V2`, **PWR wired to BCM 18 / physical 12** — `module_init()` asserts it and
SPEC §2's wiring table omits it, so without it the panel is silent and says nothing about
why. `epd.sleep()` in a `try/finally` on every path.
**Observable:** the asymmetric polarity vector. Black at (0,0) and (7,1) in an 8×2 image
serialises to `0x80 0x01`, and on the glass that is the top-left pixel and the *eighth* pixel
of the second row — asymmetric in both axes, so a mirrored, rotated or inverted buffer is
visible rather than plausible. Also answers **V2b** (is writing only `0x13` a clean full
refresh).

**E3 — The legibility gate (PLAN B2). THE GATE. Nothing below it is designed until it
answers.**
Push E0's composed dashboard through E2's wire.
**Observable:** a photograph of the panel at 60 cm, and a written yes/no on whether the 10px
`.lab` and the 11px `.ter` are readable. Also: the real full-refresh duration, and whether
the dotted rule stipples.
**If the answer is no, the type scale changes, and a bigger scale means fewer rows per
region — which changes `layout.TEMPLATES`' `holds` counts and therefore what every view can
contain.** This is exactly why PLAN §6 says buy one panel, not several.

**E4 — Partial refresh through our own wrapper (PLAN B5, S2).**
`epaper/driver.py` owning cropping, packing and polarity in one auditable place. `init_part()`,
never `init()`. **Never call the vendored `display_Partial()` directly** — broken x-alignment
guard, expects a window-cropped buffer, opposite polarity to `display()` (VALIDATION #3).
**Observable:** the minute digit changes with the rest of the page untouched; the measured
duration (**V11** — GxEPD2 says 1600 ms, SPEC §2 claims 300 ms, and one of them is wrong);
and the visible ghost after 60 consecutive partials, which is what sets the full-refresh
interval and the per-device ghosting counter (PLAN S3).

**E5 — The partial window reaches the wire (PLAN S1).**
Decide the shape here, with E4's number in hand. Recommended: `GET /frame?window=<region-id>`
returning the cropped sub-frame, plus `X-Partial-Window: x0,y0,x1,y1` echoing the rectangle
the body covers, x-bounds multiples of 8. The view declares which placement owns the window.
**Observable:** a device presenting `If-None-Match` for an unchanged page but a changed clock
receives ~2 KB and performs one partial refresh, not one full one — and the ghost counter
increments on the full refreshes only.

**E6 — The panel becomes a client (PLAN C3).**
C3 (or S3, per 9.3), GxEPD2, streamed frame, watchdog, restart-on-N-failures, **OTA — which
CLAUDE.md calls for "from day one" and which the firmware does not have at all today.**
**Observable:** the frame on the client panel is **byte-identical** to the frame the wired
harness pushed for the same view and instant. Diff them. *This is the entire reason the wired
harness stays in the tree*: it is how a render bug is told from a wire bug, and it is where
**V9** (GxEPD2's polarity and alignment) is answered against the Python findings.

**E7 — It survives the house (PLAN S6).**
**Observable:** pull the Pi's power. The panel keeps its last frame, and after N failed polls
draws a Spanish "sin servidor" *from the draw list* — the one job data push has on this glass.
Restore power; the panel recovers with no touch and no reflash. Then leave it a week and read
the ghost counter.

### 9.5 What the server is not ready for — specifically

Each of these is a defect or a hole with a file and a reason, not a wish.

1. **`/frame` drops `depth` and `shape` when it builds a single-placement scene.**
   `serve.py:device_frame` calls `_scene_for(hw, rec, caps={"w": w, "h": h})`. A component on
   that path is told, through `surface.describe`, that it is on a 16-bit screen —
   `monochrome` is False and every 1-bit decision it might make is made wrongly.
   `_composed_html` does **not** have this bug (it merges `registry.clean_caps(rec["caps"])`),
   so the one-placement and many-placement paths disagree about what glass they are drawing
   on. Fix: build the caps once — stored caps, overridden by the requested geometry — and
   hand the same dict to both.

2. **There is no tone→ink resolution for `depth: 1`, anywhere.** `draw.to_svg` paints a
   **black ground with white text and an eight-colour palette**. The caller correctly drops
   the circle mask off-square (`round_panel=(w == h)`) and stops there, so the e-paper
   preview is a photographic negative of the panel rendered in colours it cannot produce.
   Needs a `depth` argument: white ground, black ink, and 9.3's tone table. It must be the
   same function the compositor's treatments come from, or the two drift — which is the exact
   failure the two-executor design exists to prevent.

3. **No `X-Partial-Window`, no `?window=`, and no ghosting counter.** PLAN S1 and S3 are still
   "design needed". `/frame` carries an ETag, which says *whether* something changed and never
   *what*, and nothing counts full refreshes since the last clear.

4. **A view cannot declare which placement owns the partial window.** `layout.TEMPLATES` has
   no field for it and `clean_view` would drop one. Required by §4.2 and PLAN S2.

5. **There is no `masthead` component.** The `dashboard` template has a `masthead` region and
   nothing in `scenes._registry()` fits it. `status` is closest and it is a fault screen with
   `min_w: 320`. A composed dashboard today has a hole across its top 11%.

6. **The design audits exist as prose, not as tests over composed output.** Item 14 asks for
   them: only `#000`/`#fff` in the compiled CSS, no `font-size` below 10px, ≤2 `.pill`, one
   Chromium invocation per composed frame rather than one per placement. `compose.compose` is
   where all four belong. See §12.

7. **Every component's pixel body is a placeholder, and composing placeholders yields a page
   of placeholders.** `sport.py` emits `<div class="big">Home — Away</div>` at 36px — no time,
   no score, no competition, none of which is in the HTML although all three are in the draw
   list. `weather` emits four stacked lines. `claude` emits two. None of them is SPEC §9's
   design, and none was ever looked at, because nothing has ever rendered. **This, not the
   driver, is the bulk of the e-paper work.**

8. **Chromium is absent from the Pi**, so `render.find_chromium()` returns None there and
   `/frame` answers 503 from the target today. Provisioning, not code — but it is the literal
   reason this pipeline has never produced a pixel.

9. **The fleet page has no notion of a pixel-push screen.** Nothing tells an operator that
   this screen takes frames and therefore that its view must have a pixel path, and nothing
   warns when a placement's component emits a draw list but no usable HTML.

10. **`poll_floor` = 30 s for `depth: 1` was chosen against numbers that are about to be
    measured.** It assumes a ~3 s full refresh and two Chromium slots. If E4 says partial
    refresh is 1.6 s and E5 makes a clock tick cost 2 KB and no browser fork, the floor is
    protecting a cost that no longer exists on that path. Re-derive it at E5 — as a floor on
    *full* frames, with partials on their own budget. Do not touch it before then.

11. **There is no composed-view preview at all, and §4.6 is unbuilt.**
    `/api/devices/{hw}/preview.svg?view=` resolves its argument against `scenes.names()` — it
    previews **one component**, executes **the instruction list**, and emits SVG. On a
    pixel-push panel that is a different program's output than the glass will show. §4.6 asks
    for the whole view with region outlines, each region individually, and an `at` instant so
    a schedule can be previewed; none of the three exists. Without it there is no way to see
    a composed dashboard except by fetching `/frame` and decoding it, which is what
    `mockdevice` does and what E0 is built around.

12. **`region_caps` carries `w`/`h` but not the region's origin.** Correct for composition,
    but it means a component cannot know it is in the bottom band rather than the top one.
    Nothing needs this yet. Recorded so the next person does not discover it as a surprise.

---

## 10. Sport beyond football

### 10.1 Where this stands

`sport` ships and works: it picks a live match over a fixture over a last result, decides
that from the data rather than from a setting, renders both paths, and fits the pairing to
the glass. It is fed by exactly one provider, `football`, against football-data.org's free
tier, with a live key on the Pi. The owner wants basketball as well — NBA, and European
basketball (EuroLeague, and Liga ACB is the obvious Spanish one for a house in Madrid).

### 10.2 The recommendation: ONE component, SEVERAL providers

**One `sport` component. Not `basketball` beside `football`.**

The argument is this document's own §2.3 and §3.1, applied without an exception.

- **What differs between football and basketball is where the data came from, and that is
  precisely a provider difference.** §3.1: a provider "knows how to fetch one kind of data and
  nothing about screens". `football.py`'s own docstring already says it is *"named for what it
  fetches rather than for the vendor, because the component asks for 'this team's next match'
  and should not have to change if the source does."* That sentence is the whole design and
  it was written before there was a second sport to test it.
- **What does not differ is everything the component does.** A second component would
  duplicate `_pick`'s live-over-next-over-last ladder, `_when`'s Spanish relative dates, the
  `lines_fit` decision between one pairing row and two stacked names, the draw list, the HTML
  body, and every empty state. All of it is sport-agnostic. The only genuine differences are
  that a basketball score has three digits instead of one and that there is no draw.
- **The picker stays honest.** Two components named `football` and `basketball` invite a
  third, a fourth, and a component list that is a sports directory. One `sport` component with
  a sport option is one row in the picker whatever the owner follows next.

**The option shape:**

```python
OPTIONS = (
    {"key": "sport", "label": "Deporte", "type": "choice",
     "choices": ("futbol", "baloncesto"), "default": "futbol"},
    {"key": "team", "label": "Equipo", "type": "text", "default": "",
     "help": "..."},          # interpreted by the provider the sport selects
    {"key": "days", "label": "Días por delante", "type": "int", "default": 30},
)
```

`team` becomes `text`, not `int`. Today it is an `int` because football-data.org identifies
teams by a numeric id (86 = Real Madrid). No other vendor uses that scheme, and a per-sport
option pair would spend two of the twelve allowed options on every sport that is ever added.
One free-text field, interpreted by whichever provider the `sport` choice selects, with the
`help` string naming what to type for the current sport. `needs()` maps
`sport -> provider` and passes `team` through; **the component never learns which vendor
answered.**

### 10.3 The blocking change: normalise the match envelope FIRST

This must land before a second provider exists, and it is the load-bearing part of this
section.

`football.py` emits the vendor's own vocabulary and `sport.py` reads it verbatim:

```python
FINISHED = {"FINISHED", "AWARDED"}      # football-data.org's status enum
LIVE     = {"IN_PLAY", "PAUSED"}        # ...in the COMPONENT
```

`home_goals` / `away_goals` are likewise football's word. So does `tests/test_sport.py`,
whose fixtures are football-data.org response fragments. A second provider added on top of
this has to **pretend to be football-data.org** — invent an `IN_PLAY` string it never
received — and the first time a third vendor spells it `inprogress` the component grows a
translation table for vendors it is not supposed to know exist.

**The port a sport provider satisfies:**

```python
{"team": "<as the caller gave it>",
 "sport": "futbol" | "baloncesto",
 "matches": [
   {"when":        "<ISO 8601, UTC, Z>",
    "home":        "<short name, ≤24 chars>",
    "away":        "<short name, ≤24 chars>",
    "status":      "scheduled" | "live" | "finished" | "postponed",
    "home_score":  int | None,
    "away_score":  int | None,
    "competition": "<short name>"}]}
```

Four status values, not the union of every vendor's enum. `home_score`/`away_score`, because
a basket is not a goal. Sorted by `when`, capped at `MAX_MATCHES`. `sport` carried on the
envelope so the component can format a three-digit score differently from a one-digit one
without asking which provider it came from.

Renaming the two score fields is a one-line change in `football.py` and `sport.py` each, plus
the fixtures. Doing it now costs an hour. Doing it after a second provider ships costs a
migration of cached envelopes.

### 10.4 Which basketball API

The requirement is narrow, which makes the choice narrower than it looks: **the next fixture
and the last result for one followed team, in NBA and in European basketball.** No standings,
no box scores, no live play-by-play. A provider that cannot do those three things well is not
better for being able to do twenty others.

Four things decide it, in this order:

1. **Does one adapter cover both NBA *and* EuroLeague/ACB?** Two adapters is two keys, two
   cadences, two failure modes and two things to maintain, for one component. `football`
   covers every competition the owner cares about through one key; basketball should too.
2. **Is there a documented free tier with a stated number?** §3.3 requires that a
   configuration exceeding a free tier be refused *at startup with the arithmetic in the
   message*. A vendor that publishes no number cannot have that check written against it.
3. **Is it documented at all, or is it somebody's site's private JSON?** An undocumented
   endpoint changes shape without warning. SPEC §14 puts deliveries last for exactly this
   reason and the same discount applies here.
4. **Fixtures with tip-off times and final scores in one call per team.** Anything needing a
   call per fixture multiplies against a daily quota.

**Recommendation: API-SPORTS' basketball API (`api-basketball.com`), one adapter, one key.**

It is the only candidate that is documented, has a published free tier, and covers **NBA,
EuroLeague and Liga ACB from one key** — so it answers the whole of what was asked with the
same shape `football` already has. Its `/games` endpoint takes a team and a season and
returns fixtures with tip-off timestamps, status and final scores, which is one call per
followed team per cycle.

**The one thing to check before writing a line of it, because it may change the design:**
the free tier is understood to be on the order of **100 requests/day**, and
`football.DEFAULT_INTERVAL_S = 1800` would spend 48 of them per job. That means roughly
**two basketball jobs fit in the free tier and three do not** — and under §3.2 a job is
per *(provider, params)*, so two screens following two different teams is already two jobs.
So:

> Verify the published free-tier number, then set `DEFAULT_INTERVAL_S` from it — not from
> football's — and write the startup arithmetic check against it. If the number turns out
> tighter than two teams, the interval goes up (fixtures move on the scale of days; only a
> live score needs minutes) and the component polls harder only while a match is in play.

**Fallback, if the free tier is unusably tight or an account is unwanted:** two keyless
adapters — the NBA's own schedule/scoreboard JSON on `cdn.nba.com` for NBA, and
EuroLeague's own live feed for EuroLeague. Both need no signup and no key. Both are
**undocumented private feeds** that can change shape without notice, and taking them means
accepting the maintenance profile SPEC §14 assigned to deliveries. Take this path only if
option one is actually blocked, and if taken, take it for both — a mixed keyed/keyless pair
is the worst of both.

**Rejected, with reasons, so they are not re-proposed:**

| Candidate | Why not |
|---|---|
| `balldontlie.io` | NBA only. It solves half the request and leaves EuroLeague and ACB needing a second adapter anyway — which is the cost the recommendation exists to avoid. Also moved from keyless to a keyed free tier |
| TheSportsDB | Broad but thin: European basketball fixture coverage is unreliable and the endpoints that answer "next event for this team" moved behind a paid key. It is already named in item 12 as a football fallback; that is where it should stay |
| ESPN's `site.api.espn.com` | Keyless and reliable in practice, but undocumented and with no stated terms — same objection as the fallback above, without the fallback's advantage of being the league's own feed |
| football-data.org | Football only. Confirmed: it does not serve basketball, so the existing key buys nothing here |

**Verify these three before implementation, in this order** — this is the same discipline §7
applied to Q5 and it is not optional:
(a) the published free-tier request limit and window; (b) that NBA, EuroLeague **and** Liga
ACB are all on the free plan rather than a paid one; (c) that one call returns a team's
fixtures *and* results together. If (a) or (b) fails, the fallback becomes the recommendation
and 10.5's criteria apply to two adapters instead of one.

### 10.5 Acceptance criteria

**The normalisation (ships first, on its own):**
- The envelope shape of 10.3 is what `football.fetch` returns; `home_goals`/`away_goals` are
  gone from the tree, and no football-data.org status string appears anywhere in
  `homescreen/scenes/`.
- `sport.py` reads only `scheduled` / `live` / `finished` / `postponed`. A status it does not
  recognise is treated as `scheduled` and renders the fixture, never a traceback and never a
  blank.
- `tests/test_sport.py`'s fixtures are written in the normalised vocabulary, so they no longer
  document one vendor's API.
- The rendered round-panel output for a football team is **byte-identical** before and after —
  pinned, because this refactor must be provably invisible.

**The second provider:**
- A new module under `homescreen/fetch/providers/` satisfying `ProviderPort`, registered in
  `providers._modules()`, with `NAME`, `PARAMS`, `SECRETS`, `DEFAULT_INTERVAL_S`,
  `MIN_SPACING_S`, `clean_params` and `fetch`. The contract test that walks the registry
  passes with no special case.
- **Metadata importable without `requests`** — `import requests` inside `fetch`, per §3.1, and
  the C7 import-graph guard on `serve.py` still passes.
- `fetch` unit-tested against a **recorded fixture** in `tests/fixtures/`, with no network.
- `DEFAULT_INTERVAL_S` and `MIN_SPACING_S` are set from the vendor's **documented** limits, and
  a configuration that would exceed the free tier is refused **at startup with the arithmetic
  in the message** (§3.3, the `check_cadence` style).
- If it needs a key: `SECRETS = ("api_key",)`, set write-only from the dashboard, and the
  §3.6 sentinel test — which walks the app's URL map — covers it with no new assertion.
  A missing key is a job with `ok: false` and a Spanish reason; the component renders
  "sin clave"; no traceback and no blank panel.

**The component with two sports:**
- `needs()` returns the football provider for `sport=futbol` and the basketball provider for
  `sport=baloncesto`. **One test per branch**, asserting the provider name, so the mapping
  cannot silently collapse to one.
- Two placements on two screens, same sport and team, produce **one** job; different sports
  produce two (§3.2's dedup, re-asserted here because a new provider is where it breaks).
- A **three-digit score renders inside the glass on both surfaces**: `112 - 108` at size `lg`
  on 240×240 satisfies `draw.lines_fit`, and the composed 800×480 fragment does not overflow
  its region. This is the one genuinely new rendering risk basketball introduces and it is
  cheap to pin.
- A basketball fixture with no draw possible still renders correctly at half time (`live`,
  both scores present) and at tip-off (`live`, `0 - 0`).
- Changing `sport` on a placement while `team` still holds the old sport's identifier yields
  "sin partidos" with a Spanish hint, **not** an exception and not a fetch loop against an id
  the vendor will never recognise.
- A **NBA tip-off time renders in the placement's timezone**: a 19:30 ET game shows as the
  following morning in Madrid, correctly, and `_when`'s "mañana" is right about which day it
  is. Assert at a real DST-mismatched date — the US and EU do not change clocks on the same
  weekend, and that fortnight is exactly when this breaks.
- One provider failing leaves the other's placements rendering, with `ok: false` noted
  (item 12's existing criterion, now with a second provider to mean it).

**What this deliberately does not add.** No standings tables, no league ladders, no
box scores, no player statistics, no crests or logos or remote images of any kind (item 12,
unchanged), and no live score push — a screen is not a scoreboard, and `POLL_S` stays at 300.
The unit is still *the next match, or the last result*, for one team.

### 10.6 Sequencing

This slots into §6's backlog as **item 12's second half**, and it is small:

1. **12a — normalise the envelope.** One session. No new dependency, no key, no new API.
   Ships alone and invisibly. **Do this even if basketball never happens** — it removes a
   vendor's enum from a component, which is a defect on its own terms.
2. **12b — the basketball provider.** One session against a recorded fixture, plus whatever
   the account signup costs.
3. **12c — the `sport` option and the second branch of `needs()`.** One session.

None of it blocks or is blocked by §9. They are the two independent tracks in front of the
project, and they should be worked in that order of importance: **the panel first, because it
is the thing that just arrived and the thing with a measurement gate in it.**

---

## 11. Coverage audit — what is genuinely not covered

Measured against everything the owner has asked for across this project: *clock, status,
stock/currency, weather, calendar, sport events, Claude usage*; composed dashboards;
schedules; several screens of different sizes; configuration per screen.

**What is genuinely done.** Eight components and seven providers exist and are tested — **1,231
Python tests pass on this tree today**, plus 266 firmware tests. Job dedup by `(provider, params)` works. The write-only
secret store works, with a sentinel test that walks the URL map. Schedules resolve, store,
and have a week-grid editor. Views compose with per-placement CSS scoping. Per-screen
*and* per-placement credentials exist. Capabilities are described rather than enumerated, so
a screen nobody has bought is already offered every template that fits it. This is a real
platform and the audit below should be read against that, not instead of it.

### The gaps, in priority order

**G1 — Not one pixel has ever been rendered on the target.** The entire pixel-push half —
`render.py`, `compose.py`, `/frame`, and the `html` body of all eight components — has never
produced an image on the Pi, because Chromium is not installed there. Composed dashboards,
the 800×480 design, "screens of different sizes" and most of what "attractive" could mean all
live in that half. It is not an unbuilt feature; it is an **unexercised** one, which is worse,
because it looks finished. §9.4 E0 and E1 are the whole of the fix and neither takes a day.

**G2 — Currency is fetched by nobody.** `homescreen/fetch/providers/fx.py` is a complete,
keyless, ECB-backed provider (Frankfurter) with `PARAMS`, cadence and tests — and **no
component declares `{"provider": "fx"}`**. Grep the tree: the string `"fx"` appears only
inside `fx.py`. `fetch.derive` therefore never creates an fx job and the module has never run
outside its own unit tests. The owner asked for "stock/currency"; `quotes` covers shares and
crypto, and the currency half is written, working, needs no API key, and is not wired to
anything. SPEC §9 puts FX in the first cell of the markets band. This is the cheapest
outstanding item in the project.

**G3 — The composed dashboard has no content designed for it.** Two distinct holes. The
`dashboard` template declares a `masthead` region and **no component fits it** (§9.5 item 5),
so the top 11% of the panel is empty. And the four components that would fill the columns
emit placeholder HTML — `sport` renders two team names at 36px and drops the kickoff time,
the score and the competition, all of which it already computes for the draw list. Composing
placeholders produces a page of placeholders. **This is the bulk of the e-paper work and it
is not driver work**, which is the thing most likely to be underestimated now that the
hardware has arrived.

**G4 — Buttons never shipped, and they were settled, not proposed.** §4.7 and item 13
specified a device declaring `buttons=boot.short`, `GET/PUT /api/devices/{hw}/inputs`,
per-device bindings, and `refrescar` / `identificar`. The tree has **no `buttons` in
`_CAP_LISTS`, no `/inputs` route, and no binding storage** — the string `buttons` does not
appear in `homescreen/` at all. The BOOT short press is inert as item 1 required, so the
device is sitting in the state that was supposed to be temporary. With a second physical
screen about to exist, `identificar` stops being a nicety: two anonymous boards on a shelf is
now a real problem rather than a predicted one.

**G5 — Claude usage ships a number the owner cannot obtain.** Q5 is still open in this
document's own §7: the recommendation was the Anthropic Admin API's usage/cost report *"with
the endpoint and key type verified before this is scheduled"*, and it never was. There is no
admin key on the Pi. The provider and the component both exist. So the component renders "sin
clave" forever. §7 already wrote the decision: **verify the endpoint and get a key, or drop
the component — do not fake it.** Choose one this month rather than leaving a permanently
broken tile in the picker.

**G6 — The API's retirement list is untouched.** §5.2 named four surfaces to retire and all
four are still routed: `/api/config`, `PATCH|DELETE /api/config/devices/<id>`,
`GET /api/display/<id>/data`, `GET /api/display/<id>/health`. `/api/components` was designed
and never built (`/api/providers` and `/api/jobs` were). §5.4's removal bookkeeping — the
once-per-device-per-day legacy log, the `api: "legacy"|"current"` field, `?state=` on the
collection — is also outstanding. None of this is urgent; all of it is the kind of debt that
becomes load-bearing exactly when a second device class arrives, which is now.

**G7 — No OTA, on a device about to be mounted behind glass.** CLAUDE.md says *"devices need
a watchdog + restart-on-N-failures and OTA from day one"*. The firmware has neither OTA nor a
restart-on-N-failures policy. It was tolerable while the only board was a C3 on a desk with a
USB cable in it. An e-paper panel screwed to a wall is a different proposition, and E6 is the
last cheap moment to add it.

**G8 — Quiet hours do not exist.** SPEC §6's `refresh.quiet_hours`, referenced by §2.6 as a
platform stretch over each component's answer, is not in the tree. A 1-bit panel does not
emit light, so this is not about a bedroom being lit — it is about **not spending refresh
cycles, ghosting budget and API quota between 01:00 and 07:00 on a picture nobody is looking
at**. It becomes real the day the panel is on a wall.

**G9 — "Several screens of different sizes" is asserted, not demonstrated.** `surface.py`,
`layout.templates_for` and `mockdevice` are good work and the abstraction looks right — but
the fleet has been exactly one round panel for the whole project. The second screen arriving
is the first genuine test of it, and the specific things it will test are: whether the
`dashboard` template's fractional rects land where SPEC §9's measured pixels say they should
on 800×480, and whether a component handed a 764×62 band composes for it or merely fits in it
(§2.3's obligation, which nothing currently checks).

**G10 — Deliveries (SPEC §7.6) remain unscheduled**, per SPEC §14 and PLAN Phase D. Correctly
deprioritised — most fragile upstream, least essential — but it is on the owner's original
list and has not been mentioned in a long time. Naming it here so that "we covered everything
you asked for" is not said while it is quietly absent.

**Not gaps, deliberately.** Rotation (replaced by schedules, §4). Authentication (owner's
decision, §8). A layout DSL or drag-and-drop (§8). Historical charts (§8). Per-region
schedules (Q9, settled). None of these should reappear as findings.

---

## 12. "Crisp and attractive", made checkable

The owner asked for a crisp, attractive design and asked that it be iterated until it is one.
That is not currently reviewable: there is no statement anyone could disagree with, so there
is no version of the work that could fail. What follows is the smallest set of criteria that
turns it into a gate. **Eight checks, seven of them automatable, one deliberately not.**

A view passes review only if all eight hold. They apply to both surfaces and to the web
dashboard where noted.

**C1 — Density has a ceiling, per surface.** A round 240×240 placement emits **at most 5
text drawables** — there are exactly five slots in `draw.SLOTS`, and a sixth is a collision,
not a design. Any placement emits at most `kMaxPlacements` = 40 drawables total after icon
expansion. On the pixel path, per region: masthead ≤3 block elements, a column region ≤12
rows, a markets cell ≤3 lines. *Check:* count elements in the built fragment; one test per
component per offered surface.

**C2 — Nothing overflows, measured with the worst string you actually get.** For every
component × every offered geometry, against a fixture holding the longest realistic value
(`"BINANCE:BTCUSDT 63,120 ▲ 2.90%"`, a 24-char team short name, a Spanish weekday plus a
full date), `draw.lines_fit` is true; and on the pixel path the composed 800×480 PNG has **no
ink in the outer 4 px of any region rectangle**. *Check:* one bleed assertion over the
rendered frame catches every truncation, every wrapped row and every collision in one number,
and it needs no golden image to maintain.

**C3 — Every state is designed, not just the happy one.** Each component renders four
fixtures — unconfigured, no envelope at all, `ok: false` with stale data, and full — and each
produces a **non-empty** frame carrying a Spanish string. None produces an empty rectangle,
and none produces a zero that reads as a real value. This is §2.5's existing invariant turned
into a 4×N matrix instead of a paragraph. *Check:* parametrised test; it is the single
criterion most likely to fail today.

**C4 — Two inks, and the floor holds, over the composed output.** The compiled CSS of a
composed page contains only `#000` and `#fff`; no `font-size` below 10px; and the rendered
PNG's `render.grey_fraction` is **< 1%**. All three already have machinery — the third is one
existing function. *Check:* run all three over `compose.compose`'s output, not over each
fragment, because scoping and the page box are where a stray colour would enter.

**C5 — At most two inverted pills on a page.** SPEC §3's budget, now enforceable: count
`.pill` in the composed HTML; a third `bad`/`warn` degrades to `normal` (§9.3). A page of
inverted blocks has no emphasis at all, which is the failure this prevents.

**C6 — Contrast, stated honestly per surface.** On 1-bit glass contrast is 21:1 or it is
nothing, so the real criteria are structural: nothing below 10px, no stroke thinner than 1px,
and — SPEC §3 — **no text placed over a graphic**, asserted by checking no region's
instruction list contains a shape whose bounding box overlaps a text placement. On the round
panel colour exists and the criterion is the usual one: **every tone in `draw.to_svg`'s
palette holds ≥ 4.5:1 against the panel ground.** Today's eight all do — the weakest
is `bad` #e05a5a at 5.78:1 and `dim` #8a8a8a at 6.08:1, both comfortably clear — so **pin
them now**, before anyone adds a ninth tone that vanishes. The web
dashboard is held to the same 4.5:1, in both its themes.

**C7 — One alignment per region, and it is visible.** A column region's placements share one
left edge and one vertical rhythm: the composed HTML for a stacking region contains no
`text-align:center` and no per-element horizontal margin. This is the single cheapest thing
that separates a dashboard that looks *designed* from one that looks *assembled*, and it is a
string assertion.

**C8 — A person looks at it, and the artefact is attached.** *(not automatable, and that is
the point)* Every e-paper item is reviewed against **two images filed with it**: a
`mockdevice --out` screenshot, and — after §9.4 E3 — a photograph of the real panel at 60 cm.
An acceptance criterion nobody looks at is not a criterion. E3 is the only place "is this
legible" can be answered at all, and E0 is the only place "is this attractive" can be
iterated cheaply.

**What these deliberately do not do.** They do not define a house style, prescribe a grid, or
score beauty. They make *specific bad outcomes impossible* — truncation, speckle, empty
rectangles, undesigned failure states, invisible tones, ragged columns, a page of inverted
blocks — and leave the rest to E0's screenshot loop and to the person looking at it. That is
as far as "attractive" can honestly be pushed into a test suite.
