# HomeScreen Platform — Requirements

**Status:** Draft for the owner, 2026-08-27.
**Scope:** What a component is, what a provider is, and the order to build the rest in.
**Precedence:** Below `CLAUDE.md`. Extends the
[server-driven displays design](2026-08-26-server-driven-displays-design.md) §5 and §9;
where that document defers something (rotation, vocabulary revision, provider registry),
this one decides it.

This document specifies. It does not implement.

---

## 1. Product intent

A house with a few screens in it, each showing one useful thing, changed from a web page
on the Pi and never by touching the screen.

A component — clock, weather, a ticker, the calendar, aircraft overhead — is written once
and offered to every screen it can honestly draw on. A round 240×240 LCD and an 800×480
e-paper are different glass, not different products: the same component decides what fits
on each. Choosing a component for a screen shows you what it will look like *on that
screen* before you save it, because the preview and the panel execute the same
instruction list.

Configuration belongs to the screen, not to the component: two screens can show the clock
in two cities, or the ticker with two symbol sets, and neither is a property of the code.
Anything a component needs from the internet is fetched once by one daemon on the Pi, on
its own cadence, and shared by every screen that wants it.

The Pi does the work so the screens don't have to. Nothing here leaves the LAN, nothing
here needs an account, and adding a component is one Python file.

---

## 2. The component contract

A component is a module in `homescreen/scenes/<name>.py` with one entry in
`scenes._registry()`. To exist it must declare the following. Nothing in this section is
new machinery for its own sake — items 1, 3, 6 and 8 exist today and are written down
here so a new component author has one list.

### 2.1 Identity and registration

```python
# homescreen/scenes/weather.py
OPTIONS   = (...)                       # §2.2
ROTATES   = True                        # §2.6, optional, default False
def providers(options: dict) -> tuple:  # §2.4, optional, default ()
def supports(caps: dict) -> str | None: # §2.3, optional, default None
def build(ctx: SceneContext) -> Scene:  # §2.5
```

One import and one entry in `scenes._registry()`. `registry.ASSIGNABLE_SCENES` derives
from that table, so there is no second list. A component that adds no new drawing
vocabulary adds no golden fixture.

### 2.2 Options — the schema is the form, the validation and the defaults

`OPTIONS` is a tuple of field specs. The dashboard renders the form from it, the registry
stores the values against the **assignment**, and `scenes.clean_options` coerces them.
Adding an option is one edit.

| Key | Required | Notes |
|---|---|---|
| `key` | yes | stored verbatim in `devices.json` under the assignment |
| `label` | yes | **Spanish** — this is UI copy |
| `type` | yes | `text` \| `int` \| `bool` \| `choice` |
| `default` | yes | what an unconfigured assignment behaves as |
| `help` | no | **Spanish**, one sentence |
| `datalist` | no | a name in `web.fields.DATALISTS` (`timezones` today) |
| `choices` | for `choice` | |
| `min` / `max` | for `int` | |
| `placeholder` | no | |

Bounds, already enforced: ≤12 options per component, ≤120 chars per value. Unknown keys
are dropped; a bad value falls back to its default rather than rejecting the assignment.

**A blank option means "use the global default".** That is how `clock.timezone` already
resolves against `config.yaml`'s `location:`, and it is the rule for every option that has
a server-side default (§3.4).

**An option is never a secret.** Options are stored in `cache/devices.json`, returned by
the unauthenticated `/api/devices`, and rendered into `/device/<hw>`. An API key or an ICS
URL in an option is an API key on the LAN. Secrets go in the secret store and a component
references them **by name** (§3.6).

### 2.3 Rendering capabilities — which screens this component is offered to

Availability is **derived from what `build` emits**, not asserted in a table that can lie:

| The component emits | It becomes available to |
|---|---|
| `Scene.html` | pixel-push devices (`depth: 1`, no declared components) — the Pi rasterises it |
| a component carrying `draw` | any device declaring `draw_list` (§4, item 1) |
| a bespoke component (`radar`) | only devices declaring that exact name |

**Every new component must emit both** an instruction list and HTML. A component that
emits only one is available on half the fleet, and the operator finds out from a greyed
option rather than from the code.

Two geometries are in scope and a component must compose sensibly on both:

- **240×240 round.** Slots narrow towards the rim; `rim_top`/`rim_bottom` sit at
  0.12/0.88 for that reason. Realistic budget: one `xl` value, one `sm` label, one `xs`
  line at the rim. Anything more is a page (§2.6), not a smaller font.
