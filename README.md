# Tado X Proxy Thermostat

[![Tests](https://github.com/kinimodb/ha-tadox-proxy/actions/workflows/tests.yml/badge.svg)](https://github.com/kinimodb/ha-tadox-proxy/actions/workflows/tests.yml)
![Version](https://img.shields.io/badge/version-1.0.3-blue)
![HA](https://img.shields.io/badge/Home%20Assistant-2026.3%2B-41BDF5)

A Home Assistant custom component (HACS) that creates a virtual proxy thermostat
for Tado X radiator thermostats (TRVs). Uses feedforward + PI control with an
external room sensor for precise room temperature control (±0.3–0.5°C accuracy).

---

## The Problem

Tado X TRVs measure temperature at the radiator surface – not the room.
This causes the heating to shut off too early, resulting in a persistent 1–3°C undershoot.

**Tado X Proxy** compensates this offset using an external room sensor and a
feedforward + PI control loop. The correction is applied directly to the setpoint,
working *with* Tado's internal controller rather than against it.

---

## Prerequisites

- Home Assistant **2026.3** or newer
- [HACS](https://hacs.xyz) installed
- At least one Tado X TRV as a `climate.*` entity in HA
- A temperature sensor (`sensor.*`, `device_class: temperature`) in the room

---

## Installation

1. Open HACS → **Integrations** → Menu (three dots, top right) → **Custom repositories**
2. Enter URL: `https://github.com/kinimodb/ha-tadox-proxy`
3. Category: **Integration** → **Add**
4. Search for **Tado X Proxy Thermostat** in HACS and install
5. Restart Home Assistant
6. **Settings** → **Devices & Services** → **Add Integration** → *Tado X Proxy Thermostat*

---

## Configuration

Three fields are required during initial setup:

| Field | Description |
|-------|-------------|
| Source Climate Entity | The real Tado X TRV (`climate.*`) |
| External Temperature Sensor | A `sensor.*` with `device_class: temperature` in the room |
| Name | Display name for the proxy thermostat |

Additional options are available under **Settings → Devices & Services → Tado X Proxy → Configure** (control parameters, window/presence sensors).

---

## Presets

| Preset | Default | Description |
|--------|---------|-------------|
| **Comfort** | 20.0°C | Standard target temperature |
| **Eco** | 17.0°C | Energy-saving mode |
| **Boost** | 25.0°C | Short-term heating burst, auto-reverts after timer (default: 30 min) |
| **Away** | 17.0°C | Reduced temperature for absences |
| **Frost Protection** | 7.0°C | Minimum temperature (window open, extended absence) |
| **Manual** | — | Free temperature selection via slider, no preset active |

Each preset temperature is exposed as a `number.*` entity (e.g., `number.*_comfort_temperature`),
adjustable in 0.5°C steps (range 5–30°C) and usable in automations.

Moving the temperature slider without selecting a preset activates **Manual** mode
without changing the stored comfort temperature.

---

## Automation Features

### Window Detection

An optional `binary_sensor.*` (e.g., window contact) can trigger automatic frost protection:

- **Window opens:** After a configurable delay (default: 30s), switches to Frost Protection.
- **Window closes:** After a configurable close delay (default: 120s), restores the previous preset.
  The close delay prevents aggressive heating bursts after ventilation.
- If the window closes before the open delay expires, nothing happens.

### Presence Sensor

An optional `binary_sensor.*` (e.g., person tracker) can trigger automatic away mode:

- **Nobody home:** After a configurable delay (default: 30 min), switches to Away.
- **Someone returns:** Immediately restores the previous preset.

Both sensors work independently and can be active simultaneously.

### HVAC OFF Forwarding

When the proxy thermostat is turned OFF, it sends `set_hvac_mode(off)` directly
to the underlying Tado TRV. The TRV powers down completely instead of heating to
a frost protection setpoint. Returning to HEAT reactivates the TRV automatically.

### Follow Physical Thermostat

The switch `switch.*_follow_physical_thermostat` (disabled by default) lets the proxy
adopt manual temperature changes made directly on the physical TRV (>1.5°C difference).

---

## Control Parameters

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| **Kp (Proportional)** | 0.8 | 0.0–5.0 | Strength of immediate error correction |
| **Ki (Integral)** | 0.003 | 0.0–0.1 | Speed of long-term drift correction |
| **Overlay Refresh (s)** | 0 (off) | 0–3600 | Periodic resend interval for cloud-API integrations (see below) |

### Overlay Refresh (Cloud-API Users)

**Matter/Thread users: leave this at 0 (default).** Temperature commands persist
permanently via Matter – no refresh needed.

Cloud-API integrations (e.g. [ha-tado-x](https://github.com/exabird/ha-tado-x))
may use timer-based overlays that expire after 30 minutes. If your TRV reverts to
its schedule unexpectedly, set this to e.g. **1500** (25 minutes) so the proxy
periodically resends the setpoint before the overlay expires.

> For detailed tuning guidance, see [TUNING.md](TUNING.md).

---

## Sensor Resilience

During brief sensor outages (e.g., Zigbee connectivity issues), the integration
falls back to the last valid reading for up to 300 seconds (**Last-Valid-Bridging**).
Window and presence timer actions are re-validated before execution to prevent
false switching from sensor glitches.

---

## Diagnostic Attributes

Visible under **Developer Tools → States**:

| Attribute | Description |
|-----------|-------------|
| `effective_setpoint_c` | Effective setpoint including preset (°C) |
| `regulation_reason` | Reason for the last regulation decision |
| `tado_internal_temp_c` | Tado internal temperature reading (°C) |
| `feedforward_offset_c` | Feedforward correction offset (°C) |
| `p_correction_c` / `i_correction_c` | P and I correction components (°C) |
| `error_c` | Current error between target and room temperature (°C) |
| `target_for_tado_c` | Calculated setpoint sent to Tado (°C) |
| `correction_kp` / `correction_ki` | Active Kp/Ki gains |
| `window_open_active` | Window detection active |
| `window_close_delay_active` | Close delay active |
| `presence_away_active` | Presence-away mode active |
| `sensor_degraded` | External sensor unavailable, bridging active |
| `is_saturated` | Controller saturation active |
| `overlay_refresh_s` | Configured overlay refresh interval (0 = off) |

When sensor bridging is active, `room_temp_last_valid_c` and `room_temp_last_valid_age_s` are also shown.

---

## Known Limitations

- **iOS Companion App:** Entity selection crashes due to an HA frontend bug in `ha-entity-picker`. **Workaround:** Use a browser for configuration.

---

## Project Files

| File | Purpose |
|------|---------|
| [TUNING.md](TUNING.md) | Tuning guide for new rooms |
| [ROADMAP.md](ROADMAP.md) | Feature roadmap and milestones |

---

## License

MIT License – see [LICENSE](LICENSE)
