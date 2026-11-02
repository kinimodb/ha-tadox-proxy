# Integral windup is limited by a low gain and a tight floor, not by a smarter rule

The integral correction runs at `Ki = 0.001` (was 0.003) and may not go below -0.5 °C (was -2.0 °C). Its ceiling stays at +2.0 °C and `regulation.py` does not change. Entries saved at the previous Ki default of 0.003 follow the new one through a config-entry migration (minor version 1.2); any other stored Ki stays.

## Context

The radiator thermostat is taken to act on the command minus its own reading, `u = (1 + Kp) × error + integral`, which is `1.8 × error + integral` with the default `Kp`, and to keep its valve open while `u > 0`. That is what the readings show; the Tado X does not report its valve (see the last point under Consequences). Issue #124 showed the integral working against the room in both directions:

- **Too cold:** during a slow final approach the room spends its last tenths of a degree inside the 0.3 °C band and the integral builds up by `Ki × ∫ error dt`. At `Ki = 0.003` that is about `0.49 / r` for an approach speed of `r` K/h, and it is still there when the room arrives. The valve stays open above the target. A third of `Ki` gives a third of the excess; the integral time `(1 + Kp) / Ki` goes from 10 to 30 minutes, while rooms respond in hours.
- **Too warm:** a room that stays warm winds the integral down to -2. A shut valve cannot be shut further, so everything below about -0.5 only delays the next heating: the valve opens again once `1.8 × error > -integral`. With -2 that was about 1.1 K below the target or hours later. With -0.5 it is about 0.3 K below: 0.5 / 1.8 is 0.28 K, and the command is rounded to 0.1 K and only sent when it changes by at least 0.3 K.

The ceiling stays at +2: caps of +0.5 and +1 left thermostats with a proportional band too cold.

## Evidence

All numbers come from a throwaway simulation of the closed loop (it is not in the repository) and from replays of recorded inputs through the same code. None was measured on a valve. The simulation runs the real regulator and `decide_send` against three stand-ins for the valve (proportional band, PI, on/off) and three sensors (0.1 K, 0.2 K, a 300 s poll) in 18 scenarios, 162 runs in all. A second simulator, written independently of the first, was used to check the rejected option and the bias of the adopted one; it came from an adversarial check of the recommendation that a usage limit cut off before it reached a verdict, so only its interim results were used, while the two-axis review (standards and spec) of the change ran to completion. Unless a line says otherwise, "valve open" means the model from Context: the command is above the thermostat's reading.

Old regulator → new regulator (`Ki` 0.003 and floor -2.0 → `Ki` 0.001 and floor -0.5):

- Simulation: lowest integral -2.00 → -0.50; integral at the first crossing of the target 1.03 → 0.49; valve open while 0.1 K or more above the target 45 → 30 min a day; overshoot 0.46 → 0.38 K; TRV commands a day 83 → 60; boiler starts a day unchanged at 2.5.
- Replay of the four recorded evenings of #124: integral at the crossing +1.25 / +0.60 / +0.91 / +0.45 → +0.36 / +0.05 / +0.30 / +0.15; valve open after the crossing 28.5 / 36 / 20 / 11 → 9 / 3 / 6 / 1 min.
- Replay of six rooms over 5.5 days, totals over the rooms: minutes with the integral at ±2 (the old limits) 290 → 0, its range -2.00 … +1.51 → -0.50 … +0.54; valve open while 0.1 K or more warm 251 → 46 min; valve shut while 0.1 K or more cold 535 → 197 min.

## Considered Options

- **Valve-aware conditional integration** (no negative integration while `u` is below a limit): removes the negative windup completely (worst reopen delay 48 min) but rooms run warm. Thermostats that cycle around `u = 0` make the integral ratchet upwards. Freezing the integral once the error changes sign (the room has crossed its target) had the same effect and was refuted earlier. Rejected option against adopted option:
  - paired shift per room -0.029 K, worst -0.269 K, against +0.009 K, from -0.051 K to +0.096 K (negative: the room ends warmer than with the old regulator);
  - mean error with continuous supply -0.107 K against -0.030 K (negative: above the target);
  - valve open while 0.1 K or more above the target 107 against 57 min a day. This comes from the second simulator, which counts the valve as open when the thermostat's stored setpoint is above its reading. It is another comparison with another definition than the 45 → 30 min above (old against new regulator, command above reading), so the two do not contradict each other.