- **800×480 1-bit e-paper.** `scenes._style.BASE_CSS` rules are invariants, not
  preferences: `#000`/`#fff` only, nothing below 10px, hierarchy by size and weight only,
  no CDN fonts, smoothing off. Inverted pills are capped at **two on screen** (SPEC §3) —
  a component that wants one is competing for a fleet-wide budget, so it must be
  configurable off. Never place graphics behind text.

`supports(caps) -> str | None` is the escape hatch: return a **Spanish** sentence when
this component genuinely cannot draw on this glass, and the dashboard shows it as the
disabled option's reason. (Today `_scene_options` emits English strings — `"no pixel
rendering"`, `"needs radar"` — into a Spanish page. That is a bug; fixed under item 1.)

### 2.4 Data needs

```python
def providers(options: dict) -> tuple[dict, ...]:
    return ({"provider": "openmeteo",
             "params": {"lat": 40.4168, "lon": -3.7038, "units": "metric"}},)
```

Pure, cheap, no I/O — it is called by the fetch daemon for every assignment on every cycle
and by the dashboard to show which jobs a screen creates. Its return value is derived from
**this assignment's options** merged over the global defaults, which is what makes two
screens wanting different cities two jobs and two screens wanting the same city one job
(§3.2).

A component with no `providers` needs no data. `clock` and `status` stay that way.

### 2.5 Build

`build(ctx) -> Scene`, where `ctx` carries `cfg`, `cache_dir`, `caps`, `now`, `device` and
this assignment's validated `options`.

- Reads data **only** from the provider cache envelopes its `providers()` named. It never
  performs network I/O — `serve.py` must remain import-clean of anything that fetches.
- Returns `components` (data push) and/or `html` (pixel push), `layout="fill"`,
  `poll_s`, `poll_max_s`.
- **Must never raise.** `safe_build` catches, but a caught exception is a lost screen.
- **Must render with `ok: false`.** Last good data, marked stale (a tertiary `·` past one
  hour, SPEC §11). Empty sections collapse; never an empty rectangle sitting on glass for
  six hours. With no envelope at all it renders a Spanish "sin datos", not a blank.
- All third-party strings are HTML-escaped on the pixel path. `planes.py` does this because
  callsigns come from adsb.fi; every component with an upstream does the same.

### 2.6 Cadence

- `poll_s` — when to come back. **Aim at the next change**, never a fixed grid: the clock
  asks for `60 - now % 60`, a rotating component asks for the next page boundary, a
  weather component asks for the next provider refresh. One request per change beats
  twelve per minute that still land late.
- `poll_max_s` — the stable ceiling liveness is judged against.
- Precedence, unchanged: **operator setting > component > hardware floor.** The floor
  (`poll_floor`) exists because a 1-bit panel's own refresh is ~3s and there are two
  Chromium render slots for the whole fleet.
- SPEC §6's `refresh.quiet_hours` (00:30–06:30) is a **platform** stretch applied over a
  component's answer, not something each component reimplements. A component never needs
  to know what time the house sleeps.

### 2.7 Rotation

Rotation is **component configuration**, per the owner. A component declaring
`ROTATES = True` gets a platform-supplied `rotate_s` option appended to its schema (one
definition, every component gets the same field, same label, same bounds) and returns
**pages** instead of one instruction list.

The delivery split follows the existing two-path design rather than inventing a third:

| Device | Who rotates | Cadence |
|---|---|---|
| data push (LCD) | the **device**, from its own clock | `poll_s` unaffected by rotation |
| pixel push (e-paper) | the **server** picks page `int(now // rotate_s) % n` | `poll_s` aims at the next page boundary; `poll_max_s == rotate_s` |

The device rotates locally because a poll per page is the wrong shape for an 8-second
cycle. The panel rotates server-side because it has no pages to switch between — it has a
framebuffer — and because `poll_floor` (30s) already bounds how fast it may be asked to
redraw. A `rotate_s` below the floor on a `depth: 1` device is **clamped and the clamp is
shown**, not silently honoured.

The BOOT button keeps one fixed meaning: **long press = WiFi portal, short press = next
page now.** Range moves out of it (item 8).

