"""Regression tests for full satellite pass-window refinement."""

import json

from scenes.satellite.passes import compute_passes
from utilities.tle_manager import BACKUP_TLE_CACHE_PATH


def test_iss_passes_keep_full_trajectory_at_configured_elevation():
    cache = json.loads(BACKUP_TLE_CACHE_PATH.read_text())
    iss_tle = tuple(cache["entries"]["25544"]["tle"])

    windows = compute_passes([iss_tle], 55.87, -4.25, 20, 1)

    assert windows
    assert all(window.max_el >= 20 for window in windows)
    assert all((window.los - window.aos).total_seconds() > 60 for window in windows)
    assert all(len(window.trajectory) > 2 for window in windows)
