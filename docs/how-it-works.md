# How It Works

The full story, for people who want to know *why* rather than just *how*.

You do not need any of this to use the integration. Nothing here is required
reading, and nothing here asks you to change a setting. It is written for the
curious, and for anyone thinking about contributing to the code.

Every technical term is explained the first time it appears.

---

## The problem, physically

A Tado X screws onto the radiator valve. Its temperature sensor is therefore a few
centimetres from a hot metal surface.

When the radiator runs, the air around it might sit at 24 °C while the middle of the
room is at 20 °C. The Tado sees 24, decides the room is comfortably above your
target, and closes the valve. The room never gets there.

This is not a defect. It is where the sensor has to be, given that the device is a
valve head. Every radiator thermostat with a built-in sensor has this problem to some
degree. Tado X just happens to have it noticeably, and offers no way to feed it an
external reading.

The gap is typically **1 to 3 °C**, and it is not constant. It depends on how hard
the radiator is running, how the room is laid out, and where the air moves.

---

## The idea: lie about the target, not about the temperature

The obvious fix would be to take over the valve — ignore Tado's own logic and control
the opening directly. That is not possible here, and it would also be a shame: Tado's
internal controller is decent at what it does, and it keeps working when Home
Assistant is down.

So this integration does something less invasive. **It leaves Tado in charge and
changes the number Tado is aiming for.**

You want 21 °C in the room. The integration works out that Tado needs to be told
something like 23 °C for the room to actually land at 21. So it sends 23. Tado runs
its own control loop against that number, exactly as it always did, and the room
ends up where you wanted it.

This is the whole trick, and it has a pleasant side effect: if this integration ever
stops running, your heating does not stop. It simply goes back to behaving like a
plain Tado, with the last target it was given.

---

## Working out the number

Three parts are added to your requested temperature. Each has a different job.

### Part 1: Feedforward — correcting a known, measurable error

Imagine a thermometer that always reads 2 °C too high. Once you know that, you do not
need clever mathematics. You just add 2.

That is feedforward: correcting an error you can *measure directly*, before it has a
chance to cause a problem.

The integration knows both readings — what Tado reports and what your room sensor
reports. The difference between them is the error, right now, no guessing:

```
feedforward_offset = tado_reading − room_reading
```

If Tado says 23.5 and your sensor says 21.8, the offset is 1.7 °C. So the integration
adds 1.7 to your target and sends that.

**This is the main correction.** It typically does 80–90 % of the work. It also
reacts instantly: when the radiator gets hotter and the gap widens, the offset widens
with it in the same cycle. There is no waiting, no settling, no learning period.

The two parts that follow only clean up what feedforward leaves behind.

### Part 2: Proportional — reacting to the gap right now

Feedforward corrects the sensor. It does not know whether the room is actually at the
temperature you asked for. Heat losses through walls and windows, a draught, an open
door — these all mean the room can sit below target even when the sensor offset is
perfectly handled.

So the second part looks at the **error**: how far the room is from what you asked for.

```
error = your_target − room_temperature
p_correction = Kp × error
```

`Kp` is just a strength dial, default 0.8. Room 0.5 °C too cold, so push the target
0.4 °C higher. Simple and immediate.

Proportional control has a known weakness: it only acts while an error exists. A room
that settles a little too cold produces a small correction forever, and stays a little
too cold forever. Which brings us to part three.

### Part 3: Integral — remembering a persistent shortfall

The third part is memory. It accumulates the error over time:

```
integral += error × Ki × seconds_elapsed
```

If your room sits 0.2 °C too cold for an hour, the integral quietly grows and pushes
the target up until the shortfall is gone. It is what turns "close enough" into
"actually right".

`Ki` is deliberately tiny — 0.003. This part is meant to work over tens of minutes,
not seconds.

### Putting it together

```
target_sent_to_tado = your_target
                    + feedforward_offset      (the sensor gap, measured)
                    + Kp × error              (react to now)
                    + integral                (remember the persistent part)
```

The result is clamped to 5–30 °C so a strange sensor reading can never send an absurd
command.

