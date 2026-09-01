"""Weather: the temperature now, and what the sky is doing.

The first component built on the provider layer, and the first that has to
adapt itself to the glass rather than being given a layout. On a 240x240 round
panel that is one big number with a word under it; in a wide band it is the
same reading laid out along the line, because a band 62px tall has no room to
stack. Same options, same data, two presentations -- which is the contract
every component signs when it declares a surface.
"""

from __future__ import annotations

import datetime

from homescreen import draw
from homescreen.config import home_location, mapping
from homescreen.scenes import Scene, SceneContext
from homescreen.reading import Reading
from homescreen.scenes import _icons
from homescreen.scenes._style import page


def _nothing() -> Reading:
    return Reading.nothing()

#: Anywhere a number and a word are legible.
#: DISJOINT, so the order they are written in does not matter. Bounded only
#: below, "a strip is shallow" cannot be said at all -- an entry with just
#: `min_aspect: 4.0` matches every wide rectangle including tall ones, so it
#: had to be written first and then swallowed 600x140 and 800x200, blocks with
#: room for a list. Two tests hold this: no sampled geometry may match two
#: entries, and each `at` must resolve to its own shape.
SURFACES = (
    # A genuine band: shallow AND long. The real ones are 800x53 (aspect 15)
    # and 764x62 (12.3); at aspect 4.0 and 110px tall this was swallowing
    # blocks with room for a list.
    {"variant": "strip", "at": (764, 62),
     "min_w": 200, "min_h": 24, "max_h": 80, "min_aspect": 6.0},
    # A cell of a band: narrow AND shallow. Bounded on both, because bounding
    # only the width left it overlapping `card` in a small square -- and an
    # overlap is the ordering hazard back again.
    {"variant": "badge", "at": (127, 62),
     "min_w": 90, "max_w": 199, "min_h": 40, "max_h": 80},
    # A block: several rows. v6's AGENDA is 417x104 and DEPORTES 417x50
    # after their headings.
    {"variant": "card", "at": (417, 150),
     "min_short": 90, "min_h": 81, "max_h": 239},
    # A column. No `min_w`: requiring 200 to protect the six-cell hourly
    # strip made weather the only component that refuses a tall narrow screen
    # -- 26,510 geometries, including every portrait board anyone might hang
    # this on. The SHAPE is a column; how many hours fit in it is the strip's
    # business, and it now counts them.
    {"variant": "panel", "at": (321, 335),
     "min_short": 90, "min_h": 240},
)

#: OpenWeather's icon codes, reduced to the shapes we can draw.
#:
#: Their vocabulary is finer than a 240px circle can express -- "few clouds"
#: and "scattered clouds" are the same picture at this size -- so this maps
#: many to few deliberately rather than pretending to a precision the glass
#: does not have.
#: A normalised sky to a drawing. NOT a vendor's codes -- those are decoded in
#: the adapter, so this component never learns which source answered and the
#: source can be a dropdown rather than a rewrite.
_PICTURE = {"clear": "sun", "cloud": "cloud", "rain": "rain",
            "snow": "snow", "storm": "storm", "fog": "cloud"}


def _sky_icon(sky: str) -> str:
    """The drawing for a sky, or none if we have no picture for it."""
    return _PICTURE.get(str(sky or ""), "")

OPTIONS = (
    {"key": "source", "label": "Fuente", "type": "choice",
     "choices": ("openmeteo", "openweather"), "default": "openmeteo",
     "help": "Open-Meteo no necesita clave y trae previsión. "
             "OpenWeather necesita una clave y sólo da el tiempo actual."},
    {"key": "place", "label": "Sitio", "type": "text", "default": "",
     "help": "En blanco usa la ubicación del servidor."},
    {"key": "lat", "label": "Latitud", "type": "text", "default": "",
     "help": "En blanco usa la ubicación del servidor."},
    {"key": "lon", "label": "Longitud", "type": "text", "default": ""},
    {"key": "units", "label": "Unidades", "type": "choice",
     "choices": ("metric", "imperial"), "default": "metric"},
    {"key": "show_range", "label": "Mostrar mínima y máxima", "type": "bool",
     "default": True},
)

