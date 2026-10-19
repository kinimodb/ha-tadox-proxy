"""Device registry info shared by every Tado X Proxy entity."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def proxy_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the full device info of the proxy device for a config entry.

    Every platform must return the complete info, not just the identifiers:
    the platforms are set up concurrently, and whichever entity registers
    first creates the device.  With identifiers only, the device is created
    without a name, and that entity's id loses the device-name prefix
    (``number.eco_temperature`` instead of ``number.living_eco_temperature``).
    """
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Tado X Proxy",
        model="Feedforward + PI Regulator",
    )
