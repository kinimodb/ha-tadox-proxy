# Technischer Kontext – Tado X Proxy

> Dieses Dokument dient als "Gedächtnis" für das Projekt. Bei Kontextübergaben
> (neues Chat-Fenster, neue Session) kann dieses Dokument gelesen werden, um
> den vollen Stand zu erfassen.

**Letzte Aktualisierung:** 2025-03 (v0.5.0)

---

## Projektübersicht

Eine Home Assistant Custom Component (HACS), die als Proxy-Thermostat für Tado X TRVs fungiert.
Ein externer Raumsensor liefert die wahre Raumtemperatur, der Proxy berechnet daraus einen
angepassten Sollwert für das Tado X Thermostat.

**Repository:** `https://github.com/kinimodb/ha-tadox-proxy`
**Branch-Strategie:** `main` = stable, Feature-Branches `claude/*` für Entwicklung.

---

## Architektur-Entscheidungen

### Warum Feedforward + PI statt PID?

**Problem:** Tado X hat einen eigenen internen PID-Regler. Ein zweiter PID von außen
kämpft *gegen* den internen (Cascading-Problem). Die alte Implementierung (v0.3.x) hatte
Kp=7.0 – so aggressiv, dass der Regler bei 0.57°C Fehler bereits saturierte.

**Lösung (v0.4.0):** Feedforward + PI.
- **Feedforward** = `tado_sensor - room_sensor` → kompensiert den Sensor-Offset sofort.
- **PI** = kleiner Korrekturfaktor (Kp=0.8, Ki=0.003) für Restfehler.
- Arbeitet MIT Tados Regler: Wir sagen Tado einfach "stelle 23°C ein" statt zu versuchen,
  das Ventil direkt zu steuern.

### Warum Integral Deadband? (v0.4.1)

**Problem:** In v0.4.0 baute sich das Integral während der Aufheizphase auf (~2°C),
weil der Fehler durchgehend positiv war. Beim Erreichen des Sollwerts war das Integral
so hoch, dass der Befehl weiter heizte → 0.6°C Overshoot.

**Lösung:** Integral akkumuliert NUR, wenn `|error| < 0.3°C` (nahe am Ziel).
Außerhalb dieser Zone decayed das Integral mit Faktor 0.95 pro Zyklus.
Ergebnis: Overshoot reduziert von 0.6°C auf 0.3°C, Integral im Betrieb nahe 0.

### Warum Rate Limiting?

Tado X TRVs laufen auf Batterie. Häufige Befehle (jede 60s) würden die Batterie
schnell leeren. Daher: min. 180s zwischen Befehlen, mit Bypass für dringende
Absenkungen (> 1°C Differenz).

### Presets (v0.5.0)

5 Betriebsmodi: Comfort (Default), Eco (Offset), Boost (Timer), Away, Vacation.
- **Eco** nutzt `target_temp + eco_offset_c` (negative Zahl → Absenkung).
- **Boost** setzt feste Max-Temperatur mit `async_call_later`-Timer, auto-revert zu Comfort.
- **Away/Vacation** nutzen feste Temperaturen (16°C / 5°C).
- Preset wird über `RestoreEntity` persistiert, außer Boost (revert bei Neustart).
- Alle Preset-Temperaturen über Options Flow konfigurierbar.

---

## Datei-Architektur

```
custom_components/tadox_proxy/
├── __init__.py        # DataUpdateCoordinator (pollt alle 60s)
├── climate.py         # ClimateEntity mit Regulation Loop + Presets
├── config_flow.py     # Setup + Options Flow (Kp, Ki, Presets, Sensor)
├── const.py           # DOMAIN + Config-Keys + PRESET_VACATION
├── diagnostics.py     # HA Diagnostik-Export
├── manifest.json      # HACS/HA Metadata (v0.5.0)
├── parameters.py      # Zentrale Parameter-Defaults (RegulationConfig + PresetConfig)
├── regulation.py      # Feedforward + PI Engine (FeedforwardPiRegulator)
├── strings.json       # UI-Strings (Fallback)
└── translations/
    ├── en.json        # Englisch
    └── de.json        # Deutsch

tests/
├── __init__.py
└── test_regulation.py # 24 Unit Tests (importiert ohne HA-Dependency)
```

### Abhängigkeits-Kette