On e-paper every page switch is a whole-panel change, so rotation drives the ghosting
schedule: the per-device counter and `X-Full-Refresh` (VALIDATION C5, PLAN S3) decide when
a page switch is a full refresh rather than a partial one. A rotating component on 1-bit
glass is therefore a panel-wear decision, which is the other reason `rotate_s` is clamped
there rather than honoured.

### 2.8 Preview

Falls out of §2.3 with no per-component work: `/preview/<hw>/<scene>.svg` executes the
same instruction list the device executes. It is not the frame — fonts and antialiasing
are the panel's — but nothing about the layout is guessed.

Requirements a component inherits:
- A component with pages is previewable **per page** (`?page=N`), shown as a strip.
- The preview reflects the option values **currently in the form**, before saving.
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
`NAME`/`PARAMS`/`SECRETS` to render `/settings`, and the VALIDATION C7 guard forbids the
fetching machinery from entering its import graph. `adsb.py` already does `import requests`
inside the function; that pattern becomes the rule.

### 3.2 Jobs — how work is keyed and deduped

A fetch job is **(provider, params)** and nothing else. Not a device, not a screen.

```
key       = sha256(json.dumps({"provider": p, "params": params},
                              sort_keys=True, separators=(",", ":")))[:16]
cache     = cache/feed/<provider>/<key>.json
envelope  = { fetched_at, ok, error, data }        # SPEC §7, no exceptions to the shape
```

Every cycle the daemon enumerates every **approved** assignment, calls each component's
`providers(options)`, and unions the keys. Two screens showing Madrid weather are one job.
Two screens showing different tickers are two jobs. A screen that is revoked, removed or
reassigned stops creating its job on the next cycle, with no restart.

This is exactly the lesson `config.feed_data_path` already records: keying the cache by
device produced an empty sky forever, because the subscription is per-**location**, not
per-screen. The job key generalises that to every provider.

### 3.3 Cadence and rate limits

Per job: `max(provider.MIN_INTERVAL_S, operator_override or provider.DEFAULT_INTERVAL_S)`.
Same precedence shape as polling — **operator > provider > floor** — and an operator value
below the floor is **clamped, not ignored**, for the same reason a scene's cadence is: the
failure mode must be "fetches oddly", never "stopped fetching".

Per provider: `MIN_SPACING_S` is enforced as **spacing between requests, not as an
average**. adsb.fi's 1 req/s is the existing case and the reason `check_cadence` rejects a
second radar at the shipped timeouts. Each provider states its own budget; a configuration
that would exceed a documented free tier is refused **at startup with the arithmetic in the
message**, in the style of `check_cadence`, rather than discovered as a 429 at 14:00.

### 3.4 Global defaults vs per-assignment

| Lives globally (`config.yaml` / `/settings`) | Lives on the assignment |
|---|---|
| provider endpoint | which city, which symbols, which calendar |
| provider interval override | units, how many rows, `rotate_s` |
| API keys and secret URLs (§3.6) | everything else the component asks for |
| the house's own `location:` and `secondary_clock:` | |

A blank assignment option resolves to the global default. That is `clock.timezone` today
and it generalises unchanged.

> **One disagreement, stated once.** The owner asked for feed config to be "global but
> changeable per scene". For *credentials and endpoints* that is right and it is what §3.4
> says. For *content parameters* — which city, which tickers — a global default that every
> screen can override is a layer of indirection a three-screen house never cashes in: the
> answer to "why is this screen showing Madrid" becomes two lookups instead of one. My
> recommendation is that content parameters live only on the assignment. I have planned for
> what was asked anyway, because the blank-means-default rule costs nothing and the
> disagreement is about which fields get a global default, not about the mechanism.

### 3.5 Failure

Unchanged from CLAUDE.md §6 and SPEC §11, now applied per job:

- No fetcher may raise into the render path. On failure keep the previous cache and set
  `ok: false`; recording the failure is itself guarded, because a read-only SD card makes
  every write throw.
- An identical repeated failure is **not rewritten** — that is 28,800 fsyncs a day onto the
  microSD during an outage. `cache.write_failure` already does this.
- One job's failure never stops the cycle. The runner continues to the next job.
- `/settings` shows per-job health: provider, params summary (secrets redacted), age,
  last error, in Spanish.
- The daemon exits **78 (EX_CONFIG)** only for faults that will not fix themselves;
  `RestartPreventExitStatus=78` is already wired in both units.

### 3.6 Secrets

Weather (Open-Meteo) needs no key, which is why it is first. Stocks, sports and the
calendar do.

