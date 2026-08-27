"""Human-facing pages.

One module per page, plus a shared shell. Markup lives here so the routing
module never has to be read to understand what a page looks like, and vice
versa.
"""

from . import schedule_ui as _schedule_ui
from .device import render_device
from .layout import CSS as _CSS
from .fleet import render_fleet
from .layout import duration, page
from .settings import render_settings

__all__ = ["render_device", "render_fleet", "render_settings", "duration", "page"]
