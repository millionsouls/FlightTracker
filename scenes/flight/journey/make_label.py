"""Factory: select the journey label based on ``airport_display_style``."""

from __future__ import annotations

from scenes.flight.journey.full_label import FullNameLabel
from scenes.flight.journey.short_label import ShortCodeLabel
from setup.configuration import Config


def make_label(cfg: Config, panel):
    """Return the journey label for the configured airport display style.

    Style 0 with ICAO codes -> :class:`ShortCodeLabel`; all other settings
    use :class:`FullNameLabel` with stacked airport codes and names.
    """
    if cfg.airport_display_style == 0 and cfg.airport_code_format == "icao":
        return ShortCodeLabel(panel)
    return FullNameLabel(panel)
