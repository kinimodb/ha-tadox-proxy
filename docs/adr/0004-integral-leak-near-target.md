# The integral leaks toward zero near the target

Status: accepted for v1.7.0 (first shipped as a pre-release). Adds a fourth anti-windup measure to [ADR 0002](0002-integral-windup-low-gain-and-tight-floor.md) and [ADR 0003](0003-ki-default-back-to-0003.md); `Ki` 0.003, the floor of -0.5 °C, the ceiling of +2.0 °C, the band of 0.3 °C and the decay of 0.95 all stand.

Inside the integral band the integral now also drains toward zero: each cycle it loses `integral × dt / leak time`, with a leak time of 15 minutes (`integral_leak_time_s` = 900; the options dialog shows it in minutes as "Integral leak time", 0 turns it off). A lasting error `e` inside the band then holds the integral near `Ki × leak time × e`, 2.7 × `e` at the defaults, instead of summing it up to the ceiling.

## Context

- Room sensors report in steps. Most of the maintainer's sensors show 0.2 °C steps: three only odd tenths (16.9, 17.1, …), two only even tenths. A target between two steps, such as 17.0 °C on an odd-tenth sensor or the bedroom's eco 17.5 °C on an even-tenth one, can never be read: the room reads 0.1 °C too cold or 0.1 °C too warm.
- On 2026-11-23 the bedroom (eco 17.5 °C) read 17.4 °C from 19:17 to 21:29. The error of +0.1 stayed inside the band, so the integral grew by about 0.018 °C a minute, reaching +1.84 at 21:28. The push grew with it to about +2 °C, the room left 17.4 °C at 21:29 and reached 18.2 °C at 22:08, still rising, with the valve commanded shut since 21:57.
- The recorded data of the six rooms (2026-10-25 to 2026-11-14) shows the same pattern often: a stay of one to five hours inside the band with a mean error near zero, the integral reaching +1.0 to +1.5, then the room 0.5 to 0.9 °C above its target. Counted episodes (integral ≥ 1.0 while |error| < 0.3, no boost, `Ki` 0.003): guest room 25, WC 17, living room 4, kitchen 2, bathroom 0, bedroom 0 (its first one is the evening above). The guest room repeats it about every five hours on some days.
- This is the case the final reply on #124 named as a reason to look again ("a room arrives with a high integral and overshoots"), now in the maintainer's own house. No option reaches it: a lower `Ki` only slows the climb, and the ceiling cannot be set.

## Considered Options

Closed-loop model of four rooms (kitchen, living room, WC, guest room) under the two valve laws (L1: the valve keeps opening while the setpoint is above the reading; L2: it opens in proportion to the gap), from the 2026-11-14 analysis, unchanged; the room sensor on a 0.2 °C grid. Three measures: the 12-hour hold after a heat-up (highest point above target, mean of six runs), the heat-up itself, and rooms that need a lasting push of 0.2 to 1.4 °C to hold their target (from a heat-up, the last 8 of 16 hours).

| option | highest point above target in the hold, L1 / L2 (kitchen, living, WC, guest) | heat-up | rooms that need a lasting push |
|---|---|---|---|
| today (no leak) | 0.66/0.46, 0.47/0.31, 0.45/0.34, 0.74/0.49 | reference | reference |
| ceiling +1.0 / +0.5 (P1 / P2) | P2: 0.57/0.23, 0.44/0.27, 0.44/0.26, 0.61/0.28 | unchanged | P2 up to 0.5 °C colder above its ceiling (2026-11-14) |
| dead zone: no integration while abs(error) < 0.15 | 0.46/0.12, 0.41/0.14, unchanged in the WC, 0.39/0.12 | living room under L2 11 min later | under L1 0.1 to 0.3 °C warmer |
| leak 60 min / 30 min | 30 min: 0.53/0.21, 0.40/0.24, 0.42/0.26, 0.57/0.25 | unchanged | small |
| **leak 15 min (this decision)** | **0.42/0.13, 0.37/0.21, 0.39/0.22, 0.41/0.13** | **unchanged, living room under L2 2 min later** | **see Consequences** |

- The dead zone is the most direct rule ("a tenth is the sensor's step, not a reason to push") and cuts the most commands, but it does nothing in a room whose sensor steps meet the target (the WC), switches the integral off entirely in rooms whose steps never meet it, and under L1 lets rooms that need a lasting push run warm.
- The ceilings act only once the integral is already high and cost a room that needs more push than the ceiling.
- The longer leaks help less in the room with the most episodes (guest room under L1: 0.57 and 0.68 against 0.41).

## Consequences

- The overshoot after a long stay just below the target falls by about a third to three quarters in the model, under both valve laws and in all four rooms. The integral stops at about 0.27 °C on a lasting error of 0.1 °C instead of +2.0 (it reached +1.84 on 2026-11-23).
- The integral no longer removes a lasting error completely: a room that needs a lasting push settles where 1.8 × error + 2.7 × error gives that push. In the model this costs up to 0.15 °C at a needed push of 0.6 °C (living room and WC under L2) and up to 0.25 °C at 1.4 °C (WC under L2); the kitchen under L2 gets warmer, by up to 0.26 °C at 0.4 °C. Under L1 rooms run within ±0.17 °C of today. A room that turns out too cool can set a longer leak time, or 0 for the old behaviour (TUNING.md).
- After a slow final approach (the last 0.3 °C in 45 minutes) the integral at the crossing is about 0.2 °C instead of 1.2 °C, so the valve shuts sooner after the target (#124's warm side).
- Fewer commands while holding: 12 to 35 % fewer in the model's 12-hour holds.
- The leak applies inside the band only; outside it the decay of 0.95 per cycle acts as before. It uses the integral carried into the cycle and the same capped time step as the integration (at most 120 s per cycle).
- New option, stored only once the options dialog is saved: every room runs the 15 minutes after the update, including rooms whose dialog was saved before; nothing stored is changed.
- The model is fit for ranking only. The field check is the maintainer's own house on the pre-release: the guest room and WC episodes above, and the bedroom at eco 17.5 °C.
