# The Ki default goes back to 0.003; a lower Ki is a setting per room

Status: accepted for v1.6.0. Supersedes the `Ki` half of [ADR 0002](0002-integral-windup-low-gain-and-tight-floor.md); its integral floor of -0.5 °C stands.

`Ki` defaults to 0.003 again, as up to v1.5.0; the pre-releases v1.6.0-beta.1 and beta.2 ran 0.001. The floor stays at -0.5 °C, the ceiling at +2.0 °C, `Kp` at 0.8 and the decay at 0.95. The config-entry migration to minor version 1.2 stays, but it only raises the version: an update never deletes or changes a value someone has stored.

## Context

- A three-day test of beta.2 in the maintainer's six rooms found rooms short of their target more often than on comparable days before. Mostly not because of the beta: reheats after the house had been empty or a door open, mornings when the boiler was probably busy with hot water, a living room at the limit of its radiator. The beta's own share was small: `Ki = 0.001` slowed the last tenth of a degree, in one clear case.
- The tester of #124, whose boiler fires on demand, refuted the beta's headline that the valve stays open only 1 to 9 minutes after the room reaches its target. That figure replayed his old error series, overshoot included, through the new regulator. In his real runs on beta.2 the valve stayed commanded open for at least 36 minutes, without going idle in the 94 minutes recorded, and for 16 minutes: a smaller overshoot leaves a smaller proportional part to cancel the integral. `Ki` did shrink the push above the target (58 → 17 and 10 → 3 K·min in the replay). His boiler is served by the needs-heat sensor, which follows `error_c` and does not depend on `Ki`.
- No default changes for everyone because of one setup; a value that can be set is tuned per room (TUNING.md). The floor cannot be set, and it fixes a cold-side windup that the maintainer's house shows in its own data, so it stays.
- The betas' migration dropped a stored `Ki` of 0.003 without notice, also where someone had chosen it.

## Considered Options

- **Keep the beta's numbers:** fewer commands and less valve time while warm in the models, but more minutes below target (656 / 644 a day in the model below) and a slower last tenth; its gain for on-demand boilers did not hold.
- **Both numbers back** (`Ki` 0.003, floor -2): the cold-side windup returns, the warm side is the same as now. Replaying the test days open loop through the real code, the valve was commanded shut while the room was 0.1 K or more below target for 308 minutes a day in the six rooms together, against 93 with this decision and 143 with the beta.
- **The floor back alone** (`Ki` 0.001, floor -2): the worst of the four; in the model a room 0.1 K below target after eight warm hours waited 304 minutes for its valve.
- **`Ki` 0.002:** the fallback if rooms at 0.003 overshoot clearly more than with v1.5.0.
- **The tester's offer, a cap on the feedforward or the command at the target:** feedforward cancels out of the push, so only a cap on the push would act, and it would shut valves at the target where the maintainer's thermostats need a gap of 0.2 to 0.6 K to hold it (saw-tooth, colder, more commands). At most a later opt-in for on-demand boilers.
- **Later levers, model only:** a minimum push near the target (code), `Kp` 1.2 to 1.5 as a trial in one room.

## Consequences

- The warm side is as in v1.5.0: after a slow final approach the integral is three times what the beta left, about 1.2 °C after a last 0.3 °C that took 45 minutes, and the valve stays open past the target for a while (#124). Closed-loop model of four rooms in two simulators, per day, against v1.5.0: minutes more than 0.2 K below target 613 / 565 against 675 / 612; minutes with the valve commanded open while 0.1 K or more warm 145 / 136 against 135 / 129; commands 336 / 355 against 359 / 371. Tenths of a degree, not degrees. In the open-loop replay the integral reached its ceiling of +2 in two rooms; with v1.5.0 it did not.
- A boiler that fires on demand follows the needs-heat sensor, which turns off once the room has reached its target, whatever the valve does. A room can also run a lower `Ki` of its own, for example 0.001: less push is left above the target, and the last tenth takes longer (TUNING.md).
- The migration keeps every stored value. An entry whose `Ki` a beta dropped follows the default again and runs 0.003. An entry whose options dialog was saved during a beta holds 0.001 and keeps it until someone sets 0.003.
- All numbers come from models and replays of recorded inputs, not from the valve: the Tado X reports no valve position.
