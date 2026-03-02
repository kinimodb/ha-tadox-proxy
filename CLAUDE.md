# CLAUDE.md – Projektanweisungen für Claude Code

## Projekt

Home Assistant Custom Component (HACS) – Proxy-Thermostat für Tado X TRVs.
Nutzt Feedforward + PI Regelung mit externem Raumsensor.

## Sprache

- Code und Kommentare: **Englisch**
- Dokumentation (README, ROADMAP, TUNING, CONTEXT): **Deutsch** (Zielgruppe: deutschsprachige Nutzer)
- Commit-Messages: **Englisch**
- Kommunikation mit dem Nutzer: **Deutsch**

## Architektur

```
parameters.py  →  regulation.py  →  climate.py  ←  __init__.py (Coordinator)
(keine HA-Dep)    (keine HA-Dep)    (HA-Bridge)     config_flow.py
                                                     const.py
```

- `parameters.py` und `regulation.py` sind HA-unabhängig und direkt testbar.
- `climate.py` ist die Brücke zwischen Regulation-Engine und Home Assistant.
- Neue Features immer erst in `parameters.py` / `regulation.py` (testbar), dann `climate.py`.

## Tests

```bash
python -m pytest tests/ -v
```

- Tests importieren Module via `importlib.util.spec_from_file_location` um `__init__.py` (HA-Abhängigkeit) zu umgehen.
- Vor jedem Commit: Tests müssen grün sein.
- Aktuell: 23 Tests in `tests/test_regulation.py`.

## Wichtige Dateien

| Datei | Zweck | Wann ändern? |
|-------|-------|-------------|
| `parameters.py` | Alle Defaults (RegulationConfig, PresetConfig, CorrectionTuning) | Bei neuen Parametern oder Default-Änderungen |
| `regulation.py` | Feedforward + PI Engine | Bei Regelungs-Änderungen |
| `climate.py` | HA ClimateEntity, Presets, Boost-Timer, State Restore | Bei UI/HA-Features |
| `config_flow.py` | Setup + Options Flow | Bei neuen konfigurierbaren Parametern |
| `const.py` | DOMAIN, Config-Keys, Custom Preset Names | Bei neuen Config-Keys |
| `strings.json` + `translations/` | UI-Texte (EN + DE) | Bei neuen UI-Elementen |
| `manifest.json` | Version, Metadata | Bei jedem Release (Version bumpen) |

## Dokumentation

Bei jeder Feature-Änderung diese Dateien aktualisieren:

1. **ROADMAP.md** – Meilenstein-Status, Changelog-Eintrag
2. **CONTEXT.md** – Technischer Kontext für Session-Übergaben
3. **README.md** – User-facing Dokumentation
4. **TUNING.md** – Bei Regelungs-/Parameter-Änderungen

## Commit-Konventionen

```
feat: kurze Beschreibung    # Neues Feature
fix: kurze Beschreibung     # Bugfix
docs: kurze Beschreibung    # Nur Dokumentation
refactor: kurze Beschreibung # Code-Umbau ohne Funktionsänderung
```

## Regelungs-Engine: Wichtige Konzepte

- **Feedforward** kompensiert den Tado-Sensor-Offset (Heizkörper vs. Raum) sofort.
- **PI-Korrektur** ist bewusst klein (Kp=0.8, Ki=0.003) – nur für Restfehler.
- **Integral Deadband** (0.3°C): Integral sammelt NUR nahe am Ziel. Verhindert Overshoot.
- **Rate Limiting** (180s): Batterieschonung für Tado X TRVs.
- **Anti-Windup**: Dual (Sättigungs-Block + Deadband-Gating mit Decay).

## Aktueller Stand (v0.5.0)

- M1 (Core Stability) ✅
- M2 (Advanced Configuration) ✅
- M3 (Presets: Comfort, Eco, Boost, Away, Vacation) ✅
- M4 (Externe Trigger: Fensterkontakt, Präsenz) – nächster Meilenstein
- Testraum läuft stabil (±0.3–0.5°C um Sollwert, 11h+ Nachtbetrieb bestätigt)
