"""Utility helpers for tadox_proxy.

Small, dependency-light helpers that are used by the climate platform.
"""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant, State


def now_utc() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def clamp(value: float, min_value: float, max_value: float) -> float:
    """Clamp value to [min_value, max_value]."""
    return max(min_value, min(float(value), max_value))


def is_binary_sensor_on(hass: HomeAssistant, entity_id: str | None) -> bool:
    """Return True if the given binary_sensor entity is currently 'on'."""
    if not entity_id:
        return False
    state = hass.states.get(entity_id)
    return state is not None and state.state == "on"


def get_climate_hvac_mode(state: State | None, default: HVACMode = HVACMode.HEAT) -> HVACMode:
    """Extract hvac mode from a State object (used for RestoreEntity)."""
    if state is None:
        return default
    try:
        return HVACMode(state.state)
    except Exception:
        return default


def get_climate_attr_float(state: State | None, attr: str, default: float | None = None) -> float | None:
    """Extract a float attribute from a State object."""
    if state is None:
        return default
    val = state.attributes.get(attr)
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def get_climate_attr_str(state: State | None, attr: str, default: str | None = None) -> str | None:
    """Extract a string attribute from a State object."""
    if state is None:
        return default
    val = state.attributes.get(attr)
    if val is None:
        return default
    try:
        return str(val)
    except Exception:
        return default