- Secrets live in a store the dashboard writes and **nothing ever reads back over HTTP**.
- A component references a secret **by name** (`{"provider": "twelvedata", "secret":
  "api_key"}`); the runner resolves it and passes it to `fetch`. The name is public, the
  value is not.
- CLAUDE.md's rule stands and gets a test: no response body — `/`, `/device/<hw>`,
  `/settings`, `/api/devices`, `/api/devices/<hw>`, `/api/status`, `/api/config`,
  `/api/device/<hw>/scene` — may contain a stored secret value. Assert it with a sentinel.
- The dashboard shows **presence**, not value: `configurada` / `sin configurar`, an empty
  write-only input, and an explicit "borrar" checkbox. Submitting the form with the field
  blank leaves the stored secret alone, so saving an unrelated setting cannot wipe a key.
- The LAN is unauthenticated by the owner's decision, so anything on it can *overwrite* a
  key. That is the same exposure as reassigning every screen, and it is accepted. Reading
  one back is not, which is why the store is write-only over HTTP.

### 3.7 What ADS-B already proves, and what generalises

`sources/adsb.py` is the reference implementation of most of this and should not be
rewritten to fit the new shape — it should *be* the shape:

| Already right, generalise as-is | Needs to change |
|---|---|
| own daemon, own cadence, never on a device request (C7) | targets come from `config.yaml devices:`, not from assignments |
| envelope `{fetched_at, ok, error, data}` (SPEC §7) | cache is `cache/feed/<feed>.json`, not job-keyed |
| never raises, guarded down to the failure write | params live in `config.yaml` + `overrides.json`, not in options |
| dedup by cache path so N screens are one request | `check_cadence` is hardcoded to one provider's arithmetic |
| loop, not a timer — interval in config, no SD churn | `check_config` knows what an aircraft is |
| startup validation that fails loudly (EX_CONFIG) | |

The 12-second firmware extrapolation horizon and the 1 req/s limit are ADS-B's own numbers
and stay ADS-B's, expressed as `MIN_INTERVAL_S`/`MIN_SPACING_S` plus a provider-specific
startup check.

---

## 4. Prioritised backlog

Ordered so each item lands something on glass or removes a blocker the next item would hit.
Sizes: **S** one focused session, **M** a few, **L** a week or more.

---

### 1. A component reaches a screen without a reflash — **S** — DO THIS FIRST

**Goal.** Adding a component to the Pi makes it available on every round screen
immediately, with no firmware release.

Today `config::kDeclaredComponents = "radar,clock"` and the server drops any component a
device did not declare. The firmware already draws *anything* that ships an instruction
list — `componentKindFromName` returns `kDrawList` for an unknown name — so the declaration
is the only thing standing between us and `weather`. Every one of items 3, 7, 9, 10 and 11
is blocked on a one-word firmware edit until this lands.

**Acceptance criteria**
- Firmware declares `radar,draw_list`; a host test asserts the URL the client builds
  contains it (the existing declared-string test, extended).
- `GET /api/device/<hw>/scene?…&components=radar,draw_list` for a component named
  `weather` returns that component in `components` with **no** `unsupported` entry.
- A device declaring only `radar` still gets `unsupported: ["clock"]` — the widening is
  opt-in, not a hole.
- A device still declaring the old `radar,clock` keeps receiving the clock (back-compat
  during rollout; assert both strings).
- `_scene_options` offers every instruction-list component to a `draw_list` device, and
  still disables `planes` on a device that did not declare `radar`, with the reason shown.
- Every reason string in the component picker is **Spanish** (`"no dibuja píxeles"`,
  `"necesita radar"`), asserted by a test that the picker contains no English.
- `componentKindFromName("weather")` returns `kDrawList` with a draw list present and
  `kUnknown` without one; a scene with an empty list shows a status screen, not a hole.

**Dependencies.** None.

**Why first.** It is the smallest item on this list, it unblocks five others, and it is the
only one that requires physically reflashing hardware — so its lead time is longest and it
should start while the server work that needs it is still being written.

---

### 2. Providers and jobs — **L**

**Goal.** One daemon fetches everything every screen needs, deduped, each on its own
cadence, with no per-component wiring.

**Acceptance criteria**
- `sources.registry()` maps provider name → module; `sources.jobs(assignments)` returns
  deduped `Job(provider, params, key, interval_s, cache_path)`.
