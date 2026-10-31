"""Preset mixin for TadoXProxyClimate: the Home Assistant side of PresetLayers.

PresetLayers decides which preset the room runs.  This mixin only forwards
events to it (sensor states, service calls, guest mode, elapsed timers),
runs the timers it asks for and applies the effects it returns.
"""

from __future__ import annotations

import logging
import time

from homeassistant.components.climate import HVACMode
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

from .climate_controllers import DEFAULT_WINDOW_ACTION, safe_float
from .const import (
    CONF_COMFORT_TARGET,
    CONF_PRESENCE_AWAY_DELAY_S,
    CONF_PRESENCE_HOME_DELAY_S,
    CONF_PRESENCE_SENSOR_ID,
    CONF_WINDOW_ACTION,
    CONF_WINDOW_CLOSE_DELAY_S,
    CONF_WINDOW_DELAY_S,
    CONF_WINDOW_SENSOR_ID,
)
from .parameters import (
    DEFAULT_PRESENCE_AWAY_DELAY_S,
    DEFAULT_PRESENCE_HOME_DELAY_S,
    DEFAULT_WINDOW_CLOSE_DELAY_S,
    DEFAULT_WINDOW_DELAY_S,
    FROST_PROTECT_C,
)
from .preset_layers import Effects, LayersConfig

_LOGGER = logging.getLogger(__name__)


class PresetMixin:
    """Preset management methods extracted from TadoXProxyClimate."""

    # ------------------------------------------------------------------
    # Effects and timers of PresetLayers
    # ------------------------------------------------------------------

    def _apply_layer_effects(self, effects: Effects) -> str | None:
        """Run the timers PresetLayers asks for and show a changed state.

        Returns the trigger of the regulation cycle it asks for (None for
        none); the caller decides how to run it.
        """
        for timer_id, delay_s in effects.timers.items():
            self._cancel_layer_timer(timer_id)
            if delay_s is not None:
                self._start_layer_timer(timer_id, delay_s)
        if effects.changed:
            self._write_state_with_linked_entities()
        return effects.cycle

    async def _async_handle_layer_effects(self, effects: Effects) -> None:
        """Apply *effects* and run the regulation cycle they ask for."""
        trigger = self._apply_layer_effects(effects)
        if trigger is not None:
            await self._async_regulation_cycle(trigger=trigger)

    @callback
    def _handle_layer_effects(self, effects: Effects) -> None:
        """Apply *effects* from a callback; the regulation cycle runs as a task."""
        trigger = self._apply_layer_effects(effects)
        if trigger is not None:
            self.hass.async_create_task(self._async_regulation_cycle(trigger=trigger))

    def _start_layer_timer(self, timer_id: str, delay_s: float) -> None:
        """Report *timer_id* to PresetLayers as elapsed after *delay_s*."""

        async def _async_elapsed(_now) -> None:
            # Cancelled or started anew meanwhile: no news.
            if self._layer_timers.get(timer_id) is not cancel:
                return
            del self._layer_timers[timer_id]
            await self._async_handle_layer_effects(
                self._layers.timer_elapsed(timer_id, time.time())
            )

        cancel = async_call_later(self.hass, delay_s, _async_elapsed)
        self._layer_timers[timer_id] = cancel

    def _cancel_layer_timer(self, timer_id: str) -> None:
        cancel = self._layer_timers.pop(timer_id, None)
        if cancel is not None:
            cancel()

    def _cancel_layer_timers(self) -> None:
        for timer_id in list(self._layer_timers):
            self._cancel_layer_timer(timer_id)

    def _layers_config(self) -> LayersConfig:
        """PresetLayers' view of the options."""
        opts = self._config_entry.options
        return LayersConfig(
            presets=self._config.presets,
            comfort_c=self._comfort_target(),
            min_target_c=self._config.min_target_c,
            max_target_c=self._config.max_target_c,
            window_delay_s=opts.get(CONF_WINDOW_DELAY_S, DEFAULT_WINDOW_DELAY_S),
            window_close_delay_s=opts.get(
                CONF_WINDOW_CLOSE_DELAY_S, DEFAULT_WINDOW_CLOSE_DELAY_S
            ),
            away_delay_s=opts.get(CONF_PRESENCE_AWAY_DELAY_S, DEFAULT_PRESENCE_AWAY_DELAY_S),
            home_delay_s=opts.get(CONF_PRESENCE_HOME_DELAY_S, DEFAULT_PRESENCE_HOME_DELAY_S),
            window_action=opts.get(CONF_WINDOW_ACTION, DEFAULT_WINDOW_ACTION),
            has_window_sensor=bool(opts.get(CONF_WINDOW_SENSOR_ID)),
            has_presence_sensor=bool(opts.get(CONF_PRESENCE_SENSOR_ID)),
        )

    # ------------------------------------------------------------------
    # Sensors
    # ------------------------------------------------------------------

    @callback
    def _async_window_changed(self, event) -> None:
        """Forward every window sensor state to PresetLayers."""
        new_state = event.data.get("new_state")
        self._handle_layer_effects(
            self._layers.window_reported(new_state.state if new_state else None, time.time())
        )

    @callback
    def _async_presence_changed(self, event) -> None:
        """Forward every presence sensor state to PresetLayers."""
        new_state = event.data.get("new_state")
        self._handle_layer_effects(
            self._layers.presence_reported(new_state.state if new_state else None, time.time())
        )

    # ------------------------------------------------------------------
    # Guest mode
    # ------------------------------------------------------------------

    @property
    def guest_mode(self) -> bool:
        """Return True while guest mode keeps nobody home from holding the room."""
        return self._layers.guest_mode

    async def async_set_guest_mode(self, enabled: bool) -> None:
        """Switch guest mode on or off (heat normally although nobody is home)."""
        if enabled != self._layers.guest_mode:
            _LOGGER.info("Guest mode %s", "enabled" if enabled else "disabled")
        await self._async_handle_layer_effects(
            self._layers.set_guest_mode(enabled, time.time())
        )

    # ------------------------------------------------------------------
    # Presets
    # ------------------------------------------------------------------

    @property
    def boost_remaining_minutes(self) -> int:
        """Return the minutes of boost left (0 without a boost)."""
        return self._layers.boost_remaining_min(time.time())

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Pick a preset (Home Assistant has checked it against the list)."""
        _LOGGER.debug("Preset picked: %s", preset_mode)
        await self._async_handle_layer_effects(
            self._layers.pick_preset(preset_mode, time.time())
        )

    def _comfort_target(self) -> float:
        """Return the configured comfort target, falling back to the default.

        The comfort_target option is absent from config_entry.options until the
        Comfort Target number entity is edited once. Reading it with no default
        yields None; callers once skipped the assignment and left the target
        stuck at a frost/eco value. Fall back to the PresetConfig default
        (20 °C) so comfort always resolves.
        """
        comfort = safe_float(self._config_entry.options.get(CONF_COMFORT_TARGET))
        if comfort is not None:
            return comfort
        return self._config.presets.comfort_target_c

    # ------------------------------------------------------------------
    # Effective setpoint calculation
    # ------------------------------------------------------------------

    def _effective_setpoint(self) -> float:
        """Calculate the effective setpoint based on HVAC mode and preset."""
        if self._hvac_mode == HVACMode.OFF:
            return FROST_PROTECT_C
        return self._layers.room_target_c
