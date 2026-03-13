# TADOX-proxy – Roadmap

**Mission:** Ein lokaler Proxy-Regler für Tado X, der den internen Offset-Hitzestau der Hardware durch Feedforward-Kompensation eliminiert und präzise auf externe Raumsensoren regelt.

## Status (v0.10.2)

- **Architektur:** Feedforward + PI (arbeitet MIT Tados internem Regler).
- **Technik:** Python `async`, HA DataUpdateCoordinator, Number- + Switch-Plattformen.
- **Phase:** Pre-Release – ein Raum läuft stabil (±0.3–0.5°C, 11h+ Nachtbetrieb bestätigt).
- **Presets:** Comfort, Eco, Boost (mit Timer), Away, Frostschutz – alle als NumberEntität steuerbar.
- **Externe Trigger:** Fensterkontakt (→ Frostschutz) + Präsenzsensor (→ Abwesend) vollständig implementiert.
- **Sensor-Resilienz:** Last-Valid-Bridging bei kurzen Sensorausfällen, Timer-Revalidierung.
- **Window Close Delay:** Konfigurierbarer Restore-Delay nach Fensterschließen verhindert aggressive Heiz-Bursts.

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
- [x] Preset-Temperaturen über NumberEntitäten konfigurierbar (aus Options Flow entfernt in v0.8.13).
- [x] Boost-Timer mit `async_call_later` + Auto-Revert zum vorherigen Preset.
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

- [x] **Fensterkontakt:** Wechsel auf Frostschutz-Preset nach konfigurierbarer Verzögerung bei "offen", Restore bei "zu" mit konfigurierbarem Close-Delay.
- [x] **Präsenz-Sensor:** Auto-Wechsel auf Away-Preset nach konfigurierbarer Verzögerung, Restore bei Rückkehr.
- [x] Beide Trigger als optionale Entity-Selektoren im Options Flow mit separaten Delay-Feldern.
- [x] Fenster und Präsenz unabhängig: Fenster steuert Preset (Frostschutz), Präsenz steuert Preset (Abwesend).
- [x] Diagnose-Attribute `window_open_active` + `presence_away_active`.
- [x] Übersetzungen (DE/EN) für alle neuen Felder.

## M4.2 – Sensor-Resilienz (v0.10.0) ✅

**Ziel:** Robustheit bei kurzen Sensorausfällen verbessern.

- [x] **Last-Valid-Bridging:** Bei `unavailable`/`unknown` des externen Temperatursensors wird der letzte gültige Wert für eine konfigurierbare Grace-Zeit (Default 300s) weiterverwendet.
- [x] **Timer-Revalidierung:** Fenster-/Präsenz-Aktionen prüfen vor Ausführung den aktuellen Sensorzustand. Glitches lösen keine ungewollten Presetwechsel mehr aus.
- [x] **Diagnostik:** Neue Attribute `sensor_degraded`, `room_temp_last_valid_c`, `room_temp_last_valid_age_s` in den Entity-Attributen.
- [x] **Tests:** 10 neue Tests für Sensor-Grace-Logik.

## M5 – Multi-Room & Community (→ v1.0.0)

**Ziel:** Erweiterung und Community-Feedback.

- [ ] Validierung der Default-Parameter in verschiedenen Raumtypen.
- [ ] Logo in HACS sichtbar (PR an home-assistant/brands).
- [ ] Community-Forum Vorstellung.
- [ ] Dokumentation erweitern basierend auf Community-Erfahrungen.
- [ ] Optional: Raum-Gruppierung (Zonen).

---

## Changelog

### v0.10.1
- **Bugfix:** `_async_send_to_tado` erhält jetzt einen 10-Sekunden-Timeout via `asyncio.timeout`. Hängende Tado-Entities blockieren den Regelzyklus nicht mehr indefinit.
- **Bugfix:** `except Exception:` in `_async_send_to_tado` auf `except (TimeoutError, HomeAssistantError):` eingegrenzt – `asyncio.CancelledError` wird beim Shutdown nicht mehr verschluckt.
- **Bugfix:** Drei `except: pass` Blöcke in `async_update_data` (Coordinator) loggen jetzt als `WARNING` statt Sensor-Parse-Fehler still zu verwerfen.
- **Bugfix:** Dead Code entfernt: `RegulationState.last_room_temp_c` wurde jede Zykle gesetzt aber nirgendwo gelesen.
- **Refactor:** Magic Number `0.01` in `regulation.py` → benannte Konstante `_SATURATION_TOLERANCE_C`.
- **Refactor:** `Optional` Import in `regulation.py` entfernt (war nach Entfernen des dead fields obsolet).
- **Robustheit:** `_async_regulation_cycle` prüft `coordinator.last_update_success` und bricht früh ab bei stale Coordinator-Daten.
- **Infrastruktur:** GitHub Actions CI (`tests.yml`) – Tests laufen automatisch bei jedem Push/PR.
- **Infrastruktur:** `pyproject.toml` – Ruff-Lint-Konfiguration (E/F/W/I/UP) + Pytest-Pfade.
- **Tests:** 6 neue Edge-Case-Tests: negativer Feedforward-Offset, Zeit-Delta-Extreme, Integral-Decay-Verifikation.

