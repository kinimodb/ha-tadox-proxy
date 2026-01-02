"""DataUpdateCoordinator wrapper for tadox_proxy.

This module exists primarily to keep imports stable in climate.py and to provide a
single place for typing / future coordinator extensions.
"""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator


class TadoxProxyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator holding the latest source + external sensor snapshot.

    Expected coordinator.data keys:
      - room_temp: float | None
      - tado_internal_temp: float | None
      - tado_setpoint: float | None
    """
