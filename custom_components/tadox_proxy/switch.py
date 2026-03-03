"""Switch entity for Tado X Proxy optional behaviour flags."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, CONF_FOLLOW_TADO_INPUT


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tado X Proxy switch entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FollowTadoInputSwitch(coordinator, entry)])


class FollowTadoInputSwitch(CoordinatorEntity, SwitchEntity):
    """Switch that enables following physical Tado thermostat input.

    When on, the proxy detects if the user manually changes the setpoint
    on the physical Tado device and adopts that temperature as the new
    comfort target, reverting to COMFORT preset automatically.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "follow_tado_input"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the follow-Tado switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_follow_tado_input"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info so this entity appears on the same device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
        )

    @property
    def is_on(self) -> bool:
        """Return True if following physical thermostat input is enabled."""
        return self._entry.options.get(CONF_FOLLOW_TADO_INPUT, False)

    async def async_turn_on(self, **kwargs) -> None:
        """Enable following physical Tado thermostat input."""
        new_opts = {**self._entry.options, CONF_FOLLOW_TADO_INPUT: True}
        self.hass.config_entries.async_update_entry(self._entry, options=new_opts)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Disable following physical Tado thermostat input."""
        new_opts = {**self._entry.options, CONF_FOLLOW_TADO_INPUT: False}
        self.hass.config_entries.async_update_entry(self._entry, options=new_opts)
        self.async_write_ha_state()
