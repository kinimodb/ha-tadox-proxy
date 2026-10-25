"""Someone turns the dial on the TRV: the proxy notices and corrects it."""
from __future__ import annotations

from homeassistant.components.climate import PRESET_NONE

from .helpers import (
    PRESET_FROST_PROTECTION,
    TRV,
    async_advance,
    async_open_window,
    async_set_state,
    async_setup_proxy,
    trv_adopts_setpoints,
)

# DEFAULT_CONTROL_INTERVAL_S – the regulation cycle runs on this timer.
CONTROL_INTERVAL_S = 60
# RegulationConfig.min_command_interval_s
MIN_COMMAND_INTERVAL_S = 180
# BehaviourConfig.follow_grace_s – our own command's echo is ignored this long.
FOLLOW_GRACE_S = 20
# PresetConfig.frost_protection_target_c
FROST_C = 7.0
# The TRV reads 22 °C in the 20 °C room of the test setup.
TRV_INTERNAL_C = 22.0
# Where the dial is turned to – far above every preset target.
DIALLED_C = 24.0


def _setpoints(proxy) -> list[float]:
    return [c["temperature"] for c in proxy.trv_commands("set_temperature")]


async def _async_set_trv_setpoint(hass, value: float) -> None:
    """Report a setpoint on the TRV, as a turned dial does."""
    await async_set_state(
        hass, TRV, "heat", {"current_temperature": TRV_INTERNAL_C, "temperature": value}
    )


async def _async_turn_the_dial(hass, freezer, to_c: float = DIALLED_C) -> None:
    """Wait out the grace period, turn the dial, let the coordinator read it."""
    await async_advance(hass, freezer, FOLLOW_GRACE_S + 1)
    await _async_set_trv_setpoint(hass, to_c)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)


async def test_dial_change_during_an_open_window_is_corrected(hass, freezer):
    """The reported bug: frost protection and a dial at 24 °C heated forever.

    The computed target does not move while the window is open, so every
    cycle compared against its own last command and sent nothing.
    """
    proxy = await async_setup_proxy(hass)
    trv_adopts_setpoints(hass, internal_c=TRV_INTERNAL_C)
    await async_open_window(hass, freezer)
    assert proxy.preset == PRESET_FROST_PROTECTION
    sent_before = len(_setpoints(proxy))

    await _async_turn_the_dial(hass, freezer)
    await async_advance(hass, freezer, MIN_COMMAND_INTERVAL_S)

    assert len(_setpoints(proxy)) > sent_before
    assert proxy.state.attributes["regulation_reason"] == "sent(trv_drift)"
    # The correction pushes the TRV back down, it does not follow the dial.
    assert _setpoints(proxy)[-1] <= FROST_C


async def test_dial_change_waits_for_the_rate_limit(hass, freezer):
    """Battery: a correction is no reason to skip the waiting period."""
    proxy = await async_setup_proxy(hass)
    trv_adopts_setpoints(hass, internal_c=TRV_INTERNAL_C)
    await async_open_window(hass, freezer)
    sent_before = len(_setpoints(proxy))

    await _async_turn_the_dial(hass, freezer)

    assert len(_setpoints(proxy)) == sent_before
    assert proxy.state.attributes["regulation_reason"].startswith("rate_limited(")


async def test_dial_change_in_a_steady_preset_is_corrected(hass, freezer):
    """Not only frost protection: any preset whose target stands still."""
    proxy = await async_setup_proxy(hass)
    trv_adopts_setpoints(hass, internal_c=TRV_INTERNAL_C)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    regulated_c = _setpoints(proxy)[-1]
    assert regulated_c > proxy.target  # feedforward: the TRV needs more

    await _async_turn_the_dial(hass, freezer)
    await async_advance(hass, freezer, MIN_COMMAND_INTERVAL_S)

    assert proxy.preset != PRESET_NONE  # the preset is untouched
    assert _setpoints(proxy)[-1] == regulated_c
    assert proxy.state.attributes["regulation_reason"] == "sent(trv_drift)"


async def test_a_trv_on_our_setpoint_gets_no_commands(hass, freezer):
    """The proxy must not fight the echo of its own command.

    The TRV adopting what we sent is a setpoint change on the TRV too –
    mistaking it for a dial change would send a command every cycle.
    """
    proxy = await async_setup_proxy(hass)
    trv_adopts_setpoints(hass, internal_c=TRV_INTERNAL_C)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    sent = _setpoints(proxy)
    assert len(sent) == 1

    for _ in range(2 * MIN_COMMAND_INTERVAL_S // CONTROL_INTERVAL_S):
        await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    assert _setpoints(proxy) == sent
    assert proxy.state.attributes["regulation_reason"] == "already_at_target"


async def test_a_report_inside_the_grace_period_is_not_a_dial_change(hass, freezer):
    """A TRV still reporting another value right after our command is no dial.

    The waiting period is shortened here, so only the grace period can hold
    the correction back.
    """
    proxy = await async_setup_proxy(
        hass, options={"min_command_interval_s": 10, "follow_grace_s": 120}
    )
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    sent_before = len(_setpoints(proxy))

    await _async_set_trv_setpoint(hass, DIALLED_C)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    assert len(_setpoints(proxy)) == sent_before
    assert proxy.state.attributes["regulation_reason"] == "already_at_target"


async def test_following_the_dial_takes_it_as_the_new_target(hass, freezer):
    """With follow enabled the dial is the user's decision, not a drift."""
    proxy = await async_setup_proxy(hass, options={"follow_tado_input": True})
    trv_adopts_setpoints(hass, internal_c=TRV_INTERNAL_C)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    await _async_turn_the_dial(hass, freezer)
    await async_advance(hass, freezer, MIN_COMMAND_INTERVAL_S)

    assert proxy.preset == PRESET_NONE
    assert proxy.target == DIALLED_C
    # Regulating towards the dialled room temperature, not back to 20 °C.
    assert _setpoints(proxy)[-1] > DIALLED_C