- Two assignments with identical params produce **one** job; differing params produce two.
- `key` is stable across process restarts and across dict ordering:
  `job({"lat":1,"lon":2}).key == job({"lon":2,"lat":1}).key`.
- Cache path is `cache/feed/<provider>/<key>.json` and the envelope is byte-compatible with
  today's `read_cache`.
- A provider whose `fetch` raises records `ok: false`, keeps the previous `data`, and the
  runner continues — assert a 3-job cycle where the middle one raises completes all three.
- Two jobs on one provider with `MIN_SPACING_S = 1.0` are issued ≥1.0s apart (injected
  clock, no sleeping in tests).
- Cadence precedence: operator > provider default > `MIN_INTERVAL_S`, with a below-floor
  operator value **clamped**; one test per rung.
- Assignments are re-read every cycle: mutating `devices.json` between injected cycles
  starts fetching the new job without a restart.
- Startup prunes `cache/feed/<provider>/*.json` files with no live job and an mtime older
  than 7 days. (SD hygiene; CLAUDE.md §2.)
- Every provider module imports without `requests`; the existing C7 import-graph guard on
  `serve.py` still passes.
- `adsb` is registered as a provider and the radar keeps working unchanged (its params
  still come from `config.yaml` until item 8).

**Dependencies.** None. Independent of item 1.

---

### 3. Weather — **M**

**Goal.** A screen shows the temperature and today's conditions for a city you set on that
screen's page.

Open-Meteo needs **no API key**, which makes it the honest first proof of the provider
contract — the whole chain works before the secret store exists.

**Acceptance criteria**
- Provider `openmeteo` calls SPEC §7.1's documented URL; `fetch` is unit-tested against
  `tests/fixtures/openmeteo_sample.json` with no network.
- `DEFAULT_INTERVAL_S = 600`, `MIN_INTERVAL_S = 600` (Open-Meteo's free tier is generous;
  the panel is not worth more).
- Component `weather` with options `lat`, `lon` (blank ⇒ `config.yaml location:`), `units`
  (`choice`: métrico/imperial), `show_forecast` (`bool`).
- 240×240: `xl` temperature at `center`, `sm` condition at `below`, `xs` máx/mín at
  `rim_bottom`. 800×480: HTML carrying current conditions plus the 5-day list.
- WMO code → **Spanish** condition text per SPEC §7.1's table. No icons — text only (see
  Q2).
- `poll_s` aims at the next provider refresh boundary; `poll_max_s == 600`.
- With `ok: false` and a previous envelope: last good temperature plus a staleness mark.
  With no envelope: "sin datos". Neither raises.
- `/preview/<hw>/weather.svg` returns 200 with the temperature string in the SVG.
- `/device/<hw>` offers `weather` on a `draw_list` device and on a pixel-push device.

**Dependencies.** Items 1 and 2.

---

### 4. Settings that save, and a secret store — **M**

**Goal.** Change what the fetcher does, and give a provider its API key, from the browser.

`/settings` renders a form that posts to a route registered `GET`-only. The button does not
"do nothing" — it returns **405**. That is the current behaviour and the test should assert
it is gone.

**Acceptance criteria**
- `POST /settings` exists, returns 302, and a test asserts the pre-fix 405 no longer occurs.
- Endpoint and interval persist to a store both daemons read (the `overrides.py` shape,
  extended with a non-device-scoped `feeds`/`providers` section), survive a `serve` restart,
  and reach the fetch daemon within one cycle.
- An invalid value is rejected with a Spanish notice and **nothing is written** — validate
  the resulting config before it lands, as `patch_config` already does.
- Per-provider secret fields: empty write-only input, `configurada`/`sin configurar` pill,
  explicit "borrar" checkbox. Blank submit leaves the stored value untouched.
- The secret file is created mode `0600` and is gitignored.
- **Sentinel test:** with a secret set to a known nonsense string, that string appears in
  none of the eight response bodies listed in §3.6.
- Removing a secret makes its jobs fail with `ok: false` and a Spanish reason on
  `/settings`; no traceback, no blank panel.
- `/settings` lists **jobs**, not devices: provider, params summary with secrets redacted,
  age, last error.

**Dependencies.** Item 2.

---

### 5. The render loop under test — **M**

**Goal.** The three bugs that shipped in a row cannot ship again.

