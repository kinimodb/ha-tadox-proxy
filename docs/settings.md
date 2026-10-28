# Settings Reference

Everything you can see, switch, and adjust — looked up rather than read start to finish.

> **You do not need this page to use the integration.** The defaults work in most
> homes. Come here when you want a specific feature, or when you are curious what
> something in the interface means. If you are trying to *fix* a room, the
> [tuning guide](../TUNING.md) is the better place.

---

## Contents

1. [Presets](#presets)
2. [Weekly schedule](#weekly-schedule)
3. [Entities you get](#entities-you-get)
4. [Window and presence detection](#window-and-presence-detection)
5. [Guest mode](#guest-mode)
6. [Turning the thermostat off](#turning-the-thermostat-off)
7. [Following the physical thermostat](#following-the-physical-thermostat)
8. [When the sensor drops out](#when-the-sensor-drops-out)
9. [Adjustable settings](#adjustable-settings)
10. [Diagnostic attributes](#diagnostic-attributes)
11. [Supported radiators](#supported-radiators)

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
Changes take effect right away and do not interrupt a running boost, an open-window
or an away phase.

Moving the temperature slider without picking a preset switches to **Manual**. Your
stored Comfort temperature is left alone, so you can always get back to it.

Under **Configure → Manual mode**, the default is **Stay in Manual**:
it remains active until you select another preset. Choose **Return after a timer**
to make it temporary; the duration defaults to 15 minutes. With a weekly schedule,
the timer returns to the block active at expiry; without one it returns to the
previous preset. **Until the next schedule block** ends Manual at the next block
boundary, even if the next block uses the same preset. This choice needs at
least one block under **Weekly schedule**.
Every new manual temperature change restarts the countdown without changing which
preset will return. A direct change on the radiator also starts the countdown when
**Follow Physical Thermostat** is enabled. Selecting a named preset cancels it.
Window and presence protection can temporarily take over, but the countdown keeps
running; on expiry, the preset to restore is updated even if protection is still
active. The deadline survives a Home Assistant restart, including one that lasts
longer than the remaining time. A change from Boost returns to Boost's previous
preset rather than restarting Boost. If Manual was already active with no earlier
named preset, there is nothing to return to and it stays Manual.

---

## Weekly schedule

Open the proxy's **Configure → Weekly schedule** section. Each weekday has an
**Add** button. Enter a start time and choose **Comfort**, **Eco**, **Frost
protection** or **Off**. You can edit or delete a block in the same list; no YAML
or separate Schedule helper is needed. For example, add **00:00 Eco**, **07:00
Comfort** and **20:00 Eco** to each day. A preset stays active until the next
block, even across midnight and the end of the week. Days with no blocks inherit
the last selected profile. Times follow Home Assistant's configured time zone.
The schedule is stored in the integration's options and survives a restart.

The schedule applies to the proxy's existing preset temperatures. **Off** turns
the source TRV off; **Frost protection** keeps frost protection active. Presence
Away and open-window protection take priority. While Away is active, even a
scheduled Off block gives way to the Away target; when Presence returns Home, the
block active then applies. Manual ignores blocks until its selected return
condition. If another automation also sets this proxy's preset at a fixed time,
disable it when you enable the schedule to avoid competing commands.

---

## Entities you get

One entry controls exactly one Tado X climate entity, never a wall thermostat. What
that entity stands for depends on how Tado X reaches Home Assistant:

- **Matter:** one radiator thermostat (TRV), the valve head on the radiator. A room
  with two radiators therefore has two entries, both pointing at the same room
  temperature sensor.
- **Tado cloud** ([exabird/ha-tado-x](https://github.com/exabird/ha-tado-x)): one
  Tado room, which already drives all radiator thermostats in it. Each room gets one
  entry. Also set the [overlay refresh interval](#trv-communication) to 900 s.

See [how many entries you need](setup.md#how-many-entries-you-need).

Each entry creates:

| Entity | What it does |
|---|---|
| `climate.<name>` | The thermostat you actually use |
| `number.<name>_comfort_temperature` | What Comfort means, and one of these per preset |
| `sensor.<name>_boost_remaining` | Minutes of boost left. Shows 0 when boost is off |
| `binary_sensor.<name>_sensor_degraded` | Turns on when your room sensor stops reporting |
| `switch.<name>_follow_physical_thermostat` | Off by default, see [below](#following-the-physical-thermostat) |
| `switch.<name>_guest_mode` | Off by default, see [guest mode](#guest-mode) |

The `sensor_degraded` entity is worth putting on a dashboard or wiring to a
notification — a dead room sensor is the one failure that quietly ruins the
temperature in a room. It carries extra attributes: `last_valid_reading`,
`last_valid_age_s`, `grace_period_s` and `active_control_mode` (see
[when the sensor drops out](#when-the-sensor-drops-out)).

---

## Window and presence detection

Both are optional. Configure them under
**Settings → Devices & Services → Tado X Proxy → Configure**. They work
independently and can both be active at once.

### Window detection

Point it at a window contact (`binary_sensor.*`).

- **Window opens:** after a delay (default 30 s), the room switches to Frost
  Protection.
- **Action while the window is open** (default **Frost protection**): with Frost
  protection the TRV keeps the room at the frost protection temperature. With
  **Switch the TRV off**, the TRV is switched off completely instead. The room still
  shows Frost Protection, and its action shows Off. If the TRV cannot be switched
  off, frost protection takes over until the window opens again.
- **Window closes:** after a delay (default 120 s), the previous preset comes back.
  This delay is intentional — it stops the radiator from firing hard the instant you
  shut the window, while the room air is still cold from the draught.
- If the window shuts again before the first delay is up, nothing happens at all.
- **Changes while the window is open:** the room stays in Frost Protection. If you
  pick another preset or temperature in the meantime, that is what comes back when
  the window closes — not the one from before the window opened. If nobody is home,
  Away comes back instead (see [Presence detection](#presence-detection)).
- A preset or temperature change during the close delay ends the wait and takes
  effect right away. If nobody is home (presence detection has the room in Away),
  the room goes back to Away instead, and your change comes back when someone
  returns.

### Presence detection

Point it at a presence sensor or person tracker (`binary_sensor.*`).

- **Nobody home:** after a delay (default 10 minutes), the room switches to Away.
- **Someone returns:** after a delay (default 30 s), the previous preset comes back.
- **Changes while nobody is home:** the room stays in Away. If you pick another preset
  or temperature in the meantime, that is what comes back when someone returns. This
  also holds while a window is open: once it closes, the room goes back to Away.

### What counts as a change

Only a real switch between on and off counts. Many Zigbee sensors re-send their
battery level or signal strength every few minutes; those updates do not restart the
delays. A sensor that briefly drops out (`unavailable`) and comes back does not restart a
running delay, and it does not overwrite the preset that will be restored.

### After a Home Assistant restart

The preset that was active before a window opened survives a restart, and a reload
after you change the settings:

- **Window still open:** frost protection (or the switched-off TRV) simply goes on,
  without a new open delay. Closing the window brings back the saved preset.
- **Window sensor not back yet:** many Zigbee sensors report a little later than the
  integration starts. Until the sensor reports, frost protection goes on, just as when
  a sensor drops out while the window is open.
- **Window closed in the meantime:** the saved preset comes back right away, and a TRV
  the window had switched off is switched on again. This also covers a restart during
  the close delay.
- **Window opened while Home Assistant was down:** the normal open delay starts.

The attributes `window_restore_preset` and `window_restore_target_c` on the climate
entity show what comes back when the window closes.

Presence detection does not remember the preset from before everybody left. If Home
Assistant restarts while the room is in Away, coming home brings back **Comfort**. A
running boost does not survive a restart either; the room goes back to Comfort.

---

## Guest mode

`switch.<name>_guest_mode` ("Gastmodus" in German), **off by default**.

You are away, but guests are staying at your place. Your presence sensor says
"nobody home", so the room would normally drop to Away. Turn guest mode on and the
heating keeps running as usual. Put the switch on a dashboard to flip it with one tap.

- **Turning it on:** the presence sensor no longer switches the room to Away. If the
  room is already in Away, the previous preset comes back immediately.
- **Turning it off:** presence detection works normally again. If nobody is home at
  that moment, the room drops to Away after the usual delay (default 10 minutes).
- Presets, the temperature slider and window detection keep working as usual.
- The switch keeps its state across Home Assistant restarts.
- Without a presence sensor configured, the switch has no effect.

---

## Turning the thermostat off

Switching the proxy to **OFF** passes the off command straight through to the real
Tado X radiator thermostat.

If that command fails — the radiator thermostat is unreachable, for instance — the
proxy goes back to the mode it was in, and an open window keeps its frost
protection. This keeps what you see in Home Assistant honest about what the hardware
is actually doing.

If the source radiator thermostat itself becomes `unavailable` or `unknown`, the
proxy climate entity becomes unavailable too. This is different from switching
the heating off: the proxy retains the requested mode and target, and resumes
regulation when the source returns. `sensor_degraded` reports only problems with
the room temperature sensor, not with the source thermostat.

While off, the proxy shows a target of 5 °C. Turning it off while a window is open
ends the window detection for that window: the preset that would have come back when
the window closed comes back right away. A boost comes back as Comfort, because it
would run out while the heating is off; the same applies when a window closes while
the heating is off. Turning it back on checks the window again. If the radiator
thermostat does not respond when you turn it back on, the proxy stays on and tries
again with its next temperature command.

---

## Following the physical thermostat

`switch.<name>_follow_physical_thermostat`, **off by default**.

Turn it on if you want to be able to walk up to the radiator, turn the dial, and have
the proxy accept that as your decision. The proxy then switches to the **Manual**
preset and holds your temperature instead of overwriting it.

The integration distinguishes your hand from its own commands by how far the new
value sits from what it last sent — more than 0.5 °C (adjustable) counts as you.

With the switch **off**, the integration owns the setpoint. A turned dial then
holds only until the next command: it notices that the radiator thermostat no
longer sits on the value it sent and puts its own value back, after the waiting
period at the latest (**Min command interval**, 180 s by default). So a dial
turned up while a window is open no longer heats into the open window. The
attribute `regulation_reason` shows `sent(trv_drift)` for such a correction.

---

## When the sensor drops out

Room sensors miss readings sometimes, especially over Zigbee. The integration handles
short gaps rather than panicking.

While the sensor is missing, it carries on using the last good reading for a grace
period (default 5 minutes). During that time:

- `binary_sensor.<name>_sensor_degraded` turns **on**.
- The attribute `room_temp_last_valid_age_s` tells you how stale the reading is.

If the sensor stays gone past the grace period, the integration stops trusting the
old reading. What happens then is up to you — pick it under **Configure → When the
room sensor stays unavailable**:

| Choice | What happens |
|---|---|
| **Let the TRV regulate on its own** | The TRV gets your target temperature as it is and controls the room with its own built-in sensor. Presets, Eco and Away keep working. The room usually ends up a little cooler than set, because the TRV measures right next to the warm radiator. Default for new entries |
| **Regulate with a backup room sensor** | A second room sensor takes over, and the integration keeps correcting as usual. Pick it under **Backup room sensor**; it must be a different sensor. If the backup is missing too, the TRV regulates on its own |
| **Keep the last TRV command** | The TRV keeps the last temperature the integration sent, and nothing new is sent until the sensor is back. Preset changes have no effect meanwhile, and a dial turned at the radiator is left alone — without a room reading your hand is the better judge. Entries set up before this choice existed keep this behaviour until you change it |

The attribute `active_control_mode` on the climate entity and on the
`sensor_degraded` entity shows what is in charge right now: `room_sensor`,
`backup_sensor`, `trv` or `hold`.

Two things always win, whatever you picked:

- **An open window** still lowers the TRV to the frost protection temperature. With
  **Keep the last TRV command**, the TRV then regulates on its own until the room
  sensor is back, so it does not stay at the frost temperature after the window
  closes.
- **Turning the thermostat off** still turns the TRV off. Turned back on, the TRV
  regulates on its own until the room sensor is back — the integration no longer
  knows which temperature the TRV holds.

A sensor that has not reported at all since Home Assistant started gets the same
grace period, counted from the start. An open window does not wait for it: the TRV
goes to the frost protection temperature right away and regulates on its own until
the room sensor reports. Whenever the integration switches between the
room sensor, the backup sensor and the TRV, it starts its long-term correction from
zero: a correction learned for one sensor does not fit another.

The choice only reacts to a sensor that Home Assistant marks as `unavailable` or
`unknown`. A sensor that silently keeps showing an old number still looks fine.

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

**Overlay refresh is for the Tado cloud integration only.**
[exabird/ha-tado-x](https://github.com/exabird/ha-tado-x) sends every setpoint as a
timer overlay that ends after 30 minutes. Set the refresh to **900 s**: when no other
command went out for 15 minutes, the proxy resends the current setpoint, so the
overlay never runs out. Over Matter there is no timer — leave it at 0.

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
| `correction_kp` / `correction_ki` | The configured gains, before gain scheduling scales Kp |
| `window_open_active` | Window detection has taken over |
| `window_close_delay_active` | Waiting after a window closed |
| `presence_away_active` | Presence detection has taken over |
| `guest_mode` | Guest mode is on, presence detection will not switch to Away |
| `manual_target_c` | The temperature of the Manual preset. Only shown while Manual is active |
| `manual_return_preset` / `manual_return_at_ts` | Prior preset and absolute Unix timestamp of a pending timed return |
| `sensor_degraded` | Room sensor missing, running on the last reading |
| `active_control_mode` | What controls the TRV right now: `room_sensor`, `backup_sensor`, `trv` or `hold`. See [when the sensor drops out](#when-the-sensor-drops-out) |
| `overlay_refresh_s` | The configured overlay refresh interval. 0 means off |
| `is_saturated` | The correction has hit its limit and cannot push harder |

While the sensor is missing, `room_temp_last_valid_c` and
`room_temp_last_valid_age_s` appear as well.

The regulation values — `feedforward_offset_c`, `p_correction_c`, `i_correction_c`,
`error_c`, `target_for_tado_c` and `is_saturated` — only appear while the integration
is actually regulating. They disappear while the proxy is off, and when the room
sensor has been gone for longer than the grace period — unless a backup room sensor
has taken over.

### What "heating" means

The proxy shows **Heating** (`hvac_action: heating`) when the target it sent to the
Tado is above the Tado's own reading. In practice this means the valve is open. It
does **not** mean that the room still needs heat: the correction keeps the valve
slightly open to hold the room at its target, so a room only 0.05 °C below target can
show Heating. After a slow final approach it can even stay on for a while once the
room is slightly above its target (see
[Integral windup](how-it-works.md#integral-windup)).

To decide whether a boiler should run, use `error_c` instead (target minus room
temperature). Leave out rooms that are off or have `sensor_degraded` on. Add some
hysteresis so the boiler does not start for tiny gaps — for example, start when
`error_c` rises above 0.3 °C and keep running until it reaches 0, which is when the
room is at its target. Do not stop earlier: a boiler that stops while `error_c` is
still above 0 leaves the room short of its target.

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
