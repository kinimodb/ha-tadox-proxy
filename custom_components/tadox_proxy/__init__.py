"""The Tado X Proxy integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
    CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
    CONF_WINDOW_SENSOR_ID,
    CONF_PRESENCE_SENSOR_ID,
)

_LOGGER = logging.getLogger(__name__)

# List the platforms that you want to support.
PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.NUMBER, Platform.SWITCH]

# Keys whose change requires a full reload (listener re-registration).
_SENSOR_KEYS = (
    CONF_WINDOW_SENSOR_ID,
    CONF_PRESENCE_SENSOR_ID,
    CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when sensor entity IDs change.

    This listener fires AFTER HA has persisted the new options, so the
    reload always sees the correct values – no timing hacks needed.
    """
    stored = hass.data[DOMAIN].get(f"{entry.entry_id}_sensor_snapshot")
    current = {k: entry.options.get(k) for k in _SENSOR_KEYS}
    if stored != current:
        _LOGGER.info("Sensor config changed – reloading integration")
        await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tado X Proxy from a config entry."""

    # 1. Ensure DOMAIN dict exists in hass.data
    hass.data.setdefault(DOMAIN, {})

    # 2. Define the data update method
    async def async_update_data():
        """Fetch data from entities (Source & External Sensor)."""
        source_entity_id = entry.data.get("source_entity_id")
        
        # Priority: Options (Dynamic) > Data (Initial Config)
        external_sensor_id = entry.options.get(
            CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
            entry.data.get(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID)
        )
        
        data = {
            "room_temp": None,
            "tado_internal_temp": None,
            "tado_setpoint": None
        }
        
        # Get Room Temp from External Sensor
        if external_sensor_id:
            state = hass.states.get(external_sensor_id)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    data["room_temp"] = float(state.state)
                except (ValueError, TypeError):
                    pass

        # Get Tado Internal Temp & Setpoint from Source Entity
        if source_entity_id:
            state = hass.states.get(source_entity_id)
            if state:
                # Try to get internal temperature attribute (device dependent)
                if state.attributes.get("current_temperature") is not None:
                     try:
                        data["tado_internal_temp"] = float(state.attributes["current_temperature"])
                     except (ValueError, TypeError):
                        pass
                
                # Get current Setpoint
                if state.attributes.get("temperature") is not None:
                    try:
                        data["tado_setpoint"] = float(state.attributes["temperature"])
                    except (ValueError, TypeError):
                        pass
        
        return data

    # 3. Create the Coordinator
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{entry.title}",
        update_method=async_update_data,
        update_interval=timedelta(seconds=60), # Poll every 60s as backup
    )

    # 4. Attach the config entry to the coordinator
    coordinator.config_entry = entry

    # 5. Perform initial refresh
    await coordinator.async_config_entry_first_refresh()

    # 6. Store coordinator
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # 7. Snapshot current sensor IDs for change detection in the update listener.
    hass.data[DOMAIN][f"{entry.entry_id}_sensor_snapshot"] = {
        k: entry.options.get(k) for k in _SENSOR_KEYS
    }

    # 8. Register update listener – fires after HA persists new options.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # 9. Load the platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
        hass.data[DOMAIN].pop(f"{entry.entry_id}_sensor_snapshot", None)

    return unload_ok
