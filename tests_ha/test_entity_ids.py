"""Entity registry: every entity sits on the proxy device and carries its name."""
from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .helpers import DOMAIN, async_setup_proxy

# Device slug of the entry title "Living" set up by async_setup_proxy.
DEVICE_SLUG = "living"
PLATFORMS = {"binary_sensor", "climate", "number", "sensor", "switch"}


async def test_every_entity_is_on_the_named_device(hass):
    proxy = await async_setup_proxy(hass)

    # async_get_device(identifiers=...) is deprecated on newer HA versions.
    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), proxy.entry.entry_id)
    assert device.identifiers == {(DOMAIN, proxy.entry.entry_id)}
    assert device.name == "Living"

    entries = er.async_entries_for_config_entry(er.async_get(hass), proxy.entry.entry_id)
    assert {entry.domain for entry in entries} == PLATFORMS
    for entry in entries:
        assert entry.device_id == device.id, entry.entity_id
        object_id = entry.entity_id.split(".", 1)[1]
        assert object_id == DEVICE_SLUG or object_id.startswith(f"{DEVICE_SLUG}_"), entry.entity_id


async def test_existing_unprefixed_ids_are_kept(hass):
    """Ids an older version registered without the prefix are not renamed."""
    entry_id = "legacy"
    registry = er.async_get(hass)
    legacy = {
        ("number", f"{entry_id}_eco_target"): "eco_temperature",
        ("binary_sensor", f"{entry_id}_sensor_degraded"): "sensor_degraded",
    }
    for (platform, unique_id), object_id in legacy.items():
        registry.async_get_or_create(platform, DOMAIN, unique_id, suggested_object_id=object_id)

    proxy = await async_setup_proxy(hass, entry_id=entry_id)

    assert proxy.number("eco_target") == "number.eco_temperature"
    assert proxy.entity_id("binary_sensor", "sensor_degraded") == "binary_sensor.sensor_degraded"
    # Entities without an old registry entry still get the prefix.
    assert proxy.number("comfort_target").startswith(f"number.{DEVICE_SLUG}_")
