# Integral windup is limited by a low gain and a tight floor, not by a smarter rule

The integral correction runs at `Ki = 0.001` (was 0.003) and may not go below -0.5 °C (was -2.0 °C). Its ceiling stays at +2.0 °C and `regulation.py` does not change. Entries saved at the former Ki default follow the new one through a config-entry migration (minor version 1.2); any other stored Ki stays.

## Context

The radiator thermostat acts on the command minus its own reading, `u = (1 + Kp) × error + integral`, which is `1.8 × error + integral` with the default `Kp`. The valve is open while `u > 0`. Issue #124 showed the integral working against the room in both directions:

- **Too cold:** during a slow final approach the room spends its last tenths of a degree inside the 0.3 °C band and the integral builds up by `Ki × ∫ error dt`. At `Ki = 0.003` that is about `0.49 / r` for an approach speed of `r` K/h, and it is still there when the room arrives. The valve stays open above the target. A third of `Ki` gives a third of the excess; the integral time `(1 + Kp) / Ki` goes from 10 to 30 minutes, while rooms respond in hours.
- **Too warm:** a room that stays warm winds the integral down to -2. A shut valve cannot be shut further, so everything below about -0.5 only delays the next heating: the valve opens again once `1.8 × error > -integral`. With -2 that was about 1.1 K below the target or hours later; with -0.5 it is at the latest about 0.28 K below.

The ceiling stays at +2: caps of +0.5 and +1 left thermostats with a proportional band too cold.

Effect, simulation (18 scenarios × 3 valve stand-ins × 3 sensors, the real regulator and `decide_send`), before → after: lowest integral -2.00 → -0.50; integral at the first crossing of the target 1.03 → 0.49; valve open while 0.1 K or more above target 45 → 30 min a day; overshoot 0.46 → 0.38 K; TRV commands a day 83 → 60; boiler starts unchanged. Replay of the four recorded evenings of #124: integral at the crossing +1.25 / +0.60 / +0.91 / +0.45 → +0.36 / +0.05 / +0.30 / +0.15, valve open after the crossing 28.5 / 36 / 20 / 11 → 9 / 3 / 6 / 1 min. Replay of six rooms over 5.5 days, totals over the rooms: minutes at the clamp 290 → 0, valve open while 0.1 K or more warm 251 → 46 min, valve shut while 0.1 K or more cold 535 → 197 min.

## Considered Options

- **Valve-aware conditional integration** (no negative integration while `u` is below a limit): removes the negative windup completely (worst reopen delay 48 min) but rooms run warm. Paired shift -0.029 K, worst -0.269 K; mean error with continuous supply -0.107 K against -0.030 K; valve open 0.1 K or more above target 107 against 57 min a day. Thermostats that cycle around `u = 0` make the integral ratchet upwards, the same effect as the refuted "freeze on sign change". A second, independently written simulator confirmed it.
- **Add-ons on top of the adopted change** (integral warm-up timer, floor that depends on `u`, `Ki` scheduled by `|u|`, symmetric gating on heat arrival, a ceiling on `u`, the band edge inclusive / 0.25 / 0.4; screening of 16 variants × 162 runs): none clearly better. Best were a floor on `u` of -0.5 (mean reopen delay 27 → 21 min, undershoot 0.48 → 0.46 K) and a 60 min warm-up (valve open above target 25 → 17 min a day, but 16 runs shifted the bias by more than 0.05 K). Not worth the extra state.
- **Rate conditioning** (blind with a 0.2 K sensor that updates every 6 to 19 min), **heat-arrival gating alone** (cold bias), **leak** and **back-calculation** (weak or biased).
- **`Ki` alone** (the integral still reaches -1.87 in the house replay) or **the floor alone** (the positive side grows to +2 in two rooms).
- **Band edge:** `|error| < 0.3` stays exclusive. Counting 0.3 as inside is clearly worse in the house replay (minutes at the clamp, valve open while warm), so it is not a bug to fix.

## Consequences

- The options dialog stores every field it shows, so every entry saved once holds `Ki = 0.003` without anyone having chosen it. Without the migration the change would not reach them. An entry at minor version 1.2 or later keeps whatever it holds, also 0.003: from then on that is a choice. Going back to v1.5.0 is safe: Home Assistant accepts a higher minor version when the old code has no `async_migrate_entry`, and the dropped key falls back to the old default.
- The integral is not saved across restarts, so an old value never needs clamping.
- On-demand boiler, valve open with no hot water while `0 < error < 0.3`: slowed, not removed. Build-up per day without hot water 1.40 → 0.86 K at the default needs-heat thresholds 0.3 / 0. A lower switch-on value helps more (0.1 / 0: 0.11 K a day, minutes without hot water 116 → 32, boiler starts 2.5 → 3.2).
- Two rooms on one boiler: heat taken above target only slightly lower (0.32 → 0.31 kWh a day at 0.3 / 0). That is mostly the physics of a shared boiler, not of the integral.
- The worst reopen delay in a very tight simulated house stays long (385 min), because the room cools only slowly towards -0.28 K.
- Thermostats that need a lasting positive integral (the proportional-band stand-in): the last tenth takes longer in some runs (worst +88 min), up to 0.09 K colder, slower recovery after a restart. Field data speaks against this for real Tado X: the only steady three-hour blocks in 5.5 days hold the target with an integral of -0.97 and -1.55 and the valve open 23 % and 8 % of the time; no room settles on a positive integral. `Ki` stays an option, 0.002 or 0.003 can be set by hand.
- The valve law of the Tado X is unknown, and the thermostats expose no valve position, no heating demand and no `hvac_action`. Every number above comes from simulation with stand-ins and from replays of recorded inputs, not from the valve itself.
