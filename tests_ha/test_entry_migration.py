"""Config entries saved by an earlier release are migrated when they are set up.

Minor version 2 came with the new Ki default (#124): an entry saved at the
previous default 0.003 follows the new one, every other Ki stays.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .helpers import (
    CURRENT_ENTRY_MINOR_VERSION,
    DOMAIN,
    ROOM_SENSOR,
    TEMPERATURE,
    TRV,
    async_setup_proxy,
)

# CorrectionTuning.ki, and the default before it.
KI_DEFAULT = 0.001
PREVIOUS_KI_DEFAULT = 0.003

# The minor version of an entry saved by v1.5.0 and earlier.
MINOR_VERSION_BEFORE_NEW_KI = 1


async def test_ki_at_the_previous_default_follows_the_new_default(hass):
    proxy = await async_setup_proxy(
        hass,
        options={"correction_ki": PREVIOUS_KI_DEFAULT, "eco_target": 16.0},
        minor_version=MINOR_VERSION_BEFORE_NEW_KI,
    )

    assert proxy.entry.minor_version == CURRENT_ENTRY_MINOR_VERSION
    assert "correction_ki" not in proxy.entry.options
    assert proxy.state.attributes["correction_ki"] == KI_DEFAULT
    # Every other option stays.
    assert proxy.entry.options["eco_target"] == 16.0


async def test_a_ki_chosen_before_the_change_stays(hass):
    proxy = await async_setup_proxy(
        hass, options={"correction_ki": 0.005}, minor_version=MINOR_VERSION_BEFORE_NEW_KI
    )

    assert proxy.entry.minor_version == CURRENT_ENTRY_MINOR_VERSION
    assert proxy.entry.options["correction_ki"] == 0.005
    assert proxy.state.attributes["correction_ki"] == 0.005


async def test_the_previous_default_chosen_after_the_change_stays(hass):
    """From this version on a stored 0.003 is a person's choice, not the old default."""
    proxy = await async_setup_proxy(
        hass,
        options={"correction_ki": PREVIOUS_KI_DEFAULT},
        minor_version=CURRENT_ENTRY_MINOR_VERSION,
    )

    assert proxy.entry.minor_version == CURRENT_ENTRY_MINOR_VERSION
    assert proxy.entry.options["correction_ki"] == PREVIOUS_KI_DEFAULT
    assert proxy.state.attributes["correction_ki"] == PREVIOUS_KI_DEFAULT


async def test_entry_without_a_stored_ki_only_gets_the_new_version(hass):
    proxy = await async_setup_proxy(hass, minor_version=MINOR_VERSION_BEFORE_NEW_KI)

    assert proxy.entry.minor_version == CURRENT_ENTRY_MINOR_VERSION
    assert "correction_ki" not in proxy.entry.options
    assert proxy.state.attributes["correction_ki"] == KI_DEFAULT


async def test_entry_from_a_later_minor_version_is_left_alone(hass):
    """A downgrade after a later release: the entry is read as it is."""
    later_minor_version = CURRENT_ENTRY_MINOR_VERSION + 1

    proxy = await async_setup_proxy(
        hass,
        options={"correction_ki": PREVIOUS_KI_DEFAULT},
        minor_version=later_minor_version,
    )

    assert proxy.entry.minor_version == later_minor_version
    assert proxy.entry.options["correction_ki"] == PREVIOUS_KI_DEFAULT


async def test_entry_from_a_newer_major_version_is_not_set_up(hass):
    """A downgrade over a breaking change: the entry is refused, not misread."""
    hass.states.async_set(TRV, "heat", {"current_temperature": 22.0, "temperature": 20.0})
    hass.states.async_set(ROOM_SENSOR, "20.0", TEMPERATURE)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living",
        data={"source_entity_id": TRV, "external_temperature_entity_id": ROOM_SENSOR, "name": "Living"},
        options={"correction_ki": PREVIOUS_KI_DEFAULT},
        unique_id=TRV,
        version=2,
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert entry.version == 2
    assert entry.options["correction_ki"] == PREVIOUS_KI_DEFAULT


async def test_new_entry_starts_at_the_current_version(hass):
    """The config flow and async_setup_proxy agree on what a current entry is."""
    hass.states.async_set(TRV, "heat", {"current_temperature": 22.0, "temperature": 20.0})
    hass.states.async_set(ROOM_SENSOR, "20.0", TEMPERATURE)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"source_entity_id": TRV, "external_temperature_entity_id": ROOM_SENSOR, "name": "Living"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].version == 1
    assert result["result"].minor_version == CURRENT_ENTRY_MINOR_VERSION
