"""Climate platform for tadox_proxy.

Current focus: a single, reliable Proxy ClimateEntity that:
- uses an external temperature sensor as room measurement (fallback to source climate)
- runs a local PID-based regulation loop
- writes the computed target temperature to the source climate via climate.set_temperature
- exposes all relevant temperatures and regulation diagnostics as readable attributes

This file is defensive by design to minimize setup failures.
"""

from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any, Optional, Tuple

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, CONF_EXTERNAL_TEMPERATURE_ENTITY_ID, CONF_SOURCE_ENTITY_ID_
