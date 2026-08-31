"""Weather: the temperature now, and what the sky is doing.

The first component built on the provider layer, and the first that has to
adapt itself to the glass rather than being given a layout. On a 240x240 round
panel that is one big number with a word under it; in a wide band it is the
same reading laid out along the line, because a band 62px tall has no room to
stack. Same options, same data, two presentations -- which is the contract
every component signs when it declares a surface.
"""

from __future__ import annotations

from homescreen import draw
from homescreen.config import home_location, mapping
from homescreen.scenes import Scene, SceneContext
from homescreen.reading import Reading
from homescreen.scenes._style import page


def _nothing() -> Reading:
    return Reading.nothing()

#: Anywhere a number and a word are legible.
SURFACES = ({"min_short": 90},)

#: OpenWeather's icon codes, reduced to the shapes we can draw.
#:
#: Their vocabulary is finer than a 240px circle can express -- "few clouds"
#: and "scattered clouds" are the same picture at this size -- so this maps
#: many to few deliberately rather than pretending to a precision the glass
#: does not have.
_SKY = {"01": "sun", "02": "cloud", "03": "cloud", "04": "cloud",
        "09": "rain", "10": "rain", "11": "storm", "13": "snow",
        "50": "cloud"}


def _sky_icon(code: str) -> str:
    """The icon for an OpenWeather code, or none if we cannot tell."""
    return _SKY.get(str(code or "")[:2], "")

OPTIONS = (
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
    return ({"provider": "openweather",
             "params": {"lat": lat, "lon": lon,
                        "units": (options or {}).get("units") or "metric"}},)


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
        low, high = reading.get("temp_min"), reading.get("temp_max")
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
        sky = _sky_icon(reading.get("icon"))
        instructions = list(draw.icon(sky, 0.5, 0.20, 0.30,
                                      "warn" if sky == "sun" else "dim")) \
            if sky else []
        instructions.append(draw.text("center", temp, "xl", tone))
        if place:
            instructions.append(
                draw.text("below", f"{place} {unit}", "sm", "accent"))
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

    body = (f'<div class="wrap"><div class="big">{temp}</div>'
            f'<div class="lab">{place}</div>'
            f'<div class="sub">{description}</div>'
            f'<div class="ter">{span}</div></div>')
    return Scene(layout="fill", components=({"c": "weather",
                                             "draw": instructions},),
                 poll_s=POLL_S, poll_max_s=POLL_S,
                 html=page(w, h, body, CSS))


CSS = """
.wrap{padding:18px;display:flex;flex-direction:column;height:100%;
  justify-content:center}
.big{font-size:64px;font-weight:600;letter-spacing:-.02em;line-height:1}
.lab{font-size:20px;margin-top:6px}
.sub{font-size:16px;margin-top:2px}
.ter{font-size:14px;margin-top:10px}
"""
