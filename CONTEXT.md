# Technischer Kontext – Tado X Proxy

> Dieses Dokument dient als "Gedächtnis" für das Projekt. Bei Kontextübergaben
> (neues Chat-Fenster, neue Session) kann dieses Dokument gelesen werden, um
> den vollen Stand zu erfassen.

**Letzte Aktualisierung:** 2026-03 (v0.7.0)

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

### Presets (v0.5.0) + Entitäten (v0.6.0)

6 Betriebsmodi: Comfort (Default), Eco (fest), Boost (Timer), Away, Vacation, None (Manuell).
- **Eco** nutzt `eco_target_c` (feste Temperatur, Default 19°C). Seit v0.6.0 kein Offset mehr.
- **Boost** setzt feste Max-Temperatur mit `async_call_later`-Timer, auto-revert zu Comfort.
- **Away/Vacation** nutzen feste Temperaturen (16°C / 5°C).
- **None (Manuell):** Aktiviert beim Slider-Verschieben – kein Preset aktiv, eigene Temperatur.
- Preset wird über `RestoreEntity` persistiert, außer Boost (revert bei Neustart).
- **v0.6.0:** 5 NumberEntitäten (Comfort, Eco, Boost, Away, Vacation) erlauben direkte Steuerung aus Dashboards/Automationen via `number.set_value`.
- **v0.6.0:** SwitchEntität "Follow Tado Input" – erkennt physische Thermostat-Änderungen via `async_track_state_change_event` auf `temperature`-Attribut der Tado-Entity.
- **v0.6.0 Bugfix:** `target_temperature` gibt `_effective_setpoint()` zurück → UI zeigt immer den aktiven Zielwert.
- Alle Preset-Temperaturen: Range 5–30°C, via NumberEntität oder Options Flow.

### Externe Trigger (v0.7.0)

- **Fensterkontakt:** Optionaler `binary_sensor.*`. Bei "on" startet Timer (`CONF_WINDOW_DELAY_S`, Default 30s), nach Ablauf `_hvac_mode = HVACMode.OFF`. Bei "off" Restore auf gespeicherten Modus. Steuert **nur** HVAC-Modus.
- **Präsenzsensor:** Optionaler `binary_sensor.*`. Bei "off" startet Timer (`CONF_PRESENCE_AWAY_DELAY_S`, Default 1800s), nach Ablauf `_preset_mode = PRESET_AWAY`. Bei "on" Restore auf gespeichertes Preset/Temperatur. Steuert **nur** Preset.
- **Unabhängigkeit:** Fenster ↔ Preset-Modus interferieren nicht. Beide können gleichzeitig aktiv sein.
- Listener registriert via `async_track_state_change_event` + `async_call_later` für Delays.
- Diagnose-Attribute `window_open_active` + `presence_away_active`.
- `OptionsFlowWithReload` sorgt bei Sensor-Konfiguration für korrekten Re-Register der Listener.

---

## Datei-Architektur

```
custom_components/tadox_proxy/
├── __init__.py        # DataUpdateCoordinator (pollt alle 60s), Platforms: climate, number, switch
├── climate.py         # ClimateEntity mit Regulation Loop + Presets + Config-Listener
├── number.py          # 5 NumberEntitäten für Preset-Temperaturen (v0.6.0)
├── switch.py          # SwitchEntität "Follow Tado Input" (v0.6.0)
├── config_flow.py     # Setup + Options Flow (Kp, Ki, Presets, Sensor)
├── const.py           # DOMAIN + Config-Keys + PRESET_VACATION
├── diagnostics.py     # HA Diagnostik-Export
├── manifest.json      # HACS/HA Metadata (v0.6.0)
├── parameters.py      # Zentrale Parameter-Defaults (RegulationConfig + PresetConfig)
├── regulation.py      # Feedforward + PI Engine (FeedforwardPiRegulator)
├── strings.json       # UI-Strings (Fallback)
└── translations/
    ├── en.json        # Englisch
    └── de.json        # Deutsch

tests/
├── __init__.py
└── test_regulation.py # 23 Unit Tests (importiert ohne HA-Dependency)
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
| Target Range | 5–30°C (dynamisch ≥ boost_target) | parameters.py / _build_config() |
| Frost Protection | 5°C | parameters.py |
| Update Interval | 60s | __init__.py / parameters.py |

### Presets

| Parameter | Wert | Datei |
|-----------|------|-------|
| Comfort Target | 20.0°C (Default) | entry.options / NumberEntität |
| Eco Target | 19.0°C | parameters.py (PresetConfig), NumberEntität |
| Boost Target | 25.0°C | parameters.py (PresetConfig), NumberEntität |
| Boost Duration | 30 min | parameters.py (PresetConfig), Options Flow |
| Away Target | 16.0°C | parameters.py (PresetConfig), NumberEntität |
| Vacation Target | 5.0°C | parameters.py (PresetConfig), NumberEntität |
| Target Range | 5–30°C | alle Preset-Temperaturen |

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

1. **Nur ein Testraum validiert** – Default-Parameter müssen in anderen Räumen geprüft werden.
2. **Tado X spezifisch** – Nicht getestet mit anderen Tado-Modellen.
3. **Batterie-Monitoring** – Kein direktes Feedback über Batterie-Zustand des TRV.
4. **Follow Tado Input** – Schwellenwert (1.5°C, 30s Grace) könnte in Extremsituationen false positives produzieren; in der Praxis bisher nicht beobachtet.

---

## Nächster Meilenstein: M5 – Multi-Room & Community

- Validierung der Default-Parameter in verschiedenen Raumtypen.
- Dokumentation erweitern basierend auf Community-Erfahrungen.
- Optional: Raum-Gruppierung (Zonen).

---

## Test-Infrastruktur

- **Framework:** pytest
- **Import-Trick:** `importlib.util.spec_from_file_location` umgeht `__init__.py` (HA-Abhängigkeit).
- **Ausführen:** `cd /path/to/repo && python -m pytest tests/ -v`
- **Aktuell:** 23 Tests in 6 Klassen (Feedforward, PI, AntiWindup, Safety, FullScenario, PresetConfig).
