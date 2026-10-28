"""Preset management mixin for TadoXProxyClimate."""

from __future__ import annotations

import logging
import time

from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_NONE,
    HVACMode,
)
from homeassistant.core import callback

from .climate_controllers import (
    CHANGE_APPLY,
    CHANGE_FOR_PRESENCE,
    SavedState,
    is_binary_sensor_transition,
    presence_counts_as_home,
    preset_after_window,
    preset_target_c,
    route_change,
)
from .const import (
    CONF_COMFORT_TARGET,
    CONF_MANUAL_MODE_ACTION,
    CONF_MANUAL_MODE_DURATION_MIN,
    CONF_PRESENCE_AWAY_DELAY_S,
    CONF_PRESENCE_HOME_DELAY_S,
    CONF_PRESENCE_SENSOR_ID,
    CONF_WINDOW_CLOSE_DELAY_S,
    CONF_WINDOW_DELAY_S,
    CONF_WINDOW_SENSOR_ID,
    DEFAULT_MANUAL_MODE_ACTION,
    DEFAULT_MANUAL_MODE_DURATION_MIN,
    MANUAL_MODE_SCHEDULE,
    MANUAL_MODE_TIMER,
    PRESET_FROST_PROTECTION,
    PRESET_LIST,
    safe_float,
)
from .parameters import (
    DEFAULT_PRESENCE_AWAY_DELAY_S,
    DEFAULT_PRESENCE_HOME_DELAY_S,
    DEFAULT_WINDOW_CLOSE_DELAY_S,
    DEFAULT_WINDOW_DELAY_S,
    FROST_PROTECT_C,
)

_LOGGER = logging.getLogger(__name__)