#: Weather is worth redrawing on the scale it changes on, not on the scale it
#: is fetched on: the panel wakes, finds the same reading, and answers 304.
POLL_S = 300


def _where(options: dict, cfg: dict) -> tuple:
    """(lat, lon) for this assignment, or the deployment's own location."""
    options = options or {}
    try:
        lat = float(options.get("lat"))
        lon = float(options.get("lon"))
        return lat, lon
    except (TypeError, ValueError):
        pass
    home = home_location(cfg or {})
    return home.get("lat"), home.get("lon")


def needs(options: dict, cfg: dict) -> tuple:
    lat, lon = _where(options, cfg)
    if lat is None or lon is None:
        return ()
    source = (options or {}).get("source") or "openmeteo"
    if source not in ("openmeteo", "openweather"):
        source = "openmeteo"
    params = {"lat": lat, "lon": lon,
              "units": (options or {}).get("units") or "metric"}
    if source == "openmeteo":
        # Pinned by the COMPONENT, not inherited from the adapter's default.
        # Job keys are built from cleaned parameters, so a default that moves
        # takes every cached payload with it -- the panel showed "sin datos"
        # while a good forecast sat on disk under the old name. Six because
        # the panel lists five days from tomorrow.
        params["days"] = 6
        # Open-Meteo answers coordinates, not names, so the label has to be
        # ours. The operator's own beats the server's home, which beats
        # nothing -- and either beats whichever suburb a reverse lookup would
        # have picked. OpenWeather answered "Sol" for the centre of Madrid.
        params["place"] = (str((options or {}).get("place") or "").strip()
                           or str(home_location(cfg or {}).get("name") or ""))
    return ({"provider": source, "params": params},)


def _degrees(value, units: str) -> str:
    """Just the number.

    The unit moved to the line below, and not only for looks: at `xl` the panel
    picks a bitmap face that covers ASCII alone, so a degree sign in the big
    number drew a blank box. Keeping the headline numeric keeps it crisp AND
    drawable, and "31° / 33°" still carries the symbol at a size the smooth
    face handles.
    """
    if value is None:
        return "--"
    return f"{round(float(value))}"


def _temp_tone(value, units: str) -> str:
    """Cold blue through to hot orange.

    A temperature is the one number here with an intuitive scale, so the colour
    says something a label would need words for: you know it is hot before you
    have read the digits.
    """
    if value is None:
        return "dim"
    celsius = float(value) if units == "metric" else (float(value) - 32) / 1.8
    if celsius <= 5:
        return "cool"
    if celsius >= 28:
        return "hot"
    if celsius >= 20:
        return "warn"
    return "normal"


#: Spanish weekdays, spelled out here rather than taken from the locale.
#: `strftime("%a")` follows LC_TIME, the Pi has no Spanish locale generated,
#: and everything that lands on glass is Spanish -- so the day names came out
#: "Wed". A three-entry table beats a dependency on a system setting nobody
#: can see from the dashboard.
_DAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


def _clock(stamp, offset, fmt: str, zone: str = "") -> str:
    """An epoch in the PLACE's own time, not the server's.

    Prefers the ZONE. A fixed offset is the one in force when the request was
    made, applied to every day of the forecast -- so a spring transition
    inside the window shifted the later days by an hour, and since
    `daily.time` is MIDNIGHT local, an hour is a whole day. Four rows of five
    carried the wrong name, once a year.
    """
    if stamp is None:
        return ""
    if zone:
        try:
            import zoneinfo
            moment = datetime.datetime.fromtimestamp(
                int(stamp), zoneinfo.ZoneInfo(zone))
            return _DAYS[moment.weekday()] if fmt == "day" \
                else moment.strftime(fmt)
        except Exception:                               # noqa: BLE001
            pass                                        # fall back to the offset
    moment = datetime.datetime.fromtimestamp(
        int(stamp) + int(offset or 0), datetime.timezone.utc)
    if fmt == "day":
        return _DAYS[moment.weekday()]
    return moment.strftime(fmt)


