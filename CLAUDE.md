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
| `number.py` | NumberEntity für Preset-Temperaturen (Boost, Comfort, Eco, Away, Frostschutz) | Bei neuen Preset-Parametern als Entitäten |
| `switch.py` | SwitchEntity für optionale Verhaltensflags (z.B. Follow Tado Input) | Bei neuen Toggle-Features |
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

## Lieferung am Ende einer Feature-Session

Nach vollständiger Implementierung (Tests grün, Commit, Push):
1. **Merge-Anleitung** ausgeben: Step-by-step, welche GitHub-Schritte nötig sind (PR erstellen, Review, Merge, Release).
2. **Release-Beschreibung** (Deutsch) nach folgendem Format ausgeben – copy-pasteable:

```
## v0.X.Y – Kurztitel

### Neues
- Stichpunkt 1
- Stichpunkt 2

### Fixes
- Stichpunkt

### Breaking Changes (falls vorhanden)
- Was sich ändert und was Nutzer tun müssen

### Installation
Über HACS → Integration → Tado X Proxy → Update.
```

## Aktueller Stand (v0.8.12)

- M1 (Core Stability) ✅
- M2 (Advanced Configuration) ✅
- M3 (Presets: Comfort, Eco, Boost, Away, Frostschutz) ✅
- M3.1 (Preset-Setpoint-Fix + Number/Switch-Entitäten) ✅
- M4 (Externe Trigger: Fensterkontakt → Frostschutz, Präsenzsensor → Away) ✅
- M4.1 (UX-Polish: Frostschutz-Umbenennung, Icons, Sortierung) ✅
- M5 (Multi-Room & Community) – nächster Meilenstein
- Testraum läuft stabil (±0.3–0.5°C um Sollwert, 11h+ Nachtbetrieb bestätigt)

## Bekannte offene Bugs (Priorisiert)

1. ~~**Options Flow Reload Race Condition** – behoben in v0.8.1~~
2. **iOS Companion App: EntitySelector-Crash** – Die `ha-entity-picker` Web Component verursacht einen `ReferenceError: elementId` im iOS WebView. Das ist ein bekannter HA-Frontend-Bug (nicht unser Code). **Workaround:** Konfiguration über den Browser statt die iOS App durchführen. Mehrere Lösungsansätze wurden getestet (Registry-Validierung entfernen, Two-Step-Flow, SelectSelector-Dropdown) – keiner war zufriedenstellend. Betrifft Config Flow und Options Flow.
3. **Sensor-State nach Restart nicht evaluiert** – Nach Neustart wird der aktuelle Zustand von Fenster-/Präsenzsensoren nicht geprüft, nur auf zukünftige Änderungen reagiert.
4. **Nach Boost: Wechsel in Komfort statt vorheriges Preset** – Boost sollte zum vorherigen Preset zurückkehren, nicht immer zu Komfort.
5. **Boost-Timer bei Away nicht gecancelt** – Wenn Präsenz-Away während Boost aktiviert wird, läuft der Timer weiter und überschreibt später das Away-Preset.
6. **Frostschutz/Manuell Icons** – nur teilweise umgesetzt (nur in `icon` property, nicht in HA-Preset-Icons).
7. **Sortierung Steuerelemente** – nicht/nur teilweise umgesetzt.
8. **Preset-Temps doppelt konfigurierbar** – sowohl in Options Flow als auch in Number-Entitäten. Entscheidung ausstehend ob Options Flow Preset-Temps entfernt werden sollen.
