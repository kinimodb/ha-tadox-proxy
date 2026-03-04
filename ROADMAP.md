# TADOX-proxy – Roadmap

**Mission:** Ein lokaler Proxy-Regler für Tado X, der den internen Offset-Hitzestau der Hardware durch Feedforward-Kompensation eliminiert und präzise auf externe Raumsensoren regelt.

## Status (v0.8.0)

- **Architektur:** Feedforward + PI (arbeitet MIT Tados internem Regler).
- **Technik:** Python `async`, HA DataUpdateCoordinator, Number- + Switch-Plattformen.
- **Phase:** Beta-Test – ein Raum läuft stabil (±0.3–0.5°C, 11h+ Nachtbetrieb bestätigt).
- **Presets:** Comfort, Eco, Boost (mit Timer), Away, Frostschutz – alle als NumberEntität steuerbar.
- **Externe Trigger:** Fensterkontakt (→ Frostschutz) + Präsenzsensor (→ Abwesend) vollständig implementiert.

---

## M1 – Core Stability & Validation (v0.4.0–v0.4.1) ✅

**Ziel:** Stabile, zuverlässige Kernregelung.

- [x] Feedforward-Kompensation für Tado-Sensor-Offset.
- [x] PI-Korrektur mit echtem Anti-Windup (Integral friert bei Sättigung ein).
- [x] Integral Deadband (v0.4.1) – Integral sammelt nur bei |error| < 0.3°C, decayed außerhalb.
- [x] Rate Limiting (180s) mit Batterieschonung + Urgent-Decrease-Bypass.
- [x] Safety Clamping (5–25°C).
- [x] Unit Tests für Regulation-Engine (16 Tests).
- [x] Real-World-Test Testraum: Aufheizen, Halten, Nachtbetrieb bestanden.
- [ ] Real-World-Test in weiteren Räumen (nach M3).

## M2 – Advanced Configuration ✅

**Ziel:** Parameter pro Thermostat anpassbar.

- [x] Options Flow: Kp, Ki über "Konfigurieren" einstellbar.
- [x] Options Flow: Externer Sensor wechselbar.
- [x] Live-Reload: Parameter-Änderungen ohne Neustart (OptionsFlowWithReload).

## M3 – Presets & Modes (v0.5.0) ✅

**Ziel:** Verschiedene Betriebsmodi für den Alltag.

- [x] `ClimateEntityFeature.PRESET_MODE` aktiviert.
- [x] 5 Presets: Comfort (Default), Eco, Boost (Timer), Away, Frostschutz.
- [x] Preset-Temperaturen über Options Flow konfigurierbar.
- [x] Boost-Timer mit `async_call_later` + Auto-Revert zu Comfort.
- [x] Preset wird per `RestoreEntity` über HA-Neustarts hinweg gespeichert (außer Boost → revert).
- [x] `effective_setpoint_c` als neues Diagnose-Attribut.
- [x] Übersetzungen (DE/EN) für alle Preset-Parameter.
- [x] 23 Unit Tests (7 neue für PresetConfig + Setpoint-Berechnung).

## M3.1 – Preset-Entitäten & Bugfix (v0.6.0) ✅

**Ziel:** Preset-Temperaturen als eigenständige HA-Entitäten, bugfreie UI-Darstellung.

- [x] **Bugfix:** `target_temperature` zeigt jetzt immer den aktiv gültigen Sollwert (inkl. Preset).
- [x] Slider-Nutzung während aktivem Preset wechselt automatisch zu Comfort.
- [x] Eco: feste Temperatur statt Offset von Comfort (Breaking Change).
- [x] 5 NumberEntitäten (Boost, Comfort, Eco, Away, Frostschutz) – per Dashboard/Automation steuerbar.
- [x] SwitchEntität "Physischem Thermostat folgen" – übernimmt Temperatur bei physischer Änderung.
- [x] Config-Entry-Listener: NumberEntitäten aktualisieren Climate-Entity sofort ohne Full-Reload.
- [x] `max_target_c` dynamisch ≥ `boost_target_c` (Boost > 25°C möglich).
- [x] Alle Preset-Temperaturen: Range 5–30°C.
- [x] Übersetzungen (DE/EN) für Number- und Switch-Entitäten.
- [x] 23 Unit Tests weiterhin grün.

## M4 – Externe Trigger (v0.7.0) ✅

**Ziel:** Automatische Reaktion auf Umgebungsbedingungen.

- [x] **Fensterkontakt:** Wechsel auf Frostschutz-Preset nach konfigurierbarer Verzögerung bei "offen", Restore bei "zu".
- [x] **Präsenz-Sensor:** Auto-Wechsel auf Away-Preset nach konfigurierbarer Verzögerung, Restore bei Rückkehr.
- [x] Beide Trigger als optionale Entity-Selektoren im Options Flow mit separaten Delay-Feldern.
- [x] Fenster und Präsenz unabhängig: Fenster steuert Preset (Frostschutz), Präsenz steuert Preset (Abwesend).
- [x] Diagnose-Attribute `window_open_active` + `presence_away_active`.
- [x] Übersetzungen (DE/EN) für alle neuen Felder.

## M4.1 – UX-Polish & Bugfixes (v0.8.2) ✅

**Ziel:** Stabilität und UX-Verbesserungen nach Beta-Feedback.

