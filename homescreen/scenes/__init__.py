"""Scenes: what a device is told to show.

A scene is a FUNCTION, not a data file. A declarative format would be a
language to design, test, document and debug; a Python function is already
testable, already composable, and can branch on a device's declared
capabilities in ways a data format would need new syntax for.

Each scene exports:

    build(ctx: SceneContext) -> Scene

and is registered by name here. `registry.ASSIGNABLE_SCENES` is derived from
this table, so adding a scene is one import and one entry -- there is no
second list to keep in sync.
"""

from __future__ import annotations

import dataclasses
import logging
import sys

from homescreen import draw
from homescreen import reading as _reading
from pathlib import Path

log = logging.getLogger(__name__)

#: What a component is drawn as when its declaration names no shape. Every
#: surface written before variants existed means this.
DEFAULT_VARIANT = "panel"


@dataclasses.dataclass(frozen=True)
class SceneContext:
    """Everything a scene may read. Deliberately narrow: a scene that could
    reach the whole app would be untestable without one."""
    cfg: dict
    cache_dir: Path
    caps: dict
    now: float
    device: dict
    #: This assignment's options, already validated against the scene's own
    #: schema. Per ASSIGNMENT, not per scene: two screens can show stocks with
    #: different tickers, or a clock in different cities, and neither is a
    #: property of the component.
    options: dict = dataclasses.field(default_factory=dict)
    #: Which SHAPE the component is being drawn as, set by `build` from the
    #: component's own declaration and this glass. Derived rather than passed
    #: in: a caller cannot hand a component a shape its geometry contradicts,
    #: and three components each re-deriving it from `caps` is how
    #: `weather.wide_band`, `quotes.stacked` and `calendar`'s row loop came to
    #: be three private rules that only the round panel ever saw.
    variant: str = DEFAULT_VARIANT
    #: Epoch seconds of the OLDEST successful fetch behind anything on this
    #: page, or None when nothing on it is fed.
    #:
    #: CLAUDE.md makes this the panel's one safeguard against confident stale
    #: data: a fetcher that quietly died has to be visible ON THE GLASS. The
    #: masthead read it through `getattr(ctx, "oldest_fetch", None)` against a
    #: context that never had the field, so the default won every time and the
    #: stamp was always the current clock -- the exact failure the rule exists
    #: to catch, wearing a badge that said it was fine.
    oldest_fetch: float | None = None

    @property
    def rows(self) -> int:
        """How many body lines this region can hold.

        On the context so a component asks rather than derives: the number
        depends on the type ladder, which depends on the shape, which the
        context already knows.
        """
        from homescreen.scenes import _style
        caps = self.caps if isinstance(self.caps, dict) else {}
        return _style.rows(int(caps.get("w") or 0), int(caps.get("h") or 0),
                           shape=self.variant)

    @property
    def dense_rows(self) -> int:
        """How many one-line LIST rows fit, at list leading rather than prose.

        A second SHARED answer rather than a private one per component: the
        point of `rows` was that `calendar`, `sport` and `quotes` must not each
        invent their own arithmetic, so a component needing tighter leading
        asks for it here instead of computing it locally.
        """
        from homescreen.scenes import _style
        caps = self.caps if isinstance(self.caps, dict) else {}
        return _style.rows(int(caps.get("w") or 0), int(caps.get("h") or 0),
                           shape=self.variant, tight=True)

    def variant_of(self, name: str) -> str:
        """The shape `name` would take on this glass.

        On the context because the builder and the PREVIEW both need it, and
        the preview asks about components this context was not built for.
        """
        return variant_for(name, self.caps) or DEFAULT_VARIANT

    #: Read what was fetched for one of this component's requirements.
    #:
    #: A PORT, injected by whoever builds the scene. A component asks with the
    #: same requirement it declared in `needs()` and receives a `Reading` --
    #: never None, because "nothing was fetched" is a Reading with no data and
    #: every component has to handle that case anyway. It never learns where
    #: data is cached, how it got there, or that a job exists. The default lets
    #: a preview or a unit test build any scene with no daemon running.
    data: object = dataclasses.field(
        default=lambda requirement: _reading.Reading.nothing())


