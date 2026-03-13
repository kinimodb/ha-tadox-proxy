"""Climate Entity for Tado X Proxy."""
from __future__ import annotations

import asyncio
import logging
import time
import datetime
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_NONE,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.const import (
    ATTR_TEMPERATURE,
    PRECISION_TENTHS,
    UnitOfTemperature,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    DOMAIN,
    CONF_COMFORT_TARGET,
    CONF_ECO_TARGET,
    CONF_BOOST_TARGET,
    CONF_BOOST_DURATION,
    CONF_AWAY_TARGET,
    CONF_FROST_PROTECTION_TARGET,
    CONF_FOLLOW_TADO_INPUT,
    CONF_WINDOW_SENSOR_ID,
    CONF_WINDOW_DELAY_S,
    CONF_WINDOW_CLOSE_DELAY_S,
    CONF_PRESENCE_SENSOR_ID,
    CONF_PRESENCE_AWAY_DELAY_S,
    CONF_FOLLOW_THRESHOLD_C,
    CONF_FOLLOW_GRACE_S,
    CONF_URGENT_DECREASE_THRESHOLD_C,
    CONF_SENSOR_GRACE_S,
    PRESET_FROST_PROTECTION,
)
from .parameters import (
    DEFAULT_CONTROL_INTERVAL_S,
    DEFAULT_SENSOR_GRACE_S,
    FROST_PROTECT_C,
    RegulationConfig,
    BehaviourConfig,
    CorrectionTuning,
    PresetConfig,
)
from .regulation import FeedforwardPiRegulator, RegulationState
from .climate_controllers import (
    WindowAutomationController,
    PresenceAutomationController,
    FollowPhysicalController,
)

_LOGGER = logging.getLogger(__name__)

# Ordered list of presets shown in the UI.
# PRESET_NONE ("Manuell") activates when the user moves the temperature
# slider directly instead of selecting a named preset.
PRESET_LIST = [
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_BOOST,
    PRESET_AWAY,
    PRESET_FROST_PROTECTION,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tado X Proxy climate entity."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entity = TadoXProxyClimate(
        coordinator=coordinator,
        unique_id=entry.entry_id,
        config_entry=entry,
    )
    async_add_entities([entity])