def _body(variant: str, reading, place: str, temp: str, unit: str,
          description: str, span: str, now: float, rows: int,
          width: int) -> str:
    """The arrangement for this SHAPE.

    The size rule used to exist and reach only the round panel: `wide_band`
    chose between two instruction lists while the HTML underneath was the same
    string in both branches. So the e-paper got the 240x240 layout letterboxed
    into whatever rectangle it was given -- a clipped number stacked over a
    place name, in a 764x62 band.
    """
    offset = reading.get("tz_offset_s") or 0
    zone = str(reading.get("tz") or "")
    icon = _icons.sky(reading.get("sky"), _ICON_PX.get(variant, 20))

    if variant == "strip":
        # One line along the band. Everything that fits, separated by dots --
        # a band is too short to stack a label under a number.
        line = " · ".join(p for p in (place, f"{temp}{unit}", description,
                                      span) if p)
        return f'<div class="wrap strip">{icon}<span>{line}</span></div>'

    if variant == "badge":
        # A cell in a band: the number, and the least that identifies it.
        # The unit ALWAYS. `place or unit` dropped it whenever the place was
        # named, so C and F rendered identically -- the same bug as "the unit
        # vanished with the place name", reintroduced in the HTML path.
        # The unit rides ON the number. As its own line it wrapped to a
        # second row in a 116px cell and sat under the temperature looking
        # like a separate reading.
        return (f'<div class="wrap badge">'
                f'<div class="big">{temp}{unit}</div>'
                f'<div class="wx-place">{place}</div></div>')

    if variant == "card":
        return (f'<div class="wrap card">'
                f'<div class="now">{icon}<div class="big">{temp}{unit}</div></div>'
                f'<div class="sub">{description}</div>'
                f'<div class="wx-range">{span}</div></div>')

    # `panel`: room to lay it out. Current conditions, the hours ahead, then
    # the days -- which is the right-hand column of the original design.
    hours = _hourly(reading, offset, now, zone, width)
    days = _daily(reading, offset, rows, zone)
    return (f'<div class="wrap panel">'
            f'<div class="now">{icon}<div class="big">{temp}{unit}</div>'
            f'<div class="cond"><div>{description}</div>'
            f'<div class="wx-range">{span}</div></div></div>'
            f'{hours}{days}</div>')


def _hourly(reading, offset, now: float, zone: str = "",
            width: int = 0) -> str:
    """The next six hours, as the design has them: hour, sky, temperature.

    From NOW. This filtered on `reading["now"]`, a key no envelope carries, so
    it was always None and every hour passed the test -- at 12:17 the panel
    offered midnight to 05h. The time is the component's to know, and the
    component has it.
    """
    every = reading.get("hourly") or []
    ahead = [h for h in every if (h.get("time") or 0) >= int(now or 0)]
    # Falls back on TOO FEW, not only on none. `ahead or ...` only fired when
    # nothing was ahead, so three hours left gave three cells, two gave two,
    # ONE gave zero -- the strip vanished at exactly the point the fallback
    # exists for -- and none gave six.
    # As many hours as the width can hold legibly. A cell carries "14h", a
    # 13px picture and "30°", which needs about the space below; six of them
    # across a 170px column would be 28px each and unreadable.
    room = max(0, (int(width) or 321)) // _HOUR_CELL_PX
    cells = (ahead if len(ahead) >= 2 else every[-6:])[:max(0, min(6, room))]
    if len(cells) < 2:
        return ""
    inner = "".join(
        f'<div class="hr"><div class="t">{_clock(c.get("time"), offset, "%H", zone)}h'
        f'</div>{_icons.sky(c.get("sky"), 13)}'
        f'<div class="v">{_round(c.get("temp"))}°</div></div>' for c in cells)
    return f'<div class="hours">{inner}</div>'


