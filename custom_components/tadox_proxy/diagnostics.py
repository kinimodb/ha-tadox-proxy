from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_SOURCE_ENTITY_ID,
    CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
    CONF_EXTERNAL_HUMIDITY_ENTITY_ID,
    CONF_WINDOW_SENSOR_ENTITY_ID,
    CONF_PRESENCE_SENSOR_ENTITY_ID,
)

# Add keys here if you ever store sensitive data in entry.data/options (tokens, coords, etc.)
TO_REDACT: list[str] = []


def _effective_entity_id(entry: ConfigEntry, key: str) -> str | None:
    """Options override config entry data."""
    return entry.options.get(key) or entry.data.get(key)


def _state_snapshot(hass: HomeAssistant, entity_id: str) -> dict[str, Any] | None:
    """Return a JSON-serializable snapshot of an entity state."""
    st = hass.states.get(entity_id)
    if st is None:
        return None

    return {
        "state": st.state,
        "attributes": dict(st.attributes),
        "last_changed": st.last_changed.isoformat(),
        "last_updated": st.last_updated.isoformat(),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    source_entity_id = config_entry.data.get(CONF_SOURCE_ENTITY_ID)

    ext_temp_id = _effective_entity_id(config_entry, CONF_EXTERNAL_TEMPERATURE_ENTITY_ID)
    ext_hum_id = _effective_entity_id(config_entry, CONF_EXTERNAL_HUMIDITY_ENTITY_ID)
    window_id = _effective_entity_id(config_entry, CONF_WINDOW_SENSOR_ENTITY_ID)
    presence_id = _effective_entity_id(config_entry, CONF_PRESENCE_SENSOR_ENTITY_ID)

    selected_entities: list[str] = [
        eid
        for eid in [source_entity_id, ext_temp_id, ext_hum_id, window_id, presence_id]
        if eid
    ]

    # Proxy entities created by this config entry
    ent_reg = er.async_get(hass)
    proxy_entities: list[dict[str, Any]] = []
    for reg_entry in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id):
        proxy_entities.append(
            {
                "entity_id": reg_entry.entity_id,
                "unique_id": reg_entry.unique_id,
                "platform": reg_entry.platform,
                "device_id": reg_entry.device_id,
                "disabled_by": reg_entry.disabled_by,
            }
        )

    return {
        "config_entry": {
            "entry_id": config_entry.entry_id,
            "title": config_entry.title,
            "data": async_redact_data(dict(config_entry.data), TO_REDACT),
            "options": async_redact_data(dict(config_entry.options), TO_REDACT),
        },
        "effective_selection": {
            "source_entity_id": source_entity_id,
            "external_temperature_entity_id": ext_temp_id,
            "external_humidity_entity_id": ext_hum_id,
            "window_sensor_entity_id": window_id,
            "presence_sensor_entity_id": presence_id,
        },
        "proxy_entities": proxy_entities,
        "states": {eid: _state_snapshot(hass, eid) for eid in selected_entities},
    }
