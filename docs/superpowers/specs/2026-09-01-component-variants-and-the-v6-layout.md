# Component Variants, and Reproducing the v6 Layout

**Status:** Requirements. 2026-09-01. Specifies; does not implement.
**Scope:** How a component declares size-dependent rendering variants and how one is chosen;
what the v6 mockup needs block by block; what data is missing and where it comes from; the
order to build it in.
**Precedence:** Below `CLAUDE.md`, whose invariants are not negotiable here. Extends
[platform requirements](2026-08-27-platform-requirements.md) §2.3 (rendering capabilities),
§9.5 (what the server is not ready for) and §11 G3 (the composed dashboard has no content
designed for it). Where that document defers the *how* of surface adaptation to "the
component's own obligation", this one decides the mechanism.

**The mockup:** `epaper_dashboard_v6_inline_sunrise_sunset_beside_clock.html`, 800x480,
the owner's design. Read as a statement of *intent*, not as a stylesheet — 42 of its 111
`font-size` declarations are below the 10px floor and 71 of its 87 colour uses are greys.
Those are bugs per `CLAUDE.md` §6 and §2.10 below says what replaces them.

**The owner's steer, verbatim:** *"the components should be configurable. To achieve the
layout proposed, they could have different rendering options like widgets in the iphone
screen and the size determines what and how they show up."*

**The steer is right, and this document adopts it.** One `weather` component renders a
temperature in a 117x62 cell and a full column — current conditions, an hourly strip and a
five-day list — at 321x335. Not two components. §1.7 records the one place the analogy
needs correcting.

---

## 0. What this document decides

1. **Variants are declared by naming the entries in the existing `SURFACES` tuple.** No new
   mechanism. `SURFACES` already lists a component's geometries, in order, with a comment
   saying which is which; the comment becomes a `name` and `scenes.supports()` gains a
   sibling `scenes.variant_for()` that returns it. §1.2.
2. **Four conventional tier names — `strip`, `badge`, `card`, `panel` — but the thresholds
   live in the component, not in the platform.** A global pixel table would be wrong,
   because the size at which weather can afford an hourly strip is not the size at which a
   ticker can afford a change percentage. §1.3.
3. **No operator override in v1.** The operator already controls the variant, through the
   controls that exist: which region, how many placements share it, and each placement's
   `weight`. A second control that can contradict the first is a lie waiting to be told.
   §1.4 gives the counter-argument and the condition under which we would add one.
4. **The round 240x240 draw-list path keeps `draw.lines_fit`.** The tier chooses *what* to
   say; `lines_fit` decides how much of it survives the glass. Subordinate, not parallel.
   §1.5.
5. **The v6 layout is 11 placements across the 4 regions of the existing `dashboard`
   template, with no template edit and no new region.** §2.0 gives the plan with measured
   pixels. This is the strongest evidence the steer is right.
6. **Three of the mockup's blocks need data we do not fetch** (hourly + 5-day forecast,
   a dólar blue rate, deliveries), **one of which has no viable source at all** (deliveries).
   §3.
7. **Four things are prerequisites and everything else is a leaf.** §4.

---

## 1. The variant model

### 1.1 What exists today, and why it is nearly right already

Three mechanisms in the tree already do part of this job.

**`SURFACES`** — `homescreen/scenes/<name>.py` declares a tuple of constraint dicts, and
`scenes.supports(name, caps)` answers *fits / does not fit* by trying each against
`surface.fits`. Any one matching is enough. Crucially, **it is already evaluated at the
measured size of an individual slot**, not of the whole panel: `web/views_ui._for_slot`
re-judges every offered component against `slot_caps = {"w": here[2], "h": here[3]}` before
building the picker. The plumbing for a per-slot decision is built and shipped.

And the entries are already variants in everything but name:

```python
# homescreen/scenes/weather.py
SURFACES = ({"min_short": 90},
            {"min_w": 110, "min_h": 40})   # "Madrid 21 deg" in a band cell
```

Two geometries, two presentations, and a comment distinguishing them. `clock`, `quotes`,
`sport`, `claude` and `status` all have the same shape. **The variant already exists in the
code; it exists as a comment.**

**Ad-hoc size branching inside `build()`** — three components already choose a presentation
from measured geometry, each having invented its own rule:

| component | rule | line |
|---|---|---|
| `weather` | `wide_band = w / h >= 4.0` | `weather.py:139` |
| `quotes` | `stacked = len(symbols) > 1 and draw.lines_fit(...)` | `quotes.py:115` |
| `calendar` | drop rows until `draw.lines_fit(lines[:rows], ...)` | `calendar.py:86` |

Three rules, three vocabularies, no name any other part of the system can see. The builder
cannot tell the operator which one a slot will get; no test can assert a component reached a
particular presentation; and a fourth component gets to invent a fourth rule.

**`draw.lines_fit` / `draw.fit` / `_fit_to_glass`** — content-measured reduction for the
draw-list path. `scenes._fit_to_glass` runs `draw.fit` over every text instruction after
`build()`, shrinking then clipping against the real string width. This is *better* than a
tier lookup for what it does, because it measures the actual characters. §1.5.

**What is missing is a name and one function.** Not a mechanism.

### 1.2 The recommendation: extend `SURFACES`, do not add a parallel mechanism

Add an optional `name` to each `SURFACES` entry, and one accessor beside `supports()`:

```python
# homescreen/scenes/weather.py
SURFACES = (
    {"name": "panel", "min_w": 260, "min_h": 260},
    {"name": "card",  "min_short": 120},
    {"name": "strip", "min_h": 34, "min_aspect": 4.0},
    {"name": "badge", "min_w": 100, "min_h": 40},
)
```

```python
def variant_for(name: str, caps) -> str | None:
    """The FIRST declared surface this glass satisfies, by name. None if none."""
```

- `supports(name, caps)` becomes `variant_for(...) is not None`. Semantics unchanged; every
  existing caller keeps working; `_why_not` is untouched.
- An entry with no `name` keeps today's behaviour and gets an implicit one. The migration is
  eight one-line edits, and it forces each author to say out loud which of their entries is
  which — which is the point.
- `SceneContext` gains `variant: str` (and `rows: int`, §1.6). `build()` branches on
  `ctx.variant` instead of on a locally-invented aspect ratio.

