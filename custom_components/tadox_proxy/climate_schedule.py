"""Apply the weekly preset schedule below Manual, Presence and Window."""

from __future__ import annotations

import datetime as dt
import time

from homeassistant.components.climate import PRESET_NONE, HVACMode
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from .const import (
    CONF_MANUAL_MODE_ACTION,
    CONF_WEEKLY_PRESET_SCHEDULE,
    DEFAULT_MANUAL_MODE_ACTION,
    MANUAL_MODE_SCHEDULE,
    PRESET_LIST,
)
from .schedule import next_weekly_event_ts, weekly_preset_at


class ScheduleMixin:
    """Apply the current integration-owned weekly schedule."""

    def _own_schedule(self) -> dict | None:
        return self._config_entry.options.get(CONF_WEEKLY_PRESET_SCHEDULE) or None

    def _has_schedule(self) -> bool:
        return bool(self._own_schedule())

    def _scheduled_action(self) -> str | None:
        if schedule := self._own_schedule():
            return weekly_preset_at(schedule, dt_util.now())
        return None

    def _next_schedule_boundary_ts(self) -> float | None:
        if schedule := self._own_schedule():
            return next_weekly_event_ts(schedule, dt_util.now())
        return None

    def _cancel_weekly_schedule_timer(self) -> None:
        if self._schedule_unsub is not None:
            self._schedule_unsub()
            self._schedule_unsub = None

    def _arm_weekly_schedule_timer(self) -> None:
        self._cancel_weekly_schedule_timer()
        boundary = self._next_schedule_boundary_ts()
        if boundary is not None:
            self._schedule_unsub = async_track_point_in_time(
                self.hass,
                self._async_weekly_schedule_tick,
                dt.datetime.fromtimestamp(boundary + 0.5, dt.UTC),
            )

    @callback
    def _async_weekly_schedule_tick(self, _now) -> None:
        """Apply a block and arm the next one without losing a week rollover."""
        self._schedule_unsub = None
        self._arm_weekly_schedule_timer()
        self.hass.async_create_task(self._async_apply_schedule_state())

    def _schedule_base_preset(self) -> str | None:
        """Return the user preset hidden by an Away or Window hold."""
        if self._presence_ctrl.is_active:
            return self._presence_ctrl.get_saved().preset
        if self._window_ctrl.is_active:
            return self._window_ctrl.get_saved().preset
        return self._preset_mode

    async def _async_apply_schedule_state(self, *, resume_manual: bool = False) -> None:
        """Apply the current block, respecting Manual and safety holds."""
        action = self._scheduled_action()

        if self._manual_return.is_active:
            if (
                self._config_entry.options.get(CONF_MANUAL_MODE_ACTION, DEFAULT_MANUAL_MODE_ACTION)
                == MANUAL_MODE_SCHEDULE
            ):
                now = time.time()
                if now + 0.5 >= self._manual_return.end_ts:
                    await self._async_manual_return_expired(None)
                else:
                    boundary = self._next_schedule_boundary_ts()
                    if boundary is not None and boundary > now:
                        deadline = boundary + 0.5
                        if abs(deadline - self._manual_return.end_ts) > 1:
                            self._manual_return.restore(
                                self.hass,
                                self._manual_return.previous_preset,
                                self._manual_return.manual_target_c,
                                deadline,
                                self._async_manual_return_expired,
                            )
                            self.async_write_ha_state()
            return

        if not resume_manual and self._schedule_base_preset() == PRESET_NONE:
            return
        if action is None:
            return

        # Presence Away and an open window take priority over a scheduled
        # switch-off. On release, the then-current schedule is applied.
        if action == "off":
            if self._window_ctrl.is_active:
                return
            if self._presence_ctrl.is_active:
                if self._hvac_mode == HVACMode.OFF:
                    await self.async_set_hvac_mode(HVACMode.HEAT)
                return
            if self._hvac_mode != HVACMode.OFF:
                await self.async_set_hvac_mode(HVACMode.OFF)
            return

        if action not in PRESET_LIST:
            return
        if self._schedule_base_preset() != action or resume_manual:
            await self.async_set_preset_mode(action)
        if self._hvac_mode == HVACMode.OFF and not self._window_ctrl.is_active:
            await self.async_set_hvac_mode(HVACMode.HEAT)
