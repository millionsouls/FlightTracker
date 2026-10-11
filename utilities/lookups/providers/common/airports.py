"""Shared helpers used by more than one route/aircraft provider."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import TypeVar

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Airport records and code conversions, all sourced from world-airports.csv.
# ---------------------------------------------------------------------------

_icao_to_iata: dict[str, str] = {}
_iata_to_icao: dict[str, str] = {}
_world_airports_by_icao: dict[str, dict[str, str]] = {}
_world_airports_by_iata: dict[str, dict[str, str]] = {}
_world_airports_by_code: dict[str, dict[str, str]] = {}
_world_airports_loaded = False
_Value = TypeVar("_Value")


def icao_to_iata_code(icao: str) -> str:
    """Convert an ICAO airport code to IATA using world-airports.csv."""
    icao = (icao or "").strip().upper()
    if not icao:
        return ""
    _load_world_airports()
    return _icao_to_iata.get(icao, "")


def iata_to_icao_code(iata: str) -> str:
    """Convert an IATA airport code to ICAO using world-airports.csv."""
    iata = (iata or "").strip().upper()
    if not iata:
        return ""
    _load_world_airports()
    return _iata_to_icao.get(iata, "")


def reset_icao_table_cache() -> None:
    """Reset the code-mapping caches (used by tests)."""
    reset_world_airports_cache()


def _load_world_airports() -> None:
    global _icao_to_iata, _iata_to_icao, _world_airports_by_icao
    global _world_airports_by_iata, _world_airports_by_code, _world_airports_loaded
    if _world_airports_loaded:
        return
    _world_airports_loaded = True
    path = Path(__file__).parents[4] / "assets" / "world-airports.csv"
    try:
        with open(path, encoding="utf-8-sig", newline="") as file:
            rows = csv.DictReader(file)
            code_priority: dict[str, int] = {}
            icao_priority: dict[str, int] = {}
            code_fields = (
                ("iata_code", 0),
                ("icao_code", 1),
                ("gps_code", 2),
            )

            for row in rows:
                iata = (row.get("iata_code") or "").strip().upper()
                icao = (row.get("icao_code") or "").strip().upper()
                gps = (row.get("gps_code") or "").strip().upper()
                details = _row_details(row)
                if iata:
                    _world_airports_by_iata.setdefault(iata, details)
                    if icao:
                        _iata_to_icao.setdefault(iata, icao)

                if icao:
                    _offer_code(_world_airports_by_icao, icao_priority, icao, details, 0)
                    if iata:
                        _icao_to_iata.setdefault(icao, iata)
                if gps:
                    _offer_code(_world_airports_by_icao, icao_priority, gps, details, 1)

                for field, priority in code_fields:
                    code = (row.get(field) or "").strip().upper()
                    if code and (priority < 2 or len(code) <= 4):
                        _offer_code(
                            _world_airports_by_code,
                            code_priority,
                            code,
                            details,
                            priority,
                        )
                local_code = (row.get("local_code") or "").strip().upper()
                if local_code and len(local_code) <= 4:
                    _offer_code(
                        _world_airports_by_code,
                        code_priority,
                        local_code,
                        details,
                        3,
                    )
        logger.info(
            "Loaded %d airport IATA codes from world-airports.csv",
            len(_world_airports_by_iata),
        )
    except OSError as e:
        logger.warning("Failed to load %s: %s", path, e)


def _row_details(row: dict[str, str]) -> dict[str, str]:
    return {
        "name": (row.get("name") or "").strip(),
        "country_name": (row.get("country_name") or "").strip(),
        "municipality": (row.get("municipality") or "").strip(),
    }


def _offer_code(
    mapping: dict[str, _Value],
    priorities: dict[str, int],
    code: str,
    value: _Value,
    priority: int,
) -> None:
    if code and priority < priorities.get(code, 4):
        mapping[code] = value
        priorities[code] = priority


def airport_info_by_icao(icao: str) -> dict[str, str]:
    """Return world-airports.csv details for an ICAO or GPS code, if present."""
    icao = (icao or "").strip().upper()
    if not icao:
        return {}
    _load_world_airports()
    return _world_airports_by_icao.get(icao, {})


def airport_info_by_iata(iata: str) -> dict[str, str]:
    """Return world-airports.csv details for an IATA code, if present."""
    iata = (iata or "").strip().upper()
    if not iata:
        return {}
    _load_world_airports()
    return _world_airports_by_iata.get(iata, {})


def airport_info_by_code(code: str, *, include_extended: bool = False) -> dict[str, str]:
    """Return CSV airport details by IATA, or optionally by ICAO/GPS/local code."""
    code = (code or "").strip().upper()
    if not code:
        return {}
    if include_extended:
        _load_world_airports()
        return _world_airports_by_code.get(code, {})
    return airport_info_by_iata(code)


def airport_table(*, include_extended: bool = False) -> dict[str, dict[str, str]]:
    """Return the airport lookup table built from world-airports.csv."""
    _load_world_airports()
    table = _world_airports_by_code if include_extended else _world_airports_by_iata
    return dict(table)


def reset_world_airports_cache() -> None:
    """Reset the world-airports.csv cache (used by tests)."""
    global _icao_to_iata, _iata_to_icao
    global _world_airports_by_icao, _world_airports_by_iata
    global _world_airports_by_code, _world_airports_loaded
    _icao_to_iata = {}
    _iata_to_icao = {}
    _world_airports_by_icao = {}
    _world_airports_by_iata = {}
    _world_airports_by_code = {}
    _world_airports_loaded = False


def fill_airport_details(route, side: str, *, icao_code: str = "") -> bool:
    """Fill location fields from world-airports.csv, using ICAO as the key."""
    route_code = (getattr(route, side, "") or "").strip().upper()
    icao = (
        icao_code.strip().upper()
        or (getattr(route, f"{side}_icao", "") or "").strip().upper()
    )
    if icao:
        details = airport_info_by_icao(icao)
    else:
        details = airport_info_by_iata(route_code)
        if not details:
            icao = route_code
            details = airport_info_by_icao(icao)

    if not icao and details:
        icao = iata_to_icao_code(route_code)
    
    changed = False
    if details and icao:
        icao_field = f"{side}_icao"
        if getattr(route, icao_field, "") != icao:
            setattr(route, icao_field, icao)
            changed = True
        source_iata = icao_to_iata_code(icao)
        route_code_details = (
            airport_info_by_code(route_code, include_extended=True)
            if route_code
            else {}
        )
        source_code = (
            source_iata
            or (route_code if route_code_details == details else icao)
        )
        if route_code != source_code:
            setattr(route, side, source_code)
            changed = True

    name = details.get("name", "")
    municipality = details.get("municipality", "")
    country = details.get("country_name", "")

    for field, value in (
        (f"{side}_name", name),
        (f"{side}_municipality", municipality),
        (f"{side}_country", country),
    ):
        if getattr(route, field) != value:
            setattr(route, field, value)
            changed = True
    return changed
