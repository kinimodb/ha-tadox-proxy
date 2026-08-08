# Setup Guide

This guide takes you from nothing to a working room. It assumes no prior knowledge.
Plan about ten minutes.

If you have not read [what this integration does](../README.md), start there.

**Do this on a computer, not on your phone.** The Home Assistant iOS app has a bug
that crashes when you select an entity. A normal web browser works fine.

---

## Before you start

You need three things:

1. **A Tado X radiator thermostat** that already appears in Home Assistant.
   Tado X devices connect through Matter. If you cannot see your thermostat in
   Home Assistant yet, set that up first — this integration cannot help until
   Home Assistant can see the device.
2. **A temperature sensor in the room.** Any brand, any protocol, as long as it
   shows up in Home Assistant and reports a temperature.
3. **HACS installed** in Home Assistant. See [hacs.xyz](https://hacs.xyz).

---

## Step 1: Place the sensor properly

This matters more than any setting you will ever change. The whole integration
depends on this sensor telling the truth about your room.

**Put the sensor:**

- Roughly at the height of a table or shelf.
- Somewhere in the open, where air moves normally.
- Where you actually spend time in the room.

**Keep it away from:**

- The radiator — it would read too warm, and you would be back where you started.
- Windows and outside doors — draughts make it read too cold.
- Direct sunlight — a patch of sun can add several degrees.
- TVs, computers, lamps, and anything else that gets warm.

A sensor in a bad spot produces a room that is permanently too warm or too cold,
and no amount of tuning will fix it. If you later find your room is consistently
off, come back and check this first.

---

## Step 2: Install the integration

1. Open **HACS** in Home Assistant.
2. Go to **Integrations**.
3. Click the **three dots** in the top right corner.
4. Choose **Custom repositories**.
5. Paste this address:
   ```
   https://github.com/kinimodb/ha-tadox-proxy
   ```
6. Set Category to **Integration**.
7. Click **Add**.
8. Search HACS for **Tado X Proxy Thermostat** and install it.
9. **Restart Home Assistant.** The integration will not appear until you do.

---

## Step 3: Set your Tado to Manual

This step is easy to miss, and skipping it causes the most common failure.

Open the **Tado app** and make sure the room has **no schedule running**. Set the
thermostat to manual mode.

Why: this integration constantly adjusts the target temperature on your Tado.
If the Tado app also has its own schedule, the two will fight each other. Your
heating will behave unpredictably and you will not be able to tell why.

From now on you control the room through Home Assistant, not through the Tado app.

---

## Step 4: Add the room

1. Go to **Settings → Devices & Services**.
2. Click **Add Integration** (bottom right).
3. Search for **Tado X Proxy Thermostat**.
4. Fill in three fields:

| Field | What to pick |
|---|---|
| **Source Climate Entity** | Your real Tado X thermostat. It starts with `climate.` |
| **External Temperature Sensor** | The room sensor from Step 1. It starts with `sensor.` |
| **Name** | Whatever you want to call it, for example "Living Room Proxy" |

5. Click **Submit**.

That is the entire configuration. Everything else has sensible defaults.

Repeat this step for each room. Every room needs its own sensor and its own entry.

---

## Step 5: Check that it works

You now have a new thermostat in Home Assistant, named whatever you chose. Use this
one from now on. Ignore the original Tado thermostat entity — the integration drives
it for you.

**Test it:**

1. Set your new proxy thermostat to about 2 °C above the current room temperature.
2. **Wait a few minutes.** The integration only talks to the Tado every 3 minutes
   to protect the thermostat's batteries. Nothing happening in the first minute is
   normal, not a fault.
3. Now open the **Tado app** and look at the target temperature for that room.

**It is working if the Tado app shows a higher temperature than the one you set.**

That is the whole trick, and it is the clearest sign that everything is connected.
You asked for 21 °C, the Tado has been told to aim for something like 23 °C, and the
room will end up at 21 °C.

Then simply let it run. Reaching the target from cold takes 30 to 60 minutes in a
typical room — the same as it always did.

---

## Step 6: You are done

Set your temperature and use the room normally.

**Do not go into the settings.** You will find a lot of adjustable values in
**Configure**, and none of them need your attention. They exist for unusual rooms.
The defaults are the result of a lot of testing and they work in most homes.

If the room heats up and stays at the temperature you asked for, there is nothing
left to do.

---

## What you got

Alongside the thermostat, the integration creates a few extras. All of them are
optional and you can ignore them until you need them.

- **Preset buttons** — Comfort, Eco, Away, Boost, Frost Protection.
- **A number entity per preset**, so you can set what "Eco" means to you and use it
  in automations.
- **A boost timer sensor** showing how many minutes of boost are left.
- **A warning sensor** that turns on if your room sensor stops reporting.
- **A "follow physical thermostat" switch**, off by default. Turn it on if you want
  turning the dial on the wall to override the proxy.

The [settings reference](settings.md) explains each one.

---

## Optional: windows and presence

Two extras are worth setting up once the basics work. Both live under
**Settings → Devices & Services → Tado X Proxy → Configure**.

**Window detection.** Pick a window contact sensor. When the window opens, the room
drops to frost protection after 30 seconds. When it closes, the previous setting
comes back after 2 minutes. The delay is deliberate — it stops the radiator from
blasting the moment you shut the window.

**Presence detection.** Pick a presence sensor or person tracker. When nobody is
home for 10 minutes, the room drops to Away. When someone returns, it recovers after
30 seconds.

Both are optional and work independently.

---

## When something is wrong

### The room stays cold

1. **Check the Tado app.** Does the target temperature there sit higher than what
   you set in Home Assistant? If not, the integration is not reaching your Tado.
2. **Check for a Tado schedule.** This is the number one cause. Go back to
   [Step 3](#step-3-set-your-tado-to-manual). A schedule in the Tado app will
   override everything.
3. **Check your sensor.** Is it still reporting? Look at the
   `sensor_degraded` warning entity. If it is on, your room sensor has dropped out
   and the integration is flying blind.
4. **Check the radiator itself.** Is it actually getting hot? If the valve is stuck
   or the radiator needs bleeding, no software can fix that.

### The room gets too warm

Usually the sensor is in a spot that reads too cold — near a window, in a draught,
or behind furniture. Revisit [Step 1](#step-1-place-the-sensor-properly).

If the sensor placement is genuinely good, the room may need gentler settings.
See the [tuning guide](../TUNING.md).

### The temperature swings up and down

Some movement is normal. Radiator heating always overshoots and undershoots a
little, and half a degree either way is expected.

If it swings more than that, or cycles every few minutes, the room needs calmer
settings. See the [tuning guide](../TUNING.md).

### Nothing happens at all

- Did you restart Home Assistant after installing? It will not work otherwise.
- Are you looking at the right thermostat? The integration creates a **new** one.
  Changing the original Tado entity does nothing useful.
- Give it 3 minutes. Commands are deliberately rate-limited to save batteries.
- Check the logs: **Settings → System → Logs**, search for `tadox_proxy`.

### Still stuck

Open an [issue on GitHub](https://github.com/kinimodb/ha-tadox-proxy/issues) or ask
in the [community thread](https://community.home-assistant.io/t/tado-x-proxy-thermostat-temperature-control-for-tado-x/995710).

Helpful things to include: which radiator type you have, where your sensor sits,
what you set, and what actually happened.

---

## Where to go next

| If you want to… | Read this |
|---|---|
| Look up a preset, switch, or option | [Settings reference](settings.md) |
| Improve a room that is not quite right | [Tuning guide](../TUNING.md) |
| Understand how the correction works | [How it works](how-it-works.md) |