You can watch all four numbers live in **Developer Tools → States** on your proxy
entity. They are listed in the
[settings reference](settings.md#diagnostic-attributes).

---

## Why the numbers are so small

`Kp = 0.8` and `Ki = 0.003` look timid next to textbook controller values. That is
intentional, and it is a consequence of what is being controlled.

A radiator is slow. Open the valve and nothing happens for several minutes: the water
has to arrive, the metal has to warm, the air has to circulate. From valve to felt
temperature is easily 15–30 minutes.

Controlling something slow with an aggressive controller always ends the same way. The
controller sees no response, pushes harder, sees no response, pushes harder still —
and by the time all that heat finally lands, the room sails past the target. Then it
overcorrects the other way. The room oscillates, permanently.

The defence is patience, plus the fact that feedforward already handles the bulk of
the correction. The PI part is only cleaning up a small residue, so it does not need
to be strong.

**If you take one thing from this page:** turning Kp up is almost never the fix.
Rooms that heat too slowly are usually undersized radiators or a sensor in a bad spot.

---

## Two problems worth knowing about

### Integral windup

The integral has a dangerous failure mode, and it happens exactly when you would
least want it to.

Picture a cold start. The room is 4 °C below target. The error is large and stays
large for half an hour — not because the heating is too weak, but because rooms take
time. A naive integral reads that half hour as "still not enough" and accumulates a
huge correction. When the room finally arrives, all of it is still in there, pushing.
The room overshoots badly and takes an hour to come back down.

This is called *windup*, and it is the classic way a PI controller ruins a heating
system.

Two defences are used here:

1. **Only accumulate near the target.** The integral grows only when the error is
   inside a narrow band (default 0.3 °C). Outside that band it does not just stop —
   it *decays*, losing 5 % per cycle. This is the important one. It keeps the
   integral out of the bulk of a heat-up, which is precisely where it would cause
   harm. Its job is fine adjustment once you have arrived.

2. **Freeze while saturated.** If the command has already been clamped at 30 °C, more
   integral would achieve nothing except a debt to be paid off later. So while the
   output is pinned at a limit and the error still points that way, accumulation stops.

The accumulated value is also hard-limited to ±2 °C, as a last line of defence.

One case slips through the first defence. When a room creeps up the last 0.3 °C
slowly — a large room, a small radiator — it spends that whole time inside the band,
and the integral builds up anyway, to 1 °C or more. Once the room arrives, it unwinds
just as slowly: the 5 % decay only applies outside the band, and just above the
target the error is small. So the valve stays open for a while, the room ends up a
little above its target, and `hvac_action` keeps showing `heating`. The overshoot
itself is usually small, but anything that reads `hvac_action` as "this room wants
heat" keeps firing — one reason a boiler should follow `error_c` instead (see
[What "heating" means](settings.md#what-heating-means)).

### One setting, two conflicting jobs

`Kp` has to serve two situations that want opposite things.

Heating a cold room wants a large `Kp`: push hard, get there quickly. Holding a room
steady wants a small `Kp`: react gently, do not oscillate around the target.

Picking one value means accepting a compromise that is wrong in both situations.

**Gain scheduling** removes the compromise by making `Kp` depend on the situation:

| How far off | Multiplier | Why |
|---|---|---|
| More than 2 °C | × 1.5 | Cold start. Push hard |
| Between 0.5 and 2 °C | slides smoothly | No sudden jump in behaviour |
| Less than 0.5 °C | × 1.0 | Nearly there. Be gentle |

It is the same instinct you use driving: firm on the accelerator when you are far
from the speed you want, feathering it as you approach.

The transition is a linear slide rather than a step, because a controller whose
behaviour jumps at a threshold produces visible kinks in the temperature curve.

All four values are adjustable, and the whole feature can be switched off if you have
already tuned `Kp` to your liking.

---

## Why it waits three minutes

Commands to the thermostat are rate-limited: at most one every 180 seconds by default.

Tado X thermostats are wireless and battery powered. Every command is a radio
transmission, and radio is by far the most expensive thing the device does. A
controller that sent an update every 30 seconds would work beautifully and eat
batteries in a season.

Three minutes costs almost nothing in control quality, because — as established
above — the radiator needs far longer than that to respond anyway. You are not
losing precision. You are declining to spend battery on precision the physics
cannot deliver.

Two refinements soften the edges:

- **A minimum change threshold** (0.3 °C). Differences smaller than this are not
  worth a transmission, since Tado rounds to 0.1 °C steps anyway.
- **An urgent bypass.** If the target needs to drop by more than 1 °C — you opened a
  window, or switched to Away — the wait is skipped. Making a room too hot is
  uncomfortable and wastes energy, so *stopping* heat is allowed to be immediate even
  though *adding* it is not.

---

## When the thermostat leaves our setpoint

The send decision compares the new target against the setpoint the thermostat is
believed to hold. Normally that is simply the last value the integration sent: it is
always current, while the setpoint polled back from the thermostat can be a minute
old.

That comparison has a blind spot. The thermostat can leave our setpoint without us —
someone turns the dial, or a schedule in the Tado app writes a block of its own — and
our own last value still says "nothing to do". Whenever the computed target stands
still as well, nothing is ever sent: frost protection behind an open window, or an
output clamped at its limit. The room then does whatever the dial says, for as long
as it says it.

So the setpoint reported by the thermostat is weighed too. If it sits more than the
minimum change threshold away from the target, a correction goes out — at the next
allowed command, not immediately. The urgent bypass stays tied to our own target
dropping, so a thermostat that never accepts our value cannot talk us past the rate
limit and into a flat battery.

Telling a turned dial from a thermostat merely acknowledging our own command is the
same question the follow-the-dial feature asks, and it gets the same answer: more
than 0.5 °C away from what we sent, reported more than 20 seconds after we sent it.
Inside those 20 seconds every report counts as our own echo — otherwise the
integration would spend its life arguing with itself.

One case is deliberately left out: **keep the last TRV command** after the room
sensor has gone (next section). That mode sends nothing at all, a correction
included. The integration has no room temperature there, so insisting on its last
setpoint would mean defending a number computed from a reading it knows to be stale —
against the only party left who can feel the room. A hand on the dial is the better
controller of the two while the sensor is missing. The other two choices for a missing
sensor do correct a turned dial, because they are still regulating: on the backup
sensor, or by handing the room target to the thermostat.

---

## When the sensor disappears

The whole design rests on the external sensor. If it stops reporting, the feedforward
offset cannot be calculated and the integration is blind.

Sensors do drop out, especially over Zigbee — a missed message, a busy network, a
flat battery.

The response is graded rather than all-or-nothing:

- **Short gap** (under 5 minutes by default): carry on with the last valid reading.
  A room does not change much in five minutes, so this is a reasonable bet, and it
  beats abandoning control for a hiccup. `binary_sensor.*_sensor_degraded` turns on
  so the state is visible rather than silent.
- **Longer gap:** stop trusting the old reading. Continuing to control on a reading
  that may now be an hour old would be worse than doing nothing. A configurable
  *sensor-loss action* decides what replaces it:
  - **TRV regulates on its own** (default for new entries): the effective setpoint is
    sent unchanged, without feedforward or PI. The TRV closes the loop on its own
    sensor; because that sensor sits in the radiator's warm zone, the room settles
    somewhat below target — a deliberate trade for following presets and Away.
  - **Backup room sensor:** feedforward + PI continue on a second room sensor. If it
    or the TRV's own reading is missing, control falls back to the TRV.
  - **Hold:** send nothing; the TRV keeps the last regulated command. A held command
    is only kept while it *is* a regulated command — frost protection for an open
    window, or an OFF → HEAT cycle, hand control to the TRV until the room sensor
    returns, so the TRV is never parked on a frost target or an unknown setpoint.

  The active choice is exposed as `active_control_mode`. Each switch between room
  sensor, backup sensor and TRV resets the PI state: an integral learned against one
  sensor is wrong for another. Bridging a short gap is not a switch, so a Zigbee
  hiccup never wipes the integral. Switches go through the normal rate limiter;
  a large drop bypasses it as an urgent decrease, a rise waits for the next slot.

Pending window and presence actions are re-validated immediately before they fire, so
a single spurious reading during a timer cannot flip a room into Away.

---

## Design decisions, briefly

**Why not a full PID?** The D (derivative) term reacts to how fast the error is
changing. On a system this slow and this noisy — a sensor reporting in 0.1 °C steps
every few minutes — D mostly amplifies measurement noise. There is nothing useful for
it to do here.

**Why not control the valve directly?** Not available through the Matter interface,
and it would mean reimplementing what Tado already does adequately. It would also
mean the heating stops working whenever Home Assistant does.

**Why is feedforward the main term rather than the PI?** Because the sensor offset is
*measurable*. Anything you can measure directly, you should correct directly. Feedback
control is for what remains — and the less you leave to it, the more stable the whole
system is.

**Why does the "off" window action still show Frost Protection?** The window
automation keeps one model: while a window is open, the room is in frost protection
and the preset to restore is saved (and written to the state, so it survives a
restart). The action only changes what reaches the TRV — the frost target, or an off
command. Switching the proxy itself to off would mix the window up with a person
turning the heating off, which has to survive a restart and must not be undone when
the window closes. While the TRV is off, no setpoint is sent at all: every setpoint
command carries `hvac_mode: heat` and would switch the TRV back on.

---

## Reading the code

If you want to go further, the control logic is deliberately kept free of Home
Assistant dependencies so it can be read and tested on its own:

| File | Contains |
|---|---|
| `custom_components/tadox_proxy/regulation.py` | The entire control loop, ~200 lines |
| `custom_components/tadox_proxy/parameters.py` | Every default value, with rationale |
| `custom_components/tadox_proxy/climate_controllers.py` | Window, presence, boost and follow state machines; sensor grace period and sensor-loss action; when a command is sent (rate limiting); preset targets |
| `tests/` | Test suite, runnable without Home Assistant |
| `tests_ha/` | Integration tests against a real Home Assistant core (Linux/macOS) |

The formula above corresponds directly to `FeedforwardPiRegulator.compute()`.

---

## Where to go next

| If you want to… | Read this |
|---|---|
| Adjust a room that is not quite right | [Tuning guide](../TUNING.md) |
| Look up a specific setting | [Settings reference](settings.md) |
| Start from the beginning | [Setup guide](setup.md) |
