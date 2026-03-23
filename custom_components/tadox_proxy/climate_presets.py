"""Preset management mixin for TadoXProxyClimate."""

from __future__ import annotations

import logging

from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_NONE,
    HVACMode,
)
from homeassistant.core import callback

from .const import (
    CONF_COMFORT_TARGET,
    CONF_PRESENCE_AWAY_DELAY_S,
    CONF_PRESENCE_HOME_DELAY_S,
    CONF_PRESENCE_SENSOR_ID,
    CONF_WINDOW_CLOSE_DELAY_S,
    CONF_WINDOW_DELAY_S,
    CONF_WINDOW_SENSOR_ID,
    PRESET_FROST_PROTECTION,
    PRESET_LIST,
    safe_float,
)
from .parameters import FROST_PROTECT_C

_LOGGER = logging.getLogger(__name__)


def async_call_later_boost(hass, delay_s, callback):
    """Thin wrapper so boost timer scheduling stays in this module."""
    from homeassistant.helpers.event import async_call_later  # noqa: PLC0415
    return async_call_later(hass, delay_s, callback)


class PresetMixin:
    """Preset management methods extracted from TadoXProxyClimate."""

    # ------------------------------------------------------------------
    # Window sensor
    # ------------------------------------------------------------------

    @callback
    def _async_window_changed(self, event) -> None:
        """Handle window sensor state changes."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unavailable", "unknown"):
            return

        if new_state.state == "on":  # window opened
            delay = self._config_entry.options.get(CONF_WINDOW_DELAY_S, 30)
            self._window_ctrl.handle_window_opened(
                self.hass, delay, self._async_window_action
            )
        else:  # window closed
            close_delay = self._config_entry.options.get(CONF_WINDOW_CLOSE_DELAY_S, 120)
            should_restore = self._window_ctrl.handle_window_closed(
                self.hass, close_delay, self._async_window_close_action
            )
            if should_restore:
                self._restore_window_state()

    async def _async_window_action(self, _now) -> None:
        """Switch to frost protection preset after window-open delay."""
        # Revalidate: only proceed if window sensor is still "on"
        window_sensor = self._config_entry.options.get(CONF_WINDOW_SENSOR_ID)
        if window_sensor:
            current = self.hass.states.get(window_sensor)
            if current is None or current.state != "on":
                _LOGGER.info(
                    "Window action skipped: sensor is now %s",
                    current.state if current else "unavailable",
                )
                self._window_ctrl.cancel_all()
                return

        # If boost is active, cancel it and use the pre-boost preset as saved state
        if self._boost_cancel is not None:
            self._boost_cancel()
            self._boost_cancel = None
            saved_preset = self._boost_saved_preset
            saved_temp = self._boost_saved_temp
        else:
            saved_preset = self._preset_mode
            saved_temp = self._target_temp
        # Never save frost protection as the "previous" preset – fall back
        # to comfort so the user isn't stuck in frost mode after restore.
        if saved_preset == PRESET_FROST_PROTECTION:
            saved_preset = PRESET_COMFORT
            comfort = safe_float(self._config_entry.options.get(CONF_COMFORT_TARGET))
            if comfort is not None:
                saved_temp = comfort
        self._window_ctrl.activate(saved_preset, saved_temp)
        self._preset_mode = PRESET_FROST_PROTECTION
        _LOGGER.info("Window open: switching to frost protection")
        self.async_write_ha_state()
        await self._async_regulation_cycle(trigger="window_open")

    async def _async_window_close_action(self, _now) -> None:
        """Restore previous preset after window-close delay expired."""
        self._restore_window_state()

    def _restore_window_state(self) -> None:
        """Restore preset after window is closed."""
        saved = self._window_ctrl.restore()
        if saved.preset is not None:
            preset_to_restore = saved.preset
            # Safety net: never restore frost protection from window automation
            if preset_to_restore == PRESET_FROST_PROTECTION:
                preset_to_restore = PRESET_COMFORT
            self._preset_mode = preset_to_restore
            if preset_to_restore == PRESET_COMFORT:
                comfort = safe_float(self._config_entry.options.get(CONF_COMFORT_TARGET))
                if comfort is not None:
                    self._target_temp = comfort
            elif saved.temp is not None:
                self._target_temp = saved.temp

            # If restoring BOOST, start the expiry timer so it doesn't run
            # indefinitely. Use COMFORT as the post-boost fallback since the
            # original pre-boost context was lost when window automation
            # took over.
            if preset_to_restore == PRESET_BOOST:
                self._boost_saved_preset = PRESET_COMFORT
                comfort = safe_float(self._config_entry.options.get(CONF_COMFORT_TARGET))
                self._boost_saved_temp = comfort if comfort is not None else self._target_temp
                duration_s = self._config.presets.boost_duration_min * 60
                self._boost_cancel = async_call_later_boost(
                    self.hass, duration_s, self._async_boost_expired
                )
                _LOGGER.info(
                    "Boost restored after window close, timer started for %d min",
                    self._config.presets.boost_duration_min,
                )
        _LOGGER.info("Window closed: restoring previous preset")
        self.hass.async_create_task(
            self._async_regulation_cycle(trigger="window_closed")
        )
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Presence sensor
    # ------------------------------------------------------------------

    @callback
    def _async_presence_changed(self, event) -> None:
        """Handle presence sensor state changes."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unavailable", "unknown"):
            return

        if new_state.state == "off":  # nobody home
            delay = self._config_entry.options.get(CONF_PRESENCE_AWAY_DELAY_S, 600)
            self._presence_ctrl.handle_presence_away(
                self.hass, delay, self._async_presence_away_action
            )
        else:  # someone home
            home_delay = self._config_entry.options.get(CONF_PRESENCE_HOME_DELAY_S, 30)
            if self._presence_ctrl.handle_presence_home(
                self.hass, home_delay, self._async_presence_home_action,
            ):
                self._restore_presence_state()

    async def _async_presence_away_action(self, _now) -> None:
        """Switch to AWAY preset after presence-away delay."""
        # Safety: if the controller is already active (e.g. a stale timer fired
        # after a sensor flicker), do not overwrite the saved preset.
        if self._presence_ctrl.is_active:
            _LOGGER.info(
                "Presence away action skipped: controller already active"
            )
            return

        # Revalidate: only proceed if presence sensor is still "off"
        presence_sensor = self._config_entry.options.get(CONF_PRESENCE_SENSOR_ID)
        if presence_sensor:
            current = self.hass.states.get(presence_sensor)
            if current is None or current.state != "off":
                _LOGGER.info(
                    "Presence away action skipped: sensor is now %s",
                    current.state if current else "unavailable",
                )
                self._presence_ctrl.cancel_timer()
                return

        # If window automation is active, don't override frost protection.
        # Save current state for when presence returns, but keep frost mode.
        if self._window_ctrl.is_active:
            self._presence_ctrl.activate(
                self._window_ctrl.get_saved().preset or PRESET_COMFORT,
                self._window_ctrl.get_saved().temp,
            )
            # Update window saved state to AWAY so frost->close restores AWAY
            self._window_ctrl.update_saved(PRESET_AWAY, self._config.presets.away_target_c)
            _LOGGER.info("Presence away during window-open: saved AWAY for later")
            return

        # If boost is active, cancel it and use the pre-boost preset as saved state
        if self._boost_cancel is not None:
            self._boost_cancel()
            self._boost_cancel = None
            saved_preset = self._boost_saved_preset
            saved_temp = self._boost_saved_temp
        else:
            saved_preset = self._preset_mode
            saved_temp = self._target_temp
        self._presence_ctrl.activate(saved_preset, saved_temp)
        self._preset_mode = PRESET_AWAY
        _LOGGER.info("Presence away: switching to AWAY preset")
        self.async_write_ha_state()
        await self._async_regulation_cycle(trigger="presence_away")

    async def _async_presence_home_action(self, _now) -> None:
        """Restore previous preset after presence-home delay."""
        # Revalidate: only proceed if presence sensor is still "on"
        presence_sensor = self._config_entry.options.get(CONF_PRESENCE_SENSOR_ID)
        if presence_sensor:
            current = self.hass.states.get(presence_sensor)
            if current is None or current.state == "off":
                _LOGGER.info(
                    "Presence home action skipped: sensor is now %s",
                    current.state if current else "unavailable",
                )
                return
        self._restore_presence_state()

    def _restore_presence_state(self) -> None:
        """Restore preset after presence returns."""
        saved = self._presence_ctrl.restore()
        if saved.preset is None:
            _LOGGER.info("Presence home: nothing to restore")
            return

        # If window automation is active, update the window's saved state
        # instead of changing the current preset (frost protection stays).
        if self._window_ctrl.is_active:
            self._window_ctrl.update_saved(saved.preset, saved.temp)
            _LOGGER.info(
                "Presence home during window-open: saved %s for window-close restore",
                saved.preset,
            )
            return

        self._preset_mode = saved.preset
        # If restoring BOOST, start the expiry timer so it doesn't run
        # indefinitely.  Use COMFORT as the post-boost fallback since the
        # original pre-boost context was lost when presence automation
        # took over.
        if saved.preset == PRESET_BOOST:
            self._boost_saved_preset = PRESET_COMFORT
            comfort = safe_float(self._config_entry.options.get(CONF_COMFORT_TARGET))
            self._boost_saved_temp = comfort if comfort is not None else self._target_temp
            duration_s = self._config.presets.boost_duration_min * 60
            self._boost_cancel = async_call_later_boost(
                self.hass, duration_s, self._async_boost_expired
            )
            _LOGGER.info(
                "Boost restored after presence home, timer started for %d min",
                self._config.presets.boost_duration_min,
            )
        # If restoring COMFORT, take the current comfort_target from options
        # (it may have been changed via number entity while away).
        elif saved.preset == PRESET_COMFORT:
            comfort = safe_float(self._config_entry.options.get(CONF_COMFORT_TARGET))
            if comfort is not None:
                self._target_temp = comfort
        elif saved.temp is not None:
            self._target_temp = saved.temp
        _LOGGER.info("Presence home: restoring previous preset")
        self.hass.async_create_task(
            self._async_regulation_cycle(trigger="presence_home")
        )
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Preset switching
    # ------------------------------------------------------------------

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if preset_mode not in PRESET_LIST:
            _LOGGER.warning("Unknown preset mode: %s", preset_mode)
            return

        # Cancel window close delay if user manually changes preset
        if self._window_ctrl.close_delay_active:
            self._window_ctrl.cancel_all()
            _LOGGER.info("Window close delay cancelled – user changed preset to %s", preset_mode)

        # If presence automation is active (away due to presence sensor),
        # update the saved state so the new preset is restored when someone
        # returns home, but keep away mode active.
        if self._presence_ctrl.is_active and preset_mode != PRESET_AWAY:
            if preset_mode == PRESET_COMFORT:
                comfort = safe_float(self._config_entry.options.get(CONF_COMFORT_TARGET))
                save_temp = comfort if comfort is not None else self._target_temp
            elif preset_mode == PRESET_ECO:
                save_temp = self._config.presets.eco_target_c
            elif preset_mode == PRESET_BOOST:
                save_temp = self._config.presets.boost_target_c
            elif preset_mode == PRESET_FROST_PROTECTION:
                save_temp = self._config.presets.frost_protection_target_c
            else:
                save_temp = self._target_temp
            self._presence_ctrl.update_saved(preset_mode, save_temp)
            _LOGGER.info(
                "Presence away: preset %s saved for restore, keeping away mode",
                preset_mode,
            )
            self.async_write_ha_state()
            return

        # If window automation is active (frost protection due to open window),
        # update the saved state so the new preset is restored when the window
        # closes, but keep frost protection active.
        if self._window_ctrl.is_active and preset_mode != PRESET_FROST_PROTECTION:
            if preset_mode == PRESET_COMFORT:
                comfort = safe_float(self._config_entry.options.get(CONF_COMFORT_TARGET))
                save_temp = comfort if comfort is not None else self._target_temp
            elif preset_mode == PRESET_ECO:
                save_temp = self._config.presets.eco_target_c
            elif preset_mode == PRESET_AWAY:
                save_temp = self._config.presets.away_target_c
            else:
                save_temp = self._target_temp
            self._window_ctrl.update_saved(preset_mode, save_temp)
            _LOGGER.info(
                "Window open: preset %s saved for restore, keeping frost protection",
                preset_mode,
            )
            # Keep frost protection active – do not change preset_mode
            self.async_write_ha_state()
            return

        old_preset = self._preset_mode
        self._preset_mode = preset_mode

        # Cancel any running boost timer
        if self._boost_cancel is not None:
            self._boost_cancel()
            self._boost_cancel = None

        # When switching to COMFORT, restore the stored comfort target
        if preset_mode == PRESET_COMFORT:
            comfort = safe_float(self._config_entry.options.get(CONF_COMFORT_TARGET))
            if comfort is not None:
                self._target_temp = comfort

        # Start boost timer if entering boost mode
        if preset_mode == PRESET_BOOST:
            # Only update the saved preset if we're not already in boost,
            # otherwise keep the original pre-boost preset to avoid a loop
            # where boost restores into boost indefinitely.
            if old_preset != PRESET_BOOST:
                self._boost_saved_preset = old_preset
                self._boost_saved_temp = self._target_temp
            duration_s = self._config.presets.boost_duration_min * 60
            self._boost_cancel = async_call_later_boost(
                self.hass, duration_s, self._async_boost_expired
            )
            _LOGGER.info(
                "Boost started for %d min", self._config.presets.boost_duration_min
            )

        _LOGGER.debug("Preset changed: %s → %s", old_preset, preset_mode)
        self.async_write_ha_state()
        await self._async_regulation_cycle(trigger="preset_change")

    async def _async_boost_expired(self, _now) -> None:
        """Called when the boost timer expires – revert to previous preset."""
        self._boost_cancel = None
        restore_preset = self._boost_saved_preset
        _LOGGER.info("Boost expired, reverting to %s", restore_preset)

        # PRESET_NONE (manual mode) is not in PRESET_LIST and would be rejected
        # by async_set_preset_mode, so handle it directly.
        if restore_preset == PRESET_NONE:
            if self._boost_saved_temp is not None:
                self._target_temp = self._boost_saved_temp
            self._preset_mode = PRESET_NONE
            self.async_write_ha_state()
            await self._async_regulation_cycle(trigger="boost_expired")
        else:
            await self.async_set_preset_mode(restore_preset)

    # ------------------------------------------------------------------
    # Effective setpoint calculation
    # ------------------------------------------------------------------

    def _effective_setpoint(self) -> float:
        """Calculate the effective setpoint based on HVAC mode and preset."""
        if self._hvac_mode == HVACMode.OFF:
            return FROST_PROTECT_C

        if self._preset_mode in (PRESET_COMFORT, PRESET_NONE):
            return self._target_temp
        elif self._preset_mode == PRESET_ECO:
            return self._config.presets.eco_target_c
        elif self._preset_mode == PRESET_BOOST:
            return self._config.presets.boost_target_c
        elif self._preset_mode == PRESET_AWAY:
            return self._config.presets.away_target_c
        elif self._preset_mode == PRESET_FROST_PROTECTION:
            return self._config.presets.frost_protection_target_c

        return self._target_temp
