"""
Az-El polar plot renderer for SatelliteScene.

Converts (azimuth, elevation) coordinates to pixel positions on a circular
polar plot and provides routines for drawing the circle, north notch,
trajectory arcs, and current-position dots.

Layout (within the left 32x32 region of the 64x32 canvas):
    Centre  : (PLOT_CX, PLOT_CY)
    Radius  : PLOT_RADIUS  (horizon ring)
    Azimuth : 0° = North = top of circle, increases clockwise
    Elevation: 0° = horizon (edge of circle), 90° = zenith (centre)
"""

from __future__ import annotations

import math

from display.rgbpanel import Colour
from setup import fonts, screen

# ---------------------------------------------------------------------------
# Plot geometry
# ---------------------------------------------------------------------------

PLOT_CX = 49  # centre x within the canvas
PLOT_CY = 17  # centre y within the canvas
PLOT_RADIUS = 13  # pixels from centre to horizon ring

# ---------------------------------------------------------------------------
# Colour palette - one slot per satellite (index 0..4)
# Bright  = current-position dot
# Dim     = previous-position dot (trail)
# Dimmer  = predicted trajectory arc (light grey, uniform across satellites)
# ---------------------------------------------------------------------------

PALETTE_BRIGHT = [
    Colour(255, 220, 0),  # 0 amber
    Colour(0, 200, 255),  # 1 cyan
    Colour(180, 0, 255),  # 2 violet
    Colour(0, 255, 100),  # 3 green
    Colour(255, 60, 60),  # 4 red
]

PALETTE_DIM = [
    Colour(80, 60, 0),  # 0 amber dim
    Colour(0, 60, 80),  # 1 cyan dim
    Colour(55, 0, 80),  # 2 violet dim
    Colour(0, 80, 30),  # 3 green dim
    Colour(80, 20, 20),  # 4 red dim
]

PALETTE_DIMMER = [
    Colour(30, 25, 0),  # 0 amber dimmer
    Colour(0, 25, 30),  # 1 cyan dimmer
    Colour(20, 0, 30),  # 2 violet dimmer
    Colour(0, 30, 12),  # 3 green dimmer
    Colour(30, 8, 8),  # 4 red dimmer
]

RING_COLOUR = Colour(80, 80, 80)
NOTCH_COLOUR = Colour(200, 200, 200)
BACKGROUND_COLOUR = Colour(2, 4, 8)
GRID_COLOUR = Colour(28, 32, 38)
CARDINAL_COLOUR = Colour(100, 100, 100)


def sat_colour_bright(tle_index: int) -> Colour:
    return PALETTE_BRIGHT[tle_index % len(PALETTE_BRIGHT)]


def sat_colour_dim(tle_index: int) -> Colour:
    return PALETTE_DIM[tle_index % len(PALETTE_DIM)]


def sat_colour_dimmer(tle_index: int) -> Colour:
    return PALETTE_DIMMER[tle_index % len(PALETTE_DIMMER)]


# ---------------------------------------------------------------------------
# Coordinate conversion
# ---------------------------------------------------------------------------


def azel_to_xy(az_deg: float, el_deg: float) -> tuple[int, int]:
    """
    Convert (azimuth, elevation) to canvas pixel coordinates.

    Azimuth  0° = North (top of circle), increases clockwise.
    Elevation 0° = horizon (edge), 90° = zenith (centre).
    """
    # Distance from centre is proportional to (90° - elevation)
    r = PLOT_RADIUS * (90.0 - max(0.0, min(90.0, el_deg))) / 90.0
    # Azimuth: 0°=North=up means angle from +Y axis, clockwise
    angle_rad = math.radians(az_deg)
    px = PLOT_CX + round(r * math.sin(angle_rad))
    py = PLOT_CY - round(r * math.cos(angle_rad))
    return int(px), int(py)


# ---------------------------------------------------------------------------
# Drawing routines
# ---------------------------------------------------------------------------


def draw_background(panel, canvas) -> None:
    """Fill a subtle dark disk behind the polar plot."""
    radius = PLOT_RADIUS + 2
    for y in range(max(0, PLOT_CY - radius), min(screen.HEIGHT, PLOT_CY + radius + 1)):
        for x in range(max(0, PLOT_CX - radius), min(screen.WIDTH, PLOT_CX + radius + 1)):
            if (x - PLOT_CX) ** 2 + (y - PLOT_CY) ** 2 <= radius**2:
                panel.set_pixel(
                    canvas,
                    x,
                    y,
                    BACKGROUND_COLOUR.red,
                    BACKGROUND_COLOUR.green,
                    BACKGROUND_COLOUR.blue,
                )


def draw_elevation_grid(panel, canvas) -> None:
    """Draw reference circles for 30 and 60 degrees elevation."""
    for elevation in (30, 60):
        radius = round(PLOT_RADIUS * (90 - elevation) / 90)
        panel.draw_circle(canvas, PLOT_CX, PLOT_CY, radius, GRID_COLOUR)