`4eb4624`, `bd55df2` and `84b7462` all reached hardware because host tests call
`renderScene()` directly while the device reaches it through `loop()`'s render policy,
which no test exercises. Item 6 adds a *second* reason to redraw; doing it on an untested
loop is how a fourth one ships.

**Acceptance criteria**
- The body of `loop()` is extracted to a testable step (`ui::tick(LoopState&)` or
  equivalent) and `loop()` becomes the call plus `delay(10)`.
- Regression tests, each failing against the pre-fix code: a static component redraws when
  `contentGeneration` changes; a composited-but-not-blitted frame is retried and not
  recorded as painted; a `px` value reaches `displayFontSetPixelHeight`, never the scale
  setter.
- Every branch of the connected / disconnected / task-retry / portal-changed ladder has a
  test.
- `pio test -e native` passes and the count is reported in the commit, per project custom.

**Dependencies.** None. Best done alongside item 1, which is the other firmware change.

---

### 6. Rotation, and the BOOT button's one meaning — **M**

**Goal.** A component with more than one thing to say cycles through them, and the button
on the back advances it.

**Acceptance criteria**
- `rotate_s` is platform-supplied to any component declaring `ROTATES = True` — one
  definition, one Spanish label, bounds `0` (off) and `3..600`.
- Data push: the scene carries `pages: [[…],[…]]` and `rotate_s`; the device advances from
  its own clock; `poll_s` is unchanged by rotation.
- Pixel push: the server emits page `int(now // rotate_s) % n`, `poll_s` aims at the next
  boundary, `poll_max_s == rotate_s`.
- `rotate_s` below `poll_floor` on a `depth: 1` device is clamped, and the dashboard says
  so in Spanish rather than accepting a number it will not honour.
- One page: no rotation, and `RenderPolicy` is **not** woken — assert `shouldRender` stays
  false on an idle single-page component.
- BOOT **short press advances the page immediately and resets the timer**; BOOT **long
  press still opens the WiFi portal**. Range is no longer on the button.
- `/preview/<hw>/<scene>.svg?page=N` renders page N; the device page shows the strip.
- A firmware test drives page advance through the loop step from item 5, not through
  `renderScene()`.

**Dependencies.** Items 1 and 5.

---

### 7. Stocks and currency — **M**

**Goal.** A screen shows one ticker large, its change below, and rotates through the rest.
This is the owner's worked example and the first component that needs a key.

**Acceptance criteria**
- Component `quotes`, options: `symbols` (text, comma-separated), `provider`
  (`choice`: twelvedata | stooq | coingecko), `currency` (`choice`), `rotate_s`.
- **Five symbols is the hard ceiling on the pixel path** (SPEC §9: the markets band is
  764px split into ~117px cells, and six truncates the symbols). Rotation is what lifts
  the limit on the round screen; the e-paper composition refuses the sixth with a Spanish
  notice rather than rendering a truncated row.
- FX arrows compare against the **previous cached value, persisted in the envelope**
  (SPEC §7.3), not against an official rate — otherwise they point the same way for weeks
  and stop meaning anything.
