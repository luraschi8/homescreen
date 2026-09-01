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
#: Ordered MOST DEMANDING FIRST, because the first match wins and these are
#: MINIMUMS: a rule with small minimums matches every larger glass too, so
#: writing `badge` above `panel` makes every column a badge. I wrote it the
#: other way round first and every shape below `badge` was dead.
#:
#: `tests/test_variants.py` samples a geometry grid and fails when a declared
#: shape is unreachable, so the next person finds out at the moment they write
#: it rather than on the glass.
SURFACES = (
    # A wide, shallow band: one line along it. `min_aspect` is what makes this
    # a SHAPE rather than a size -- 764x62 and 800x53 are both strips, and
    # neither is "smaller" than the cell below.
    {"variant": "strip", "min_w": 200, "min_h": 24, "min_aspect": 4.0},
    # A column: current conditions, an hourly strip, and the days ahead.
    {"variant": "panel", "min_short": 90, "min_h": 240},
    # Room for the sky, the number and a line about it.
    {"variant": "card", "min_short": 90, "min_h": 120},
    # A small cell in a band: the temperature and the place, stacked.
    {"variant": "badge", "min_w": 90, "min_h": 40},
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


def _clock(stamp, offset, fmt: str) -> str:
    """An epoch in the PLACE's own time, not the server's."""
    if stamp is None:
        return ""
    moment = datetime.datetime.fromtimestamp(
        int(stamp) + int(offset or 0), datetime.timezone.utc)
    if fmt == "day":
        return _DAYS[moment.weekday()]
    return moment.strftime(fmt)


def _body(variant: str, reading, place: str, temp: str, unit: str,
          description: str, span: str, w: int, h: int) -> str:
    """The arrangement for this SHAPE.

    The size rule used to exist and reach only the round panel: `wide_band`
    chose between two instruction lists while the HTML underneath was the same
    string in both branches. So the e-paper got the 240x240 layout letterboxed
    into whatever rectangle it was given -- a clipped number stacked over a
    place name, in a 764x62 band.
    """
    offset = reading.get("tz_offset_s") or 0
    icon = _icons.sky(reading.get("sky"), _ICON_PX.get(variant, 20))

    if variant == "strip":
        # One line along the band. Everything that fits, separated by dots --
        # a band is too short to stack a label under a number.
        line = " · ".join(p for p in (place, f"{temp}{unit}", description,
                                      span) if p)
        return f'<div class="wrap strip">{icon}<span>{line}</span></div>'

    if variant == "badge":
        # A cell in a band: the number, and the least that identifies it.
        return (f'<div class="wrap badge"><div class="big">{temp}</div>'
                f'<div class="wx-place">{place or unit}</div></div>')

    if variant == "card":
        return (f'<div class="wrap card">'
                f'<div class="now">{icon}<div class="big">{temp}{unit}</div></div>'
                f'<div class="sub">{description}</div>'
                f'<div class="wx-range">{span}</div></div>')

    # `panel`: room to lay it out. Current conditions, the hours ahead, then
    # the days -- which is the right-hand column of the original design.
    hours = _hourly(reading, offset)
    days = _daily(reading, offset)
    return (f'<div class="wrap panel">'
            f'<div class="now">{icon}<div class="big">{temp}{unit}</div>'
            f'<div class="cond"><div>{description}</div>'
            f'<div class="wx-range">{span}</div></div></div>'
            f'{hours}{days}</div>')


def _hourly(reading, offset) -> str:
    """The next six hours, as the design has them: hour, sky, temperature."""
    ahead = [h for h in (reading.get("hourly") or [])
             if (h.get("time") or 0) >= (reading.get("now") or 0)]
    cells = (ahead or (reading.get("hourly") or []))[:6]
    if len(cells) < 2:
        return ""
    inner = "".join(
        f'<div class="hr"><div class="t">{_clock(c.get("time"), offset, "%H")}h'
        f'</div>{_icons.sky(c.get("sky"), 13)}'
        f'<div class="v">{_round(c.get("temp"))}°</div></div>' for c in cells)
    return f'<div class="hours">{inner}</div>'


def _daily(reading, offset) -> str:
    """The days ahead: name, sky, chance of rain, high and low."""
    days = (reading.get("daily") or [])[1:6]
    if not days:
        return ""
    rows = []
    for day in days:
        chance = day.get("precip_pct")
        rows.append(
            f'<div class="dy"><div class="d">'
            f'{_clock(day.get("date"), offset, "day")}</div>'
            f'{_icons.sky(day.get("sky"), 15)}'
            f'<div class="p">{"" if not chance else str(round(chance)) + "%"}</div>'
            f'<div class="mx">{_round(day.get("max"))}°</div>'
            f'<div class="mn">{_round(day.get("min"))}°</div></div>')
    return f'<div class="days">{"".join(rows)}</div>'


def _round(value) -> str:
    try:
        return str(round(float(value)))
    except (TypeError, ValueError):
        return "--"


#: How big the sky is drawn in each shape. Below 12px a line drawing stops
#: reading as anything, so the strip gets a word instead of a smaller picture.
_ICON_PX = {"strip": 14, "badge": 0, "card": 22, "panel": 28}


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
    wide_band = h and w / max(h, 1) >= 4.0

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

    body = _body(ctx.variant, reading, place, temp, unit, description, span, w, h)
    return Scene(layout="fill", components=({"c": "weather",
                                             "draw": instructions},),
                 poll_s=POLL_S, poll_max_s=POLL_S,
                 html=page(w, h, body, CSS))


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
.cond>div:first-child{font-size:var(--fs)}
.wrap.panel{justify-content:flex-start;gap:var(--pad-sm)}

/* The hours ahead: six equal cells under a rule. */
.hours{display:flex;border-top:1px solid #000;padding-top:var(--pad-sm)}
.hr{flex:1;text-align:center}
.hr .t{font-size:var(--xs)}
.hr .ic{margin:1px auto}
.hr .v{font-size:var(--sm);font-weight:500}

/* The days ahead: name, sky, chance of rain, high and low. */
.days{border-top:1px solid #000;padding-top:var(--pad-sm)}
.dy{display:flex;align-items:center;gap:var(--pad-sm);
  padding:calc(var(--pad-sm) / 2) 0}
.dy .d{font-size:var(--sm);font-weight:500;width:2.4em;flex:none}
.dy .p{font-size:var(--xs);width:2.6em;flex:none}
.dy .mx{margin-left:auto;font-size:var(--fs);font-weight:500}
.dy .mn{font-size:var(--sm);width:2.4em;text-align:right}
.big{font-size:var(--hero);font-weight:600;letter-spacing:-.02em;line-height:1}
.wx-place{font-size:var(--lg);margin-top:var(--pad-sm)}
.sub{font-size:var(--fs);margin-top:2px}
.wx-range{font-size:var(--sm);margin-top:var(--pad-sm)}
"""
