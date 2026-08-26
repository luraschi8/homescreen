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
from pathlib import Path

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class SceneContext:
    """Everything a scene may read. Deliberately narrow: a scene that could
    reach the whole app would be untestable without one."""
    cfg: dict
    cache_dir: Path
    caps: dict
    now: float
    device: dict


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


def _registry() -> dict:
    from homescreen.scenes import clock, status, planes
    return {"clock": clock.build, "status": status.build, "planes": planes.build}


def names() -> tuple[str, ...]:
    return tuple(sorted(_registry()))


def build(name: str, ctx: SceneContext) -> Scene:
    """Build a scene by name. Raises KeyError if unknown."""
    return _registry()[name](ctx)


def safe_build(name: str, ctx: SceneContext) -> Scene:
    """Build, falling back to the `status` scene on any failure.

    Spec §6.2: a scene that raises must not reach the device, and must not
    blank a screen with no explanation. The fallback says what broke.
    """
    try:
        return build(name, ctx)
    except KeyError:
        log.warning("unknown scene %r", name)
        return _fallback(ctx, f"unknown scene: {name}")
    except Exception as exc:  # noqa: BLE001
        log.exception("scene %r failed", name)
        return _fallback(ctx, f"{name} failed: {type(exc).__name__}")


def _fallback(ctx: SceneContext, message: str) -> Scene:
    from homescreen.scenes import status
    return status.build(ctx, message=message)