class TadoXProxyClimate(CoordinatorEntity, ClimateEntity, RestoreEntity):
    """Proxy climate entity that controls a Tado X TRV via feedforward + PI."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_TENTHS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.PRESET_MODE
    )
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = PRESET_LIST
    _attr_translation_key = "tadox_proxy"

    def __init__(self, coordinator, unique_id: str, config_entry: ConfigEntry):
        """Initialize the proxy thermostat."""
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._config_entry = config_entry
        self._attr_name = None  # uses translation key

        # Build regulation + behaviour config from defaults + options
        self._config = self._build_config(config_entry)
        self._behaviour = self._build_behaviour(config_entry)
        self._regulator = FeedforwardPiRegulator(self._config)
        self._reg_state = RegulationState()

        # UI state
        self._hvac_mode = HVACMode.HEAT
        self._target_temp: float = config_entry.options.get(CONF_COMFORT_TARGET, PresetConfig.comfort_target_c)
        self._preset_mode: str = PRESET_COMFORT

        # Boost timer
        self._boost_cancel: CALLBACK_TYPE | None = None
        self._boost_saved_preset: str = PRESET_COMFORT
        self._boost_saved_temp: float | None = None

        # Timing
        self._last_regulation_ts = 0.0
        self._last_command_sent_ts = 0.0
        self._last_sent_setpoint: float | None = None

        # Sensor resilience: last-valid values for grace-period bridging
        self._last_valid_room_temp: float | None = None
        self._last_valid_room_temp_ts: float = 0.0
        self._sensor_grace_s: int = config_entry.options.get(
            CONF_SENSOR_GRACE_S, DEFAULT_SENSOR_GRACE_S
        )
        self._sensor_degraded: bool = False

        # State-machine controllers (hold their own timer + saved-state)
        self._window_ctrl = WindowAutomationController()
        self._presence_ctrl = PresenceAutomationController()

        # Diagnostics
        self._last_result = None
        self._last_reason = "startup"

    # ------------------------------------------------------------------
    # Config builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_config(entry: ConfigEntry) -> RegulationConfig:
        """Build regulation config, applying options over defaults."""
        config = RegulationConfig()
        opts = entry.options
        if opts:
            kp = opts.get("correction_kp", config.tuning.kp)
            ki = opts.get("correction_ki", config.tuning.ki)
            config.tuning = CorrectionTuning(kp=kp, ki=ki)
            config.presets = PresetConfig(
                eco_target_c=opts.get(CONF_ECO_TARGET, config.presets.eco_target_c),
                boost_target_c=opts.get(CONF_BOOST_TARGET, config.presets.boost_target_c),
                boost_duration_min=opts.get(CONF_BOOST_DURATION, config.presets.boost_duration_min),
                away_target_c=opts.get(CONF_AWAY_TARGET, config.presets.away_target_c),
                frost_protection_target_c=opts.get(CONF_FROST_PROTECTION_TARGET, config.presets.frost_protection_target_c),
            )
            # Ensure max_target_c is at least as high as boost_target_c
            config.max_target_c = max(config.max_target_c, config.presets.boost_target_c)
        return config

    @staticmethod
    def _build_behaviour(entry: ConfigEntry) -> BehaviourConfig:
        """Build behaviour config, applying options over defaults."""
        defaults = BehaviourConfig()
        opts = entry.options
        if not opts:
            return defaults
        return BehaviourConfig(
            follow_threshold_c=opts.get(CONF_FOLLOW_THRESHOLD_C, defaults.follow_threshold_c),
            follow_grace_s=opts.get(CONF_FOLLOW_GRACE_S, defaults.follow_grace_s),
            urgent_decrease_threshold_c=opts.get(
                CONF_URGENT_DECREASE_THRESHOLD_C, defaults.urgent_decrease_threshold_c
            ),
        )

    @property
    def icon(self) -> str | None:
        """Return a distinct icon based on the active preset mode."""
        icons = {
            PRESET_COMFORT: "mdi:sofa",
            PRESET_ECO: "mdi:leaf",
            PRESET_BOOST: "mdi:rocket-launch",
            PRESET_AWAY: "mdi:home-export-outline",
            PRESET_FROST_PROTECTION: "mdi:snowflake",
            PRESET_NONE: "mdi:hand-back-right",
        }
        return icons.get(self._preset_mode)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the proxy."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._config_entry.entry_id)},
            name=self._config_entry.title,
            manufacturer="Tado X Proxy",
            model="Feedforward + PI Regulator",
        )

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to HA."""
        await super().async_added_to_hass()

        # Restore previous state
        last_state = await self.async_get_last_state()
        if last_state:
            if last_state.state in (HVACMode.HEAT, HVACMode.OFF):
                self._hvac_mode = HVACMode(last_state.state)
            temp = last_state.attributes.get(ATTR_TEMPERATURE)
            if temp is not None:
                try:
                    self._target_temp = float(temp)
                except (ValueError, TypeError):
                    pass
            # Restore preset (default to comfort if missing or invalid)
            restored_preset = last_state.attributes.get("preset_mode")
            if restored_preset in PRESET_LIST or restored_preset == PRESET_NONE:
                self._preset_mode = restored_preset
                # Don't restore boost – it's time-limited and the timer is gone
                if self._preset_mode == PRESET_BOOST:
                    self._preset_mode = PRESET_COMFORT
                # Don't restore frost protection – it's window-driven and the
                # controller state is not persisted across restarts
                if self._preset_mode == PRESET_FROST_PROTECTION:
                    self._preset_mode = PRESET_COMFORT

        # If the active preset is COMFORT, the comfort_target in options is
        # authoritative (may have changed via the number entity while HA was down).
        # For PRESET_NONE (manual), the restored slider temperature wins.
        if self._preset_mode == PRESET_COMFORT:
            opts_comfort = self._config_entry.options.get(CONF_COMFORT_TARGET)
            if opts_comfort is not None:
                self._target_temp = float(opts_comfort)

        # Initialize baseline for follow-tado from current tado setpoint so
        # the feature works immediately without waiting for the first regulation.
        tado_sp = self.coordinator.data.get("tado_setpoint")
        if tado_sp is not None and self._last_sent_setpoint is None:
            self._last_sent_setpoint = tado_sp

        # Config entry update listener (from number/switch entities)
        self.async_on_remove(
            self._config_entry.add_update_listener(self._async_config_entry_updated)
        )

        # State change listener on source Tado entity (follow physical thermostat)
        source_entity = self._config_entry.data.get("source_entity_id")
        if source_entity:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [source_entity],
                    self._async_tado_state_changed,
                )
            )

        # Window sensor listener
        window_sensor = self._config_entry.options.get(CONF_WINDOW_SENSOR_ID)
        if window_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [window_sensor],
                    self._async_window_changed,
                )
            )
            # Evaluate current state after restart
            window_state = self.hass.states.get(window_sensor)
            if window_state and window_state.state == "on":
                delay = self._config_entry.options.get(CONF_WINDOW_DELAY_S, 30)
                self._window_ctrl.handle_window_opened(
                    self.hass, delay, self._async_window_action
                )
                _LOGGER.info("Startup: window sensor is open, action in %ds", delay)

        # Presence sensor listener
        presence_sensor = self._config_entry.options.get(CONF_PRESENCE_SENSOR_ID)
        if presence_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [presence_sensor],
                    self._async_presence_changed,
                )
            )
            # Evaluate current state after restart
            presence_state = self.hass.states.get(presence_sensor)
            if presence_state and presence_state.state == "off":
                delay = self._config_entry.options.get(CONF_PRESENCE_AWAY_DELAY_S, 600)
                self._presence_ctrl.handle_presence_away(
                    self.hass, delay, self._async_presence_away_action
                )
                _LOGGER.info("Startup: presence sensor is away, action in %ds", delay)

        # Start periodic regulation
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_regulation_cycle_timer,
                datetime.timedelta(seconds=DEFAULT_CONTROL_INTERVAL_S),
            )
        )

    async def _async_config_entry_updated(self, hass, entry) -> None:
        """Called when config entry options change (e.g. from number entities)."""
        self._config_entry = entry
        self._config = self._build_config(entry)
        self._behaviour = self._build_behaviour(entry)
        self._regulator = FeedforwardPiRegulator(self._config)
        self._sensor_grace_s = entry.options.get(
            CONF_SENSOR_GRACE_S, DEFAULT_SENSOR_GRACE_S
        )
        # Only sync comfort target when COMFORT preset is active; PRESET_NONE
        # (manual) keeps its independently set temperature.
        if self._preset_mode == PRESET_COMFORT:
            comfort = entry.options.get(CONF_COMFORT_TARGET)
            if comfort is not None:
                self._target_temp = float(comfort)
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Follow physical thermostat
    # ------------------------------------------------------------------

    @callback
    def _async_tado_state_changed(self, event) -> None:
        """Detect physical thermostat changes and follow them if enabled."""
        if not self._config_entry.options.get(CONF_FOLLOW_TADO_INPUT, False):
            return

        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None or new_state.state in ("unavailable", "unknown"):
            return

        new_temp_attr = new_state.attributes.get("temperature")
        old_temp_attr = old_state.attributes.get("temperature") if old_state else None
        if new_temp_attr is None or new_temp_attr == old_temp_attr:
            return

        try:
            tado_setpoint = float(new_temp_attr)
        except (ValueError, TypeError):
            return

        if not FollowPhysicalController.should_follow(
            tado_setpoint=tado_setpoint,
            last_sent=self._last_sent_setpoint,
            last_sent_ts=self._last_command_sent_ts,
            threshold_c=self._behaviour.follow_threshold_c,
            grace_s=self._behaviour.follow_grace_s,
        ):
            return

        _LOGGER.info(
            "Physical Tado change detected: %.1f°C → following (last sent: %.1f°C)",
            tado_setpoint,
            self._last_sent_setpoint,
        )
        self._target_temp = tado_setpoint
        self._preset_mode = PRESET_NONE
        if self._boost_cancel is not None:
            self._boost_cancel()
            self._boost_cancel = None
        self.async_write_ha_state()
        # Trigger immediate regulation so the new target takes effect fast.
        self.hass.async_create_task(
            self._async_regulation_cycle(trigger="follow_tado")
        )

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
            comfort = self._config_entry.options.get(CONF_COMFORT_TARGET)
            if comfort is not None:
                saved_temp = float(comfort)
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
                comfort = self._config_entry.options.get(CONF_COMFORT_TARGET)
                self._target_temp = float(comfort) if comfort is not None else self._target_temp
            elif saved.temp is not None:
                self._target_temp = saved.temp

            # If restoring BOOST, start the expiry timer so it doesn't run
            # indefinitely. Use COMFORT as the post-boost fallback since the
            # original pre-boost context was lost when window automation
            # took over.
            if preset_to_restore == PRESET_BOOST:
                self._boost_saved_preset = PRESET_COMFORT
                comfort = self._config_entry.options.get(CONF_COMFORT_TARGET)
                self._boost_saved_temp = float(comfort) if comfort is not None else self._target_temp
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
            if self._presence_ctrl.handle_presence_home():
                self._restore_presence_state()

    async def _async_presence_away_action(self, _now) -> None:
        """Switch to AWAY preset after presence-away delay."""
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
            # Update window saved state to AWAY so frost→close restores AWAY
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
        # If restoring COMFORT, take the current comfort_target from options
        # (it may have been changed via number entity while away).
        if saved.preset == PRESET_COMFORT:
            comfort = self._config_entry.options.get(CONF_COMFORT_TARGET)
            self._target_temp = float(comfort) if comfort is not None else self._target_temp
        elif saved.temp is not None:
            self._target_temp = saved.temp
        _LOGGER.info("Presence home: restoring previous preset")
        self.hass.async_create_task(
            self._async_regulation_cycle(trigger="presence_home")
        )
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Standard climate controls
    # ------------------------------------------------------------------

    async def _async_regulation_cycle_timer(self, _now) -> None:
        """Periodic timer callback."""
        await self._async_regulation_cycle(trigger="timer")

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target HVAC mode."""
        if hvac_mode not in self._attr_hvac_modes:
            return
        # Manual HVAC change clears any active window-open state so the user's
        # intention is respected.
        if self._window_ctrl.is_active:
            self._window_ctrl.cancel_all()
        self._hvac_mode = hvac_mode
        self.async_write_ha_state()
        await self._async_regulation_cycle(trigger="hvac_mode_change")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature; enters manual (PRESET_NONE) mode.

        Moving the slider is treated as a temporary manual override. It does
        NOT change the stored comfort target – use the Comfort number entity
        or the options flow for that.
        """
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        self._target_temp = float(temp)

        # Cancel window close delay if user manually changes temperature
        if self._window_ctrl.close_delay_active:
            self._window_ctrl.cancel_all()

        # Any direct temperature change activates manual mode and cancels
        # any running boost timer.
        if self._preset_mode != PRESET_NONE:
            if self._boost_cancel is not None:
                self._boost_cancel()
                self._boost_cancel = None
            self._preset_mode = PRESET_NONE

        self.async_write_ha_state()
        await self._async_regulation_cycle(trigger="set_temperature")

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if preset_mode not in PRESET_LIST:
            _LOGGER.warning("Unknown preset mode: %s", preset_mode)
            return

        # Cancel window close delay if user manually changes preset
        if self._window_ctrl.close_delay_active:
            self._window_ctrl.cancel_all()
            _LOGGER.info("Window close delay cancelled – user changed preset to %s", preset_mode)

        # If window automation is active (frost protection due to open window),
        # update the saved state so the new preset is restored when the window
        # closes, but keep frost protection active.
        if self._window_ctrl.is_active and preset_mode != PRESET_FROST_PROTECTION:
            if preset_mode == PRESET_COMFORT:
                comfort = self._config_entry.options.get(CONF_COMFORT_TARGET)
                save_temp = float(comfort) if comfort is not None else self._target_temp
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
            comfort = self._config_entry.options.get(CONF_COMFORT_TARGET)
            if comfort is not None:
                self._target_temp = float(comfort)

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
        if restore_preset == PRESET_NONE and self._boost_saved_temp is not None:
            self._target_temp = self._boost_saved_temp
        _LOGGER.info("Boost expired, reverting to %s", restore_preset)
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

    # ------------------------------------------------------------------
    # Core regulation
    # ------------------------------------------------------------------

    async def _async_regulation_cycle(self, trigger: str) -> None:
        """Execute one control cycle."""
        now = time.time()

        # Guard: skip when coordinator data is stale (update method raised an exception).
        if not self.coordinator.last_update_success:
            _LOGGER.debug("Skipping regulation cycle – coordinator update failed")
            self._last_reason = "coordinator_unavailable"
            self.async_write_ha_state()
            return

        # 1. Gather sensor data from coordinator
        room_temp = self.coordinator.data.get("room_temp")
        tado_internal = self.coordinator.data.get("tado_internal_temp")

        # Sensor resilience: bridge short gaps with last-valid value
        if room_temp is not None:
            self._last_valid_room_temp = room_temp
            self._last_valid_room_temp_ts = now
            self._sensor_degraded = False
        elif (
            self._last_valid_room_temp is not None
            and (now - self._last_valid_room_temp_ts) <= self._sensor_grace_s
        ):
            room_temp = self._last_valid_room_temp
            self._sensor_degraded = True
            age = int(now - self._last_valid_room_temp_ts)
            _LOGGER.debug(
                "Room temp unavailable – using last valid %.1f°C (age %ds, grace %ds)",
                room_temp, age, self._sensor_grace_s,
            )
        else:
            self._sensor_degraded = room_temp is None and self._last_valid_room_temp is not None

        if room_temp is None or tado_internal is None:
            self._last_reason = "waiting_for_sensors"
            self.async_write_ha_state()
            return

        # 2. Time delta
        dt = (now - self._last_regulation_ts) if self._last_regulation_ts > 0 else 0.0
        self._last_regulation_ts = now

        # 3. Effective setpoint (considers HVAC mode + preset)
        setpoint = self._effective_setpoint()

        # 4. Compute regulation
        result = self._regulator.compute(
            setpoint_c=setpoint,
            room_temp_c=room_temp,
            tado_internal_c=tado_internal,
            time_delta_s=dt,
            state=self._reg_state,
        )
        self._reg_state = result.new_state
        self._last_result = result

        # 5. Rate limiting & send decision
        current_tado_setpoint = self.coordinator.data.get("tado_setpoint", 0.0)
        diff = abs(result.target_for_tado_c - current_tado_setpoint)
        time_since_last = now - self._last_command_sent_ts
        is_rate_limited = time_since_last < self._config.min_command_interval_s

        should_send = False
        reason = "noop"

        if diff < self._config.min_change_threshold_c:
            reason = "already_at_target"
        elif is_rate_limited:
            is_urgent_decrease = (
                result.target_for_tado_c
                < current_tado_setpoint - self._behaviour.urgent_decrease_threshold_c
            )
            if is_urgent_decrease:
                should_send = True
                reason = "urgent_decrease"
            else:
                remaining = int(self._config.min_command_interval_s - time_since_last)
                reason = f"rate_limited({remaining}s)"
        else:
            should_send = True
            reason = "normal_update"

        # 6. Send command to Tado
        if should_send:
            await self._async_send_to_tado(result.target_for_tado_c)
            self._last_command_sent_ts = now
            self._last_reason = f"sent({reason})"
        else:
            self._last_reason = reason

        self.async_write_ha_state()

    async def _async_send_to_tado(self, target_c: float) -> None:
        """Send a temperature command to the source Tado entity."""
        source_entity = self._config_entry.data.get("source_entity_id")
        if not source_entity:
            return

        _LOGGER.debug("Sending %.1f°C to %s", target_c, source_entity)

        try:
            async with asyncio.timeout(10):
                await self.hass.services.async_call(
                    domain="climate",
                    service="set_temperature",
                    service_data={
                        "entity_id": source_entity,
                        "temperature": target_c,
                        "hvac_mode": HVACMode.HEAT,
                    },
                    blocking=True,
                )
            self._last_sent_setpoint = target_c
        except (TimeoutError, HomeAssistantError):
            _LOGGER.exception("Failed to send command to Tado")

    # ------------------------------------------------------------------
    # Properties for HA UI
    # ------------------------------------------------------------------

    @property
    def current_temperature(self) -> float | None:
        """Return the room temperature from the external sensor."""
        return self.coordinator.data.get("room_temp")

    @property
    def target_temperature(self) -> float | None:
        """Return the effective setpoint so HA always shows the active target."""
        return self._effective_setpoint()

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        return self._hvac_mode

    @property
    def hvac_action(self) -> HVACAction:
        """Infer heating/idle from the Tado entity state."""
        if self._hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        tado_internal = self.coordinator.data.get("tado_internal_temp")
        tado_setpoint = self.coordinator.data.get("tado_setpoint")

        if tado_internal is not None and tado_setpoint is not None:
            if tado_setpoint > tado_internal + 0.05:
                return HVACAction.HEATING

        return HVACAction.IDLE

    @property
    def preset_mode(self) -> str:
        """Return the current preset mode."""
        return self._preset_mode

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes visible in HA Developer Tools."""
        attrs: dict[str, Any] = {
            "regulation_reason": self._last_reason,
            "tado_internal_temp_c": self.coordinator.data.get("tado_internal_temp"),
            "correction_kp": self._config.tuning.kp,
            "correction_ki": self._config.tuning.ki,
            "effective_setpoint_c": self._effective_setpoint(),
            "window_open_active": self._window_ctrl.is_active,
            "window_close_delay_active": self._window_ctrl.close_delay_active,
            "presence_away_active": self._presence_ctrl.is_active,
            "sensor_degraded": self._sensor_degraded,
        }

        # Sensor resilience diagnostics
        if self._last_valid_room_temp is not None:
            attrs["room_temp_last_valid_c"] = self._last_valid_room_temp
            if self._last_valid_room_temp_ts > 0:
                age = int(time.time() - self._last_valid_room_temp_ts)
                attrs["room_temp_last_valid_age_s"] = age

        if self._last_result:
            r = self._last_result
            attrs.update({
                "feedforward_offset_c": r.feedforward_offset_c,
                "p_correction_c": r.p_correction_c,
                "i_correction_c": r.i_correction_c,
                "error_c": r.error_c,
                "target_for_tado_c": r.target_for_tado_c,
                "is_saturated": r.is_saturated,
            })

        return attrs


# ---------------------------------------------------------------------------
# Module-level helper (imported lazily to allow HA-free tests of controllers)
# ---------------------------------------------------------------------------

def async_call_later_boost(hass, delay_s, callback):
    """Thin wrapper so boost timer scheduling stays in this module."""
    from homeassistant.helpers.event import async_call_later  # noqa: PLC0415
    return async_call_later(hass, delay_s, callback)
