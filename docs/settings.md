# Settings Reference

Everything you can see, switch, and adjust — looked up rather than read start to finish.

> **You do not need this page to use the integration.** The defaults work in most
> homes. Come here when you want a specific feature, or when you are curious what
> something in the interface means. If you are trying to *fix* a room, the
> [tuning guide](../TUNING.md) is the better place.

---

## Contents

1. [Presets](#presets)
2. [Entities you get](#entities-you-get)
3. [Window and presence detection](#window-and-presence-detection)
4. [Turning the thermostat off](#turning-the-thermostat-off)
5. [Following the physical thermostat](#following-the-physical-thermostat)
6. [When the sensor drops out](#when-the-sensor-drops-out)
7. [Adjustable settings](#adjustable-settings)
8. [Diagnostic attributes](#diagnostic-attributes)
9. [Supported radiators](#supported-radiators)

---

## Presets

| Preset | Default | What it is for |
|---|---|---|
| **Comfort** | 20.0 °C | Your normal temperature |
| **Eco** | 17.0 °C | Saving energy |
| **Boost** | 25.0 °C | A short burst of heat. Reverts by itself after 30 minutes |
| **Away** | 17.0 °C | Nobody home |
| **Frost Protection** | 7.0 °C | Just enough to stop pipes freezing |
| **Manual** | — | You picked a temperature yourself, no preset active |

Every preset temperature is its own entity, named like
`number.<your_name>_comfort_temperature`. You can change what each preset means in
0.5 °C steps between 5 and 30 °C, from the interface or from an automation.

Moving the temperature slider without picking a preset switches to **Manual**. Your
stored Comfort temperature is left alone, so you can always get back to it.

---

## Entities you get

One entry controls exactly one Tado X radiator thermostat (TRV) — the valve head on
the radiator, never a wall thermostat. A room with two radiators therefore has two
entries, both pointing at the same room temperature sensor. See
[one entry per radiator thermostat](setup.md#one-entry-per-radiator-thermostat).

Each entry creates:

| Entity | What it does |
|---|---|
| `climate.<name>` | The thermostat you actually use |
| `number.<name>_comfort_temperature` | What Comfort means, and one of these per preset |
| `sensor.<name>_boost_remaining` | Minutes of boost left. Shows 0 when boost is off |
| `binary_sensor.<name>_sensor_degraded` | Turns on when your room sensor stops reporting |
| `switch.<name>_follow_physical_thermostat` | Off by default, see [below](#following-the-physical-thermostat) |

The `sensor_degraded` entity is worth putting on a dashboard or wiring to a
notification — a dead room sensor is the one failure that quietly ruins the
temperature in a room. It carries extra attributes: `last_valid_reading`,
`last_valid_age_s` and `grace_period_s`.

---

## Window and presence detection

Both are optional. Configure them under
**Settings → Devices & Services → Tado X Proxy → Configure**. They work
independently and can both be active at once.

### Window detection

Point it at a window contact (`binary_sensor.*`).

- **Window opens:** after a delay (default 30 s), the room switches to Frost
  Protection.
- **Window closes:** after a delay (default 120 s), the previous preset comes back.
  This delay is intentional — it stops the radiator from firing hard the instant you
  shut the window, while the room air is still cold from the draught.
- If the window shuts again before the first delay is up, nothing happens at all.
- The preset that comes back is exactly the one you had before — including Frost
  Protection if you had chosen it yourself.
- Restarting Home Assistant or saving these settings does not change the preset. If
  a window is open during a restart, the room stays in window mode and your previous
  preset still comes back when it closes. The same applies to Away and to a running
  Boost, which carries on for the time it had left.

### Presence detection

Point it at a presence sensor or person tracker (`binary_sensor.*`).

- **Nobody home:** after a delay (default 10 minutes), the room switches to Away.
- **Someone returns:** after a delay (default 30 s), the previous preset comes back.

---

## Turning the thermostat off

Switching the proxy to **OFF** passes the off command straight through to the real
Tado X radiator thermostat.

If that command fails — the radiator thermostat is unreachable, for instance — the
proxy goes back to the mode it was in. This keeps what you see in Home Assistant
honest about what the hardware is actually doing.

---

## Following the physical thermostat

`switch.<name>_follow_physical_thermostat`, **off by default**.

Turn it on if you want to be able to walk up to the radiator, turn the dial, and have
the proxy accept that as your decision. The proxy then switches to the **Manual**
preset and holds your temperature instead of overwriting it.

The integration distinguishes your hand from its own commands by how far the new
value sits from what it last sent — more than 0.5 °C (adjustable) counts as you.

---

## When the sensor drops out

Room sensors miss readings sometimes, especially over Zigbee. The integration handles
short gaps rather than panicking.

While the sensor is missing, it carries on using the last good reading for a grace
period (default 5 minutes). During that time:

- `binary_sensor.<name>_sensor_degraded` turns **on**.
- The attribute `room_temp_last_valid_age_s` tells you how stale the reading is.

If the sensor stays gone past the grace period, the correction pauses rather than
acting on data it cannot trust.

Pending window and presence actions are re-checked just before they fire, so a single
glitchy reading cannot flip your room into Away.

---

## Adjustable settings

All of these live under
**Settings → Devices & Services → Tado X Proxy → Configure**.

> Changing these is not part of normal use. If a room is misbehaving, work through
> the [tuning guide](../TUNING.md) instead of adjusting values at random — it tells
> you *which* one to change for a given symptom.

### PI Controller

How strongly the integration reacts to the gap between the temperature you want and
the temperature you have.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Kp (Proportional) | 0.8 | 0.0–5.0 | How hard it reacts to the current gap |
| Ki (Integral) | 0.003 | 0.0–0.1 | How fast it corrects a long-standing offset |
| Integral precision zone | 0.3 °C | 0.1–1.0 °C | Long-term correction only builds up inside this band |

### Gain Scheduling

Automatically reacts harder when the room is far off target and gentler when it is
close, so you do not have to compromise between fast heat-up and a steady room.

| Setting | Default | Range | What it does |
|---|---|---|---|
| Adaptive Gain Scheduling | On | On/Off | Master switch for this section |
| Near-target strength | 1.0 | 0.3–1.5 | Multiplies Kp when close to target |
| Cold-start boost | 1.5 | 1.0–3.0 | Multiplies Kp while heating up from cold |
| Cold-start zone threshold | 2.0 °C | 0.5–5.0 °C | Above this gap, the cold-start boost applies |
| Near-target zone threshold | 0.5 °C | 0.1–2.0 °C | Below this gap, the near-target strength applies |

Between the two thresholds the strength slides smoothly from one to the other.

### TRV Communication

How often the integration is allowed to talk to your radiator thermostat (TRV).

| Setting | Default | Range | What it does |
|---|---|---|---|
| Min command interval | 180 s | 60–600 s | Shortest gap between commands. Lower means faster reaction and shorter battery life |
| Min change threshold | 0.3 °C | 0.1–1.0 °C | Ignore differences smaller than this instead of sending a command |
| Overlay refresh interval | 0 s | 0–3600 s | Periodically resend the setpoint to keep cloud overlays alive. 0 turns this off |

**Do not lower the command interval without a good reason.** Tado X thermostats
communicate wirelessly and every command costs battery.

### Behaviour

| Setting | Default | Range | What it does |
|---|---|---|---|
| Sensor grace period | 300 s | 0–1800 s | How long to keep using the last reading when the sensor is missing |
| Follow-tado threshold | 0.5 °C | 0.1–2.0 °C | How far a change must be from the last sent value to count as a human turning the dial |
| Follow-tado grace period | 20 s | 5–120 s | Ignore changes on the Tado for this long after sending a command, so it does not mistake its own command for you |
| Urgent decrease threshold | 1.0 °C | 0.5–3.0 °C | Skip the waiting period when the temperature needs to drop by at least this much |

---

## Diagnostic attributes

Visible under **Developer Tools → States** on your proxy entity. Useful when
something is wrong or when you are curious what the integration is thinking.

| Attribute | Meaning |
|---|---|
| `effective_setpoint_c` | The temperature you asked for, including any active preset |
| `regulation_reason` | Why it did what it did on the last cycle |
| `tado_internal_temp_c` | What the Tado thinks the temperature is |
| `feedforward_offset_c` | How much warmer the Tado reads than the room. Normally 1–5 °C |
| `p_correction_c` | Correction from the current gap |
| `i_correction_c` | Correction from a persistent offset |
| `error_c` | The gap between target and actual room temperature |
| `target_for_tado_c` | The number actually sent to the Tado |
| `correction_kp` / `correction_ki` | The gains currently in effect |
| `window_open_active` | Window detection has taken over |
| `window_close_delay_active` | Waiting after a window closed |
| `presence_away_active` | Presence detection has taken over |
| `sensor_degraded` | Room sensor missing, running on the last reading |
| `is_saturated` | The correction has hit its limit and cannot push harder |

While the sensor is missing, `room_temp_last_valid_c` and
`room_temp_last_valid_age_s` appear as well.

The [tuning guide](../TUNING.md#diagnostics-what-the-attributes-tell-you) shows what
healthy and unhealthy values look like side by side.

---

## Supported radiators

Tado X thermostats screw onto hot-water radiators with a thermostatic valve.

**Works:**

- Panel radiators (Type 11, Type 22) — the most common kind
- Column and sectional radiators
- Cast iron radiators, with an adapter
- Towel radiators, if they have a standard valve connection

**Does not work:** underfloor heating, electric radiators, steam heating. These
either have no valve to control or respond far too slowly for this approach.

Different radiator types benefit from slightly different settings. The
[tuning guide](../TUNING.md#tuning-by-radiator-type) has a starting point for each.