- One job per (provider, symbol set): two screens with the same symbols dedupe to one job;
  a different set is a second job. Batch into one upstream call where the provider supports
  it (SPEC §7.4's budget note).
- Per page on 240×240: `sm` ticker at `above`, `xl` price at `center`, `sm` percentage at
  `below` with tone `good`/`bad`.
- Outside market hours an equity renders `cerrado`, not a 0,0% arrow (SPEC §7.4). Crypto is
  exempt.
- A configuration exceeding the provider's documented free tier is refused **at startup
  with the arithmetic in the message**.
- Missing key ⇒ job `ok: false` with a Spanish reason, component renders "sin clave", no
  traceback anywhere.
- Tone `bad` on a 1-bit panel resolves to an inverted block, not a missing glyph — assert
  the pixel path.

**Dependencies.** Items 2, 4, 6.

---

### 8. Range and radar parameters become assignment configuration — **M**

**Goal.** Change what the radar shows from the dashboard, not by pressing a button on the
back of the screen or editing `config.yaml`.

**Acceptance criteria**
- `planes` gains options `radius_km` (`choice` over the existing presets), `units`
  (km | mi), `show_ground`, `max_aircraft`.
- The served `radar` component's `radius_km` follows the assignment; the device uses the
  served value and `rangeCurrent()` follows it.
- Changing it in the dashboard changes the ring labels within one poll.
- NVS keeps only what a device needs with no server (WiFi, server URL). The range key is
  removed; an existing stored value is ignored, not migrated into a conflict.
- The ADS-B job's params come from the assignment; the horizon invariant
  (`connect + read + interval < 12s`) is re-asserted **per job** and still refuses a
  configuration that would breach it.
- `overrides.py`'s device-scoped `SETTABLE_KEYS` and the `config.yaml devices:` list are
  retired for the radar, or their retirement is explicitly deferred with a note — two
  places holding the same fact is the bug that produced the empty-sky mismatch.

**Accepted trade.** The design spec chose polar coordinates over screen coordinates
specifically so that changing range stayed "instant and local" rather than a network
round-trip. Moving it to the dashboard makes it a round-trip — bounded by one poll, ~5s on
the radar's cadence. That is the right trade at the owner's instruction: the button now has
one meaning everywhere in the fleet instead of a different one per component, and the
projection code that made polar coordinates worth keeping is untouched either way.

**Dependencies.** Items 2 and 6 (the button's meaning changes in 6).

---

### 9. Calendar — **M**

**Goal.** Today's events on a screen, from a Google Calendar secret ICS address.

**Acceptance criteria**
- The ICS URL is a **secret**, never an option. The component's option is a `choice` over
  the *names* of configured calendar secrets.
- `recurring-ical-events` expands RRULEs (plain `icalendar` does not — CLAUDE.md §6).
- Events for today, sorted by start, capped at `max_events`, with a `+N más` tertiary row
  on overflow. An empty day **collapses**.
- 240×240: next event only — `sm` time at `above`, `md` title truncated at `center`,
  `xs` countdown at `rim_bottom`.
- The ICS URL appears in no response body (the §3.6 sentinel test covers it).
- A malformed calendar yields an empty list and `ok: false`, never an exception.

**Dependencies.** Items 2, 4.

---

### 10. Sport events — **M**

**Goal.** The next few fixtures, text only.

**Acceptance criteria**
- Providers `footballdata` and `thesportsdb` per SPEC §7.5; merged, sorted by kickoff,
  capped at `max_fixtures`.
- Options: `competitions` (`choice` or comma list), `max_fixtures` (`int`), `rotate_s`.
- Text only — no crests, no logos, no remote images (CLAUDE.md: assets are local).
- Times rendered in the assignment's timezone, defaulting to `config.yaml location:`.
- One provider failing still renders the other's fixtures with `ok: false` noted.

**Dependencies.** Items 2, 4, 6.

---

### 11. Claude usage — **S/M**

**Goal.** A screen showing how much of the month's budget is spent.

**Acceptance criteria** (contingent on Q5)
- Provider reads a usage/cost figure with an admin key from the secret store,
  `DEFAULT_INTERVAL_S = 3600`.
- 240×240: `xl` figure at `center`, `sm` period label at `below`, `xs` "actualizado hh:mm"
  at `rim_bottom`; tone `bad` past a configurable threshold.
- The key never reaches a response body.
- A 401/403 renders "sin acceso" and marks the job failed, rather than showing a zero that
  reads as "nothing spent".

**Dependencies.** Items 2, 4, and an answer to Q5.

---

### 12. A scene builder for the 800×480 panel — **L, later**

**Goal.** Compose several components into regions on the e-paper, the way SPEC §9 designs
it, instead of giving 384,000 pixels to one component.

Deferred deliberately: the panel is **not purchased yet** (CLAUDE.md §2) and B2's
legibility gate may change the type scale, which would change what a region is worth. It is
listed so its shape is not accidentally foreclosed by items 1–11 — specifically, the
assignment record must be able to grow from "one component" to "a list of (region,
component, options)" without a migration that touches every device.

**Acceptance criteria** (sketch, to be firmed after B2)
- An assignment on a `render: server` device is a list of placements, and a single-component
  assignment is the one-element case of it.
- `grid` stops being deferred and gets a consumer, or stays deferred and the regions are
  CSS on the Pi — decide with the panel in hand, not before (Q6).

**Dependencies.** Panel hardware; PLAN.md B2.

---

### Not scheduled

**Deliveries (SPEC §7.6).** Gmail parsing of Amazon shipment mail. Stays last, per SPEC §14
and PLAN Phase D: most fragile, least essential, and the only provider whose upstream
changes shape without warning a few times a year. It is a component like any other under
§2 and needs no platform work; it is simply not worth a slot ahead of the eleven above.

---

## 5. Open questions for the owner

Six forks where different answers mean materially different work.

**Q1 — Where do secrets live, and who writes them?**
`config.local.yaml` over SSH (exists today, zero new code, but every key means an SSH
session), or a dashboard-written `cache/secrets.json` at mode 0600 (new write path, and
anything on the LAN can overwrite a key)?
→ **Recommend the dashboard.** The stated point of this project is that a screen is
configured from a web page. A key you can only set over SSH makes half of every new
component an SSH job. The overwrite exposure is the same one already accepted for
reassigning every screen in the house, and reading a key back is separately forbidden.

**Q2 — Does the draw vocabulary grow before weather and stocks ship?**
Text-only (what exists), or add `icon` / `spark` / `arrow` from the design spec's §5.2
table?
→ **Recommend text-only for v1.** Every vocabulary addition costs two implementations, a
golden fixture, and — for icons — a font on both sides. The design spec already predicts
"one revision after the first non-radar scene renders on hardware". Ship weather and quotes
in text, look at them on the round screen, then decide what is actually missing. A `▲`/`▼`
from the existing face covers the delta case at no cost.

**Q3 — Does rotation cycle pages within one component, or also between components?**
"Clock, then weather, then the ticker, on one screen" is a different feature from "three
tickers in one component".
→ **Recommend within-component only for now.** Between-components is a playlist: the
assignment stops being one component with one option set and becomes an ordered list of
them, which changes the record, the form, the preview and the cadence rule all at once.
Item 6's page mechanism is the substrate a playlist would reuse later, so nothing is lost
by waiting.

**Q4 — Do per-assignment options replace `config.yaml devices:` and `overrides.json` for
the radar?**
Migrating retires `/api/config`, the `SETTABLE_KEYS` PATCH API and the device list in
`config.yaml`. Not migrating leaves the radar's parameters in a second place forever.
→ **Recommend migrating** (item 8). Two systems holding the same fact is exactly what
produced the empty-sky cache-path mismatch. But it is a real API removal, and if anything
outside this repo PATCHes `/api/config`, say so now.

**Q5 — What is the source for "Claude usage"?**
The plausible path is the Anthropic Admin API's usage/cost report with an org admin key —
which means an admin key on the Pi, and a figure for the whole organisation rather than for
you personally. Alternatives: a manually maintained number, or nothing.
→ **Recommend the admin usage/cost report**, with the exact endpoint and key type confirmed
before item 11 is scheduled — I have not verified it against current docs. If an admin key
is not acceptable on a LAN box, this component should be dropped rather than faked.

**Q6 — Is the 800×480 panel one component or a composed dashboard?**
SPEC §9 designs it as bands and columns — masthead, clock, weather, calendar, markets. Item
12 assumes that. One component per panel is far simpler and wastes the panel.
→ **Recommend composed**, and therefore recommend that the assignment record be shaped from
the start so a list of placements is not a migration. Answering this now costs nothing;
answering it after eleven items are built costs a data migration across every device.

---

## 6. Non-goals

- **Not a general dashboard builder.** No layout DSL, no user-authored components, no
  drag-and-drop canvas. A component is a Python file someone writes.
- **Not authenticated, and not exposed.** Plain HTTP on the LAN is the owner's decision.
  Nothing here is a step toward a login, a token, a reverse proxy or a public URL.
- **Not a cloud service.** No account, no telemetry leaving the house, no remote config.
  The Pi is the whole backend.
- **Not a browser kiosk.** Devices are HTTP clients that draw; Chromium is a rasteriser
  invoked per frame, not a runtime, and there is no per-device JavaScript.
- **Not a historian.** No time-series database, no retention, no charts over months. The
  cache holds the latest envelope per job and a 7-day prune. The SD card is 15 GB and
  unmitigated for wear.
- **Not animated.** The e-paper is 1-bit with a ~3s full refresh and two render slots for
  the whole fleet. Rotation is page switching, not motion.
- **Not a home-automation controller.** Screens display; they do not actuate. No switches,
  no scenes-that-do-things, no MQTT.
- **Not a fleet product.** 64 devices, one household, one operator, no roles, no audit log,
  no multi-tenancy.
- **No build step and no CDN.** No bundler, no npm, no webfont fetched at boot. The
  dashboard must render when the network is the thing being debugged.