class PresetMixin:
    """Preset management methods extracted from TadoXProxyClimate."""

    # ------------------------------------------------------------------
    # Boost timer helpers
    # ------------------------------------------------------------------

    @property
    def boost_remaining_minutes(self) -> int:
        """Return remaining boost minutes (0 when boost is not active)."""
        return self._boost.remaining_minutes()

    def _start_boost(self) -> None:
        """Start the boost expiry timer with the configured duration."""
        self._boost.start(
            self.hass,
            self._config.presets.boost_duration_min * 60,
            self._async_boost_expired,
        )

    def _begin_manual_return(self, target_c: float) -> None:
        """Arm a timed return, keeping the original preset across adjustments."""
        action = self._config_entry.options.get(
            CONF_MANUAL_MODE_ACTION, DEFAULT_MANUAL_MODE_ACTION
        )
        if action not in (MANUAL_MODE_TIMER, MANUAL_MODE_SCHEDULE):
            self._manual_return.cancel()
            return

        if self._manual_return.is_active:
            previous = self._manual_return.previous_preset
        elif self._presence_ctrl.is_active:
            previous = self._presence_ctrl.get_saved().preset
        elif self._window_ctrl.is_active:
            previous = self._window_ctrl.get_saved().preset
        elif self._boost.is_active:
            # Boost is temporary; return to the preset it would have restored.
            previous = self._boost.saved.preset
        else:
            previous = self._preset_mode

        if previous not in PRESET_LIST:
            scheduled = self._scheduled_action()
            previous = scheduled if scheduled in PRESET_LIST else (
                PRESET_COMFORT if scheduled == "off" else None
            )
        if previous not in PRESET_LIST:
            self._manual_return.cancel()
            return

        if action == MANUAL_MODE_SCHEDULE:
            boundary = self._next_schedule_boundary_ts()
            if boundary is None or boundary <= time.time():
                _LOGGER.warning("Cannot time Manual until the next schedule block: no boundary")
                self._manual_return.cancel()
                return
            duration_s = boundary + 0.5 - time.time()
        else:
            minutes = self._config_entry.options.get(
                CONF_MANUAL_MODE_DURATION_MIN, DEFAULT_MANUAL_MODE_DURATION_MIN
            )
            duration_s = minutes * 60
        self._manual_return.start(
            self.hass, previous, target_c, duration_s,
            self._async_manual_return_expired,
        )

    async def _async_manual_return_expired(self, _now) -> None:
        """Resume the current schedule if set, otherwise the prior preset."""
        previous = self._manual_return.expire()
        action = self._scheduled_action()
        if action == "off":
            if previous not in PRESET_LIST:
                return
            if self._presence_ctrl.is_active or self._window_ctrl.is_active:
                await self.async_set_preset_mode(previous)
            else:
                # Leave Manual without sending a stale target before OFF.
                self._preset_mode = previous
                self._target_temp = self._get_preset_target(previous)
                self.async_write_ha_state()
            await self._async_apply_schedule_state()
            return
        if action in PRESET_LIST:
            await self._async_apply_schedule_state(resume_manual=True)
            return
        if previous in PRESET_LIST:
            await self.async_set_preset_mode(previous)

    # ------------------------------------------------------------------
    # Window sensor
    # ------------------------------------------------------------------

    @callback
    def _async_window_changed(self, event) -> None:
        """Handle window sensor state changes."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if not is_binary_sensor_transition(
            old_state.state if old_state else None,
            new_state.state if new_state else None,
        ):
            return

        if new_state.state == "on":  # window opened
            delay = self._config_entry.options.get(
                CONF_WINDOW_DELAY_S, DEFAULT_WINDOW_DELAY_S
            )
            self._window_ctrl.handle_window_opened(
                self.hass, delay, self._async_window_action
            )
        else:  # window closed
            close_delay = self._config_entry.options.get(
                CONF_WINDOW_CLOSE_DELAY_S, DEFAULT_WINDOW_CLOSE_DELAY_S
            )
            should_restore = self._window_ctrl.handle_window_closed(
                self.hass, close_delay, self._async_window_close_action
            )
            if should_restore:
                self._restore_window_state()
                return
        # A close delay that started or ended shows right away.
        self.async_write_ha_state()

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
        saved = self._boost.interrupt(self._preset_mode, self._target_temp)
        saved_preset, saved_temp = saved.preset, saved.temp
        # Never save frost protection as the "previous" preset – fall back
        # to comfort so the user isn't stuck in frost mode after restore.
        if saved_preset == PRESET_FROST_PROTECTION:
            saved_preset = PRESET_COMFORT
            saved_temp = self._comfort_target()
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
        self._apply_window_restore(self._window_ctrl.restore())
        _LOGGER.info("Window closed: restoring previous preset")
        self.hass.async_create_task(self._async_resume_after_window())
        self.async_write_ha_state()

    async def _async_resume_after_window(self) -> None:
        """Reconcile the current schedule and send the restored setpoint."""
        if self._scheduled_action() is not None:
            await self._async_apply_schedule_state()
        await self._async_regulation_cycle(trigger="window_closed")

    def _end_window_close_delay(self) -> bool:
        """End a running window close delay early for a user change.

        The preset the window saved comes back first, as if the delay had
        expired now, so the change applies to that preset – e.g. it is saved
        for later while presence keeps the room in Away.  The caller runs the
        regulation cycle once the change is applied; a cycle for the restored
        preset alone could use up the rate limit before the change is sent.
        Returns True when a close delay was ended.
        """
        saved = self._window_ctrl.end_close_delay()
        if saved is None:
            return False
        self._apply_window_restore(saved)
        _LOGGER.info(
            "Window close delay ended early by a user change – %s restored first",
            self._preset_mode,
        )
        return True

    def _apply_window_restore(self, saved: SavedState) -> None:
        """Switch back to the preset the ending window automation saved."""
        if saved.preset is None:
            # Nothing saved: frost protection was not active (any more).
            return
        restored = preset_after_window(
            saved,
            presence_home=self._presence_effectively_home(),
            comfort_c=self._comfort_target(),
            heating_off=self._hvac_mode == HVACMode.OFF,
        )
        if restored.preset != saved.preset:
            _LOGGER.info(
                "Window restore: %s replaced by %s", saved.preset, restored.preset
            )
        self._preset_mode = restored.preset
        if restored.temp is not None:
            self._target_temp = restored.temp

        # If restoring BOOST, start the expiry timer so it doesn't run
        # indefinitely. Use COMFORT as the post-boost fallback since the
        # original pre-boost context was lost when window automation
        # took over.
        if restored.preset == PRESET_BOOST:
            self._boost.save(PRESET_COMFORT, self._comfort_target())
            self._start_boost()
            _LOGGER.info(
                "Boost restored after window close, timer started for %d min",
                self._config.presets.boost_duration_min,
            )

    # ------------------------------------------------------------------
    # Presence sensor
    # ------------------------------------------------------------------

    @callback
    def _async_presence_changed(self, event) -> None:
        """Handle presence sensor state changes."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if not is_binary_sensor_transition(
            old_state.state if old_state else None,
            new_state.state if new_state else None,
        ):
            return

        if new_state.state == "off":  # nobody home
            self._schedule_presence_away()
        else:  # someone home
            home_delay = self._config_entry.options.get(
                CONF_PRESENCE_HOME_DELAY_S, DEFAULT_PRESENCE_HOME_DELAY_S
            )
            if self._presence_ctrl.handle_presence_home(
                self.hass, home_delay, self._async_presence_home_action,
            ):
                self._restore_presence_state()

    def _schedule_presence_away(self) -> None:
        """Arm the away timer with the configured delay (nobody home)."""
        delay = self._config_entry.options.get(
            CONF_PRESENCE_AWAY_DELAY_S, DEFAULT_PRESENCE_AWAY_DELAY_S
        )
        self._presence_ctrl.handle_presence_away(
            self.hass, delay, self._async_presence_away_action
        )

    def _presence_effectively_home(self) -> bool:
        """Return True if presence counts as home (sensor home or guest mode).

        Unavailable/unknown or missing sensors do not count as home.
        """
        presence_sensor = self._config_entry.options.get(CONF_PRESENCE_SENSOR_ID)
        ps = self.hass.states.get(presence_sensor) if presence_sensor else None
        return presence_counts_as_home(
            ps.state if ps is not None else None,
            guest_mode=self._presence_ctrl.guest_mode,
        )

    @property
    def guest_mode(self) -> bool:
        """Return True while guest mode suspends the presence-away automation."""
        return self._presence_ctrl.guest_mode

    async def async_set_guest_mode(self, enabled: bool) -> None:
        """Enable/disable guest mode (heat normally although nobody is home).

        Enabling while AWAY is active restores the saved preset immediately.
        Disabling re-arms the away timer if the presence sensor reports
        nobody home, so AWAY follows after the configured away delay.
        """
        if enabled == self._presence_ctrl.guest_mode:
            return
        should_restore = self._presence_ctrl.set_guest_mode(enabled)
        _LOGGER.info("Guest mode %s", "enabled" if enabled else "disabled")
        if should_restore:
            self._restore_presence_state()
        elif not enabled:
            presence_sensor = self._config_entry.options.get(CONF_PRESENCE_SENSOR_ID)
            ps = self.hass.states.get(presence_sensor) if presence_sensor else None
            if ps is not None and ps.state == "off":
                self._schedule_presence_away()
        self._write_state_with_linked_entities()

    async def _async_presence_away_action(self, _now) -> None:
        """Switch to AWAY preset after presence-away delay."""
        # Safety: if the controller is already active (e.g. a stale timer fired
        # after a sensor flicker), do not overwrite the saved preset.
        if self._presence_ctrl.is_active:
            _LOGGER.info(
                "Presence away action skipped: controller already active"
            )
            return
        if self._presence_ctrl.guest_mode:
            _LOGGER.info("Presence away action skipped: guest mode active")
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
            self.async_write_ha_state()
            return

        # If boost is active, cancel it and use the pre-boost preset as saved state
        saved = self._boost.interrupt(self._preset_mode, self._target_temp)
        saved_preset, saved_temp = saved.preset, saved.temp
        # Never save AWAY as restore state – fall back to COMFORT so the user
        # isn't stuck in AWAY→AWAY after restore (e.g. after HA restart where
        # the presence sensor was briefly unavailable at boot).
        if saved_preset == PRESET_AWAY:
            saved_preset = PRESET_COMFORT
            saved_temp = self._comfort_target()
        self._presence_ctrl.activate(saved_preset, saved_temp)
        self._preset_mode = PRESET_AWAY
        _LOGGER.info("Presence away: switching to AWAY preset")
        self.async_write_ha_state()
        if self._hvac_mode == HVACMode.OFF and self._has_schedule():
            await self.async_set_hvac_mode(HVACMode.HEAT)
        else:
            await self._async_regulation_cycle(trigger="presence_away")

    async def _async_presence_home_action(self, _now) -> None:
        """Restore previous preset after presence-home delay."""
        # Revalidate: skip if the sensor reports nobody home again (or is
        # gone).  A sensor that is briefly unavailable does not undo the return.
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

        # The room may have been Away across several schedule blocks. Restore
        # today's active block, not the block that was saved on departure.
        scheduled = self._scheduled_action()
        if saved.preset != PRESET_NONE and scheduled in PRESET_LIST:
            saved = SavedState(scheduled, self._get_preset_target(scheduled))

        # If window automation is active, update the window's saved state
        # instead of changing the current preset (frost protection stays).
        if self._window_ctrl.is_active:
            self._window_ctrl.update_saved(saved.preset, saved.temp)
            _LOGGER.info(
                "Presence home during window-open: saved %s for window-close restore",
                saved.preset,
            )
            self.async_write_ha_state()
            return

        self._preset_mode = saved.preset
        # If restoring BOOST, start the expiry timer so it doesn't run
        # indefinitely.  Use COMFORT as the post-boost fallback since the
        # original pre-boost context was lost when presence automation
        # took over.
        if saved.preset == PRESET_BOOST:
            self._boost.save(PRESET_COMFORT, self._comfort_target())
            self._start_boost()
            _LOGGER.info(
                "Boost restored after presence home, timer started for %d min",
                self._config.presets.boost_duration_min,
            )
        # If restoring COMFORT, take the current comfort_target from options
        # (it may have been changed via number entity while away).
        elif saved.preset == PRESET_COMFORT:
            self._target_temp = self._comfort_target()
        elif saved.temp is not None:
            self._target_temp = saved.temp
        _LOGGER.info("Presence home: restoring previous preset")
        if scheduled == "off" and saved.preset != PRESET_NONE:
            self.hass.async_create_task(self._async_apply_schedule_state())
        else:
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

        self._manual_return.cancel()

        # A change during the window close delay ends the delay right away.
        # The window's saved preset comes back first and the change applies
        # to it, so frost protection never outlives the window automation.
        close_delay_ended = self._end_window_close_delay()

        # While presence away or an open window holds the room, the change is
        # saved for when that automation ends.
        route = route_change(
            preset_mode,
            presence_away=self._presence_ctrl.is_active,
            window_open=self._window_ctrl.is_active,
        )
        if route != CHANGE_APPLY:
            await self._async_save_change(
                route,
                preset_mode,
                self._get_preset_target(preset_mode),
                close_delay_ended=close_delay_ended,
            )
            return

        old_preset = self._preset_mode
        self._preset_mode = preset_mode

        # Cancel any running boost timer
        self._boost.cancel()

        # When switching to COMFORT, restore the stored comfort target
        if preset_mode == PRESET_COMFORT:
            self._target_temp = self._comfort_target()

        # Start boost timer if entering boost mode
        if preset_mode == PRESET_BOOST:
            # Only update the saved preset if we're not already in boost,
            # otherwise keep the original pre-boost preset to avoid a loop
            # where boost restores into boost indefinitely.
            if old_preset != PRESET_BOOST:
                self._boost.save(old_preset, self._target_temp)
            self._start_boost()
            _LOGGER.info(
                "Boost started for %d min", self._config.presets.boost_duration_min
            )

        _LOGGER.debug("Preset changed: %s → %s", old_preset, preset_mode)
        self.async_write_ha_state()
        await self._async_regulation_cycle(trigger="preset_change")

    async def _async_boost_expired(self, _now) -> None:
        """Called when the boost timer expires – revert to previous preset."""
        saved = self._boost.expire()
        restore_preset = saved.preset
        _LOGGER.info("Boost expired, reverting to %s", restore_preset)

        # PRESET_NONE (manual mode) is not in PRESET_LIST and would be rejected
        # by async_set_preset_mode, so handle it directly.
        if restore_preset == PRESET_NONE:
            # Don't override active window/presence automation – save for later.
            route = route_change(
                PRESET_NONE,
                presence_away=self._presence_ctrl.is_active,
                window_open=self._window_ctrl.is_active,
            )
            if route != CHANGE_APPLY:
                await self._async_save_change(
                    route, PRESET_NONE, saved.temp, close_delay_ended=False
                )
                return
            if saved.temp is not None:
                self._target_temp = saved.temp
            self._preset_mode = PRESET_NONE
            self.async_write_ha_state()
            await self._async_regulation_cycle(trigger="boost_expired")
        else:
            await self.async_set_preset_mode(restore_preset)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _async_save_change(
        self, route: str, preset: str, temp: float | None, *, close_delay_ended: bool
    ) -> None:
        """Save a change for when the automation holding the room ends.

        *route* comes from route_change: presence away keeps the room in Away
        until someone comes home, an open window keeps frost protection until
        it closes.  If the change ended a window close delay, the room has just
        left frost protection for the restored preset, so the regulation cycle
        runs now.
        """
        change = f"{temp:.1f}°C" if preset == PRESET_NONE and temp is not None else preset
        if route == CHANGE_FOR_PRESENCE:
            self._presence_ctrl.update_saved(preset, temp)
            _LOGGER.info("Presence away: %s saved for restore, keeping away mode", change)
        else:
            self._window_ctrl.update_saved(preset, temp)
            _LOGGER.info("Window open: %s saved for restore, keeping frost protection", change)
        self.async_write_ha_state()
        if close_delay_ended:
            await self._async_regulation_cycle(trigger="window_closed")

    def _comfort_target(self) -> float:
        """Return the configured comfort target, falling back to the default.

        The comfort_target option is absent from config_entry.options until the
        Comfort Target number entity is edited once. Reading it with no default
        yields None; callers previously skipped the assignment and left
        _target_temp stuck at a frost/eco value. Fall back to the PresetConfig
        default (20 °C) so comfort always resolves.
        """
        comfort = safe_float(self._config_entry.options.get(CONF_COMFORT_TARGET))
        if comfort is not None:
            return comfort
        return self._config.presets.comfort_target_c

    def _get_preset_target(self, preset_mode: str) -> float:
        """Return the target temperature for a given preset mode.

        Used to save a preset for later restore, so COMFORT reads the
        configured comfort target rather than the current base target.
        """
        return preset_target_c(
            preset_mode,
            self._config.presets,
            comfort_c=self._comfort_target(),
            manual_c=self._target_temp,
        )

    # ------------------------------------------------------------------
    # Effective setpoint calculation
    # ------------------------------------------------------------------

    def _effective_setpoint(self) -> float:
        """Calculate the effective setpoint based on HVAC mode and preset."""
        if self._hvac_mode == HVACMode.OFF:
            return FROST_PROTECT_C
        # COMFORT and manual both run on the base target, which is kept in
        # sync with the comfort option while COMFORT is active.
        return preset_target_c(
            self._preset_mode,
            self._config.presets,
            comfort_c=self._target_temp,
            manual_c=self._target_temp,
        )