#: Layout modes the wire protocol actually carries. `grid` is deferred (spec
#: §5.4): the only device that could use it lays out in CSS on the Pi, so a
#: wire-level grid would be a second, weaker layout engine in front of a good
#: one. Listing it here without a consumer would let a scene ship a mode no
#: device can draw and no test can honestly exercise.
LAYOUTS = ("fill",)

#: Bounds on the cadence a scene may ask for, in seconds. Mirrors the
#: firmware's kPollMinMs/kPollMaxMs. A scene asking outside this is CLAMPED
#: rather than ignored: a device that stops polling cannot be recovered from
#: the dashboard, so the failure mode has to be "polls oddly", never "gone".
POLL_MIN_S = 1
POLL_MAX_S = 600


def check_layout(layout: str) -> str:
    """The one place a layout name is admitted. Raises ValueError otherwise."""
    if layout not in LAYOUTS:
        raise ValueError(
            f"unsupported layout {layout!r}; this server carries {LAYOUTS}")
    return layout


@dataclasses.dataclass(frozen=True)
class Scene:
    """What a scene produces.

    `html` is for pixel-push devices (the Pi renders it); `components` is for
    data-push devices (the device renders them). A scene may provide either or
    both -- which one is used depends on the device, not the scene.
    """
    layout: str = "fill"
    components: tuple = ()
    html: str | None = None
    #: Set only by `safe_build`'s fallback. Spec §6.2 requires the failure to
    #: be recorded in the fleet view, and a swallowed exception cannot be:
    #: the caller had no way to tell a fallback from a real scene.
    error: str | None = None
    #: How long the device should wait before asking again, in seconds.
    #:
    #: The right cadence is a property of the CONTENT, not of the device: a
    #: clock changes once a minute and is unchanged for the 59 seconds after
    #: it, while a radar wants fresh vectors every few seconds. Only the scene
    #: knows which it is. None means "no opinion" and leaves the device's own
    #: default in place.
    #:
    #: A scene may also point at the next CHANGE rather than a fixed period --
    #: `60 - now % 60` wakes a clock on the minute boundary -- which costs one
    #: request a minute instead of twelve and lands the new minute within a
    #: second of when it becomes true.
    poll_s: int | None = None
    #: The LONGEST this scene will ever ask for, in seconds.
    #:
    #: Separate from `poll_s` because a scene that aims at the next change asks
    #: for a different number every time -- a clock counts 60, 59, 58 down to
    #: the boundary. Liveness cannot be judged against that: three times a
    #: one-second cadence would call a healthy panel dead. This is the stable
    #: number, so the fleet view and the device agree on when silence means
    #: something. Defaults to `poll_s`, which is right for any fixed cadence.
    poll_max_s: int | None = None

    def __post_init__(self):
        check_layout(self.layout)
        object.__setattr__(self, "poll_s", clean_poll_s(self.poll_s))
        object.__setattr__(self, "poll_max_s",
                           clean_poll_s(self.poll_max_s) or self.poll_s)


def needs(name: str, options: dict, cfg: dict) -> tuple:
    """What this component needs fetched, given how it is configured.

    A function of OPTIONS, because that is what makes a requirement specific:
    weather needs a place, quotes need symbols, and which ones is the
    assignment's business. A component that declares nothing needs nothing --
    the clock, for instance, which is why it works with the network down.

    Never raises: this runs inside job collection over every record in the
    fleet, and one malformed assignment must not stop the daemon fetching for
    all the others.
    """
    try:
        build_fn = _registry()[name]
    except KeyError:
        return ()
    module = sys.modules.get(build_fn.__module__)
    declare = getattr(module, "needs", None)
    if not callable(declare):
        return ()
    try:
        return tuple(declare(options or {}, cfg or {}) or ())
    except Exception:                                   # noqa: BLE001
        # Logged, not swallowed. Returning "needs nothing" is indistinguishable
        # from a component that genuinely needs nothing, so a typo here becomes
        # a daemon that fetches nothing and looks idle -- which is exactly how
        # an ImportError in this function went unnoticed while every job
        # silently disappeared.
        log.exception("component %s failed to declare what it needs", name)
        return ()


