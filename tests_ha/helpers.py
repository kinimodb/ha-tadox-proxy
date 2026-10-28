"""Setup and time helpers shared by the Home Assistant integration tests."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import EVENT_CALL_SERVICE, STATE_ON
from homeassistant.core import Event, HomeAssistant, ServiceRegistry, State, callback
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
WINDOW_CLOSE_DELAY_S = 120

BASE_OPTIONS: dict[str, Any] = {
    "presence_sensor_id": PRESENCE_SENSOR,
    "presence_away_delay_s": PRESENCE_AWAY_DELAY_S,
    "presence_home_delay_s": PRESENCE_HOME_DELAY_S,
    "window_sensor_id": WINDOW_SENSOR,
    "window_delay_s": WINDOW_DELAY_S,
    "window_close_delay_s": WINDOW_CLOSE_DELAY_S,
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
            if _is_trv_command(
                event.data["domain"], event.data["service"], event.data["service_data"], service
            )
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
    source_state: str = "heat",
    entry_id: str | None = None,
    trv_adopts: bool = False,
) -> Proxy:
    """Create the source TRV and sensors, then set up one proxy entry.

    Pass ``room_state`` to start with a room sensor that has not reported
    yet, and ``entry_id`` to prepare registry entries for the entry before
    setup.

    Pass ``trv_adopts`` whenever the test runs regulation cycles over time:
    the TRV then holds what it is sent, as a real one does.  See
    ``trv_adopts_setpoints`` for why a TRV that does not is a trap.
    """
    source_attributes = (
        {"current_temperature": 22.0, "temperature": 20.0}
        if source_state not in ("unavailable", "unknown")
        else {}
    )
    hass.states.async_set(TRV, source_state, source_attributes)
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
    if trv_adopts:
        # Registered before the first cycle: the setup itself sends nothing.
        trv_adopts_setpoints(hass)
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
        if _is_trv_command(domain, service, service_data, rejected_service):
            raise HomeAssistantError(f"{TRV} did not respond")
        return await original(self, domain, service, service_data, *args, **kwargs)

    with patch.object(ServiceRegistry, "async_call", async_call):
        yield


def on_trv_command(hass: HomeAssistant, service: str, action: Callable[[], None]) -> None:
    """Run *action* whenever ``climate.<service>`` goes to the TRV, before the TRV gets it.

    Shows what the proxy's state says while its command is on the way.
    """

    @callback
    def _listener(event: Event) -> None:
        data = event.data
        if _is_trv_command(data["domain"], data["service"], data["service_data"], service):
            action()

    hass.bus.async_listen(EVENT_CALL_SERVICE, _listener)


def trv_adopts_setpoints(hass: HomeAssistant, *, internal_c: float = 22.0) -> None:
    """Let the fake TRV hold what the proxy sends it, like a real TRV does.

    Usually reached through ``async_setup_proxy(trv_adopts=True)``; call it
    directly only to pick another ``internal_c`` or to start adopting later.

    Without this the TRV state keeps the setpoint ``async_setup_proxy`` gave
    it, which the proxy correctly reads as a TRV that drifted away from its
    command (see ``trv_drifted_setpoint``).  Any test that lets more than the
    rate-limit interval pass after a command then gets a correction it did
    not ask for, so every test that runs regulation cycles over time wants
    this.
    """

    @callback
    def _listener(event: Event) -> None:
        data = event.data
        if not _is_trv_command(
            data["domain"], data["service"], data["service_data"], "set_temperature"
        ):
            return
        hass.states.async_set(
            TRV,
            "heat",
            {
                "current_temperature": internal_c,
                "temperature": data["service_data"]["temperature"],
            },
        )

    hass.bus.async_listen(EVENT_CALL_SERVICE, _listener)


async def async_open_window(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Open the window and wait out the open delay: frost protection is on."""
    await async_set_state(hass, WINDOW_SENSOR, STATE_ON)
    await async_advance(hass, freezer, WINDOW_DELAY_S + 1)


def _is_trv_command(
    domain: str, service: str, service_data: dict[str, Any] | None, wanted: str
) -> bool:
    """True for a ``climate.<wanted>`` call addressed to the TRV."""
    return (
        domain == "climate"
        and service == wanted
        and (service_data or {}).get("entity_id") == TRV
    )