- **Add-ons on top of the adopted change** (integral warm-up timer, floor that depends on `u`, `Ki` scheduled by `|u|`, symmetric gating on heat arrival, a ceiling on `u`, the band edge inclusive / 0.25 / 0.4): 16 variants screened over the same 162 runs, none clearly better. Best were a floor on `u` of -0.5 (mean reopen delay 27 → 21 min, undershoot 0.48 → 0.46 K) and a 60 min warm-up (valve open above the target 25 → 17 min a day, but 16 of the 162 runs shifted the bias by more than 0.05 K). Each arrow compares the adopted change alone with the adopted change plus that add-on; the screening has its own baseline (for example 25 min a day, not the 30 above). Not worth the extra state.
- **Rate conditioning** (blind with a 0.2 K sensor that updates every 6 to 19 min), **heat-arrival gating alone** (cold bias), **leak** and **back-calculation** (weak or biased).
- **`Ki` alone** (the integral still reaches -1.87 in the house replay) or **the floor alone** (the positive side grows to +2 in two of the six rooms of the house replay).
- **Band edge:** `|error| < 0.3` stays exclusive. Counting 0.3 as inside is clearly worse in the house replay (the integral spends more time at its limits and the valve more time open while the room is warm), so it is not a bug to fix.

## Consequences

- The adopted option has a bias of its own: rooms end up a little colder than before. Paired per room against the old regulator the shift is +0.009 K on average, from -0.051 K (a room that ends warmer) to +0.096 K (a room that ends colder). Both simulators agree in sign and size. The rejected option shifted the other way and further (-0.029 K on average, worst -0.269 K).
- The options dialog stores every field it shows, so every entry saved once holds `Ki = 0.003` without anyone having chosen it. Without the migration the change would not reach them. A `Ki` that somebody set by hand to exactly 0.003 is replaced as well, because the entry cannot tell the two apart; they set it again. An entry at minor version 1.2 or later keeps whatever it holds, also 0.003: from then on that is a choice. Going back to v1.5.0 is safe: Home Assistant accepts a higher minor version when the old code has no `async_migrate_entry`, and the dropped key falls back to the previous default.
- The integral is not saved across restarts, so an old value never needs clamping.
- On-demand boiler, valve open with no hot water while `0 < error < 0.3`: slowed, not removed. At the default needs-heat thresholds 0.3 / 0 the integral builds up 1.40 K a day without hot water with the old regulator and 0.86 K a day with the new one. A lower switch-on value helps more. With the new regulator both times, the thresholds 0.3 / 0 → 0.1 / 0 give: build-up 0.86 → 0.11 K a day, minutes a day without hot water 116 → 32, boiler starts a day 2.5 → 3.2.
- Two rooms on one boiler: heat taken above the target is only slightly lower, 0.32 → 0.31 kWh a day (old → new regulator, thresholds 0.3 / 0). That is mostly the physics of a shared boiler, not of the integral.
- The wait between the room falling below its target and the valve reopening is shorter, not gone. The worst case in the simulation, a very tight house, went from 577 min to 385 min (old → new regulator), because the room cools only slowly towards about 0.3 K below the target.
- Thermostats that need a lasting positive integral (the proportional-band stand-in): compared with the old regulator the last tenth takes longer in some runs (worst +88 min), up to 0.09 K colder, and recovery after a restart is slower. Field data speaks against this for real Tado X: the only steady three-hour blocks in 5.5 days hold the target with an integral of -0.97 and -1.55 and the valve open 23 % and 8 % of the time; no room settles on a positive integral. `Ki` stays an option, 0.002 or 0.003 can be set by hand.
- The valve law of the Tado X is unknown, and the thermostats expose no valve position, no heating demand and no `hvac_action`. Every number above comes from simulation with stand-ins and from replays of recorded inputs, not from the valve itself.