- [x] **Bugfix:** Options-Flow-Reload Race-Condition behoben – Update-Listener in `__init__.py` statt fragiles `async_call_later(0.5)` in config_flow.
- [x] **Bugfix:** Fenster-/Präsenz-Sensoren funktionieren jetzt sofort nach Einrichtung (kein doppeltes Speichern mehr nötig).
- [x] **Bugfix:** Initiale Sensor-Evaluierung ohne Delay – nach Restart/Reload wird der aktuelle Zustand sofort übernommen.
- [x] **Bugfix:** Boost-Rückkehr zum korrekten vorherigen Preset (statt immer Comfort). Re-Boost während Boost behält Original-Preset bei.
- [x] **Feature:** Icons für alle Presets: Comfort=Sofa, Eco=Blatt, Boost=Flamme, Away=Pfeil, Frostschutz=Schneeflocke, Manuell=Hand.
- [x] **Feature:** Number- und Switch-Entitäten als `EntityCategory.CONFIG` – korrekte Gruppierung im HA Device-Panel.
- [x] **Fix:** Falscher Docstring in switch.py korrigiert (Follow-Tado → PRESET_NONE, nicht COMFORT).

## M5 – Multi-Room & Community

**Ziel:** Erweiterung und Community-Feedback.

- [ ] Validierung der Default-Parameter in verschiedenen Raumtypen.
- [ ] Dokumentation erweitern basierend auf Community-Erfahrungen.
- [ ] Optional: Raum-Gruppierung (Zonen).

---

## Changelog

### v0.8.2
- **Bugfix:** Options-Flow-Reload Race-Condition behoben – Fenster-/Präsenz-Sensoren funktionieren jetzt sofort nach Einrichtung.
- **Bugfix:** Initiale Sensor-Evaluierung ohne Delay – nach Restart/Reload wird offenes Fenster/Abwesenheit sofort übernommen.
- **Bugfix:** Boost-Rückkehr zum korrekten vorherigen Preset (nicht mehr immer Comfort). Re-Boost behält Original bei.
- **Feature:** Icons für alle 6 Presets (Sofa, Blatt, Flamme, Pfeil, Schneeflocke, Hand).
- **Feature:** Number-/Switch-Entitäten als `EntityCategory.CONFIG` – korrekte Gruppierung im Device-Panel.

### v0.8.0
- **Breaking:** Preset "Urlaub/Vacation" umbenannt in "Frostschutz/Frost protection". Config-Key: `frost_protection_target` (vorher `vacation_target`). Bestehende Einstellungen müssen ggf. neu gesetzt werden.
- **Feature:** Fensterkontakt wechselt bei Öffnung auf **Frostschutz-Preset** (vorher: HVAC AUS). Temperatur wird auf Frostschutz-Niveau gesenkt statt komplett abzuschalten.
- **Feature:** Preset-Icons: Frostschutz = Schneeflocke (`mdi:snowflake`), Manuell = Hand (`mdi:hand-back-right`).
- **Feature:** Entitäten-Sortierung warm→kalt: Boost, Komfort, Eco, Abwesend, Frostschutz.
- **Docs:** Alle Dokumentation aktualisiert (README, ROADMAP, CONTEXT, CLAUDE.md).

### v0.7.0
- **Feature:** Fensterkontakt-Unterstützung – optionaler `binary_sensor.*` mit konfigurierbarer Verzögerung (0–3600s). Bei offenem Fenster wird auf Frostschutz-Preset gewechselt, bei Schließen automatisch wiederhergestellt.
- **Feature:** Präsenzsensor-Unterstützung – optionaler `binary_sensor.*` mit konfigurierbarer Verzögerung (0–7200s). Bei Abwesenheit wird auf Away-Preset umgeschaltet, bei Rückkehr das vorherige Preset wiederhergestellt.
- **Feature:** Diagnose-Attribute `window_open_active` + `presence_away_active`.
- **Docs:** README vollständig neu geschrieben (Deutsch), alle Features dokumentiert.

### v0.6.0
- **Bugfix:** Zieltemperatur im HA-UI spiegelt jetzt korrekt das aktive Preset wider.
- **Bugfix:** Slider-Nutzung während aktivem Preset kehrt automatisch zu Comfort zurück.
- **Feature:** 5 neue NumberEntitäten (Comfort-, Eco-, Boost-, Away-, Urlaub-Temperatur) – steuerbar per Dashboard, Automation und Service `number.set_value`.
- **Feature:** Switch "Physischem Thermostat folgen" – HA übernimmt Temperaturänderungen am physischen Tado-Gerät.
- **Feature:** Boost-Temperatur kann jetzt > 25°C konfiguriert werden (max. 30°C).
- **Breaking:** Eco nutzt jetzt eine feste Zieltemperatur (Default 19°C) statt eines Offsets von der Comfort-Temperatur. Bestehende `eco_offset`-Einstellungen gehen verloren.

### v0.5.0
- **Feature:** Presets – Comfort, Eco, Boost, Away, Frostschutz.
- Eco: konfigurierbarer Offset (Default −2°C) von der Komfort-Temperatur.
- Boost: temporär max. Temperatur mit Auto-Revert-Timer (Default 30 min).
- Away: feste niedrige Temperatur (Default 16°C).
- Frostschutz: Minimale Temperatur (Default 5°C).
- Alle Preset-Temperaturen über Options Flow einstellbar.
- `effective_setpoint_c` Diagnose-Attribut zeigt den tatsächlich genutzten Sollwert.
- 23 Unit Tests (7 neue).

### v0.4.1
- **Fix:** Integral Deadband mit Decay – verhindert Overshoot beim Aufheizen.
- Overshoot reduziert von 0.6°C (v0.4.0) auf 0.3°C.
- 16 Unit Tests (inkl. Overshoot-Regression-Test).

### v0.4.0
- **Komplett-Rewrite:** PID (Kp=7.0) ersetzt durch Feedforward + PI (Kp=0.8, Ki=0.003).
- Neue Architektur arbeitet MIT Tados internem Regler statt dagegen.
- Ungenutzte Parameter entfernt, Code aufgeräumt.
- Unit Tests eingeführt.
- `@callback` + `async` Bug behoben.
