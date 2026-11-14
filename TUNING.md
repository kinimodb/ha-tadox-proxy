# Tuning Guide

For a room that runs, but not quite right — it overshoots, it swings, or it settles
a little cold.

No control engineering knowledge is assumed. Every symptom below tells you which
single value to change and in which direction.

---

## Do you need this page?

Most people do not. Check these first:

| Situation | What to do |
|---|---|
| The room reaches the target and holds it within about half a degree | **Nothing.** That is the expected result. Close this page |
| The room never warms up at all, or nothing seems to happen | Not a tuning problem — see [setup troubleshooting](docs/setup.md#when-something-is-wrong) |
| You have not checked where your sensor sits | [Do that first](docs/setup.md#step-1-place-the-sensor-properly). A badly placed sensor cannot be tuned away |
| You just installed it and are tuning pre-emptively | Wait. Run it as-is for a day. The defaults are good |

Change **one value at a time**, then give the room several hours before judging the
result. Heating is slow: two changes in an afternoon tell you nothing about which one
did what.

---

## Start here: what is the symptom?

| What you observe | Usual cause | Go to |
|---|---|---|
| Room sails past the target, then drifts back down | Correction too strong | [Strong overshoot](#strong-overshoot--1c) |
| Temperature cycles up and down repeatedly | Correction too strong, or commands too frequent | [Oscillation](#temperature-oscillates-strongly) |
| Room creeps the last tenth slowly, or stays a little below target for hours | Long-term correction too weak; some thermostats need a lasting positive correction | Less than about 0.3°C below target: if you lowered [Ki](#ki-integral-correction), set it back to 0.003 (the default), otherwise raise it a little, to 0.004 or 0.005 (a bit more overshoot). From about 0.3°C below target Ki does not help: the correction only builds up inside the band `integral_deadband_c` (0.3°C by default) and decays outside it. Widening that band might help; this has not been tested |
| The boiler keeps firing, or the valve keeps heating, after the room has reached its target (a boiler that only runs on demand) | After a slow final approach the long-term correction keeps the valve open for a while, and a boiler that follows the valve or `hvac_action` keeps running with it | [Heating goes on after the target](#heating-goes-on-after-the-room-reached-its-target) |
| A room at its target still takes some heat while another room keeps a shared boiler running | Mostly the shared boiler, not the settings. Tuning changes little here | [Still not right?](#still-not-right) |
| Room takes very long to warm up | Often the radiator or the hot water supply, and then no setting helps. Sometimes the radiator stays cool because the room is already close to its target | [Room takes long to warm up](#room-takes-long-to-warm-up) |
| Room feels fine but the reading is always off | Sensor placement, not tuning | [Sensor placement](docs/setup.md#step-1-place-the-sensor-properly) |

If you want to understand *why* a value has the effect it does, see
[how it works](docs/how-it-works.md).

---

## Table of Contents

1. [Testing Strategy (3 Phases)](#testing-strategy-3-phases)
2. [Parameter Reference](#parameter-reference)
3. [Diagnostics: What the Attributes Tell You](#diagnostics-what-the-attributes-tell-you)
4. [Troubleshooting](#troubleshooting)

> Setting a room up for the first time is covered in the
> [setup guide](docs/setup.md), not here. This page assumes it is already running.

---

## Testing Strategy (3 Phases)

### Phase 1: Heat-Up Test (1–2 Hours)

**Goal:** Verify that the basic control works and no strong overshoot occurs.

**Procedure:**
1. Set the proxy to a target temperature ~2–4°C above the current room temperature.
2. Observe the proxy entity in Developer Tools (States).
3. Wait until the room temperature reaches the setpoint.

**What to look for:**

| Attribute | Good Value | Problem |
|-----------|-----------|---------|
| `feedforward_offset_c` | 1.0–5.0°C | < 0 indicates wrong sensor |
| `error_c` | Trending towards 0 | Stays > 1°C after 30 min → check `feedforward_offset_c` before you change anything: [Room takes long to warm up](#room-takes-long-to-warm-up) |
| `i_correction_c` | < 0.3 during heat-up | > 1.0 when the room arrives → built up during a slow final approach, expect a small overshoot |
| `target_for_tado_c` | Decreases as room warms | Stuck at max (30°C) → the radiator is asked for all it can give; no setting adds heat ([Still not right?](#still-not-right)) |

**Evaluate results:**
- **Overshoot < 0.5°C:** All good → proceed to Phase 2.
- **Overshoot 0.5–1.0°C:** Reduce Kp by 0.1–0.2, retest.
- **Overshoot > 1.0°C:** Reduce Kp to 0.5. If you raised Ki, set it back to 0.003 (the default); otherwise lowering it to 0.001 can help.
- **Room warms too slowly or doesn't get warm enough:** see
  [Room takes long to warm up](#room-takes-long-to-warm-up) before you touch Kp.

### Phase 2: Hold Test (4–8 Hours)

**Goal:** Verify that temperature is held stable without drift.

**Procedure:**
1. Leave the proxy running at the target temperature (ideally during the day).
2. Export the history via Developer Tools or HA Recorder.

**What to look for:**

| Metric | Good Value | Action if Deviating |
|--------|-----------|---------------------|
| Fluctuation around setpoint | ±0.3–0.5°C | ±0.5°C is normal for TRV control |
| Mean deviation | < 0.2°C | If consistently too cold: slightly increase Ki (0.004–0.005), or set it back to 0.003 if you lowered it |
| Cycle duration (heating→idle→heating) | 30–90 min | < 15 min = too much oscillation → reduce Kp |
| `i_correction_c` during operation | −0.5 to +0.5 | Above +1.0 for hours: possible systematic error. At −0.5 for hours: the valve is already as shut as it gets, so something else warms the room (sun, a neighbouring room) |

**Evaluate results:**
- **Stable ±0.5°C:** Perfect → proceed to Phase 3.
- **Slow drift in one direction:** Increase Ki by 0.001.
- **Fast oscillation:** Reduce Kp by 0.1–0.2.

### Phase 3: Overnight / Long-Term Test (12–24 Hours)

**Goal:** Confirm stability over extended periods, including night setback.

**Procedure:**
1. Leave the proxy running overnight.
2. Optional: Test a setpoint change (e.g., from 21°C to 18°C in the evening, back in the morning).

**What to look for:**
- No drift overnight (temperature stays within ±0.5°C band).
- After setpoint change: new target is reached within 30–60 min.
- `i_correction_c` stays in the range −0.5 to +0.5 (−0.5 is the lowest value it can take).

**If Phase 3 passes:** The room is production-ready. You can set up the next room.

---

## Parameter Reference

### Kp (Proportional Correction)

| Value | Behavior |
|-------|----------|
| 0.0 | Feedforward only, no error correction |
| **0.5** | Gentle, low overshoot, slow heat-up |
| **0.8** | Default – good compromise |
| **1.2** | Reacts harder to the gap, higher overshoot risk |
| 2.0+ | Very strong reaction, high overshoot risk |

**Rule of thumb:** Lower Kp = less overshoot. Higher Kp = a stronger reaction to the
gap. A higher Kp does not make a room warm up faster once its radiator is hot, so it
is rarely the answer to a slow room: see
[Room takes long to warm up](#room-takes-long-to-warm-up).

### Ki (Integral Correction)

| Value | Behavior |
|-------|----------|
| 0.0 | No long-term correction (P + feedforward only) |
| **0.001** | A third of the default. Less push past the target after a slow approach, but the last tenth takes longer |
| **0.002** | Two thirds of the default |
| **0.003** | Default. While the room stays 0.2°C off, the correction grows by about 0.36°C every 10 minutes |
| **0.005** | Faster, higher overshoot risk |
| 0.01+ | Aggressive – only for systematic offset |

The correction changes by Ki × gap every second, so a smaller gap means a slower
correction: at half the gap, half the speed.

**Rule of thumb:** Higher Ki = a lasting shortfall is closed sooner, but overshoot risk increases.

**Room creeps the last tenth slowly, or stays a little below target for hours?** If you
lowered Ki, set it back to 0.003, the default. Otherwise raise it a little, to 0.004 or
0.005. This happens most with a thermostat that opens its valve in proportion to the
gap: it needs a lasting positive correction to close the last tenth. The price of a
higher Ki is a bit more overshoot after a slow approach.

This works while the room is less than about 0.3°C below target. Further below, the
correction does not build up at all, whatever Ki is: it decays, and the band
`integral_deadband_c` is what matters. A wider band might help there; this has not
been tested.

**Heating goes on after the room has reached its target?** That is the other side of
the same correction. Let the boiler follow the needs heat sensor; a lower Ki for that
room is optional (see
[Heating goes on after the target](#heating-goes-on-after-the-room-reached-its-target)).

### Adaptive Gain Scheduling

When enabled (default: on), the proportional gain Kp is automatically scaled based on
the current error magnitude. This gives you the best of both worlds: fast heat-up on
cold start and gentle control near the target temperature.

| Error Zone | Condition | Kp Multiplier | Effect |
|-----------|-----------|---------------|--------|
| **Startup** | \|error\| > 2.0°C | × 1.5 (configurable) | Aggressive heating on cold start |
| **Transition** | 0.5°C ≤ \|error\| ≤ 2.0°C | × 1.0–1.5 (linear) | Smooth interpolation between fine and startup |
| **Fine** | \|error\| < 0.5°C | × 1.0 (configurable) | No attenuation by default |

Both multipliers are now configurable in the options flow under "Gain Scheduling".
The zones go by the size of the error (\|error\|), so they apply in the same way
while the room is above its target. That is deliberate.

**When to disable:** If you have already tuned Kp carefully for your room and are
satisfied with both heat-up speed and steady-state stability, you can disable adaptive
scheduling in the options flow under "Gain Scheduling". The base Kp value will then be
used unchanged.

### Configurable Parameters (Options → PI Controller)

These parameters can be adjusted in the options flow under "PI Controller":

| Parameter | Default | Range | Meaning |
|-----------|---------|-------|---------|
| `correction_kp` | 0.8 | 0.0–5.0 | Proportional gain of the PI controller |
| `correction_ki` | 0.003 | 0.0–0.1 | Integral gain of the PI controller |
| `integral_deadband_c` | 0.3°C | 0.1–1.0°C | Integral only accumulates when error is within this zone |

### Configurable Parameters (Options → Gain Scheduling)

These parameters can be adjusted in the options flow under "Gain Scheduling":

| Parameter | Default | Range | Meaning |
|-----------|---------|-------|---------|
| `gain_fine_multiplier` | 1.0 | 0.3–1.5 | Kp multiplier near target (gain scheduling) |
| `gain_startup_multiplier` | 1.5 | 1.0–3.0 | Kp multiplier on cold start (gain scheduling) |
| `gain_startup_threshold_c` | 2.0°C | 0.5–5.0°C | Error above this activates cold-start multiplier |
| `gain_fine_threshold_c` | 0.5°C | 0.1–2.0°C | Error below this activates near-target multiplier |

### Configurable Parameters (Options → TRV Communication)

These parameters can be adjusted in the options flow under "TRV Communication":

| Parameter | Default | Range | Meaning |
|-----------|---------|-------|---------|
| `min_command_interval_s` | 180s | 60–600s | Minimum interval between commands (battery conservation) |
| `min_change_threshold_c` | 0.3°C | 0.1–1.0°C | Only send when the difference is at least this value |
| `overlay_refresh_s` | 0s | 0–3600s | Periodically resend setpoint to keep cloud overlays alive (0 = off) |

### Configurable Parameters (Options → Behaviour)

These parameters can be adjusted in the options flow under "Behaviour":

| Parameter | Default | Range | Meaning |
|-----------|---------|-------|---------|
| `sensor_grace_s` | 300s | 0–1800s | How long to use last valid reading when sensor is unavailable |
| `follow_threshold_c` | 0.5°C | 0.1–2.0°C | Min divergence to detect physical user input on Tado |
| `follow_grace_s` | 20s | 5–120s | Ignore Tado changes for this long after sending a command |
| `urgent_decrease_threshold_c` | 1.0°C | 0.5–3.0°C | Bypass rate limiting for large decreases |

### Internal Parameters (Not Adjustable)

These values are defined in `parameters.py` and optimized for Tado X:

| Parameter | Value | Meaning |
|-----------|-------|---------|
| `integral_decay` | 0.95 | Integral loses 5% per cycle when error > deadband |
| `integral_min_c / max_c` | −0.5 / +2.0°C | Absolute limits for the integral. The lower one is tight on purpose: a shut valve cannot be shut further, so a lower value would only delay the next heating |
| `max_integration_dt_s` | 120s | Max time step per integration – prevents an integral jump after a sensor outage |
| `min_target_c / max_target_c` | 5 / 30°C | Safety limits for Tado commands |

### Tuning by Radiator Type

The Tado X TRV works with hot-water radiators that have a thermostatic valve (TRV) connection.
Different radiator types have different thermal characteristics. Use these recommendations as
a starting point and fine-tune from there.

| Radiator Type | Near-target | Cold-start | Interval | Notes |
|--------------|-------------|------------|----------|-------|
| **Type 22 panel** (standard) | 1.0 | 1.5 | 180s | Defaults work well |
| **Type 11 / small panel** | 0.7–0.8 | 1.5–2.0 | 180s | Fast thermal response, lower near-target to avoid oscillation |
| **Column radiator** (Gliederheizkörper) | 1.0 | 1.5 | 180–300s | Similar to Type 22 |
| **Cast iron** (old building) | 0.8–1.0 | 1.3–1.5 | 180–300s | High thermal mass, self-buffering |
| **Towel radiator** | 0.7 | 1.5 | 120–180s | Small and fast |
| **Poorly insulated room** | 1.0 | 2.0–2.5 | 120–180s | High heat loss needs more aggression |

**Not compatible:** underfloor heating, electric radiators, steam heating.

---

## Diagnostics: What the Attributes Tell You

In Home Assistant, go to: **Developer Tools > States** > search for your proxy entity.

### Healthy State (Example)

```
feedforward_offset_c: 1.7       ← Tado reads 1.7°C more than room (normal)
p_correction_c: 0.08            ← small error, small correction
i_correction_c: 0.06            ← integral near 0 = stable
error_c: 0.1                    ← room 0.1°C below target = good
target_for_tado_c: 19.8         ← setpoint computed for the Tado (sent in this cycle, see regulation_reason)
is_saturated: false              ← not at limit
regulation_reason: sent(normal_update)
```

### Problematic State

```
feedforward_offset_c: 8.5       ← unusually high – radiator extremely hot
i_correction_c: 1.8             ← integral near its upper limit (2.0) = possible overshoot
error_c: -0.8                   ← room 0.8°C above target
target_for_tado_c: 30.0         ← clamped at maximum
is_saturated: true
regulation_reason: rate_limited(95s)
```

**In this case:** Reduce Kp, check if the external sensor is working correctly.

A `regulation_reason` of `send_failed(...)` means the command to the TRV failed
(e.g. TRV unreachable). The proxy retries after the rate-limit interval.

A `regulation_reason` of `sent(trv_drift)` means the TRV was holding a different
setpoint than the proxy had sent – someone turned the dial, or a Tado app schedule
wrote its own block – and the proxy put its own value back. Expected right after a
dial change; if it repeats every rate-limit interval, the TRV is not accepting the
commands (check the Tado integration and whether the zone is in manual mode).

A `regulation_reason` of `sent(urgent_increase)` means a person started a boost and
its command went out without waiting for the rate-limit interval. It only shows when
another command went out less than the rate-limit interval before. Otherwise the boost
shows `sent(normal_update)` like any change. In that same situation a boost started
by a schedule or an automation shows `rate_limited(...)` first, and so does a second
boost within the interval.

---

## Troubleshooting

### Room Doesn't Heat Up at All

1. Check `feedforward_offset_c`: Should be positive (typically 1–5°C).
2. Check `target_for_tado_c`: Should be well above the current Tado temperature.
3. Is the Tado X thermostat set to "Manual"? Automatic schedules from the Tado app can interfere.
4. Is the external sensor reachable? Check the `sensor_degraded` attribute – if `true`, the sensor has failed and the control loop is using the last valid reading.

### Room Takes Long to Warm Up

Watch `feedforward_offset_c` while the room heats up. It shows how far the radiator
thermostat, which sits on the radiator, reads above the room. So it tells you whether
the radiator is hot. There are two cases, and they need opposite answers.

1. **The radiator is already hot and the room still warms slowly.** The offset is
   several degrees. No setting helps here. The valve is open and the radiator gives
   what it can: the limit is the size of the radiator or the hot water the boiler
   supplies. A higher Kp only asks for more of what is not there. See
   [Still not right?](#still-not-right).
2. **The radiator is still cool while the room is within about 1°C of its target.**
   The offset stays small. Close to the target the proxy pushes only a little, on
   purpose, and a radiator thermostat answers a small push slowly. The setting to
   try, for that room only, is the **Cold-start zone threshold** (Options → Gain
   Scheduling): lower it step by step. The stronger cold-start reaction then applies
   in full from a smaller gap. Raising the **Cold-start boost** alone changes little
   here: below the threshold only a part of it applies, and less the closer the room
   is to its target. The near-target zone threshold must stay below the cold-start
   zone threshold. The price is more commands to the thermostat, which costs
   battery. Give the room a few days and watch for overshoot before you go further.

### Strong Overshoot (> 1°C)

1. Reduce Kp (e.g., from 0.8 to 0.5).
2. If gain scheduling is enabled, reduce the near-target strength (e.g., 0.7).
3. Check `i_correction_c` when the room arrives: above 1.0 means the integral built up while the room crept up the last few tenths of a degree, and it unwinds only slowly ([why](docs/how-it-works.md#integral-windup)). Steps 4 and 5 keep it small.
4. If you raised Ki above its default of 0.003, set it back. At the default, lower it for this room, for example to 0.001; the last tenth then takes longer.
5. Reduce `integral_deadband_c` (Options → PI Controller) to tighten the precision zone.

### Heating Goes On After the Room Reached Its Target

This matters most for a boiler that only runs on demand. After a slow final approach
the long-term correction is still pushing when the room arrives, so the valve stays
open for a while and `hvac_action` keeps showing `heating`
([why](docs/how-it-works.md#integral-windup)).

1. Let the boiler follow the [needs heat sensor](docs/settings.md#needs-heat-for-a-boiler),
   not the valve or `hvac_action`. It turns off once the room has reached its target,
   whatever the valve does. This is the fix for the boiler.
2. Optional, for that room only: lower Ki (Options → PI Controller), for example to
   0.001. About a third of the long-term correction is then left when the room
   arrives, so it pushes less past the target. The valve does not necessarily shut
   sooner, and the last tenth of a degree takes longer to close.

### Temperature Oscillates Strongly

1. Reduce Kp (e.g., to 0.5).
2. Increase `min_command_interval_s` (Options → TRV Communication) if oscillation is fast.
3. Increase `min_change_threshold_c` (Options → TRV Communication) to filter small fluctuations.
4. Check if the Tado app has its own schedules active (conflicts).

### External Sensor Goes Down Briefly

The integration automatically bridges short sensor outages (**Last-Valid-Bridging**,
default: 5 minutes). During this time:

- The control loop continues with the last valid reading.
- The attribute `sensor_degraded` shows `true`.
- `room_temp_last_valid_age_s` shows the age of the last reading in seconds.

If the sensor is down longer than the grace period, the configured sensor-loss action
takes over (TRV regulates on its own, backup room sensor, or hold the last command) –
see [When the sensor drops out](docs/settings.md#when-the-sensor-drops-out). The
attribute `active_control_mode` shows which one is active.

### Integration Not Responding

1. Check Home Assistant logs (Settings > System > Logs > "tadox_proxy").
2. Check coordinator refresh: Data is updated every 60s.
3. Test a service call: Developer Tools > Services > `climate.set_temperature` on the proxy entity.

---

## Still not right?

Tuning cannot fix everything, and it is worth knowing when to stop. If a room
resists every adjustment, the cause is usually physical rather than numerical:

- **The radiator is too small for the room, or the boiler supplies too little hot
  water.** No setting adds heating capacity. `target_for_tado_c` pinned at 30 °C with
  `is_saturated: true` is the giveaway, and so is a `feedforward_offset_c` of several
  degrees (the radiator is hot) while the room hardly rises.
- **The sensor is in the wrong place.** Worth re-checking even if you are sure —
  see [sensor placement](docs/setup.md#step-1-place-the-sensor-properly).
- **A schedule is still active in the Tado app.** It will quietly fight every
  command this integration sends.
- **The radiator needs bleeding, or the valve is stuck.**
- **Another room keeps a shared boiler running.** A room that is already at its
  target still takes some heat while the boiler runs for another room, because its
  valve stays open a crack to hold the room. This mostly follows from sharing one
  boiler, so tuning will change little here.

If none of those apply, please open an
[issue](https://github.com/kinimodb/ha-tadox-proxy/issues) with your radiator type,
sensor position, the values you tried, and what happened. Reports like that are what
improve the defaults for everyone.

---

## Where to go next

| If you want to… | Read this |
|---|---|
| Look up what a setting does | [Settings reference](docs/settings.md) |
| Understand *why* a value has its effect | [How it works](docs/how-it-works.md) |
| Set up another room | [Setup guide](docs/setup.md) |
