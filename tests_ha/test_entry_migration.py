"""Config entries saved by an earlier release are migrated when they are set up.

Minor version 2 came with v1.6.0 (#124).  Its step only raises the version:
every stored option stays, whatever its value.  (The pre-releases
v1.6.0-beta.1 and beta.2 dropped a stored Ki of 0.003 in this step.)
"""
from __future__ import annotations

import pytest
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

# CorrectionTuning.ki
KI_DEFAULT = 0.001
# The Ki default up to v1.5.0.  The options dialog stores every field it shows,
# so an entry saved there once holds it.
V150_KI = 0.003
# The Ki default of the pre-releases v1.6.0-beta.1 and beta.2.  An entry whose
# options dialog was saved while one of them ran holds it.
BETA_KI = 0.001

# The minor version of an entry saved by v1.5.0 and earlier.
MINOR_VERSION_UP_TO_V150 = 1


@pytest.mark.parametrize("stored_ki", [V150_KI, BETA_KI, 0.005])
async def test_a_stored_ki_stays(hass, stored_ki):
    proxy = await async_setup_proxy(
        hass,
        options={"correction_ki": stored_ki, "eco_target": 16.0},
        minor_version=MINOR_VERSION_UP_TO_V150,
    )

    assert proxy.entry.minor_version == CURRENT_ENTRY_MINOR_VERSION
    assert proxy.entry.options["correction_ki"] == stored_ki
    assert proxy.state.attributes["correction_ki"] == stored_ki
    # Every other option stays as well.
    assert proxy.entry.options["eco_target"] == 16.0


@pytest.mark.parametrize(
    "minor_version",
    [
        MINOR_VERSION_UP_TO_V150,
        # A pre-release dropped a stored 0.003 and left the entry at this one.
        CURRENT_ENTRY_MINOR_VERSION,
    ],
)
async def test_entry_without_a_stored_ki_runs_the_default(hass, minor_version):
    proxy = await async_setup_proxy(hass, minor_version=minor_version)

    assert proxy.entry.minor_version == CURRENT_ENTRY_MINOR_VERSION
    assert "correction_ki" not in proxy.entry.options
    assert proxy.state.attributes["correction_ki"] == KI_DEFAULT


async def test_a_ki_saved_during_a_pre_release_stays(hass):
    """The entry is at the current version already, so nothing migrates it."""
    proxy = await async_setup_proxy(
        hass,
        options={"correction_ki": BETA_KI},
        minor_version=CURRENT_ENTRY_MINOR_VERSION,
    )

    assert proxy.entry.minor_version == CURRENT_ENTRY_MINOR_VERSION
    assert proxy.entry.options["correction_ki"] == BETA_KI
    assert proxy.state.attributes["correction_ki"] == BETA_KI


async def test_entry_from_a_later_minor_version_is_left_alone(hass):
    """A downgrade after a later release: the entry is read as it is."""
    later_minor_version = CURRENT_ENTRY_MINOR_VERSION + 1

    proxy = await async_setup_proxy(
        hass,
        options={"correction_ki": V150_KI},
        minor_version=later_minor_version,
    )

    assert proxy.entry.minor_version == later_minor_version
    assert proxy.entry.options["correction_ki"] == V150_KI


async def test_entry_from_a_newer_major_version_is_not_set_up(hass):
    """A downgrade over a breaking change: the entry is refused, not misread."""
    hass.states.async_set(TRV, "heat", {"current_temperature": 22.0, "temperature": 20.0})
    hass.states.async_set(ROOM_SENSOR, "20.0", TEMPERATURE)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living",
        data={"source_entity_id": TRV, "external_temperature_entity_id": ROOM_SENSOR, "name": "Living"},
        options={"correction_ki": V150_KI},
        unique_id=TRV,
        version=2,
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert entry.version == 2
    assert entry.options["correction_ki"] == V150_KI


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