**Why extend rather than add.** A second declaration — a `VARIANTS` table beside `SURFACES`
— would immediately have to answer "what if a component declares a variant for a geometry
its SURFACES refuses?", and every answer to that is a bug. One list, read twice, cannot
disagree with itself. This is the same argument §9.5 item 2 makes about tone resolution:
the two surfaces must come from one function or they drift.

**First match wins, and the list is ordered by the author.** Ordering is by *specificity*,
not by size: `strip` carries an aspect constraint and must be tried before `badge`, because
a 764x62 band satisfies both and the wide one is the honest answer.

**The guard against a shadowed entry is a test, not a rule.** Sample a grid of geometries —
the six distinct slot sizes of §2.0 plus 240x240 and a few extremes — run `variant_for`
over every component, and assert **every declared name is reached at least once**. An entry
shadowed by an earlier one is then a failing test at the moment it is written, not a
presentation nobody ever sees. This is cheap, decidable, and it is the only ordering rule
worth writing down.

### 1.3 The tiers, and where their thresholds live

Four conventional names, so the builder and the tests have a shared vocabulary:

| tier | what it means | typical slot |
|---|---|---|
| `strip` | one line along the slot | masthead 800x53; a weather band 764x62 |
| `badge` | a headline and one or two labels, stacked | a ticker cell 117x62 |
| `card` | a headline plus a few rows | an agenda 417x104; a fixture list 417x50 |
| `panel` | the full treatment: headline, secondary block, list | weather 321x335 |

**The threshold numbers belong to the component, not to the platform.** This is the one
substantive design decision in §1 and it deserves its reason.

A global table — "`panel` above 260x260, `card` above 120 short" — is a statement about
glass. But the question a variant answers is *how much of this content is worth showing
here*, and that is a statement about content. `weather` at 321x335 can afford six hourly
cells and five daily rows because a weather reading decomposes into rows of 20px.
`quotes` at 117x62 can afford three lines because a symbol, a price and a change are 10, 14
and 10 pixels tall — it needs no `panel` tier at all, and inventing one for it would be a
tier no geometry in this product ever reaches. `planes` declares `min_short: 160` because a
radar needs room for rings, and that number has nothing to do with text.

`surface.fits` already carries the whole vocabulary needed to say any of this —
`min_w`, `min_h`, `min_short`, `min_aspect`, `max_aspect`, `shape` — and it is already
tested. Nothing new is required.

**Counter-argument, and it is a real one.** Per-component thresholds mean eight components
can disagree about what "card" means, and a reader of the builder's slot map cannot predict
what a name will produce. Mitigated two ways: the four names are fixed by the platform so
nobody invents a fifth; and the reachability test of §1.2 plus the density ceilings of the
existing §12 C1 pin what each tier is allowed to emit per surface. If those two prove
insufficient in practice, the fix is to publish *recommended* thresholds as constants in
`_style.py` that components import — advisory, still overridable. Do not start there.

**The one platform-wide threshold that should exist** is the floor: below
`surface.MIN_REGION_PX` (24) nothing is offered at all, which `layout.templates_for`
already enforces.

### 1.4 How one is chosen: measured, with no operator override in v1

**Recommendation: purely by measured slot size. No `size` option.**

The reason is that the operator already has the control, and it is a better one. A
placement's variant is a function of its rectangle; its rectangle is a function of which
region it sits in, how many placements share that region, and its `weight`. All three are
already in the builder, and `weight` is already a per-placement number the operator types.
Asking for a richer weather block *is* asking for more of the column, and the existing
control says exactly that. A `size` option would be a second dial that can contradict the
first — an operator selecting `panel` in a 117x62 cell has asked for something that cannot
be drawn, and both possible responses (draw it truncated, or silently draw something else)
break the promise the builder made when it showed them the option.

**The strongest counter-argument.** There are slots where measurement is genuinely
ambiguous and taste should decide. `417x104` can honestly be a `card` (four agenda rows) or
a `strip` (one large next-event, the way a small screen answers). Both are defensible
designs and no threshold can know which the owner wants. Under this recommendation the only
way to express the second is to shrink the slot, which also moves everything below it.

**The resolution.** Ship `auto`-only. Add the override **only if E0's screenshot loop
produces a specific slot where measurement picks wrong** — and add it then as
`{"key": "variant", "type": "choice", "choices": ("auto", *declared_names)}`, using
`clean_options`' existing `choice` handling, with the builder **refusing** a variant that
does not fit the measured slot at save time (the same `fits`-per-slot call
`views_ui._for_slot` already makes) rather than downgrading it at render time. One truth:
the render always draws what the builder promised.

### 1.5 The round panel, the draw list, and `lines_fit`

**They are not in competition, and neither should be removed.**

- `variant_for` decides **what a component says**: whether weather mentions a five-day
  forecast at all.
- `draw.lines_fit` / `draw.fit` decide **how much of what was said survives the glass**:
  whether `BINANCE:BTCUSDT 63,120` fits the chord of a 240px circle at `sm`.

The second is content-measured and the first is not, which is exactly why both are needed
and why neither can replace the other. A tier cannot know that one team is called `AVL` and
another `Borussia Mönchengladbach`; a character count cannot know that a five-day list is a
different design from a temperature.

**Concretely:**

- `variant_for` is computed for **both** paths from the same `ctx.caps`, so a component may
  read `ctx.variant` when building instructions as well as HTML. On the 240x240 round panel
  the variant is `card`; on a hypothetical 128x64 OLED it would be `strip`, and the
  component gets that for free.
- `scenes._fit_to_glass` stays exactly as it is, and stays the last thing that runs on the
  draw list. It is the backstop.
- **Do not build a tier ladder for the round panel's five slots.** It has one region at one
  size; a tier lookup there resolves to a constant and adds a place to be wrong.
- `calendar.py`'s row-dropping loop (`while rows > 1 and not draw.lines_fit(...)`) is the
  correct pattern and should be kept, not replaced by `ctx.rows`. `ctx.rows` is the *budget*;
  `lines_fit` is the *check*.

### 1.6 The platform changes this requires — the whole list

Small, and all of it in three files.

1. `scenes/__init__.py`: optional `name` on `SURFACES` entries; `variant_for(name, caps)`;
   `supports()` reimplemented on top of it; `SceneContext.variant` and `SceneContext.rows`.
