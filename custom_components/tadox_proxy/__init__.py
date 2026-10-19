"""The Tado X Proxy integration."""
from __future__ import annotations

import logging
import math
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_BACKUP_TEMPERATURE_ENTITY_ID,
    CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
    DOMAIN,
    LIVE_OPTION_KEYS,
)

_LOGGER = logging.getLogger(__name__)

# List the platforms that you want to support.
PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.CLIMATE, Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]

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
            "room_temp_ts": None,
            "backup_room_temp": None,
            "tado_internal_temp": None,
            "tado_setpoint": None,
        }

        # Get Room Temp from External Sensor
        room = _read_room_temperature(hass, external_sensor_id)
        if room is not None:
            data["room_temp"], data["room_temp_ts"] = room

        # Backup room sensor, used once the external sensor's grace period
        # has expired (sensor-loss action "backup_sensor")
        backup = _read_room_temperature(
            hass, entry.options.get(CONF_BACKUP_TEMPERATURE_ENTITY_ID)
        )
        if backup is not None:
            data["backup_room_temp"] = backup[0]

        # Get Tado Internal Temp & Setpoint from Source Entity
        if source_entity_id:
            state = hass.states.get(source_entity_id)
            if state:
                # Try to get internal temperature attribute (device dependent)
                if state.attributes.get("current_temperature") is not None:
                    try:
                        value = float(state.attributes["current_temperature"])
                        if math.isfinite(value):
                            data["tado_internal_temp"] = value
                        else:
                            _LOGGER.warning(
                                "Tado internal temperature from %s is not finite: %s",
                                source_entity_id, value,
                            )
                    except (ValueError, TypeError):
                        _LOGGER.warning(
                            "Cannot parse tado internal temperature from %s: %r",
                            source_entity_id, state.attributes.get("current_temperature"),
                        )

                # Get current Setpoint
                if state.attributes.get("temperature") is not None:
                    try:
                        value = float(state.attributes["temperature"])
                        if math.isfinite(value):
                            data["tado_setpoint"] = value
                        else:
                            _LOGGER.warning(
                                "Tado setpoint from %s is not finite: %s",
                                source_entity_id, value,
                            )
                    except (ValueError, TypeError):
                        _LOGGER.warning(
                            "Cannot parse tado setpoint from %s: %r",
                            source_entity_id, state.attributes.get("temperature"),
                        )

        return data

    # 3. Create the Coordinator
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        config_entry=entry,
        name=f"{DOMAIN}_{entry.title}",
        update_method=async_update_data,
        update_interval=timedelta(seconds=60), # Poll every 60s as backup
    )

    # 5. Perform initial refresh
    await coordinator.async_config_entry_first_refresh()

    # 6. Store coordinator
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # 7. Load the platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 8. Reload integration when options change via the options flow.
    # This listener fires AFTER HA has persisted the new options, so the
    # reload always sees the up-to-date values.  Options written at runtime
    # by number/switch entities (LIVE_OPTION_KEYS) are applied live by the
    # climate entity and must not reload – compare against a snapshot of the
    # reload-relevant options taken at setup.
    reload_snapshot = _reload_relevant_options(entry.options)

    async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Reload the integration when reload-relevant options changed."""
        if _reload_relevant_options(entry.options) != reload_snapshot:
            await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


def _read_room_temperature(
    hass: HomeAssistant, entity_id: str | None
) -> tuple[float, float] | None:
    """Return a temperature sensor's finite reading and report time.

    The report time is ``last_reported``: it also advances when the sensor
    re-sends an unchanged value; ``last_updated`` does not, which made a
    stable reading look stale and skipped the grace bridging on the next
    outage.  Returns None when the sensor has no usable reading.
    """
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        return None
    try:
        value = float(state.state)
    except (ValueError, TypeError):
        _LOGGER.warning(
            "Cannot parse room temperature from sensor %s: %r", entity_id, state.state
        )
        return None
    if not math.isfinite(value):
        _LOGGER.warning("Room temperature from %s is not finite: %s", entity_id, value)
        return None
    return value, state.last_reported.timestamp()


def _reload_relevant_options(options) -> dict:
    """Return the options whose change requires a full reload."""
    return {k: v for k, v in options.items() if k not in LIVE_OPTION_KEYS}

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