def draw_cardinal_labels(panel, canvas) -> None:
    """Mark north, east, south, and west around the horizon ring."""
    font = fonts.extrasmall
    panel.draw_text(
        canvas, font, PLOT_CX - 2, PLOT_CY - PLOT_RADIUS - 2,
        CARDINAL_COLOUR, "N"
    )
    panel.draw_text(
        canvas, font, screen.WIDTH - 4, PLOT_CY + 2, CARDINAL_COLOUR, "E"
    )
    panel.draw_text(
        canvas, font, PLOT_CX - 2, screen.HEIGHT - 1, CARDINAL_COLOUR, "S"
    )
    panel.draw_text(
        canvas, font, PLOT_CX - PLOT_RADIUS - 4, PLOT_CY + 2,
        CARDINAL_COLOUR, "W"
    )


def draw_horizon_ring(panel, canvas) -> None:
    panel.draw_circle(canvas, PLOT_CX, PLOT_CY, PLOT_RADIUS, RING_COLOUR)
    panel.draw_line(
        canvas,
        PLOT_CX,
        PLOT_CY - PLOT_RADIUS,
        PLOT_CX,
        PLOT_CY - PLOT_RADIUS - 1,
        NOTCH_COLOUR,
    )


def draw_trajectory(
    panel, canvas, trajectory: list[tuple[float, float]], tle_index: int
) -> None:
    """
    Paint the full pass trajectory as dimmer pixels (predicted path).

    Args:
        trajectory: list of (az_deg, el_deg) pairs for the pass
        tle_index : used to pick the dimmer palette colour
    """
    colour = sat_colour_dimmer(tle_index)
    for az, el in trajectory:
        x, y = azel_to_xy(az, el)
        panel.set_pixel(canvas, x, y, colour.red, colour.green, colour.blue)


def draw_trail(panel, canvas, az_deg: float, el_deg: float, tle_index: int) -> None:
    """Draw the previous-position pixel in the dim palette colour."""
    colour = sat_colour_dim(tle_index)
    x, y = azel_to_xy(az_deg, el_deg)
    panel.set_pixel(canvas, x, y, colour.red, colour.green, colour.blue)


def draw_trail_pixel(panel, canvas, px: int, py: int, tle_index: int) -> None:
    """Draw the previous-position pixel at given canvas coords in dim colour."""
    colour = sat_colour_dim(tle_index)
    panel.set_pixel(canvas, px, py, colour.red, colour.green, colour.blue)


def erase_trajectory(panel, canvas, trajectory: list[tuple[float, float]]) -> None:
    """Clear trajectory pixels (paint black)."""
    for az, el in trajectory:
        x, y = azel_to_xy(az, el)
        panel.set_pixel(canvas, x, y, 0, 0, 0)


def draw_position(panel, canvas, az_deg: float, el_deg: float, tle_index: int) -> None:
    """Draw the current-position dot in the bright palette colour."""
    colour = sat_colour_bright(tle_index)
    x, y = azel_to_xy(az_deg, el_deg)
    panel.set_pixel(canvas, x, y, colour.red, colour.green, colour.blue)


def draw_position_with_glow(
    panel, canvas, az_deg: float, el_deg: float, tle_index: int, frame: int
) -> None:
    """Draw a softly pulsing halo around the bright current-position dot."""
    colour = sat_colour_bright(tle_index)
    pulse = 0.12 + 0.12 * (0.5 + 0.5 * math.sin(frame * 0.35))
    glow = Colour(
        int(colour.red * pulse),
        int(colour.green * pulse),
        int(colour.blue * pulse),
    )
    x, y = azel_to_xy(az_deg, el_deg)
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        px, py = x + dx, y + dy
        if 0 <= px < screen.WIDTH and 0 <= py < screen.HEIGHT:
            panel.set_pixel(canvas, px, py, glow.red, glow.green, glow.blue)
    draw_position(panel, canvas, az_deg, el_deg, tle_index)


def draw_motion_vector(
    panel,
    canvas,
    position: tuple[float, float],
    previous_position: tuple[float, float],
    tle_index: int,
) -> None:
    """Connect the previous and current positions with a dim direction line."""
    x0, y0 = azel_to_xy(*previous_position)
    x1, y1 = azel_to_xy(*position)
    if (x0, y0) == (x1, y1):
        return
    panel.draw_line(canvas, x0, y0, x1, y1, sat_colour_dim(tle_index))
    draw_position(panel, canvas, *position, tle_index)


def draw_satellite_info(
    panel, canvas, name: str, az_deg: float, el_deg: float, tle_index: int
) -> None:
    """Draw a compact name and Az/El readout above the polar plot."""
    font = fonts.extrasmall
    colour = sat_colour_bright(tle_index)
    panel.draw_text(
        canvas, font, PLOT_CX - PLOT_RADIUS - 2, 4, colour, name[:7]
    )
    info = f"A{int(az_deg) % 360:03d} E{int(el_deg):02d}"
    panel.draw_text(
        canvas,
        font,
        PLOT_CX - PLOT_RADIUS - 2,
        10,
        CARDINAL_COLOUR,
        info[:7],
    )
