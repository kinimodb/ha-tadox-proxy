from __future__ import annotations

from homeassistant.components.climate import ClimateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_NAME


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the proxy climate entity (placeholder for now)."""
    name = entry.data[CONF_NAME]
    async_add_entities([TadoxProxyPlaceholderClimate(entry.entry_id, name)])


class TadoxProxyPlaceholderClimate(ClimateEntity):
    """Step 1 MVP: Placeholder entity so setup works end-to-end."""

    def __init__(self, entry_id: str, name: str) -> None:
        self._attr_name = name
        self._attr_unique_id = entry_id

    @property
    def available(self) -> bool:
        return True
