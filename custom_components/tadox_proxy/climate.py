"""Climate Entity for Tado X Proxy."""
from __future__ import annotations

import asyncio
import datetime
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    PRECISION_TENTHS,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .climate_controllers import (
    CONTROL_BACKUP_SENSOR,
    DEFAULT_SENSOR_LOSS_ACTION,
    ControlModeResolver,
    FollowPhysicalController,
    safe_float,
)
from .climate_presets import PresetMixin
from .climate_regulation import RegulationMixin
from .const import (
    CONF_AWAY_TARGET,
    CONF_BOOST_DURATION,
    CONF_BOOST_TARGET,
    CONF_CORRECTION_KI,
    CONF_CORRECTION_KP,
    CONF_ECO_TARGET,
    CONF_FOLLOW_GRACE_S,
    CONF_FOLLOW_TADO_INPUT,
    CONF_FOLLOW_THRESHOLD_C,
    CONF_FROST_PROTECTION_TARGET,
    CONF_GAIN_FINE_MULTIPLIER,
    CONF_GAIN_FINE_THRESHOLD_C,
    CONF_GAIN_SCHEDULING,
    CONF_GAIN_STARTUP_MULTIPLIER,
    CONF_GAIN_STARTUP_THRESHOLD_C,
    CONF_INTEGRAL_DEADBAND_C,
    CONF_MIN_CHANGE_THRESHOLD_C,
    CONF_MIN_COMMAND_INTERVAL_S,
    CONF_OVERLAY_REFRESH_S,
    CONF_PRESENCE_SENSOR_ID,
    CONF_SENSOR_GRACE_S,
    CONF_SENSOR_LOSS_ACTION,
    CONF_URGENT_DECREASE_THRESHOLD_C,
    CONF_WINDOW_SENSOR_ID,
    DOMAIN,
    PRESET_FROST_PROTECTION,
    PRESET_LIST,
)
from .device import proxy_device_info
from .parameters import (
    DEFAULT_CONTROL_INTERVAL_S,
    DEFAULT_OVERLAY_REFRESH_S,
    DEFAULT_SENSOR_GRACE_S,
    BehaviourConfig,
    CorrectionTuning,
    PresetConfig,
    RegulationConfig,
)
from .preset_layers import PresetLayers, StartSnapshot
from .regulation import FeedforwardPiRegulator, RegulationState

_LOGGER = logging.getLogger(__name__)


def _source_state_available(state: State | None) -> bool:
    """Return whether the source thermostat can currently be reached."""
    return state is not None and state.state not in ("unavailable", "unknown")


@dataclass
class _ProxyRestoreData(ExtraStoredData):
    """Keep the desired climate state when HA hides unavailable attributes."""

    _values: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return self._values


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
    coordinator.climate_entity = entity
    async_add_entities([entity])