2. `scenes/_style.py`: `rows(width, height)` — the number of body rows at `metrics()['row']`
   that fit the rectangle — so a component writes `days[:ctx.rows]` rather than `days[:5]`.
   **This is what makes the mockup reflow when the operator changes a weight**, and it is the
   single most valuable line in this section. Note it counts rows at `fs`; a `badge` packing
   10px lines gets more than `ctx.rows` and is right to.
3. `compose.py`: **subtract the section heading's height before calling `build_scene`.** See
   §5 D1 — today a component with a `label` is told it has 121px and is then rendered into
   104px. Nothing else in this document works reliably until that is fixed.
4. `web/views_ui.py`: show the chosen variant name beside each slot in the picker, and
   measure `slot_caps` with the stored `weight` rather than an even share (§5 D2).

### 1.7 Where the owner's steer needs correcting

The steer is right about the mechanism and slightly wrong about the ladder.

**iOS widgets live on a uniform grid.** Small is 2x2, medium 4x2, large 4x4 — every size is
within a factor of two of square, so one linear small/medium/large ladder describes them.
**Our slots span 15:1 to 0.96:1.** The masthead is 800x53; the weather column is 321x335.
Between those two, *aspect decides the presentation and area decides the amount*, and they
are independent: a 764x62 band and a 117x62 cell have the same height and want completely
different designs — one line along the band, three stacked lines in the cell.

`weather.py` already knows this and branches on `w/h >= 4.0`, not on area. So the tier names
in §1.3 are **not** a size ladder; `strip` is not "smaller than `badge`". They are named
shapes, and the ordering rule in §1.2 is specificity rather than size for exactly that
reason.

This is a correction to the analogy, not to the idea. Everything else in the steer holds,
and §2.0 is the proof.

---

## 2. The gap list, block by block

### 2.0 First: the layout fits the template we already have

Eleven placements, four regions, the `dashboard` template unedited. Measured, not estimated
— `layout.regions` and `layout.slots` on 800x480:

| region | placement | component | weight | measured | `metrics` fs / row / hero | body rows |
|---|---|---|---|---|---|---|
| masthead | 1 of 1 | `masthead` **(new)** | — | 800x53 | 10 / 16 / 26 | 2 |
| main_left | 1 of 5 | `clock` | 1.85 | 417x78 | 13 / 20 / 37 | 3 |
| main_left | 2 of 5 | `calendar` "AGENDA" | 2.90 | 417x121 → 104 | 13 / 20 / 56 | 4 |
| main_left | 3 of 5 | *deliveries* "ENTREGAS" | 1.65 | 417x69 → 52 | 13 / 20 / 34 | 2 |
| main_left | 4 of 5 | `sport` "DEPORTES" | 1.60 | 417x67 → 50 | 13 / 20 / 32 | 2 |
| main_right | 1 of 3 | `weather` | — | 321x335 | 13 / 20 / 56 | 14 |
| markets | 1 of 6 | `fx` **(new)** "BLUE" | 1.55 | 181x62 | 12 / 19 / 30 | 2 |
| markets | 2–6 of 6 | `quotes` x5 | 1 each | 117x62 (x3), 116x62 (x2) | 12 / 19 / 30 | 2 |

