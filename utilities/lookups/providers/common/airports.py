"""Shared helpers used by more than one route/aircraft provider."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bundled ICAO->IATA airport code table
# ---------------------------------------------------------------------------

_icao_to_iata: dict[str, str] = {}
_icao_to_iata_loaded = False
_iata_to_icao: dict[str, str] = {}
_iata_to_icao_loaded = False
_world_airports_by_icao: dict[str, dict[str, str]] = {}
_world_airports_loaded = False


def _load_icao_to_iata() -> None:
    """Load the ICAO-to-IATA mapping from assets/airports_icao_to_iata.json."""
    global _icao_to_iata, _icao_to_iata_loaded
    if _icao_to_iata_loaded:
        return
    _icao_to_iata_loaded = True
    _icao_to_iata = _load_code_map("airports_icao_to_iata.json")


def _load_iata_to_icao() -> None:
    """Load the IATA-to-ICAO mapping from assets/airports_iata_to_icao.json.

    The reverse of the table above; powers the optional ICAO journey-code
    display, which converts the IATA codes route services emit into their
    4-letter ICAO form.
    """
    global _iata_to_icao, _iata_to_icao_loaded
    if _iata_to_icao_loaded:
        return
    _iata_to_icao_loaded = True
    _iata_to_icao = _load_code_map("airports_iata_to_icao.json")


def _load_code_map(filename: str) -> dict[str, str]:
    path = Path(__file__).parents[4] / "assets" / filename
    if not path.exists():
        return {}
    try:
        import json

        with open(path) as fh:
            loaded = json.load(fh)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def icao_to_iata_code(icao: str) -> str:
    """Convert an ICAO airport code to IATA using the bundled table.

    Returns "" when unknown - the bundled table is the sole source.
    """
    icao = (icao or "").strip().upper()
    if not icao:
        return ""
    _load_icao_to_iata()
    return _icao_to_iata.get(icao, "")


def iata_to_icao_code(iata: str) -> str:
    """Convert an IATA airport code to ICAO using the bundled table.

    Returns "" when unknown - the bundled table is the sole source.
    """
    iata = (iata or "").strip().upper()
    if not iata:
        return ""
    _load_iata_to_icao()
    return _iata_to_icao.get(iata, "")


def reset_icao_table_cache() -> None:
    """Reset the code-mapping caches (used by tests)."""
    global _icao_to_iata, _icao_to_iata_loaded
    global _iata_to_icao, _iata_to_icao_loaded
    _icao_to_iata = {}
    _icao_to_iata_loaded = False
    _iata_to_icao = {}
    _iata_to_icao_loaded = False


def _load_world_airports() -> None:
    global _world_airports_by_icao, _world_airports_loaded
    if _world_airports_loaded:
        return
    _world_airports_loaded = True
    path = Path(__file__).parents[4] / "assets" / "world-airports.csv"
    try:
        with open(path, encoding="utf-8-sig", newline="") as file:
            count = 0
            for row in csv.DictReader(file):
                icao = (row.get("icao_code") or "").strip().upper()
                if not icao or icao in _world_airports_by_icao:
                    continue
                _world_airports_by_icao[icao] = {
                    "name": (row.get("name") or "").strip(),
                    "country_name": (row.get("country_name") or "").strip(),
                    "municipality": (row.get("municipality") or "").strip(),
                }
                count += 1
            logger.info(f"Loaded {count} airports from world-airports.csv")
    except OSError as e:
        logger.warning("Failed to load %s: %s", path, e)


def airport_info_by_icao(icao: str) -> dict[str, str]:
    """Return the world-airports.csv details for an ICAO code, if present."""
    icao = (icao or "").strip().upper()
    if not icao:
        return {}
    _load_world_airports()
    return _world_airports_by_icao.get(icao, {})


def reset_world_airports_cache() -> None:
    """Reset the world-airports.csv cache (used by tests)."""
    global _world_airports_by_icao, _world_airports_loaded
    _world_airports_by_icao = {}
    _world_airports_loaded = False


def fill_airport_details(route, side: str, *, icao_code: str = "") -> bool:
    """Fill location fields from world-airports.csv, using ICAO as the key."""
    route_code = (getattr(route, side, "") or "").strip().upper()
    icao = (
        icao_code
        or getattr(route, f"{side}_icao", "")
        or iata_to_icao_code(route_code)
    )
    logger.debug("Looking up airport details for ICAO %s", icao)
    # Only lookup if we have a valid ICAO code
    details = airport_info_by_icao(icao) if icao else {}

    logger.debug("Airport details for ICAO %s: %s", icao, details)
    
    if not details:
        return False
    name = details.get("name", "")
    municipality = details.get("municipality", "")
    country = details.get("country_name", "")
    if not (name or municipality or country):
        return False

    changed = False
    for field, value in (
        (f"{side}_name", name),
        (f"{side}_municipality", municipality),
        (f"{side}_country", country),
    ):
        if value and getattr(route, field) != value:
            setattr(route, field, value)
            changed = True
    return changed