class TadoXProxyClimate(RegulationMixin, PresetMixin, CoordinatorEntity, ClimateEntity, RestoreEntity):
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
    # Class-level defaults so HA's CachedProperties metaclass sees 5/30
    # BEFORE super().__init__() runs (prevents fallback to HA's 7/35).
    _attr_min_temp: float = 5.0
    _attr_max_temp: float = 30.0

    def __init__(self, coordinator, unique_id: str, config_entry: ConfigEntry):
        """Initialize the proxy thermostat."""
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._config_entry = config_entry
        self._attr_name = None  # uses translation key

        # Build regulation + behaviour config from defaults + options
        self._config = self._build_config(config_entry)
        self._attr_min_temp = self._config.min_target_c
        self._attr_max_temp = self._config.max_target_c
        # Invalidate HA's CachedProperties cache for min/max_temp so the
        # instance-level values from _config take effect immediately.
        self.__dict__.pop("min_temp", None)
        self.__dict__.pop("max_temp", None)
        self._behaviour = self._build_behaviour(config_entry)
        self._regulator = FeedforwardPiRegulator(self._config)
        self._reg_state = RegulationState()

        # UI state
        self._hvac_mode = HVACMode.HEAT

        # Timing
        self._regulation_lock = asyncio.Lock()
        self._last_regulation_ts = 0.0
        self._last_command_sent_ts = 0.0
        self._last_sent_setpoint: float | None = None

        # Sensor resilience: bridges short room-sensor gaps and applies the
        # sensor-loss action once the grace period has expired
        self._control = ControlModeResolver(
            config_entry.options.get(CONF_SENSOR_GRACE_S, DEFAULT_SENSOR_GRACE_S),
            self._sensor_loss_action(config_entry),
        )

        # Overlay refresh: optional periodic resend for cloud-API integrations
        # with timer-based overlays (e.g. exabird ha-tado-x uses 30 min TIMER).
        # Value of 0 means disabled (default, correct for Matter/Thread).
        self._overlay_refresh_s: int = config_entry.options.get(
            CONF_OVERLAY_REFRESH_S, DEFAULT_OVERLAY_REFRESH_S
        )

        # Which preset the room runs: the user choice plus the window,
        # nobody-home and boost layers.  Its timers run here, by id.
        self._layers = PresetLayers(self._layers_config())
        self._layer_timers: dict[str, Callable[[], None]] = {}

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
            kp = opts.get(CONF_CORRECTION_KP, config.tuning.kp)
            ki = opts.get(CONF_CORRECTION_KI, config.tuning.ki)
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
            # Adaptive gain scheduling
            config.gain_scheduling_enabled = opts.get(
                CONF_GAIN_SCHEDULING, config.gain_scheduling_enabled
            )
            config.gain_fine_multiplier = opts.get(
                CONF_GAIN_FINE_MULTIPLIER, config.gain_fine_multiplier
            )
            config.gain_startup_multiplier = opts.get(
                CONF_GAIN_STARTUP_MULTIPLIER, config.gain_startup_multiplier
            )
            config.gain_startup_threshold_c = opts.get(
                CONF_GAIN_STARTUP_THRESHOLD_C, config.gain_startup_threshold_c
            )
            config.gain_fine_threshold_c = opts.get(
                CONF_GAIN_FINE_THRESHOLD_C, config.gain_fine_threshold_c
            )
            config.min_command_interval_s = opts.get(
                CONF_MIN_COMMAND_INTERVAL_S, config.min_command_interval_s
            )
            config.min_change_threshold_c = opts.get(
                CONF_MIN_CHANGE_THRESHOLD_C, config.min_change_threshold_c
            )
            config.integral_deadband_c = opts.get(
                CONF_INTEGRAL_DEADBAND_C, config.integral_deadband_c
            )
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

    @staticmethod
    def _sensor_loss_action(entry: ConfigEntry) -> str:
        """Return the configured sensor-loss action (options > data > default).

        New entries store it in data at setup; older entries have none and
        keep holding the last command.
        """
        return entry.options.get(
            CONF_SENSOR_LOSS_ACTION,
            entry.data.get(CONF_SENSOR_LOSS_ACTION, DEFAULT_SENSOR_LOSS_ACTION),
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the proxy."""
        return proxy_device_info(self._config_entry)

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to HA."""
        await super().async_added_to_hass()

        window_sensor = self._config_entry.options.get(CONF_WINDOW_SENSOR_ID)
        presence_sensor = self._config_entry.options.get(CONF_PRESENCE_SENSOR_ID)

        # PresetLayers starts from the state written before the restart and
        # the sensors now.  The comfort target comes from the options (it may
        # have changed via the number entity while HA was down).
        last_state = await self.async_get_last_state()
        restored_attributes = dict(last_state.attributes) if last_state else {}
        if extra_data := await self.async_get_last_extra_data():
            restored_attributes.update(extra_data.as_dict())
        restored_hvac_mode = restored_attributes.get(
            "desired_hvac_mode", last_state.state if last_state else None
        )
        if restored_hvac_mode in (HVACMode.HEAT, HVACMode.OFF):
            self._hvac_mode = HVACMode(restored_hvac_mode)
        snapshot = StartSnapshot.from_state(
            restored_hvac_mode or (last_state.state if last_state else None),
            restored_attributes,
            window_state=self._sensor_state(window_sensor),
            presence_state=self._sensor_state(presence_sensor),
        )
        self._apply_layer_effects(self._layers.start(snapshot, time.time()))

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

        # Sensor listeners: every state goes to PresetLayers.
        if presence_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [presence_sensor],
                    self._async_presence_changed,
                )
            )
        if window_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [window_sensor],
                    self._async_window_changed,
                )
            )

        # Start periodic regulation
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_regulation_cycle_timer,
                datetime.timedelta(seconds=DEFAULT_CONTROL_INTERVAL_S),
            )
        )

        # Sub-entities (e.g. the guest-mode switch) may have been added before
        # this restore ran – refresh them with the restored state.
        self._write_linked_entities()

    def _sensor_state(self, entity_id: str | None) -> str | None:
        """Raw state of a sensor now; None without one or while it is missing."""
        state = self.hass.states.get(entity_id) if entity_id else None
        return state.state if state else None

    async def async_will_remove_from_hass(self) -> None:
        """Cancel all timers when the entity is being removed."""
        self._cancel_layer_timers()
        await super().async_will_remove_from_hass()

    async def _async_config_entry_updated(self, hass, entry) -> None:
        """Called when config entry options change (e.g. from number entities)."""
        self._config_entry = entry
        self._config = self._build_config(entry)
        self._attr_min_temp = self._config.min_target_c
        self._attr_max_temp = self._config.max_target_c
        self.__dict__.pop("min_temp", None)
        self.__dict__.pop("max_temp", None)
        self._behaviour = self._build_behaviour(entry)
        self._regulator.cfg = self._config
        self._control.grace_s = entry.options.get(
            CONF_SENSOR_GRACE_S, DEFAULT_SENSOR_GRACE_S
        )
        self._control.loss_action = self._sensor_loss_action(entry)
        self._overlay_refresh_s = entry.options.get(
            CONF_OVERLAY_REFRESH_S, DEFAULT_OVERLAY_REFRESH_S
        )
        # New preset targets (e.g. the comfort target from its number entity)
        # apply right away.
        effects = self._layers.options_changed(self._layers_config(), time.time())
        self.async_write_ha_state()
        await self._async_handle_layer_effects(effects)

    # ------------------------------------------------------------------
    # Follow physical thermostat
    # ------------------------------------------------------------------

    @callback
    def _async_tado_state_changed(self, event) -> None:
        """Track source availability and optionally follow physical changes."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if _source_state_available(new_state) != _source_state_available(old_state):
            self.async_write_ha_state()
            self.hass.async_create_task(self._async_source_availability_changed())

        if not self._config_entry.options.get(CONF_FOLLOW_TADO_INPUT, False):
            return
        if new_state is None or new_state.state in ("unavailable", "unknown"):
            return

        new_temp_attr = new_state.attributes.get("temperature")
        old_temp_attr = old_state.attributes.get("temperature") if old_state else None
        if new_temp_attr is None or new_temp_attr == old_temp_attr:
            return

        tado_setpoint = safe_float(new_temp_attr)
        if tado_setpoint is None:
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
        # Counts like setting the temperature in the app – also while a layer
        # holds the room, then it comes back once the layer lets go.
        self._handle_layer_effects(
            self._layers.valve_changed(tado_setpoint, time.time())
        )

    async def _async_source_availability_changed(self) -> None:
        """Refresh source readings and resume regulation after reconnection."""
        await self.coordinator.async_refresh()
        await self._async_regulation_cycle(trigger="source_availability")

    # ------------------------------------------------------------------
    # Standard climate controls
    # ------------------------------------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target HVAC mode."""
        if hvac_mode not in self._attr_hvac_modes:
            return
        # Hold the regulation lock for the whole transition: a cycle whose
        # setpoint command (sent with hvac_mode=heat) is still in flight must
        # not interleave with turning the TRV off.  The follow-up cycle runs
        # the lock-free body – asyncio.Lock is not re-entrant.
        async with self._regulation_lock:
            previous_mode = self._hvac_mode
            previous_result = self._last_result
            self._hvac_mode = hvac_mode
            if hvac_mode == HVACMode.OFF:
                # No regulation while OFF: error_c and the corrections must not
                # linger as if still current – also not while the TRV command
                # below is on its way.
                self._last_result = None
            self.async_write_ha_state()

            # Forward HVAC mode to the source TRV entity.  This handler runs
            # the follow-up cycle itself, under the lock: the cycle PresetLayers
            # asks for is not run separately.
            if hvac_mode == HVACMode.OFF:
                try:
                    await self._async_send_hvac_mode_to_tado(HVACMode.OFF)
                except (TimeoutError, HomeAssistantError):
                    # Command failed – revert local state so the proxy stays in
                    # sync with the TRV (which may still be heating).  Nothing
                    # else changes: the layers and a boost stay as they were.
                    self._hvac_mode = previous_mode
                    self._last_result = previous_result
                    self._last_reason = "hvac_off_failed"
                    self.async_write_ha_state()
                    return
                # Only now that the valve is off: a boost pauses.  Window and
                # presence detection keep following their sensors.
                self._apply_layer_effects(self._layers.heating_off(time.time()))
                self._last_reason = "sent(hvac_off)"
                self.async_write_ha_state()
            elif hvac_mode == HVACMode.HEAT and previous_mode == HVACMode.OFF:
                # Returning from OFF → reset timestamp so the first HEAT cycle uses
                # dt=0 and avoids an integral spike from the long OFF period.
                self._last_regulation_ts = 0
                # Reported before the heat command: an open window with the
                # window action "off" keeps the valve off.
                self._apply_layer_effects(self._layers.heating_on(time.time()))
                # Reactivate the TRV, then run regulation to send the correct setpoint.
                # If set_hvac_mode fails, still run the cycle: every setpoint
                # command carries hvac_mode=heat, so it retries the activation
                # (rate limited) instead of aborting with an unhandled error.
                if not self._layers.valve_held_off:
                    try:
                        await self._async_send_hvac_mode_to_tado(HVACMode.HEAT)
                    except (TimeoutError, HomeAssistantError):
                        _LOGGER.warning(
                            "HVAC OFF→HEAT: activating the TRV failed, retrying via setpoint command"
                        )
                await self._async_regulation_cycle_locked(trigger="hvac_mode_change")
            else:
                await self._async_regulation_cycle_locked(trigger="hvac_mode_change")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a temperature: Manual with it becomes the user choice.

        While a layer holds the room it applies once the layer lets go.  It
        does NOT change the stored comfort target – use the Comfort number
        entity or the options flow for that.
        """
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        try:
            temp_f = float(temp)
        except (ValueError, TypeError):
            return
        if not math.isfinite(temp_f):
            _LOGGER.warning("Ignoring non-finite temperature: %s", temp)
            return
        await self._async_handle_layer_effects(
            self._layers.set_temperature(temp_f, time.time())
        )

    # ------------------------------------------------------------------
    # Properties for HA UI
    # ------------------------------------------------------------------

    @property
    def _source_available(self) -> bool:
        """Whether the configured source climate entity is available."""
        source_entity = self._config_entry.data.get("source_entity_id")
        source_state = self.hass.states.get(source_entity) if source_entity else None
        return _source_state_available(source_state)

    @property
    def available(self) -> bool:
        """The proxy cannot control an unavailable source thermostat."""
        return super().available and self._source_available

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        """Persist requested mode and layers while HA hides state attributes."""
        return _ProxyRestoreData({
            "desired_hvac_mode": self._hvac_mode,
            **self._layers.attributes(),
        })

    @property
    def current_temperature(self) -> float | None:
        """Return the room temperature the proxy regulates on.

        That is the backup sensor while it stands in for the external one.
        Under direct TRV control nothing is shown: the TRV's own reading is
        not a room temperature.
        """
        if self._control.mode == CONTROL_BACKUP_SENSOR:
            return self.coordinator.data.get("backup_room_temp")
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
    def hvac_action(self) -> HVACAction | None:
        """Infer heating/idle from the Tado entity state."""
        if not self._source_available:
            return None
        if self._hvac_mode == HVACMode.OFF or self._layers.valve_off_by_window:
            return HVACAction.OFF

        tado_internal = self.coordinator.data.get("tado_internal_temp")
        tado_setpoint = self.coordinator.data.get("tado_setpoint")

        if tado_internal is not None and tado_setpoint is not None:
            if tado_setpoint > tado_internal + 0.05:
                return HVACAction.HEATING

        return HVACAction.IDLE

    @property
    def icon(self) -> str:
        """Return preset-specific icon so the primary entity icon reflects the active preset."""
        if self._hvac_mode == HVACMode.OFF:
            return "mdi:power"
        return {
            PRESET_COMFORT: "mdi:sofa",
            PRESET_ECO: "mdi:leaf",
            PRESET_BOOST: "mdi:rocket-launch",
            PRESET_AWAY: "mdi:home-export-outline",
            PRESET_FROST_PROTECTION: "mdi:snowflake",
            PRESET_NONE: "mdi:hand-back-right",
        }.get(self._layers.active_preset, "mdi:thermostat")

    @property
    def preset_mode(self) -> str:
        """Return the active preset."""
        return self._layers.active_preset

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes visible in HA Developer Tools."""
        attrs: dict[str, Any] = {
            "regulation_reason": self._last_reason,
            "tado_internal_temp_c": self.coordinator.data.get("tado_internal_temp"),
            "correction_kp": self._config.tuning.kp,
            "correction_ki": self._config.tuning.ki,
            "effective_setpoint_c": self._effective_setpoint(),
            # User choice, layer flags and the window's valve state – also
            # read back after a restart.
            **self._layers.attributes(),
            "sensor_degraded": self._control.degraded,
            "active_control_mode": self._control.mode,
            "overlay_refresh_s": self._overlay_refresh_s,
        }

        # The last valid room reading, only while the sensor is missing.
        missing = self._control.missing_reading(time.time())
        if missing is not None:
            attrs["room_temp_last_valid_c"], attrs["room_temp_last_valid_age_s"] = missing

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
