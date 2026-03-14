> **[Deutsche Version](README_DE.md)**  |  English (this page)

# Tado X Proxy Thermostat

[![Tests](https://github.com/kinimodb/ha-tadox-proxy/actions/workflows/tests.yml/badge.svg)](https://github.com/kinimodb/ha-tadox-proxy/actions/workflows/tests.yml)
![Version](https://img.shields.io/badge/version-1.0.0-blue)
![HA](https://img.shields.io/badge/Home%20Assistant-2026.3%2B-41BDF5)

A Home Assistant custom component (HACS) that creates a virtual proxy thermostat
for Tado X radiator thermostats (TRVs). At its core is a feedforward + PI control
loop that compensates the Tado internal sensor using an external room sensor –
for precise room temperature control instead of radiator surface temperature.

> **v1.0.0** – Stable Release. Validated in multiple rooms with ±0.3–0.5°C accuracy.

---

## The Problem – and the Solution

Tado X TRVs measure their own surface temperature directly at the radiator – not
the actual room temperature. This causes the heating to shut off too early
(the radiator is already warm, but the room is not). Result: a persistent
undershoot of 1–3°C.

**Tado X Proxy** solves this problem with a feedforward approach:
- An external room sensor (e.g., Zigbee temperature sensor) provides the true room temperature.
- The proxy calculates a correction offset from the difference `Tado internal – room`.
- This offset is applied directly to the setpoint – without delay.
- A PI controller corrects any remaining residual error (Kp=0.8, Ki=0.003).

Result: ±0.3–0.5°C accuracy, confirmed over 11+ hours of overnight operation.

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

---

## Presets

The proxy thermostat supports six operating modes:

| Preset | Description |
|--------|-------------|
| **Comfort** | Target temperature from the comfort setting (configurable) |
| **Eco** | Fixed eco temperature (configurable, default: 17°C) |
| **Boost** | Short-term heating burst to boost temperature, then automatically returns |
| **Away** | Reduced temperature for short absences |
| **Frost Protection** | Minimum temperature for frost protection (e.g., when a window is open or during extended absence) |
| **Manual** | Free temperature selection via slider – no preset active |

**Manual** mode is automatically activated when the temperature slider is moved
without selecting a preset. The manually set temperature does **not** change
the stored comfort temperature.

The **Boost** timer automatically switches back to Comfort after the configured
duration (default: 30 minutes). An active boost mode is also reset to Comfort
on HA restart for safety.

---

## Preset Temperatures as Entities

Each preset temperature is available as a `number.*` entity in HA and can be
used directly in automations:

| Entity | Description | Default |
|--------|-------------|---------|
| `number.*_comfort_temperature` | Comfort target temperature | 20.0°C |
| `number.*_eco_temperature` | Eco target temperature | 17.0°C |
| `number.*_boost_temperature` | Boost target temperature | 25.0°C |
| `number.*_away_temperature` | Away target temperature | 17.0°C |
| `number.*_frost_protection_temperature` | Frost protection temperature | 7.0°C |

All entities are adjustable in the range 5–30°C in 0.5°C steps.

---

## Switch: Follow Physical Thermostat

The switch `switch.*_follow_physical_thermostat` enables an optional mode:

When someone manually sets a new temperature on the physical Tado TRV
(difference > 1.5°C from the last sent setpoint), the proxy automatically
adopts this change and switches to **Manual** mode.

> **Disabled** by default. Must be explicitly enabled.

---

## Window Detection

An optional binary sensor (e.g., window contact sensor) can be configured.
When the window is opened (`state: on`):

1. A configurable timer starts (default: 30 seconds).
2. After expiry: switches to the **Frost Protection** preset – temperature is reduced to frost protection level.
3. When the window is closed: the previous preset is automatically **restored**.

If the window is closed before the timer expires, the timer is cancelled –
no intervention in heating.

**After closing:** A configurable **close delay** (default: 120 seconds)
waits before restoring the previous preset. This prevents an aggressive
heating burst after ventilation – the thermal mass of walls and furniture
partially equalizes the room temperature within a few minutes on its own.

If the window is reopened during the close delay, the proxy stays in
frost protection mode (without a new open delay).

**Configuration** (in integration options):
- *Window sensor*: `binary_sensor.*` (optional)
- *Window open delay*: 0–3600 seconds (wait time before frost protection)
- *Window close delay*: 0–600 seconds (wait time before preset restore, 0 = immediate)

The attributes `window_open_active` and `window_close_delay_active` show the current state.

---

## Presence Sensor

An optional presence sensor (e.g., person tracker) can be configured.
When nobody is home (`state: off`):

1. A configurable timer starts (default: 30 minutes).
2. After expiry: switches to the **Away** preset.
3. When someone returns (`state: on`): automatically returns to the **previous preset**.

If someone returns before the timer expires, the timer is cancelled.

**Configuration** (in integration options):
- *Presence sensor*: `binary_sensor.*` (optional)
- *Away delay*: 0–7200 seconds

The attribute `presence_away_active` shows the current state.

> Window and presence sensors are **independent of each other**: the window controls
> the preset (frost protection), the presence sensor controls the preset
> (away). Both can be active simultaneously.

---

## Control Parameters (Options)

Adjustable via **Settings → Devices & Services → Tado X Proxy → Configure**:

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| **Kp (Proportional)** | 0.8 | 0.0–5.0 | Strength of immediate error correction |
| **Ki (Integral)** | 0.003 | 0.0–0.1 | Speed of long-term drift correction |

> For detailed tuning guidance, see [TUNING.md](TUNING.md).

---

## Diagnostic Attributes

The proxy entity exposes the following attributes (visible under **Developer Tools → States**):

### Always Visible

| Attribute | Description |
|-----------|-------------|
| `regulation_reason` | Reason for the last regulation decision (e.g., `sent(normal_update)`, `rate_limited(120s)`) |
| `tado_internal_temp_c` | Tado internal temperature reading (°C) |
| `correction_kp` | Active proportional gain (Kp) |
| `correction_ki` | Active integral gain (Ki) |
| `effective_setpoint_c` | Effective setpoint including preset (°C) |
| `window_open_active` | Window open detection active (`true`/`false`) |
| `window_close_delay_active` | Close delay after window close active (`true`/`false`) |
| `presence_away_active` | Presence-away mode active (`true`/`false`) |
| `sensor_degraded` | External sensor unavailable, bridging active (`true`/`false`) |

### Conditionally Visible (Sensor Resilience)

| Attribute | Description |
|-----------|-------------|
| `room_temp_last_valid_c` | Last valid reading from the external sensor (°C) |
| `room_temp_last_valid_age_s` | Age of the last valid reading (seconds) |

### Conditionally Visible (After First Regulation Cycle)

| Attribute | Description |
|-----------|-------------|
| `feedforward_offset_c` | Feedforward correction offset (°C) |
| `p_correction_c` | Proportional (P) correction (°C) |
| `i_correction_c` | Integral (I) correction (°C) |
| `error_c` | Current error between target and room temperature (°C) |
| `target_for_tado_c` | Calculated setpoint sent to Tado (°C) |
| `is_saturated` | Controller saturation active (`true`/`false`) |

---

## Sensor Resilience

During brief sensor outages (e.g., Zigbee connectivity issues), the integration
falls back to the last valid reading (**Last-Valid-Bridging**). The grace period
defaults to 300 seconds (5 minutes). During this time, the attribute
`sensor_degraded` shows `true`.

Window and presence actions are re-validated against the current sensor state
before execution (**timer revalidation**), to prevent false switching caused
by brief sensor glitches.

---

## Project Files

| File | Purpose |
|------|---------|
| [TUNING.md](TUNING.md) | Detailed tuning guide for new rooms (English) |
| [TUNING_DE.md](TUNING_DE.md) | Tuning-Anleitung für neue Räume (Deutsch) |
| [ROADMAP.md](ROADMAP.md) | Feature roadmap and milestones |
| [CLAUDE.md](CLAUDE.md) | Project instructions for AI-assisted development |

---

## Known Limitations

- **iOS Companion App:** The configuration (entity selection) does not work reliably in the iOS Companion App – an HA frontend bug in the `ha-entity-picker` component causes a crash. **Workaround:** Open the initial configuration and options via a browser (not the app).

---

## License

MIT License – see [LICENSE](LICENSE)