def surfaces(name: str) -> tuple:
    """What glass this component says it can draw on.

    A component declaring nothing is offered everywhere, which is what every
    component did before this existed. Declaring is how a component says "a
    radar needs room for rings" or "this is a text page, not a badge" without
    anyone maintaining a table of known panels somewhere else.
    """
    try:
        build_fn = _registry()[name]
    except KeyError:
        return ()
    module = sys.modules.get(build_fn.__module__)
    declared = getattr(module, "SURFACES", ())
    return tuple(d for d in declared if isinstance(d, dict))


def variant_for(name: str, caps):
    """Which SHAPE this component would draw on this glass, or None.

    Named shapes, not a size ladder: our slots run from 15:1 to 0.96:1, so
    aspect decides the presentation and area decides the amount. `strip` is
    not "smaller than `badge`", it is a different shape.

        strip   a wide, shallow band -- a masthead, a markets row
        badge   a small cell -- one ticker in a six-cell band
        card    a modest block -- half a column
        panel   room to lay things out -- a full column, a whole small screen

    The FIRST matching entry wins, so a broad declaration written above a
    narrow one shadows it. That is a real hazard and it is guarded by a test
    that samples a geometry grid and fails when a declared shape is
    unreachable, rather than by a rule nobody can check.

    Returns None when nothing matches, which is exactly "this component cannot
    draw here" -- so `supports` is defined in terms of this and the two cannot
    drift apart.
    """
    from homescreen import surface as _surface
    if name not in _registry():
        # A typo in a stored record is not a component that fits everywhere.
        # `surfaces()` swallows the KeyError and answers `()`, which reads as
        # "declares nothing, so anywhere".
        return None
    declared = surfaces(name)
    if not declared:
        return DEFAULT_VARIANT          # declaring nothing means anywhere
    screen = _surface.describe(caps)
    if not screen["w"] or not screen["h"]:
        # Geometry not declared yet. Unknown is not the same as too small, and
        # judging a device before it has said what it is would disable every
        # component on a board that has only just called in.
        #
        # The component's own most GENEROUS shape, not a global default and
        # not whichever entry happens to be written first. Answering "panel"
        # for a component that never declared one hands it a name it does not
        # implement; answering `declared[0]` made a legal reordering of
        # disjoint entries silently change the answer.
        return _roomiest(declared)
    for spec in declared:
        # `variant` names the shape and `at` records the rectangle it was
        # written for; neither is a constraint. Passing them to `fits` raised
        # TypeError, which the handler below swallows -- so every entry
        # silently never matched and the component drew nothing anywhere.
        rules = {k: v for k, v in spec.items() if k not in ("variant", "at")}
        try:
            if _surface.fits(screen, **rules):
                return spec.get("variant") or DEFAULT_VARIANT
        except TypeError:
            continue                     # a spec naming an unknown constraint
    return None


def supports(name: str, caps) -> tuple[bool, str]:
    """(can this component draw on this glass, why not). Never raises.

    Any ONE declared surface matching is enough: a component may serve a small
    round panel one way and a wide band another, and it only has to be able to
    do one of them here. Defined in terms of `variant_for` so the question
    "does it fit" and the question "how would it look" are answered by one
    piece of code reading one declaration.
    """
    if variant_for(name, caps) is not None:
        return True, ""
    from homescreen import surface as _surface
    return False, _why_not(surfaces(name), _surface.describe(caps))