def _daily(reading, offset, rows: int, zone: str = "") -> str:
    """The days ahead: name, sky, chance of rain, high and low."""
    # Five, from tomorrow. The provider is asked for `days: 5`, so slicing
    # `[1:6]` off a five-entry list left FOUR rows on a panel captioned as a
    # five-day forecast.
    # Bounded by the ROOM. `[1:]` made the count the length of the forecast:
    # fifteen days gave fourteen rows, of which the region drew five and
    # `overflow:hidden` ate the rest -- the last cut through its x-height.
    days = (reading.get("daily") or [])[1:1 + max(1, min(rows, 6))]
    if not days:
        return ""
    rows = []
    for day in days:
        chance = day.get("precip_pct")
        rows.append(
            f'<div class="dy"><div class="d">'
            f'{_clock(day.get("date"), offset, "day", zone)}</div>'
            f'{_icons.sky(day.get("sky"), 15)}'
            f'<div class="p">{_precip(chance)}</div>'
            f'<div class="mx">{_round(day.get("max"))}°</div>'
            f'<div class="mn">{_round(day.get("min"))}°</div></div>')
    return f'<div class="days">{"".join(rows)}</div>'


def _precip(chance) -> str:
    """Chance of rain, or an em dash for none.

    Blank read as "we do not know" in a column where every other row had a
    number; the dash is the design's own answer and says "none".
    """
    try:
        value = round(float(chance))
    except (TypeError, ValueError):
        return "—"
    return f"{value}%" if value else "—"


def _round(value) -> str:
    try:
        return str(round(float(value)))
    except (TypeError, ValueError):
        return "--"


#: How big the sky is drawn in each shape. Below 12px a line drawing stops
#: reading as anything, so the strip gets a word instead of a smaller picture.
_ICON_PX = {"strip": 14, "badge": 0, "card": 22, "panel": 28}

#: What one cell of the hourly strip needs: a 13px picture between an hour
#: label and a temperature, with air around them.
_HOUR_CELL_PX = 36


def build(ctx: SceneContext) -> Scene:
    options = ctx.options or {}
    units = options.get("units") or "metric"
    wanted = needs(options, ctx.cfg)
    reading = ctx.data(wanted[0]) if wanted else None
    reading = reading if reading is not None else _nothing()

    temp = _degrees(reading.get("temp"), units)
    # What YOU called the place beats what the vendor calls the nearest
    # station: OpenWeather answered "Sol" for a Madrid centre, which is the
    # neighbourhood the observation came from and not the city anyone lives in.
    place = (options.get("place")
             or home_location(ctx.cfg or {}).get("name")
             or reading.get("place") or "")
    description = (reading.get("description") or "").capitalize()
    span = ""
    if options.get("show_range", True):
        # TODAY's extremes, from an actual forecast. This used to read
        # `temp_min`/`temp_max` off the current-conditions endpoint, which are
        # the spread across a city's extent at this instant -- the panel said
        # "21 / 24" on an afternoon that ran 18.0 to 33.6. A source with no
        # forecast now shows no range, which is the honest answer.
        today = (reading.get("daily") or [{}])[0]
        low, high = today.get("min"), today.get("max")
        if low is not None and high is not None:
            span = f"{round(float(low))}° / {round(float(high))}°"

    # The surface decides the arrangement, not the caller. A band is too short
    # to stack a label under a number, so the reading goes along it instead.
    w = int(ctx.caps.get("w") or 240)
    h = int(ctx.caps.get("h") or 240)
    # The DECLARED shape, not a second copy of the rule. This recomputed
    # `w / h >= 4.0` beside a surface entry saying `min_aspect: 4.0` -- two
    # rules with one meaning, free to drift, which is the disease the variant
    # work exists to cure.
    wide_band = ctx.variant == "strip"

    tone = _temp_tone(reading.get("temp"), units)
    unit = "°C" if units == "metric" else "°F"
    if wide_band:
        line = " · ".join(p for p in (place, f"{temp}{unit}", description,
                                      span) if p)
        instructions = [draw.text("center", line, "md", tone)]
    else:
        # The sky as a picture, above the number. A word for the sky is a word
        # you have to read; a sun is a thing you have already seen by the time
        # you have registered the temperature.
        sky = _sky_icon(reading.get("sky"))
        instructions = list(draw.icon(sky, 0.5, 0.20, 0.30,
                                      "warn" if sky == "sun" else "dim")) \
            if sky else []
        instructions.append(draw.text("center", temp, "xl", tone))
        # The unit rides on the place line, so dropping that line when there
        # is no name left a bare number with no way to tell C from F. A source
        # that answers coordinates has no name to give, so this is the normal
        # case rather than an edge one.
        instructions.append(
            draw.text("below", f"{place} {unit}".strip(), "sm",
                      "accent" if place else "dim"))
        if span:
            instructions.append(draw.text("rim_bottom", span, "xs", "cool"))
        elif description:
            instructions.append(draw.text("rim_bottom", description, "xs",
                                          "dim"))

    if reading.missing:
        # Say why, on the glass. A blank panel and a broken key look identical
        # from the sofa.
        instructions = [draw.text("center", "--", "xl"),
                        draw.text("below", "sin datos del tiempo", "xs", "dim")]

    body = _body(ctx.variant, reading, place, temp, unit, description, span,
                 ctx.now, ctx.rows, w)
    return Scene(layout="fill", components=({"c": "weather",
                                             "draw": instructions},),
                 poll_s=POLL_S, poll_max_s=POLL_S,
                 html=page(w, h, body, CSS, shape=ctx.variant))


