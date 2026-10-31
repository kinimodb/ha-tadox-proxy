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
4. [Guest mode](#guest-mode)
5. [Turning the thermostat off](#turning-the-thermostat-off)
6. [Following the physical thermostat](#following-the-physical-thermostat)
7. [When the sensor drops out](#when-the-sensor-drops-out)
8. [Needs heat for a boiler](#needs-heat-for-a-boiler)
9. [Adjustable settings](#adjustable-settings)
10. [Diagnostic attributes](#diagnostic-attributes)
11. [Supported radiators](#supported-radiators)

---

## Presets

| Preset | Default | What it is for |
|---|---|---|
| **Comfort** | 20.0 °C | Your normal temperature |
| **Eco** | 17.0 °C | Saving energy |
| **Boost** | 25.0 °C | A short burst of heat for 30 minutes, then your own preset is back |
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

### Your choice, and what can take over

The preset you pick (or the temperature you set) is **your choice**. The room
remembers it. Three things can take over the room for a while:

1. **An open window** – Frost Protection (see [window detection](#window-detection))
2. **Nobody home** – Away (see [presence detection](#presence-detection))
3. **A boost** – Boost (see [boost](#boost))

They rank in this order: an open window beats nobody home, and nobody home beats a
boost. Whatever ranks highest decides the preset you see. None of them changes your
choice. Once they let go, your choice is back – after airing, after coming home,
after a boost and after a restart.

- **A change while something has taken over** becomes your new choice right away. You
  see it once the room is free again, so you never have to repeat it.
- **Tapping the preset that is forced right now** (Away while nobody is home, Frost
  Protection while a window is open) does not become your choice. Confirming what you
  see does not leave the room cold later. It does end a running boost, though:
  tapping any preset ends one.
- **Away or Frost Protection picked by hand** is your choice like any other preset. It
  stays after airing.

The attribute `user_preset` on the climate entity shows your choice at any time.

### Boost

- Picking Boost heats at the boost temperature for the boost time (default 30
  minutes). Then your own choice is back – a boost started from Eco goes back to Eco.
- The time only counts down while the boost is what heats the room. An open window,
  nobody home or heating that is off **pauses** it. Afterwards it continues with the
  time that was left. `sensor.<name>_boost_remaining` stands still meanwhile.
- A boost picked while a window is open, nobody is home or the heating is off waits
  and then runs its full time.
- Picking Boost again during a boost starts its full time again. Picking another
  preset or setting a temperature ends it.
- **Boost while nobody is home:** it waits until someone comes home. To warm the
  house before you arrive, turn [guest mode](#guest-mode) on. Without a presence
  sensor there is nothing to wait for, and a boost heats right away.
- A boost does not survive a Home Assistant restart. Your own choice comes back.

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
| `sensor.<name>_boost_remaining` | Minutes of boost left. Stands still while the boost is paused, 0 without a boost |
| `binary_sensor.<name>_sensor_degraded` | Turns on when your room sensor stops reporting |
| `binary_sensor.<name>_needs_heat` | On while the room needs heat. For a boiler that only runs on demand, see [needs heat for a boiler](#needs-heat-for-a-boiler) |
| `switch.<name>_follow_physical_thermostat` | Off by default, see [below](#following-the-physical-thermostat) |
| `switch.<name>_guest_mode` | Off by default, see [guest mode](#guest-mode) |

The `sensor_degraded` entity is worth putting on a dashboard or wiring to a
notification — a dead room sensor is the one failure that quietly ruins the
temperature in a room. It carries extra attributes: `grace_period_s` and
`active_control_mode`, and while the sensor is missing also `last_valid_reading`
and `last_valid_age_s` (see [when the sensor drops out](#when-the-sensor-drops-out)).

---

## Window and presence detection

Both are optional. Configure them under
**Settings → Devices & Services → Tado X Proxy → Configure**. They work
independently and can both be active at once. They keep working while the
thermostat is [off](#turning-the-thermostat-off).

### Window detection

Point it at a window contact (`binary_sensor.*`).

- **Window opens:** after a delay (default 30 s), the room switches to Frost
  Protection.
- **Action while the window is open** (default **Frost protection**): with Frost
  protection the TRV keeps the room at the frost protection temperature. With
  **Switch the TRV off**, the TRV is switched off completely instead. The room still
  shows Frost Protection, and its action shows Off. If the TRV cannot be switched
  off, frost protection takes over until the window opens again.
- **Window closes:** after a delay (default 120 s), your own choice comes back – or
  Away, if nobody is home. This delay is intentional — it stops the radiator from
  firing hard the instant you shut the window, while the room air is still cold from
  the draught.
- If the window shuts again before the first delay is up, nothing happens at all. If
  it opens again during the close delay, Frost Protection simply goes on, without a
  new open delay.
- **Changes while the window is open:** the room stays in Frost Protection. A preset
  or temperature you pick meanwhile is your new choice and applies once the window
  closes.
- **The card shows the frost protection target meanwhile.** Tapping **+** starts from
  there: with the default 7 °C, one tap makes Manual 7.5 °C your choice – and that is
  what you get once the window has closed. To have a warm room again, pick a preset or set the temperature
  you actually want.
- **Any change during the close delay** ends the wait and takes effect right away –
  you are obviously back in the room. Tapping Frost Protection counts too: the wait is
  over, so it is a real choice and the room stays in Frost Protection. If nobody is
  home, the room goes back to Away instead, and your change applies when someone
  returns.

### Presence detection

Point it at a presence sensor or person tracker (`binary_sensor.*`).

- **Nobody home:** after a delay (default 10 minutes), the room switches to Away.
- **Someone returns:** after a delay (default 30 s), your own choice comes back.
- **Nobody home again during that delay:** the return is off, Away stays.
- **Changes while nobody is home:** the room stays in Away. A preset or temperature
  you pick meanwhile is your new choice and applies once someone returns. This also
  holds while a window is open: once it closes, the room goes back to Away.
- A presence sensor that is `unavailable` or `unknown` does not count as someone
  home. If it drops out while the home delay runs and is still out when the delay
  ends, Away stays until it reports someone home again and the delay has run once
  more. To heat in the meantime, switch [guest mode](#guest-mode) on.

### What counts as a change

Only a real switch between on and off counts. Many Zigbee sensors re-send their
battery level or signal strength every few minutes; those updates do not restart the
delays. A sensor that briefly drops out (`unavailable`) and comes back does not restart a
running delay, and it does not change what comes back afterwards.

### After a Home Assistant restart

Your own choice survives a restart, and a reload after you change the settings,
whatever had taken over the room – including a Manual temperature. A running boost
does not survive; your own choice comes back.

- **Window still open:** frost protection (or the switched-off TRV) simply goes on,
  without a new open delay. Closing the window brings back your choice.
- **Window sensor not back yet:** many Zigbee sensors report a little later than the
  integration starts. Until the sensor reports, frost protection goes on, just as when
  a sensor drops out while the window is open.
- **Window closed in the meantime:** your choice comes back right away, and a TRV the
  window had switched off is switched on again. This also covers a restart during the
  close delay.
- **Window opened while Home Assistant was down:** the normal open delay starts.
- **Away before the restart:** Away goes on without a new away delay while nobody is
  home, or while the presence sensor has not reported yet. Coming home brings back
  your choice. If someone came home while Home Assistant was down, your choice comes
  back right away.
- **Nobody home, but no Away before the restart:** the normal away delay starts.
- **Guest mode** keeps its state.

---

## Guest mode

`switch.<name>_guest_mode` ("Gastmodus" in German), **off by default**.

You are away, but guests are staying at your place. Your presence sensor says
"nobody home", so the room would normally drop to Away. Turn guest mode on and the
heating keeps running as usual. Put the switch on a dashboard to flip it with one tap.

- **Turning it on:** the presence sensor no longer switches the room to Away. If the
  room is already in Away, your own choice comes back immediately.
- **Warming the house before you get home:** a boost picked while nobody is home
  waits until someone returns. Turn guest mode on first, then the boost heats right
  away.
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
proxy goes back to the mode it was in and nothing else changes: an open window keeps
its frost protection and a boost keeps running. This keeps what you see in Home
Assistant honest about what the hardware is actually doing.

While off, the proxy shows a target of 5 °C. Window and presence detection keep
running:

- The preset shows what will hold the room once the heating is back on: Frost
  Protection while a window is open, Away while nobody is home.
- A window that closes while the heating is off lets go after the usual close delay.
- Turning back on with a window still open goes straight to Frost Protection – no new
  open delay, no heating into the open window. With the window action **Switch the
  TRV off**, the TRV simply stays off. It is never switched on while the heating is
  off, even if the window closes meanwhile.
- A boost pauses while the heating is off and continues with the time that was left
  once you turn it back on. A boost picked while off starts with its full time then.

If the radiator thermostat does not respond when you turn it back on, the proxy stays
on and tries again with its next temperature command.

---

## Following the physical thermostat

`switch.<name>_follow_physical_thermostat`, **off by default**.

Turn it on if you want to be able to walk up to the radiator, turn the dial, and have
the proxy accept that as your decision. Turning the dial counts exactly like setting
the temperature in the app: the proxy switches to the **Manual** preset and holds your
temperature instead of overwriting it. It ends a running boost and a running close
delay. While a window is open or nobody is home, your temperature is kept as your
choice and applies once the room is free again.

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

## Needs heat for a boiler

`binary_sensor.<name>_needs_heat`, one for each entry.

Some boilers only run when a room asks for heat – for example an OpenTherm boiler
that an automation switches on and off. This sensor gives you that signal. It is
**on** while the room needs heat.

- It turns **on** once the room is more than 0.3 °C below its target.
- It turns **off** once the room has reached its target.
- In between, it stays as it is. So it does not start the boiler for a tiny gap,
  and it does not stop the boiler before the room is warm.
- It is always **off** while the thermostat is off, and while
  `binary_sensor.<name>_sensor_degraded` is on. A missing room sensor should not
  keep your boiler running.
- It is also **off** while the integration does not regulate the room. Its
  attribute `error_c` is then empty. This happens for up to a minute after Home
  Assistant starts or the integration reloads. It also happens while the radiator
  thermostat does not report, before the room sensor reports for the first time,
  and while an open window has switched the radiator thermostat off.

Its attribute `error_c` shows the gap the sensor follows: the target minus the room
temperature, the same value as on the climate entity. It is empty while the
integration does not regulate, for example while the thermostat is off.

**Switching the boiler.** The boiler should run while at least one room needs heat.
Collect the sensors of all rooms in a group:

1. Go to **Settings → Devices & Services → Helpers**.
2. Click **Create helper** and choose **Group → Binary sensor group**.
3. Add the needs heat sensor of every room. Leave **All entities** off.

The group is on while at least one room needs heat. Let your boiler automation follow
the group.

### Changing when it switches

Under **Settings → Devices & Services → Tado X Proxy → Configure → Needs heat
sensor**:

| Setting | Default | Range | What it does |
|---|---|---|---|
| Switch on above | 0.3 °C | 0.0–2.0 °C | The sensor turns on once the room is more than this much below its target |
| Switch off at | 0.0 °C | -1.0–1.0 °C | The sensor turns off once the room is no more than this much below its target. 0 means: at the target |

The switch-on value must be higher than the switch-off value. Changes apply right
away.

A switch-off value above 0 turns the sensor off a little before the room reaches its
target. This can suit a heavy radiator, for example a cast-iron one, that keeps
heating for a while after the boiler stops. Its stored heat brings the room the rest
of the way. If you set the value too high, the room stays below its target. A value
below 0 keeps the boiler running until the room is that much above its target.

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
| Urgent decrease threshold | 1.0 °C | 0.5–3.0 °C | Skip the waiting period when the temperature needs to drop by more than this much |

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
| `user_preset` | Your own choice – what comes back once nothing else holds the room. `none` means Manual |
| `manual_target_c` | The temperature of Manual. Shown whenever your choice is Manual, also while something else holds the room |
| `window_open_active` | An open window holds the room, including the close delay. Also while the heating is off |
| `window_close_delay_active` | Waiting after a window closed |
| `window_trv_off` | The window action switched the TRV off, and it has not heated since. True while the window holds the room, including the close delay; gone once the TRV is switched on again |
| `presence_away_active` | Nobody home holds the room. Also while the heating is off |
| `guest_mode` | Guest mode is on, presence detection will not switch to Away |
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

To decide whether a boiler should run, use the
[needs heat sensor](#needs-heat-for-a-boiler) instead. It follows `error_c` (target
minus room temperature), not the valve. It starts at a clear gap, keeps running until
the room has reached its target, and leaves out rooms that are off or have a missing
sensor.

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
