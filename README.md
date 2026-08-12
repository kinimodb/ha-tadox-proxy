# Tado X Proxy Thermostat

[![Tests](https://github.com/kinimodb/ha-tadox-proxy/actions/workflows/tests.yml/badge.svg)](https://github.com/kinimodb/ha-tadox-proxy/actions/workflows/tests.yml)
![Version](https://img.shields.io/badge/version-1.1.8-blue)
![HA](https://img.shields.io/badge/Home%20Assistant-2026.3%2B-41BDF5)

**Read this page in your language:**
[Deutsch](https://github-com.translate.goog/kinimodb/ha-tadox-proxy?_x_tr_sl=en&_x_tr_tl=de&_x_tr_hl=de) ·
[Nederlands](https://github-com.translate.goog/kinimodb/ha-tadox-proxy?_x_tr_sl=en&_x_tr_tl=nl&_x_tr_hl=nl) ·
[Français](https://github-com.translate.goog/kinimodb/ha-tadox-proxy?_x_tr_sl=en&_x_tr_tl=fr&_x_tr_hl=fr) ·
[Italiano](https://github-com.translate.goog/kinimodb/ha-tadox-proxy?_x_tr_sl=en&_x_tr_tl=it&_x_tr_hl=it) ·
[Español](https://github-com.translate.goog/kinimodb/ha-tadox-proxy?_x_tr_sl=en&_x_tr_tl=es&_x_tr_hl=es)

*(Automatic translation by Google. The English text is the original.)*

---

## Your room is colder than your thermostat says

Your Tado X shows 21 °C. Your room feels like 19 °C. You are not imagining it.

A Tado X sits on the radiator. So it measures the air right next to the radiator.
That air is always warmer than the rest of the room. The Tado believes the room is
warm enough and turns the heating down too early.

The result: your room stays 1 to 3 °C colder than the number on the display.
Every single day.

---

## What this integration does

You place a small temperature sensor somewhere in the room — on a shelf, on a wall,
anywhere away from the radiator and the window. That sensor knows the real temperature.

This integration then does what you would do by hand: **it sets the Tado higher than
you actually want it.**

Here is the idea:

> You ask for 21 °C. The room is at 19 °C.
> So the integration tells the Tado: *"heat to 23"*.
> The radiator keeps running. The room reaches 21 °C.
> Then the integration dials the Tado back down.

It repeats this every few minutes. Over time it learns how much extra your particular
room needs, and it adjusts by itself. Rooms differ: a small room with a big radiator
needs less help than a draughty room with a small one.

You never deal with those numbers. You set 21 °C and you get 21 °C.

In practice most rooms stay within about half a degree of the temperature you asked for.

---

## Is this for me?

**This helps you if:**

- You have one or more Tado X radiator thermostats.
- Your rooms never quite reach the temperature you set.
- You have a temperature sensor in the room, or you are willing to buy one.
- You run Home Assistant.

**This will not help you if:**

- You have underfloor heating, electric radiators, or steam heating.
  Those work completely differently.
- You only have Tado wall thermostats and no radiator thermostats.
- You do not want a second sensor in the room. Without it the integration cannot
  know the real temperature, and there is nothing it can do.

---

## What it costs you

- **A temperature sensor for each room you want to fix.** Any sensor that shows up
  in Home Assistant works — Zigbee, Bluetooth, Wi-Fi, whatever you already use.
  These typically start around 10–15 €.
- **About ten minutes** of setup per room.
- **Nothing else.** No cloud service, no account, no subscription. The integration
  runs entirely inside your own Home Assistant.

---

## Getting started

**→ [Step-by-step setup guide](docs/setup.md)**

It walks you through installing, connecting your radiator thermostat and your room
sensor, and checking that it works. No prior knowledge needed.

---

## Do I have to configure anything?

**No.** This is the most common misunderstanding, so to be clear:

The integration ships with settings that work in most rooms. Install it, point it at
your radiator thermostat and your room sensor, and leave everything else alone. It
will do its job.

There *are* a lot of adjustable values, and you will see them if you go looking.
They exist for unusual rooms and for people who enjoy fiddling. **You can ignore all
of them.** If your room heats up and holds its temperature, you are finished.

Come back to the settings only if something is actually wrong.

---

## Everyday features

Beyond the temperature correction, you get:

- **Presets** — Comfort, Eco, Away, Boost, Frost Protection. One tap each.
- **Window detection** — when a window contact opens, heating drops to frost
  protection and comes back afterwards. Optional.
- **Presence detection** — when nobody is home, the room drops to Away and recovers
  when someone returns. Optional.
- **Manual override** — turn the dial on the radiator thermostat itself and, if you
  enable it, the proxy follows you instead of fighting you.

Details for all of these are in the [settings reference](docs/settings.md).

---

## Documentation

Start at the top and go down only as far as you need to.

| If you want to… | Read this |
|---|---|
| Get it running | **[Setup guide](docs/setup.md)** |
| Look up a preset, a switch, or an option | [Settings reference](docs/settings.md) |
| Fix a room that heats too slowly or overshoots | [Tuning guide](TUNING.md) |
| Understand how the correction actually works | [How it works](docs/how-it-works.md) |

---

## Something not working?

The [setup guide](docs/setup.md#when-something-is-wrong) covers the common problems:
the room stays cold, the temperature swings up and down, or the integration seems to
do nothing at all.

If that does not help, open an
[issue on GitHub](https://github.com/kinimodb/ha-tadox-proxy/issues) or ask in the
[Home Assistant community thread](https://community.home-assistant.io/t/tado-x-proxy-thermostat-temperature-control-for-tado-x/995710).

**Known problem:** configuring the integration in the iOS Companion App crashes when
you pick an entity. This is a bug in Home Assistant itself, not in this integration.
Use a normal web browser to set it up.

---

## Requirements

- Home Assistant 2026.3 or newer
- [HACS](https://hacs.xyz) installed
- At least one Tado X radiator thermostat visible in Home Assistant — the one screwed
  onto the radiator, not a wall thermostat
- One temperature sensor per room
- One entry per radiator: a room with two radiators gets two entries, both using the
  same room sensor

---

## License

MIT License — see [LICENSE](LICENSE)