("→" is the height after the composer's section heading, §5 D1. `markets`' 1.55/1/1/1/1/1 is
already the template's own weight tuple, taken from SPEC §9.)

**Three things this proves.**

1. **No new region, no new template, no horizontal sub-split.** The mockup's clock row puts
   three things side by side inside one 417x78 block — and that is one *component's* internal
   arrangement, not a layout-engine feature. `main_left` stacking vertically is not a
   limitation here; it is the correct division of labour.
2. **The variant model is load-bearing, not decorative.** `weather` at 321x335 and `weather`
   at 117x62 are the same component with the same options. Without variants the mockup needs
   a `weather_column` component and a `weather_cell` component, and then a third the first
   time a slot lands between them.
3. **The 10px floor costs about 12–20px of the left column's 335px and it still fits.** The
   mockup's 8px labels become 10 and its 9px rows become 10–11; three label rows and three
   small rows at +2px each is ~12px. It fits. **A fifth block in `main_left` does not.**

### 2.1 Masthead — sun glyph, "Lunes 24 de agosto", "actualizado 14:32"

**Verdict: needs a new component.** Already recorded as §9.5 item 5 and G3 — the `dashboard`
template declares a `masthead` region and nothing in `scenes._registry()` fits it, so the top
11% of a composed frame is empty today.

- The date is local; no fetch. Variant `strip` and nothing else.
- **"actualizado 14:32" needs a platform addition.** `CLAUDE.md` §6: *"the masthead timestamp
  reflects the oldest successful fetch, so a silently dead fetcher is visible."* No component
  can compute that — `ctx.data` reads one requirement at a time and only the ones the
  component itself declared. Add `SceneContext.oldest_fetch: float | None`, populated by
  whoever builds the scene from the job store. Small, and it is the difference between a
  masthead that is decorative and one that does the job `CLAUDE.md` assigns it.
- The mockup's `actualizado` is 9px `#6b6b6b`: becomes 10px `#000`.
- The leading sun glyph is a Tabler CDN font (§2.10).

### 2.2 Clock, with sunrise and sunset inline

**Verdict: variant + an option + data we already fetch and discard.**

- Two cities with labels: **already possible.** `clock.py` does it, `metrics` gives 37px hero
  and ~20px sub at 417x78 (the mockup's 48/26 assumed a taller row; the weights are the dial).
- "mañana · −5h" under the second city: **already possible.** Both are pure `zoneinfo`
  arithmetic on the secondary clock's local hour. No data.
- **Sunrise and sunset: the data is already in the response and thrown away.**
  `openweather.fetch` reads `body["main"]` and `body["weather"]` and never touches
  `body["sys"]`, which carries `sunrise` and `sunset` as unix timestamps. Normalising two more
  fields is the cheapest item in this entire document.
- **Who draws them is the real question, and the mockup answers it.** They sit *beside the
  clock*, in the clock's row — one visual object with a shared baseline. `main_left` stacks
  vertically, so they cannot be a second placement.
  - **Recommend: `clock` gains `show_sun` (default off) and declares an `openweather`
    requirement only when it is on.** `needs()` is already a function of options
    (`scenes.needs` §2.4), so this costs nothing when off.
  - **The objection, taken seriously:** the clock is currently the only component that works
    with the network down, and this gives it a data dependency. It survives — `needs()`
    returns `()` when `show_sun` is off, and with it on and the fetch failed the row collapses
    per `CLAUDE.md`'s collapse rule while the clock still renders. The dependency is optional
    and its failure mode is designed.
  - **One honesty rule.** Sun times come from a lat/lon; the clock's `timezone` is a separate
    option. A clock set to `America/Argentina/Buenos_Aires` with `show_sun` on must **hide the
    row** unless lat/lon are also set, rather than showing Madrid's sunrise beside Buenos
    Aires' time.
- The decorative thermometer between the two clocks is `#e8e8e8` on `#c8c8c8`: **cannot be
  done as drawn.** Drop it, or replace it with a 1px black vertical rule.

### 2.3 AGENDA — four rows

**Verdict: needs a variant. No new data.**

`calendar.py` has the data (`ics` provider, `_when` already relativises to hoy/mañana/weekday)
and emits a placeholder: an 8-row `<table>` at `var(--lg)` = 16px with a `9em` time column.
The `card` variant is the design work — a fixed ~34px time column, one row per event, count
from `ctx.rows` (4 at 417x104), the fourth row smaller for tomorrow.

- The **"28 min" inverted pill** is derived from the event time and `ctx.now`. No data.
  But note the budget: §12 C5 allows **two `.pill` per page** and the mockup has exactly two
  (this and ENTREGAS' "hoy"). One of them is in a block that has no data source (§2.4), so
  in practice v1 has one. Do not add a third.
- The 9px "mañ / 09:00 · Dentista" row becomes 10px.

### 2.4 ENTREGAS — two rows

**Verdict: cannot be done as designed. No viable source.** Detail and the recommendation in
§3.3. This is `SPEC` §7.6 / G10, unscheduled since the first plan, and it should stay
unscheduled rather than be faked.

The slot itself is not wasted: §3.3 recommends what to put in it instead, and the `weight`
arithmetic in §2.0 works either way because a region divides among the placements it
actually carries.

### 2.5 DEPORTES — three fixtures instead of one

**Two separate gaps, and only one of them is about data.**

**(a) Three rows from one team: needs a variant. No new data.** `football.fetch` already
returns up to `MAX_MATCHES = 10` normalised matches. `sport._pick` deliberately reduces them
to one — *"a live match beats everything, a fixture beats a result you have already seen"* —
which is the right answer for a 240px circle and the wrong one for a 417x50 card. Add
`_next_n` beside `_pick`; `card` lists `ctx.rows` fixtures, `badge` and `strip` keep `_pick`.
The HTML is a placeholder today (`<div class="big">Home — Away</div>`, no time, no score, no
competition — all three already computed for the draw list, §9.5 item 7).

**(b) The mockup's actual three rows need three sources.** "Argentina — Brasil",
"River — Boca" and "F1 · GP Monza" are a national team, an Argentine league fixture and a
motorsport event. See §3.4 for what is reachable.

**(c) A placement takes one `team`.** Three rows from three teams needs either three
placements (which would put six blocks in a region that `holds: 5`) or a comma-separated
`teams` option, `needs()` returning N requirements and the component merging across readings
— exactly the shape `quotes.symbols_of` already has. **Recommend the comma list**, for
consistency with `quotes` and because it keeps the section a single visual object with one
heading.

**Dependency:** the existing spec §10.3 already requires normalising the match envelope
(`scheduled`/`live`/`finished`/`postponed`, no vendor enum in `scenes/`) and §10.6 sequences
it as "12a, ships alone and invisibly, do this even if basketball never happens". The `card`
variant reads `status`, so **12a lands first**.

### 2.6 Weather column — current, hourly strip, five-day list

The clearest case in the mockup, and the one that most vindicates the steer: **one
placement, one options set, at 321x335, rendering all three sub-blocks.**

| element | verdict |
|---|---|
| 34px temperature + sky icon | already possible; needs the `panel` variant (today: four stacked divs) |
| "Despejado" | already possible (`description`) |
| "viento 12" | **needs new data (trivial)** — `wind.speed` is in the raw response and discarded, exactly like `sys.sunrise` |
| "UV 7" | **needs new data (not trivial)** — not in `/data/2.5/weather` at all. §3.1 |
| hourly strip, 6 cells | **needs new data** — a forecast endpoint. §3.1 |
| five-day list with min/max | **needs new data** — same endpoint |
| precipitation "70%" | **needs new data** — `pop`, same endpoint |

`ctx.rows` = 14 at 321x335, which is what lets the five-day list become four days if the
operator puts something else in `main_right`, without a code change.

### 2.7 The FX "BLUE" box — USD and EUR rows

**Verdict: needs a new component AND new data.**

- **The component does not exist.** This is G2, *"the cheapest outstanding item in the
  project"*: `homescreen/fetch/providers/fx.py` is complete, keyless, ECB-backed and tested,
  and **no component declares `{"provider": "fx"}`** — the string `"fx"` appears only inside
  its own module. A `fx` component at `badge` (181x62, two rows) wires it up.
- **But Frankfurter cannot produce the number in the mockup.** "Dólar blue" is an Argentine
  parallel-market rate. It is not an ECB reference rate and no amount of configuration of
  `fx.py` will yield it. §3.2.
- **The arrow and the percentage may not be obtainable.** §3.2; if the source publishes no
  previous close, recommend showing the published **compra/venta spread** instead of
  inventing a delta. `store.save` overwrites, so the job store remembers nothing by design,
  and adding a history side-file for one arrow is not worth it.

### 2.8 The five ticker cells

**Verdict: needs a variant. No new data.** `quotes` has price, previous close and
`change_pct` normalised; the HTML is a placeholder. `badge` at 117x62 is three lines —
symbol 10/500, price ~14/500, arrow + change 10 — which is 34px of type in a 62px cell and
comfortable. Note `ctx.rows` reports 2 here because it counts *body* rows at 12px; a badge
packing 10px lines correctly exceeds it (§1.6).

Two small things: `$94,2k` needs a compact formatter, and **VWCE is a European ETF —
confirm Finnhub's free tier returns it** before promising that cell. Everything else in the
band (AAPL, NVDA, MELI, BTC via `BINANCE:BTCUSDT`) is known-good.

### 2.9 Summary table

| block | verdict |
|---|---|
| masthead date + updated stamp | **new component** + `SceneContext.oldest_fetch` |
| Madrid / BS AS clocks, "mañana · −5h" | already possible + variant |
| sunrise / sunset inline | **variant + option**; data already fetched and discarded |
| AGENDA, 4 rows, countdown pill | **variant** |
| ENTREGAS | **cannot be done — no source** (§3.4) |
| DEPORTES, 3 rows from one team | **variant** (+ envelope normalisation first) |
| DEPORTES, the mockup's 3 actual sources | **new data** (§3.4) |
| weather current conditions | **variant** |
| wind | **new data (trivial)** |
| UV index | **new data** (§3.1) |
| hourly strip | **new data** (§3.1) |
| five-day list + precipitation | **new data** (§3.1) |
| FX BLUE box | **new component + new data** (§3.2) |
| 5 ticker cells | **variant** |
| all greys, all sub-10px type, all icons | **cross-cutting**, §2.10 |

### 2.10 Cross-cutting: what the mockup cannot keep

Three things run through every block and are worth one decision each rather than fourteen.

**Greys.** The mockup uses eleven non-black, non-white values; 71 of its 87 colour uses are
grey. `CLAUDE.md` §6 is explicit that these are bugs and that hierarchy comes from size and
weight only. The consequential part is the **rules**: the mockup has four rule weights
(`#000`, `#a8a8a8`, `#d4d4d4`, `#ececec`) and we have one.

> **Recommendation: a 1px black rule only where the mockup used `#000` or `#a8a8a8` — the
> structural divisions (masthead underline, column divider, markets top border, and the
> section headings `compose` already draws). No rule at all where the mockup used `#d4d4d4`
> or `#ececec` — the per-row separators. Use whitespace.**
>
> Reason: thirty 1px black hairlines on a 1-bit panel read as a grid rather than as a list,
> and every one of them is ghosting surface that a partial refresh has to keep repainting.
> **Counter-argument:** without row rules a dense four-row agenda is harder to scan.
> Mitigated by the fixed-width time column, which gives the eye the same vertical edge a
> rule would — which is why the mockup's own agenda works even at `#ececec`.

**Sub-10px type.** 42 of 111 declarations (26 at 9px, 16 at 8px) are below the floor. They
all become 10px. `_style.metrics` already refuses to emit anything smaller and
`tests/test_style_metrics.py` pins it, so this is not a new risk — but it does compress the
mockup's five sizes (8/9/10/11/13) into three (10/11/13), and §2.0 shows the left column
absorbs it.

**Icons.** The mockup uses **24 Tabler glyphs across 9 names** (`ti-sun`, `ti-cloud`,
`ti-cloud-rain`, `ti-moon`, `ti-sunrise`, `ti-sunset`, `ti-package`, `ti-arrow-up-right`,
`ti-arrow-down-right`) via a **CDN icon font**, which `CLAUDE.md` §6 forbids outright:
*"Never a CDN web font — a slow network at boot silently swaps in a fallback face and the
layout breaks."* An icon font is the worst case of that rule, because the fallback is a
blank box rather than a different letterform.

> **Recommendation: one module producing both an inline `<svg>` and a `draw` shape list from
> the same geometry**, extending `draw.ICONS` (which already has `_sun`, `_cloud`, `_rain`,
> `_snow`, `_storm`, `_up`, `_down` for the round panel) rather than drawing the set twice.
> `sunrise`, `sunset`, `package` and `moon` are the four new shapes. This is the same
> argument §9.5 item 2 makes for tones, and it is a **prerequisite** (§4) because the
> masthead, the weather panel, the FX box and the ticker badges all need it.

---
## 3. Data

Verified against the live services on 2026-09-01. Where something is unverified it says so.

### 3.1 Weather: the hourly strip and the five-day list

**What we discard today.** `openweather.fetch` reads `body["main"]` and `body["weather"]` and
normalises eight fields. The raw `/data/2.5/weather` response also carries
**`sys.sunrise`** and **`sys.sunset`** ("Sunrise time, unix, UTC" / "Sunset time, unix,
UTC") and **`wind.speed`** — all three documented, all three already arriving, all three
thrown away. Adding them is the cheapest item in this document and it delivers §2.2's whole
block.

**What the current endpoint cannot give, at any price.** `/data/2.5/weather` is current
conditions only: no hourly, no daily, no UV.

**The options, measured:**

| | OpenWeather `/data/2.5/forecast` | OpenWeather One Call 3.0 / 4.0 | **Open-Meteo** |
|---|---|---|---|
| key | **same key as today** | **separate subscription** | **none** |
| payment | none | a billing form, post-paid per call | none |
| free allowance | 60/min, 1,000,000/month | 1,000 calls/**day**, then ~£0.0012/call | 600/min, 10,000/day, 300,000/month |
| hourly granularity | **3-hourly**, 5 days | true hourly, 48h | **true hourly**, 168 entries / 7 days |
| **daily min/max** | **not available — see below** | 8 days | 7 days, direct and genuine |
| precipitation probability | `list[].pop`, a **0–1 fraction** | yes | `precipitation_probability`, % |
| UV index | **no** | yes | yes (`uv_index`) |
| sunrise / sunset | `city.sunrise/sunset` (unix) | yes | daily `sunrise`/`sunset`, **local ISO strings** |
| calls for the mockup's column | **2 jobs** (current + forecast) | 1 | **1 job** |

Verified 2026-09-01 against the live services and the vendors' own documentation. Two things
are **unconfirmed** and are not relied on above: the default entry count of
`/data/2.5/forecast` (widely observed as 40 = 5 x 8, but the docs state only that `cnt`
limits it), and whether One Call's billing form strictly requires a card — OpenWeather never
says so verbatim, though the model is post-paid per call. Note also that **One Call 3.0 is
now superseded by 4.0** on the same subscription model, so building on 3.0 would start on a
product the vendor already steers new work away from. Separately, OpenWeather has
**deprecated its built-in geocoder** (`q=`, `zip=`, `id=`); `openweather.py` already passes
`lat`/`lon`, so it is on the surviving path.

**Recommendation: add `openmeteo` as a provider and make it the default for weather.**

Four reasons, in order of weight.

1. **The five-day list cannot be built honestly from OpenWeather's free tier.** This is the
   decisive one, and it is easy to miss. `/data/2.5/forecast` *has* `main.temp_min` and
   `main.temp_max`, but they are **not daily highs and lows** — the docs describe them as the
   min/max across the city's geographic extent *at that moment*, and state that "in most
   cases both parameters have the same volume as `temp`". Producing the mockup's "33° / 19°"
   from that endpoint means aggregating eight 3-hour steps per day ourselves, in the adapter,
   for five days — arithmetic that Open-Meteo simply answers. Reading those two fields as
   daily extremes would put a wrong number on the glass that looks completely plausible,
   which is the worst failure mode this project has.
2. **The mockup's hourly strip is unreachable on OpenWeather's free tier.** It shows
   15h/16h/17h/18h/19h/20h. `/data/2.5/forecast` steps in three hours, so the honest strip
   there is 15h/18h/21h/00h/03h/06h — a different design. Reproducing the design as drawn
   means a One Call subscription and a billing form for a desk ornament.
3. **"UV 7" does not exist on the free OpenWeather path at all.** The old `/data/2.5/uvi`
   is retired; UV is a One Call field.
4. **Keyless, and the tree has already decided it prefers that.** Spec §10.5, on the
   basketball providers: *"`SECRETS = ()`. Both feeds are keyless, which means no `sin
   clave` state, no free-tier arithmetic and no §3.6 surface at all."* The same argument
   applies unchanged. It also collapses two jobs into one — one cache file, one cadence,
   one failure mode.

**The strongest counter-argument.** `openweather.py` is written, tested and working, and a
second weather provider is a second envelope that must agree with the first or
`weather.build` grows a branch per vendor. Open-Meteo also uses **WMO codes** rather than
OpenWeather icon codes (so `weather._SKY` needs a sibling), returns **columnar parallel
arrays** rather than an array of objects, and its free tier is explicitly non-commercial
**with no uptime guarantee**.

**That counter-argument is answered by doing the normalisation properly, and it is a
prerequisite rather than an afterthought** — the same move spec §10.3 makes for sport
("normalise the match envelope FIRST"). `openweather.fetch`'s own docstring already
promises it: *"Normalised here, not at draw time... so swapping the provider does not touch
the component."* Today that promise is kept for a flat current reading and would break the
moment a second shape arrives.

**The envelope both adapters must emit:**

```
{
  "place": "Madrid", "units": "metric",
  "current": {"temp", "feels_like", "description", "icon", "wind", "humidity", "uv"},
  "sun":     {"sunrise": <iso8601 local>, "sunset": <iso8601 local>},
  "hourly":  [{"at": <iso>, "temp", "icon", "pop"}, ...],
  "daily":   [{"day": <iso>, "icon", "temp_min", "temp_max", "pop"}, ...],
}
```

Three rules about it, each with a reason:

- **`icon` is OUR vocabulary (`sun|cloud|rain|snow|storm`), never the vendor's code.**
  `weather._SKY` moves out of `scenes/weather.py` and into each adapter. Otherwise a second
  vendor means a second mapping table in the component, which is exactly the thing the
  provider port exists to prevent. The reduced vocabulary is already the set
  `draw.ICONS` can draw, so nothing is lost.
- **`sun` times are ISO local strings, not unix.** Open-Meteo's native form; the
  OpenWeather adapter converts. Chosen this way round because the component wants a wall
  clock, and a unix stamp needs a timezone the component would have to guess.
- **The adapter trims before it caches.** `forecast_days=6` and hourly clipped to the next
  ~24 entries. Open-Meteo's default is 168 hourly entries and this cache lives on a microSD
  whose wear `CLAUDE.md` §2 already flags as live and unmitigated.

**Cadence.** `DEFAULT_INTERVAL_S = 600` as today. 10,000 calls/day against one call every
ten minutes is 144 — three orders of magnitude of headroom, for however many screens,
because the job model's dedup by `(provider, params)` makes it one fetch regardless.

**Keep `openweather.py`.** It works, it is tested, and a second adapter against the shared
envelope is what proves the envelope is real rather than a description of one vendor.

### 3.2 The FX "blue" box

**Plainly: "dólar blue" is not an ECB reference rate, and `fx.py` cannot produce it.**
Frankfurter serves the ECB's daily fixing. The blue is Argentina's parallel-market quote,
published by newspapers rather than by a central bank. No configuration of the existing
provider reaches it.

**Keyless sources exist. Two, both fetched live today, both usable, neither official.**

| | dolarapi.com | **api.bluelytics.com.ar** |
|---|---|---|
| key / signup | none | none |
| USD blue | `/v1/dolares/blue` → `compra` 1535, `venta` 1555, `fechaActualizacion` | `/v2/latest` → `blue.value_buy` 1522, `value_sell` 1555, `value_avg` 1538.5 |
| **EUR blue** | **no** — `/v1/cotizaciones/eur` is `casa: "oficial"`; `/v1/euros/blue` 404s | **yes** — `blue_euro` |
| mid rate | no | `value_avg` |
| day change % | **no** | **no** |
| history | via `api.argentinadatos.com` (same maintainer) | `/v2/evolution.json`, 2011→today, **USD only, no euro** |
| run by | Enzo Notario, scrapes DolarHoy | Pablo Seibelt |
| rate limit / SLA | **none documented** | **none found** |

**Recommendation: `bluelytics`, because it is the only one with a EUR blue rate**, and the
mockup's box has a EUR row. dolarapi is the documented alternative if only USD is wanted.

**On the arrow and the percentage: neither source publishes a change, and we should not
invent one.** `store.save` overwrites, so the job store deliberately remembers nothing, and
adding a history side-file to produce one arrow is not worth the wear. A EUR delta is not
obtainable at all — Bluelytics' `evolution.json` carries no euro series.

> **Recommend showing the published compra/venta spread instead of a delta.** For a
> parallel rate the spread is genuinely informative — it is the number that widens when the
> market is stressed — whereas a fabricated arrow is a claim we cannot support.
> **Counter-argument:** the arrow is the only thing in that box readable from across the
> room, and a spread is two more numbers in a 181x62 cell. If the arrow must exist, the
> honest version is USD-only, computed from `evolution.json`, with the EUR row simply not
> having one — an asymmetry that will look like a bug.

**Trade-offs to state out loud before this ships.** Both are one-maintainer community
services behind Cloudflare, with no documented rate limit, no SLA and no stability
guarantee. The underlying quote is scraped from newspapers and **does not update on
weekends or Argentine holidays**, which interacts badly with `CLAUDE.md`'s *"mark with a
tertiary `·` past 1 hour"* rule — the dot would be lit all weekend, every weekend, and a
staleness marker that is always on is a staleness marker nobody reads. **Mark staleness
against the payload's own `last_update` and the source's publishing rhythm, not against a
flat hour.** `DEFAULT_INTERVAL_S = 1800`.

### 3.3 ENTREGAS: there is no viable source, and we should not pretend otherwise

**Two independent blockers, either of which is fatal.**

1. **Discovery is impossible.** Every parcel API in existence takes a tracking number as
   *input*. Nothing enumerates "parcels addressed to this person". The mockup's block
   assumes a list that no API can produce.
2. **Amazon has no consumer API.** SP-API is for sellers with a Professional selling
   account. Login with Amazon offers `profile`, `profile:user_id` and `postal_code` — there
   is no orders or purchase-history scope. Only scraping, a manual GDPR export, or email
   parsing.

**And the carriers are contract-gated.** Correos' official Trackpub API needs a Correos ID
*and a signed transport contract*; SEUR needs CIT/CCC/NIF from a franchise; MRW needs an
issued subscriber code. GLS Spain's formerly-open endpoint now redirects to a registration
page. (One undocumented Correos backend does answer keyless — and it still needs the
tracking number, so blocker 1 stands.)

**Aggregator free tiers do not include API access.** AfterShip's free plan (50 shipments/mo)
and TrackingMore's (50/mo) both **exclude the API** — it starts at their paid tiers.
17TRACK's recurring free allowance ended in January 2026, leaving a **one-time 200 calls**.
*Unverified:* exact AfterShip/17TRACK price points and whether TrackingMore Basic includes
the API; sources contradict and both block automated fetches.

**Recommendation: do not build a deliveries provider.** Three alternatives for the slot,
ranked by cost:

1. **A second `calendar` placement pointed at an "Entregas" calendar. Zero new code.**
   `calendar` already takes a per-placement `url`; the owner adds a dated entry when they
   order something. It reuses the component, the provider, the variant and the tests, and
   it is the only option that ships today.
2. **Give the slot away.** Four rows to AGENDA and three to DEPORTES instead of two blocks
   of two. §2.0's weights already accommodate it and the layout is better for it.
3. **A manual `list` component** — items typed into the placement's options. ~60 lines, no
   provider, no network. Honest, but it is a to-do list the owner has to maintain, which is
   option 1 with worse ergonomics.

**Explicitly rejected for now: Gmail/IMAP parsing.** It is the architecture that would
actually work (parse shipping mail for tracking numbers, then resolve them), and it is
disproportionate: Google OAuth infrastructure that this LAN-only, unauthenticated,
no-account backend does not have and does not otherwise want, for four lines of text. It is
also the most fragile upstream in the project, which is SPEC §14's own stated reason for
putting it last. Recommend it stays last.

### 3.4 Three sports fixtures: what is actually reachable

The mockup's three rows come from three different places and they do not cost the same.

| row | source | verdict |
|---|---|---|
| "River — Boca" | football-data.org **`ASL` Liga Profesional** | **`plan: TIER_TWO` — €49/month.** Not free. |
| "Argentina — Brasil" | a national-team fixture | free tier covers `WC` and `EC` only; qualifiers and friendlies are not in it |
| "F1 · GP Monza" | **jolpica-f1** | **free and keyless**, confirmed live |

**football-data.org's free tier is 12 competitions**, read from the live `plan` field rather
than from the coverage table: `PL`, `BL1`, `SA`, `PD`, `FL1`, `PPL`, `DED`, `ELC`, `CL`,
`EC`, `WC`, and **`BSA` Campeonato Brasileiro Série A**. Brazil is free; **Argentina is
not**. (The coverage page puts its Odds and Stats columns *before* the tier columns, so
counting checkmarks left to right makes Argentina look free. It is not.)

Also confirmed: **a free token is required** — anonymous requests get 403 on everything past
the competition list — and the free limit is **10 calls/minute**, with remaining quota in a
hyphenated `X-Requests-Available` header the docs spell without the hyphen.

**Recommendation.**

- **Build the `card` variant against the teams that are reachable** (§2.5a). It is a
  variant, it needs no new data, and it is the part that makes the block look like the
  mockup.
- **Add `f1` as a keyless provider.** It fits spec §10.2's settled shape — one `sport`
  component, several providers — costs no key and no quota, and it is the one row of the
  three that is free. `https://api.jolpi.ca/ergast/f1/current/next.json`; **4 req/s burst,
  500 req/hour**; envelope is Ergast-identical (`MRData.RaceTable.Races[]`).
  Two things that will otherwise be discovered at 14:00 as an outage, and must carry their
  reason in a comment the way §10.5 requires of the NBA adapter's headers:
  **jolpica requires a custom `User-Agent`** of the form `HomeScreen/1.0`, and **the base
  URL belongs behind one constant** — a non-Ergast-compatible v1 is planned but unscheduled,
  and only the `/ergast/f1/` prefix answers today.
  **Do not fall back to Ergast.** `ergast.com/api/f1/` now returns 404 and `ergast.com/mrd/`
  redirects to gambling spam. It should not appear in the tree, even as a comment.
- **Argentine club football is a €49/month product decision, not an engineering one.** Put
  it to the owner rather than quietly substituting Brazil.

### 3.5 Summary of data verdicts

| need | source | key | cost | verdict |
|---|---|---|---|---|
| sunrise / sunset | already in `/data/2.5/weather` `sys.*` | have it | free | **discarded today; add two fields** |
| wind | already in `/data/2.5/weather` `wind.speed` | have it | free | **discarded today; add one field** |
| hourly strip, 5-day list, UV | **Open-Meteo** | **none** | free | new provider, shared envelope |
| dólar blue USD **and EUR** | **Bluelytics** | **none** | free | new provider; unofficial, no SLA |
| dólar blue day change | — | — | — | **not obtainable for EUR; show the spread** |
| ECB rates | `fx.py`, already written | none | free | **exists, wired to nothing (G2)** |
| deliveries | — | — | — | **no viable source** |
| F1 | **jolpica-f1** | **none** | free | new provider; custom User-Agent required |
| Argentine club football | football-data.org `ASL` | token | **€49/mo** | owner's decision |
| Brazilian Série A | football-data.org `BSA` | token | free | available |

---



## 4. Sequencing

Four prerequisites. Everything else is a leaf and can be worked in any order, or in parallel.

### Prerequisite 0 — render one pixel (§9.4 E0/E1)

**Nothing in this document is reviewable until a composed frame exists as a PNG.** G1 is
still true: the entire pixel-push half has never produced an image, because Chromium is not
installed on the Pi. Every variant, every threshold, every "does the left column fit"
judgement in §2 is arithmetic until somebody looks at a picture. §9.4 says E0 costs an
afternoon and needs no hardware.

**This is the only item that blocks literally everything below.**

### Prerequisite 1 — the variant mechanism (§1.6 items 1 and 2)

`name` on `SURFACES`, `variant_for()`, `SceneContext.variant`, `SceneContext.rows`,
`_style.rows()`. Blocks every component rewrite. Small: three functions and eight one-line
component edits. No new dependency, no data, no hardware.

### Prerequisite 2 — the icon module (§2.10)

Inline SVG and draw shapes from one geometry, extending `draw.ICONS` with `sunrise`,
`sunset`, `package`, `moon`. Blocks the masthead, the weather panel, the FX box and the
ticker badges — four of the six component items below. Ship it before them, not alongside.

### Prerequisite 3 — the design audits as tests over `compose.compose` (§12 C1–C7)

Only `#000`/`#fff` in compiled CSS; no `font-size` below 10px; `grey_fraction < 1%`;
`.pill` count ≤ 2; no ink in the outer 4px of any region rect. All five already have
machinery and none of them currently runs over composed output.

Not a prerequisite in the dependency sense — it is a prerequisite in the practical one.
Six component rewrites against a mockup made of greys and 9px type will reintroduce greys
and 9px type unless a test says no on the first commit rather than the sixth. Cheap. Do it
with Prerequisite 1.

### Also do first, because it is a defect and it corrupts every measurement

**§5 D1 — the composer's heading height.** A component with a `label` is told it has 121px
and rendered into 104px. Every `ctx.rows` budget and every threshold in §1.3 is computed
against the wrong number until this is fixed. One-line-ish, and it belongs with Prerequisite 1.

### The leaves

Once 0–3 are in, these are independent of each other:

| leaf | size | depends on | note |
|---|---|---|---|
| `openweather` normalises `sys.sunrise`/`sys.sunset`/`wind.speed` | XS | — | data already in the response; do this first among the leaves, it unblocks two others |
| `fx` component on the existing `fx.py` provider | S | P1, P2 | G2, "the cheapest outstanding item in the project" |
| `masthead` component + `SceneContext.oldest_fetch` | S | P1, P2 | fills the empty top 11% |
| `quotes` `badge` variant | S | P1, P2 | |
| `calendar` `card` variant | S | P1 | |
| `clock` `card` variant + `show_sun` | S | P1, P2, the openweather leaf | |
| sport envelope normalisation (spec §10.6 12a) | S | — | already sequenced in the tree's own docs; ships alone and invisibly |
| `sport` `card` variant + `teams` comma list | M | P1, 12a | |
| forecast provider + `weather` `panel` variant | M | P1, P2, §3.1's decision | the largest single piece |
| `bluelytics` provider behind `fx`'s `source` option | M | the `fx` leaf, §3.2's decision | |
| deliveries | — | — | **not scheduled**; §3.3 |

### The two decisions that gate leaves rather than blocking them

- **§3.1 — which weather source.** Gates the forecast leaf only. Make it before starting
  that leaf, not before starting anything else.
- **§3.2 — whether an unofficial dólar blue feed is acceptable.** Gates the `bluelytics` leaf
  only. The `fx` component ships against ECB rates regardless and is useful on its own.

### What is deliberately not in this list

No template change. No new region. No horizontal sub-split in `layout.slots`. No layout DSL.
§2.0 shows none of them is needed, and the existing spec §8 has already ruled the last one
out.

---

## 5. Defects found while reading, which this work depends on

Each is small, each has a file and a line, and each corrupts something in §1 or §2.

**D1 — `compose` measures the slot before it adds the section heading.**
`compose.py:130` calls `build_scene(..., {**caps, "w": w, "h": h})` with the **full** slot
height, and only afterwards wraps the result in `.rg-body{height:calc(100% - 17px)}`
(`compose.py:152`). A component with a `label` is therefore told it has 17px more than it
gets. Today that only costs a placeholder some slack; under §1 it means `variant_for` and
`ctx.rows` are both computed against a rectangle that does not exist. Fix: subtract the
heading height before the call, and make `17` a named constant shared with the CSS.

**D1b — the `17` is not derived from its parts.** `.rg-label` is `font-size:10px` +
`padding-top:3px` + `margin-bottom:2px` + a 1px border; at a default line-height that is
about 18px, not 17. Nothing pins it — `tests/test_compose.py` asserts the class is present
and never its height. Whatever D1 chooses, one test should assert the constant and the CSS
agree.

**D2 — the builder judges fit at a size the render will not use.**
`web/views_ui.py:141` measures `slot_caps` as `layout.slots(spec, index + 1)[index]` — an
**even** share — while `compose._placed` divides by the stored `weight`. An operator who
sets `weight: 2.9` on the agenda is shown the picker for a 67px slot and gets a 121px one.
Under §1 that means the builder names the wrong variant. Fix: measure with the weights the
view actually holds.

**D2b — `slot_caps` drops `shape` and `depth`.** Benign today (`surface.describe` defaults
`shape` to `rect`) but wrong, and it is the same class of bug as §9.5 item 1, which is
already recorded against `serve.py:device_frame`. Fix both in one place.

**D3 — no component can read the oldest successful fetch.** `CLAUDE.md` §6 requires the
masthead timestamp to reflect it. `ctx.data` answers one requirement at a time and only for
requirements the component declared. §2.1 proposes `SceneContext.oldest_fetch`.

---

## 6. Open questions

**Q-A — does the operator ever need to override the variant?** §1.4 recommends shipping
without the override and naming the exact evidence that would change the answer: a slot,
found in E0's screenshot loop, where measurement picks a presentation the owner does not
want and no `weight` fixes it. Until such a slot exists, the option is speculative.

**Q-B — what goes in the ENTREGAS slot?** §3.3 recommends. The owner should choose, because
every option trades automation against honesty and that is a taste decision, not a technical
one.

**Q-C — does Finnhub's free tier return VWCE?** One request answers it. It decides whether
the markets band's second cell is an ETF or another share.

**Q-D — is the mockup's four-weight rule hierarchy replaceable by one weight plus
whitespace?** §2.10 recommends yes and gives the counter-argument. This is exactly the kind
of question E0 exists to answer with a picture rather than with prose, and it should be
re-asked after the first composed frame.
