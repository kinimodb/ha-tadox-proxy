"""Setup and time helpers shared by the Home Assistant integration tests."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import Event, HomeAssistant, ServiceRegistry, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

# Not imported from the integration: custom_components must stay unimported
# until the hass fixture has mounted the harness's package (see conftest.py).
DOMAIN = "tadox_proxy"

TRV = "climate.trv"
ROOM_SENSOR = "sensor.room"
PRESENCE_SENSOR = "binary_sensor.presence"
WINDOW_SENSOR = "binary_sensor.window"

# Entity id of the proxy climate entity (derived from the entry title).  Restore
# scenarios key their cached state by it.
CLIMATE = "climate.living"

PRESET_FROST_PROTECTION = "frost_protection"

PRESENCE_AWAY_DELAY_S = 60
PRESENCE_HOME_DELAY_S = 30
WINDOW_DELAY_S = 30

BASE_OPTIONS: dict[str, Any] = {
    "presence_sensor_id": PRESENCE_SENSOR,
    "presence_away_delay_s": PRESENCE_AWAY_DELAY_S,
    "presence_home_delay_s": PRESENCE_HOME_DELAY_S,
    "window_sensor_id": WINDOW_SENSOR,
    "window_delay_s": WINDOW_DELAY_S,
    "window_close_delay_s": 120,
}


@dataclass
class Proxy:
    """A set-up proxy config entry and handles to its entities."""

    hass: HomeAssistant
    entry: MockConfigEntry
    service_calls: list[Event] = field(repr=False)

    def entity_id(self, platform: str, unique_suffix: str | None = None) -> str:
        """Return the entity id registered for this entry and unique-id suffix."""
        unique_id = self.entry.entry_id
        if unique_suffix:
            unique_id = f"{unique_id}_{unique_suffix}"
        entity_id = er.async_get(self.hass).async_get_entity_id(platform, DOMAIN, unique_id)
        assert entity_id is not None, f"no {platform} entity with unique id {unique_id}"
        return entity_id

    @property
    def climate(self) -> str:
        return self.entity_id("climate")

    @property
    def guest_switch(self) -> str:
        return self.entity_id("switch", "guest_mode")

    def number(self, conf_key: str) -> str:
        """Return the preset number entity for an options key (e.g. eco_target)."""
        return self.entity_id("number", conf_key)

    @property
    def state(self) -> State:
        state = self.hass.states.get(self.climate)
        assert state is not None
        return state

    @property
    def preset(self) -> str:
        return self.state.attributes["preset_mode"]

    @property
    def target(self) -> float:
        return self.state.attributes["temperature"]

    def trv_commands(self, service: str) -> list[dict[str, Any]]:
        """Return the data of every ``climate.<service>`` call sent to the TRV."""
        return [
            event.data["service_data"]
            for event in self.service_calls
            if event.data["domain"] == "climate"
            and event.data["service"] == service
            and event.data["service_data"].get("entity_id") == TRV
        ]

    async def call(self, domain: str, service: str, entity_id: str, **data: Any) -> None:
        """Call a service on one entity and wait until HA is idle."""
        await self.hass.services.async_call(
            domain, service, {"entity_id": entity_id, **data}, blocking=True
        )
        await self.hass.async_block_till_done()


async def async_setup_proxy(
    hass: HomeAssistant,
    *,
    options: dict[str, Any] | None = None,
    presence: str = "on",
    window: str = "off",
    room_state: str = "20.0",
    entry_id: str | None = None,
) -> Proxy:
    """Create the source TRV and sensors, then set up one proxy entry.

    Pass ``room_state`` to start with a room sensor that has not reported
    yet, and ``entry_id`` to prepare registry entries for the entry before
    setup.
    """
    hass.states.async_set(TRV, "heat", {"current_temperature": 22.0, "temperature": 20.0})
    hass.states.async_set(
        ROOM_SENSOR, room_state, {"device_class": "temperature", "unit_of_measurement": "°C"}
    )
    hass.states.async_set(PRESENCE_SENSOR, presence)
    hass.states.async_set(WINDOW_SENSOR, window)

    # The TRV is only a state here: its climate.* commands reach no entity.
    # Recording the service-call events shows what the proxy sent without
    # replacing the climate services, which the proxy entity itself needs.
    service_calls = async_capture_events(hass, EVENT_CALL_SERVICE)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living",
        data={"source_entity_id": TRV, "external_temperature_entity_id": ROOM_SENSOR, "name": "Living"},
        options={**BASE_OPTIONS, **(options or {})},
        unique_id=TRV,
        entry_id=entry_id,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return Proxy(hass, entry, service_calls)


async def async_set_state(
    hass: HomeAssistant, entity_id: str, state: str, attributes: dict[str, Any] | None = None
) -> None:
    """Change a sensor state and let the proxy's listeners react.

    Wait before moving the clock: older HA versions run state listeners only
    on the next loop iteration, so a timer they start would otherwise begin
    after the jump and never become due.
    """
    hass.states.async_set(entity_id, state, attributes)
    await hass.async_block_till_done()


async def async_advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    """Move the clock forward and run every timer that became due."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@contextmanager
def trv_rejects(rejected_service: str) -> Iterator[None]:
    """Make the TRV reject ``climate.<rejected_service>``, as when it is unreachable.

    Only calls to the TRV fail.  The climate services stay in place, since
    the proxy entity needs them too.
    """
    original = ServiceRegistry.async_call

    async def async_call(self, domain, service, service_data=None, *args, **kwargs):
        if (
            domain == "climate"
            and service == rejected_service
            and (service_data or {}).get("entity_id") == TRV
        ):
            raise HomeAssistantError(f"{TRV} did not respond")
        return await original(self, domain, service, service_data, *args, **kwargs)

    with patch.object(ServiceRegistry, "async_call", async_call):
        yield