CSS = """
.wrap{padding:var(--pad);display:flex;flex-direction:column;height:100%;
  justify-content:center}
.ic{flex:none;display:block}

/* A band: one line along it, the sky first. */
.wrap.strip{flex-direction:row;align-items:center;gap:var(--pad-sm);
  justify-content:flex-start;font-size:var(--fs)}

/* A cell: the number, and the least that identifies it. */
.wrap.badge{justify-content:center;text-align:center}

/* Current conditions, in a card or at the top of a panel. */
.now{display:flex;align-items:center;gap:var(--pad-sm)}
.now .big{line-height:1}
.cond{margin-left:auto;text-align:right}
.cond>div:first-child{font-size:var(--xs)}
.wrap.panel{justify-content:flex-start;gap:var(--pad-sm)}

/* The hours ahead: six equal cells under a rule. */
.hours{display:flex;border-top:1px solid #000;
  padding:var(--pad-sm) 0}
.hr{flex:1;text-align:center}
.hr .t{font-size:var(--xs)}
.hr .ic{margin:1px auto}
.hr .v{font-size:var(--sm);font-weight:500}

/* The days ahead: name, sky, chance of rain, high and low. */
.days{border-top:1px solid #000;padding-top:var(--pad-sm)}
.dy{display:flex;align-items:center;gap:var(--pad-sm);
  padding:var(--pad-sm) 0}
/* Dotted, and not on the first. Five solid black hairlines in a 150px stack
   is heavier than the design ever was -- and the design's own separators are
   #ececec, which thresholds to nothing at 1-bit anyway. */
.dy + .dy{border-top:1px dotted #000}
.dy .d{font-size:var(--sm);font-weight:500;width:2.4em;flex:none}
.dy .p{font-size:var(--xs);width:2.6em;flex:none}
.dy .mx{margin-left:auto;font-size:var(--fs);font-weight:500}
/* Beside the maximum, not exiled to the right edge: they are a pair, and a
   fixed right-aligned column made them read as two unrelated numbers. */
.dy .mn{font-size:var(--sm);margin-left:.45em}
.big{font-size:var(--hero);font-weight:600;letter-spacing:-.02em;line-height:1}
.wx-place{font-size:var(--lg);margin-top:var(--pad-sm)}
.sub{font-size:var(--fs);margin-top:2px}
.wx-range{font-size:var(--sm);margin-top:var(--pad-sm)}
"""