#: Most room first. Used only for a device that has not said what it is: it
#: will report its geometry on its next poll, and until then the fullest
#: presentation is the better guess.
_ROOM = ("panel", "card", "badge", "strip")


def _roomiest(declared) -> str:
    names = {s.get("variant") for s in declared if s.get("variant")}
    for shape in _ROOM:
        if shape in names:
            return shape
    return DEFAULT_VARIANT


def _why_not(declared, screen) -> str:
    """A reason in the dashboard's language, not a dump of the constraint."""
    wants_shape = {d.get("shape") for d in declared if d.get("shape")}
    if wants_shape and screen.get("shape") not in wants_shape:
        return f"necesita pantalla {'/'.join(sorted(wants_shape))}"
    smallest = min((d.get("min_short") or d.get("min_w") or 0)
                   for d in declared) if declared else 0
    if smallest and screen.get("short", 0) < smallest:
        return f"necesita al menos {smallest}px"
    return "no encaja en esta pantalla"


def clean_poll_s(value):
    """Coerce a scene's requested cadence to a usable number of seconds.

    Never raises. Scenes are the part of this system most likely to be edited
    casually, and this runs on the device's only route -- a TypeError here is a
    500 on the request that keeps a panel alive.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(POLL_MIN_S, min(POLL_MAX_S, n))


def _registry() -> dict:
    from homescreen.scenes import (blank, calendar, claude, clock, date,
                                   planes, quotes, sport, status, weather)
    return {"clock": clock.build, "status": status.build, "blank": blank.build,
            "date": date.build,
            "planes": planes.build, "weather": weather.build,
            "quotes": quotes.build, "calendar": calendar.build,
            "sport": sport.build, "claude": claude.build}


def names() -> tuple[str, ...]:
    return tuple(sorted(_registry()))


def build(name: str, ctx: SceneContext) -> Scene:
    """Build a scene by name. Raises KeyError if unknown."""
    builder = _registry()[name]
    shape = variant_for(name, ctx.caps) or DEFAULT_VARIANT
    if ctx.variant != shape:
        ctx = dataclasses.replace(ctx, variant=shape)
    return _fit_to_glass(builder(ctx), ctx.caps)


def _fit_item(item: dict, w: int, h: int, shape: str) -> dict:
    if item.get("t") != "text":
        return item
    value, size = draw.fit(item.get("v", ""), item.get("slot", "center"),
                           item.get("size", "md"), w, h, shape)
    if value == item.get("v") and size == item.get("size"):
        return item                      # untouched, so not rebuilt
    return {**item, "v": value, "size": size}


def _fit_to_glass(scene: Scene, caps: dict) -> Scene:
    """Shorten any line that would run off the panel it is bound for.

    Done HERE, once, rather than in each component: eight components each
    remembering to measure is eight chances to forget, and the two that did
    forget were only found by measuring every scene at once. A component says
    what it means; how much of that survives a 240px circle is a property of
    the glass.

    Server-side and final -- the device receives the string it should draw, so
    there is no second truncation rule that has to stay in step with this one.
    """
    if not scene.components:
        return scene
    caps = caps if isinstance(caps, dict) else {}
    try:
        w, h = int(caps.get("w") or 0), int(caps.get("h") or 0)
    except (TypeError, ValueError):
        return scene
    if w <= 0 or h <= 0:
        return scene
    shape = str(caps.get("shape") or "rect")
    changed = []
    for component in scene.components:
        instructions = component.get("draw")
        if not instructions:
            changed.append(component)
            continue
        changed.append({**component, "draw": [_fit_item(item, w, h, shape)
                                              for item in instructions]})
    return dataclasses.replace(scene, components=tuple(changed))


def safe_build(name: str, ctx: SceneContext) -> Scene:
    """Build, falling back to the `status` scene on any failure.

    Spec §6.2: a scene that raises must not reach the device, and must not
    blank a screen with no explanation. The fallback says what broke, in
    Spanish -- everything that lands on glass is Spanish, everything the
    operator reads (JSON, /home, logs) is English. See CLAUDE.md.
    """
    try:
        return build(name, ctx)
    except KeyError:
        log.warning("unknown scene %r", name)
        return _fallback(ctx, f"escena desconocida: {name}")
    except Exception as exc:  # noqa: BLE001
        log.exception("scene %r failed", name)
        return _fallback(ctx, f"fallo en {name}: {type(exc).__name__}")


def without_cadence(scene: Scene) -> Scene:
    """The same scene with no opinion about polling.

    Used where a scene is borrowed for its LOOK rather than its content: an
    unassigned panel shows the status scene, but it is waiting to be given a
    job, and an operator who assigns one should see the panel change rather
    than wait out a cadence chosen for host stats.
    """
    return dataclasses.replace(scene, poll_s=None, poll_max_s=None)


def _fallback(ctx: SceneContext, message: str) -> Scene:
    from homescreen.scenes import status
    return dataclasses.replace(without_cadence(status.build(ctx,
                                                            message=message)),
                               error=message)


# --- component options ------------------------------------------------------
#
# A component declares which options it takes; the dashboard renders fields from
# that declaration and the registry stores the values against the assignment.
# One schema drives the form, the validation and the defaults, so a new option
# is one edit rather than three that can disagree.

#: How much option data one assignment may hold. These are written from an
#: unauthenticated LAN, like everything else here.
MAX_OPTIONS = 12
MAX_OPTION_LEN = 120


def option_schema(name: str) -> tuple:
    """The options this scene takes, or () if it takes none.

    Read off the scene module's `OPTIONS`, so a component declares its own
    options next to the code that uses them rather than in a table somewhere
    else that can fall out of step.
    """
    build_fn = _registry().get(name)
    if build_fn is None:
        return ()
    module = sys.modules.get(build_fn.__module__)
    return tuple(getattr(module, "OPTIONS", ()))


def defaults(name: str) -> dict:
    """Every option at its default. What an unconfigured assignment behaves as."""
    return {f["key"]: f.get("default") for f in option_schema(name)}


def clean_options(name: str, raw) -> dict:
    """Coerce and range-check options against the scene's schema. Never raises.

    Unknown keys are dropped rather than stored: an option nothing reads is an
    option that looks configured and does nothing. Bad values fall back to the
    default rather than rejecting the whole assignment -- a typo in one field
    should not stop a screen showing anything at all.
    """
    if not isinstance(raw, dict):
        raw = {}
    out = {}
    for field in option_schema(name)[:MAX_OPTIONS]:
        key = field["key"]
        value = raw.get(key, field.get("default"))
        kind = field.get("type", "text")
        if kind == "int":
            try:
                n = int(float(value))
            except (TypeError, ValueError):
                n = field.get("default", 0)
            lo, hi = field.get("min", 0), field.get("max", 10_000)
            out[key] = max(lo, min(hi, int(n)))
        elif kind == "bool":
            if isinstance(value, bool):
                out[key] = value
            else:
                out[key] = str(value).strip().lower() in ("1", "true", "yes", "on")
        elif kind == "choice":
            allowed = tuple(field.get("choices", ()))
            out[key] = value if value in allowed else field.get("default")
        elif kind == "lines":
            # Newlines preserved, everything else about it a text field. The
            # cap is per LIST rather than per line, and blank lines go: an
            # operator pasting three URLs leaves a trailing newline and that
            # is not a fourth calendar.
            text = "" if value is None else str(value)
            kept = [ln.strip() for ln in text.replace("\r", "").split("\n")]
            out[key] = "\n".join(ln for ln in kept if ln)[:MAX_OPTION_LEN * 4]
        else:
            text = "" if value is None else str(value)
            out[key] = text.strip()[:MAX_OPTION_LEN]
    return out