```
parameters.py ← regulation.py ← climate.py
                                    ↑
__init__.py (Coordinator) ──────────┘
config_flow.py (Options) ──────────┘
const.py ──────────────────────────┘
```

- `parameters.py` und `regulation.py` haben **keine** Home Assistant Abhängigkeit → testbar ohne HA.
- `climate.py` importiert aus HA und ist die Brücke zwischen Regulation-Engine und HA.
- `__init__.py` stellt den DataUpdateCoordinator bereit (Sensor-Daten alle 60s).

---

## Regelungs-Formel

```
effective_setpoint = f(preset, target_temp)   # Comfort→target, Eco→target-2, etc.
command = effective_setpoint + (tado_internal - room_temp) + kp * error + integral
                               └── feedforward ──────────┘   └── PI ──┘
```

Wobei:
- `error = effective_setpoint - room_temp`
- `integral` akkumuliert nur bei `|error| < 0.3°C`, decayed sonst
- `command` geclampt auf [5°C, 25°C]
- Anti-Windup: Integral friert bei Sättigung + Deadband-Gating

---

## Aktuelle Parameter (Defaults)

### Regelung

| Parameter | Wert | Datei |
|-----------|------|-------|
| Kp | 0.8 | parameters.py |
| Ki | 0.003 | parameters.py |
| Integral Deadband | 0.3°C | parameters.py |
| Integral Decay | 0.95 | parameters.py |
| Integral Limits | ±2.0°C | parameters.py |
| Min Command Interval | 180s | parameters.py |
| Min Change Threshold | 0.3°C | parameters.py |
| Target Range | 5–25°C | parameters.py |
| Frost Protection | 5°C | parameters.py |
| Update Interval | 60s | __init__.py / parameters.py |

### Presets

| Parameter | Wert | Datei |
|-----------|------|-------|
| Eco Offset | −2.0°C | parameters.py (PresetConfig) |
| Boost Target | 25.0°C | parameters.py (PresetConfig) |
| Boost Duration | 30 min | parameters.py (PresetConfig) |
| Away Target | 16.0°C | parameters.py (PresetConfig) |
| Vacation Target | 5.0°C | parameters.py (PresetConfig) |

---

## Real-World-Testergebnisse

### Testraum (Schlafzimmer, ca. 15m²)

| Version | Overshoot | Stabilität | Integral im Betrieb | Bewertung |
|---------|-----------|-----------|---------------------|-----------|
| v0.4.0 | 0.6°C | Stabil nach Warmup | ~2.0°C (zu hoch) | Overshoot-Problem |
| v0.4.1 | 0.3°C | ±0.3–0.5°C, 11h+ Nacht bestanden | ~0.06°C | Gut |
| v0.5.0 | – | Presets implementiert, Test steht aus | – | Pending |

**Typische Messwerte (v0.4.1, Nachtbetrieb):**
- Ziel: 18.0°C, Raum pendelt: 17.7–18.5°C
- Feedforward Offset: ~1.7°C (Tado misst wärmer als Raum)
- Zyklusdauer: ~60–90 min (Heizen→Idle→Heizen)
- Integral: 0.02–0.10°C (vernachlässigbar klein)

---

## Bekannte Einschränkungen

1. **Keine externen Trigger** – Fensterkontakt, Präsenz noch nicht angebunden (M4).
2. **Nur ein Testraum validiert** – Default-Parameter müssen in anderen Räumen geprüft werden.
3. **Tado X spezifisch** – Nicht getestet mit anderen Tado-Modellen.
4. **Batterie-Monitoring** – Kein direktes Feedback über Batterie-Zustand des TRV.

---

## Nächster Meilenstein: M4 – Externe Trigger

- Fensterkontakt: Sofort auf Frostschutz bei "offen", Restore bei "zu".
- Präsenz-Sensor: Auto-Wechsel auf Away/Eco bei Abwesenheit.

---

## Test-Infrastruktur

- **Framework:** pytest
- **Import-Trick:** `importlib.util.spec_from_file_location` umgeht `__init__.py` (HA-Abhängigkeit).
- **Ausführen:** `cd /path/to/repo && python -m pytest tests/ -v`
- **Aktuell:** 23 Tests in 6 Klassen (Feedforward, PI, AntiWindup, Safety, FullScenario, PresetConfig).
