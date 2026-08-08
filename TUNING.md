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
| Room settles slightly below target and stays there | Long-term correction too weak | [Ki](#ki-integral-correction) |
| Room takes very long to warm from cold | Cold-start boost too low — or the radiator is simply undersized | [Kp](#kp-proportional-correction) and the [radiator table](#tuning-by-radiator-type) |
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
| `error_c` | Trending towards 0 | Stays > 1°C after 30 min → Kp too low |
| `i_correction_c` | < 0.3 during heat-up | > 1.0 → integral building up (should not happen) |
| `target_for_tado_c` | Decreases as room warms | Stuck at max (30°C) → room too large or Kp too low |

**Evaluate results:**
- **Overshoot < 0.5°C:** All good → proceed to Phase 2.
- **Overshoot 0.5–1.0°C:** Reduce Kp by 0.1–0.2, retest.
- **Overshoot > 1.0°C:** Reduce Kp to 0.5, optionally reduce Ki to 0.001.
- **Room doesn't get warm enough:** Increase Kp by 0.2.

### Phase 2: Hold Test (4–8 Hours)

**Goal:** Verify that temperature is held stable without drift.

**Procedure:**
1. Leave the proxy running at the target temperature (ideally during the day).
2. Export the history via Developer Tools or HA Recorder.

**What to look for:**

| Metric | Good Value | Action if Deviating |
|--------|-----------|---------------------|
| Fluctuation around setpoint | ±0.3–0.5°C | ±0.5°C is normal for TRV control |
| Mean deviation | < 0.2°C | If consistently too cold: slightly increase Ki (0.004–0.005) |
| Cycle duration (heating→idle→heating) | 30–90 min | < 15 min = too much oscillation → reduce Kp |
| `i_correction_c` during operation | −0.5 to +0.5 | If > 1.0 or < −1.0: possible systematic error |

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
- `i_correction_c` stays in the range −0.5 to +0.5.

**If Phase 3 passes:** The room is production-ready. You can set up the next room.

---

## Parameter Reference

### Kp (Proportional Correction)

| Value | Behavior |
|-------|----------|
| 0.0 | Feedforward only, no error correction |
| **0.5** | Gentle, low overshoot, slow heat-up |
| **0.8** | Default – good compromise |
| **1.2** | More aggressive, faster heat-up, higher overshoot risk |
| 2.0+ | Only for large rooms with slow heating |

**Rule of thumb:** Lower Kp = less overshoot, higher Kp = faster heat-up.

### Ki (Integral Correction)

| Value | Behavior |
|-------|----------|
| 0.0 | No long-term correction (P + feedforward only) |
| **0.001** | Very slow, correction over hours |
| **0.003** | Default – correction over ~30 min |
| **0.005** | Faster, higher overshoot risk |
| 0.01+ | Aggressive – only for systematic offset |

**Rule of thumb:** Higher Ki = target temperature is reached more accurately, but overshoot risk increases.

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
| `min_change_threshold_c` | 0.3°C | 0.1–1.0°C | Only send when difference exceeds this value |
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
| `integral_min_c / max_c` | ±2.0°C | Absolute limit for integral accumulation |
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
target_for_tado_c: 19.8         ← command sent to Tado
is_saturated: false              ← not at limit
regulation_reason: sent(normal_update)
```

### Problematic State

```
feedforward_offset_c: 8.5       ← unusually high – radiator extremely hot
i_correction_c: 1.8             ← integral very high = possible overshoot
error_c: -0.8                   ← room 0.8°C above target
target_for_tado_c: 30.0         ← clamped at maximum
is_saturated: true
regulation_reason: rate_limited(95s)
```

**In this case:** Reduce Kp, check if the external sensor is working correctly.

---

## Troubleshooting

### Room Doesn't Heat Up at All

1. Check `feedforward_offset_c`: Should be positive (typically 1–5°C).
2. Check `target_for_tado_c`: Should be well above the current Tado temperature.
3. Is the Tado X thermostat set to "Manual"? Automatic schedules from the Tado app can interfere.
4. Is the external sensor reachable? Check the `sensor_degraded` attribute – if `true`, the sensor has failed and the control loop is using the last valid reading.

### Strong Overshoot (> 1°C)

1. Reduce Kp (e.g., from 0.8 to 0.5).
2. If gain scheduling is enabled, reduce the near-target strength (e.g., 0.7).
3. Check `i_correction_c`: If > 1.0 during heat-up → possible issue, please report as an issue.
4. Reduce `integral_deadband_c` (Options → PI Controller) to tighten the precision zone.

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

If the sensor is down longer than the grace period, the control loop pauses automatically.

### Integration Not Responding

1. Check Home Assistant logs (Settings > System > Logs > "tadox_proxy").
2. Check coordinator refresh: Data is updated every 60s.
3. Test a service call: Developer Tools > Services > `climate.set_temperature` on the proxy entity.

---

## Still not right?

Tuning cannot fix everything, and it is worth knowing when to stop. If a room
resists every adjustment, the cause is usually physical rather than numerical:

- **The radiator is too small for the room.** No setting adds heating capacity.
  `target_for_tado_c` pinned at 30 °C with `is_saturated: true` is the giveaway.
- **The sensor is in the wrong place.** Worth re-checking even if you are sure —
  see [sensor placement](docs/setup.md#step-1-place-the-sensor-properly).
- **A schedule is still active in the Tado app.** It will quietly fight every
  command this integration sends.
- **The radiator needs bleeding, or the valve is stuck.**

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