### v0.10.0
- **Feature:** Sensor-Resilienz – bei kurzen Sensorausfällen (≤5 min) wird der letzte gültige Messwert weiterverwendet statt die Regelung zu unterbrechen. Konfigurierbar via `sensor_grace_s`.
- **Feature:** Timer-Revalidierung – Fenster- und Präsenz-Aktionen prüfen vor Ausführung nochmals den aktuellen Sensorzustand. Verhindert ungewollte Presetwechsel durch kurze Sensor-Glitches.
- **Feature:** Neue Diagnose-Attribute: `sensor_degraded`, `room_temp_last_valid_c`, `room_temp_last_valid_age_s`.
- **Tests:** 10 neue Tests für Sensor-Grace-Logik.

### v0.9.8
- **Cleanup:** Blueprint (Zeitplan) entfernt – die Scheduler-Card (Drittanbieter) funktioniert wieder und ist die empfohlene Lösung für Tagesabläufe.
- **Refactor:** Alle Preset-Default-Werte zentralisiert in `PresetConfig` (parameters.py). Inkonsistenter Comfort-Fallback (19°C statt 20°C) korrigiert.
- **Docs:** Default-Werte in README und CONTEXT.md an parameters.py angeglichen (Eco 17°C, Away 17°C, Frostschutz 7°C).

### v0.9.6
- **Bugfix:** Fenster-Schließ-Logik: Frostschutz wird nicht mehr als „vorheriges Preset" gespeichert. Nach HA-Restart mit offenem Fenster blieb der Nutzer im Frostschutz gefangen, weil der WindowAutomationController-State (is_active, saved preset) nicht über Restarts persistiert wird. Drei Guards (Startup, Save, Restore) verhindern das Problem jetzt an mehreren Stellen (defense-in-depth).

### v0.9.0
- **Feature:** Window Close Delay – konfigurierbarer Restore-Delay (0–600s, Standard: 120s) nach dem Schließen des Fensters. Verhindert aggressive Heiz-Bursts nach dem Stoßlüften, da die thermische Masse die Raumtemperatur teilweise von selbst ausgleicht.
- **Feature:** Neues Diagnose-Attribut `window_close_delay_active`.
- **Edge Cases:** Window-Reopen während Close-Delay bleibt im Frostschutz (kein erneuter Open-Delay). Manueller Preset-/Temperaturwechsel während Close-Delay cancelt den Timer.

### v0.8.13
- **Bugfix:** Boost kehrt jetzt zum vorherigen Preset zurück statt immer zu Komfort.
- **Bugfix:** Boost-Timer wird bei Präsenz-Away und Fenster-Öffnung korrekt gecancelt (verhindert Mode-Flip).
- **Bugfix:** Sensor-State (Fenster/Präsenz) wird nach HA-Restart/Reload initial evaluiert – kein Warten auf nächste Änderung mehr nötig.
- **Feature:** Preset-Icons vollständig: Komfort (Sofa), Eco (Blatt), Boost (Rakete), Abwesend (Haus-Export), Frostschutz (Schneeflocke), Manuell (Hand).
- **Cleanup:** Preset-Temperaturen aus Options Flow entfernt – Konfiguration ausschließlich über NumberEntitäten (Steuerelemente). Bestehende Werte bleiben erhalten.

### v0.8.12
- **Bugfix:** Options Flow Reload Race Condition behoben (v0.8.1). Der Reload wird jetzt über einen `update_listener` in `__init__.py` ausgelöst, der garantiert NACH dem Speichern feuert.
- **Bugfix:** Redundante Backend-Entity-Registry-Validierung aus Config/Options Flow entfernt. EntitySelector validiert bereits im Frontend; die Backend-Checks verursachten false negatives in der Companion App.
- **Bekannt:** iOS Companion App EntitySelector-Crash (`ReferenceError: elementId` in `ha-entity-picker`) bleibt offen – das ist ein HA-Frontend-Bug, kein Tado X Proxy-Bug. Workaround: Konfiguration im Browser durchführen. Mehrere Lösungsansätze wurden evaluiert (Two-Step Config Flow, SelectSelector-Dropdown) – alle verworfen wegen UX-Nachteilen.
- **Docs:** Alle Dokumentation aktualisiert für saubere Übergabe.

### v0.8.1
- **Bugfix:** Race Condition beim Options-Flow-Reload behoben. Der Reload wurde bisher gestartet BEVOR die neuen Options gespeichert waren → Integration lud mit veralteten Werten neu → Fenster-/Präsenzsensoren funktionierten erst nach erneutem Speichern. Jetzt wird der Reload über einen `update_listener` in `__init__.py` ausgelöst, der garantiert NACH dem Speichern feuert.

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
- **Breaking:** Eco nutzt jetzt eine feste Zieltemperatur (Default 17°C) statt eines Offsets von der Comfort-Temperatur. Bestehende `eco_offset`-Einstellungen gehen verloren.

### v0.5.0
- **Feature:** Presets – Comfort, Eco, Boost, Away, Frostschutz.
- Eco: konfigurierbarer Offset (Default −2°C) von der Komfort-Temperatur.
- Boost: temporär max. Temperatur mit Auto-Revert-Timer (Default 30 min).
- Away: feste niedrige Temperatur (Default 17°C).
- Frostschutz: Minimale Temperatur (Default 7°C).
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
