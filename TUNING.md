> **[Deutsche Version](TUNING_DE.md)**  |  English (this page)

# Tuning Guide for Tado X Proxy

This guide describes how to set up, test, and fine-tune the control loop for a
new room. It is aimed at users without a control engineering background.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Initial Room Setup](#initial-room-setup)
3. [Testing Strategy (3 Phases)](#testing-strategy-3-phases)
4. [Parameter Reference](#parameter-reference)
5. [Diagnostics: What the Attributes Tell You](#diagnostics-what-the-attributes-tell-you)
6. [Troubleshooting](#troubleshooting)

---

## Prerequisites

- A **Tado X Thermostat** (TRV) visible in Home Assistant as a `climate.*` entity.
- An **external room sensor** (e.g., Aqara, Sonoff) that measures the *actual* room temperature.
  - Must be available as a `sensor.*` entity with `device_class: temperature`.
  - Should **not** be placed near windows, doors, or radiators.
- The proxy is installed via HACS and configured for this room.

---

## Initial Room Setup

1. **Add integration:** Settings > Devices & Services > Add Integration > *Tado X Proxy*.
2. **Source Entity:** Select the Tado X thermostat (e.g., `climate.bedroom`).
3. **External Sensor:** Select the external room sensor.
4. **Name:** Choose a unique name (e.g., "Bedroom Proxy").
5. **Keep defaults:** The default values (Kp=0.8, Ki=0.003) are a good starting point.
6. **Tado X mode:** Set the Tado X thermostat to "Manual". The Tado app must not have
   its own schedule active, as the proxy takes over control.

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
| `target_for_tado_c` | Decreases as room warms | Stays at 25°C → room too large or Kp too low |

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

### Additional Internal Parameters (Not Adjustable via UI)

These values are defined in `parameters.py` and optimized for Tado X:

| Parameter | Value | Meaning |
|-----------|-------|---------|
| `integral_deadband_c` | 0.3°C | Integral only accumulates when error < 0.3°C |
| `integral_decay` | 0.95 | Integral loses 5% per cycle when error > deadband |
| `integral_min_c / max_c` | ±2.0°C | Absolute limit for integral accumulation |
| `min_command_interval_s` | 180s | Minimum interval between commands (battery conservation) |
| `min_change_threshold_c` | 0.3°C | Only send when difference > 0.3°C |
| `min_target_c / max_target_c` | 5 / 25°C | Safety limits for Tado commands |

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
target_for_tado_c: 25.0         ← clamped at maximum
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
2. Check `i_correction_c`: If > 1.0 during heat-up → possible issue, please report as an issue.
3. Consider room thermal mass: underfloor heating has more inertia than radiators.

### Temperature Oscillates Strongly

1. Reduce Kp (e.g., to 0.5).
2. `min_command_interval_s` is set to 180s – more frequent commands would drain the battery.
3. Check if the Tado app has its own schedules active (conflicts).

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
