"""Commands the proxy sends to the source TRV."""
from __future__ import annotations

from homeassistant.components.climate import HVACMode

from .helpers import CONTROL_INTERVAL_S, TRV, async_advance, async_setup_proxy


async def test_regulation_cycle_sends_setpoint_to_trv(hass, freezer):
    proxy = await async_setup_proxy(hass)
    assert proxy.trv_commands("set_temperature") == []

    await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    sent = proxy.trv_commands("set_temperature")
    assert len(sent) == 1
    assert sent[0]["hvac_mode"] == HVACMode.HEAT
    assert sent[0]["temperature"] == proxy.state.attributes["target_for_tado_c"]
    # The TRV reads 22 °C in a 20 °C room, so it needs a higher setpoint.
    assert sent[0]["temperature"] > proxy.target


async def test_hvac_mode_is_forwarded_to_trv(hass):
    proxy = await async_setup_proxy(hass)

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)
    assert proxy.state.state == HVACMode.OFF
    assert proxy.trv_commands("set_hvac_mode") == [{"entity_id": TRV, "hvac_mode": HVACMode.OFF}]

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.HEAT)
    assert proxy.state.state == HVACMode.HEAT
    assert [c["hvac_mode"] for c in proxy.trv_commands("set_hvac_mode")] == [HVACMode.OFF, HVACMode.HEAT]
