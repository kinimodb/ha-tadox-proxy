# CLAUDE.md – Projektanweisungen

## Projekt

Home Assistant Custom Component (HACS) – Proxy-Thermostat für Tado X TRVs.
Feedforward + PI-Regelung mit externem Raumsensor.

## Sprache

- Kommunikation mit dem Nutzer: **Deutsch**
- Code, Kommentare, Commits, Doku: **Englisch**

## Architektur

```
HA-frei (direkt testbar):          HA-Bridge:
parameters.py → regulation.py      climate.py (Entity, Mixin-Komposition)
climate_controllers.py             ├─ climate_regulation.py (RegulationMixin)
                                   ├─ climate_presets.py (PresetMixin)
                                   └─ __init__.py (Coordinator), config_flow.py
```

Neue Features immer erst in den HA-freien Modulen (`parameters.py`, `regulation.py`, `climate_controllers.py` – testbar ohne HA), dann in den HA-Modulen verdrahten.

### Schlüsseldateien

- `parameters.py` – Defaults (RegulationConfig, PresetConfig, CorrectionTuning, BehaviourConfig)
- `regulation.py` – Feedforward + PI Engine (HA-unabhängig)
- `climate_controllers.py` – Window/Presence/Follow-Zustandsmaschinen (HA-unabhängig)
- `climate.py` – HA ClimateEntity: Properties, Lifecycle, Config, Follow-Tado
- `climate_regulation.py` – RegulationMixin: Regelzyklus, Rate-Limiting, TRV-Kommandos
- `climate_presets.py` – PresetMixin: Preset-Wechsel, Boost-Timer, Window/Presence-Aktionen
- `number.py` – NumberEntity für Preset-Temperaturen
- `sensor.py` – Boost-Restzeit-Sensor
- `binary_sensor.py` – Sensor-Degraded-Diagnose
- `switch.py` – Toggle-Features (z.B. Follow Tado Input)
- `config_flow.py` – Setup + Options Flow
- `diagnostics.py` – HA-Diagnostics-Export
- `const.py` – DOMAIN, Config-Keys, Custom Preset Names, `safe_float()`
- `strings.json` + `translations/` – UI-Texte (EN + DE)
- `manifest.json` – Version, Metadata

## Tests

```bash
python -m pytest tests/ -v
```

- Tests umgehen `__init__.py` via `importlib.util.spec_from_file_location` (HA-Abhängigkeit).
- **Vor jedem Commit müssen alle Tests grün sein.**

## Commit-Konventionen

```
feat: …     # Neues Feature
fix: …      # Bugfix
docs: …     # Nur Dokumentation
refactor: … # Code-Umbau ohne Funktionsänderung
```

## Versionierung

Vor jedem Release synchron aktualisieren:
- `manifest.json` → `"version": "x.y.z"`
- `README.md` → Version-Badge URL

## Dokumentation

Bei Feature-Änderungen aktualisieren:
- **README.md** – User-facing Doku
- **TUNING.md** – Bei Regelungs-/Parameter-Änderungen

## Git-Branching

| Branch | Zweck |
|--------|-------|
| `main` | Einziger Langzeit-Branch. **Nie direkt pushen** – nur via PR. |
| `claude/*` | Kurzlebige Feature-Branches von Claude Code, basierend auf `main`. |

Nach dem Merge den Feature-Branch löschen. Es gibt keinen `dev`-Branch –
PRs immer gegen `main`.

## Workflow

1. Feature-Branch von `main` erstellen: `claude/<beschreibung>`
2. Implementieren, Tests grün
3. Commit & Push
4. PR gegen `main` erstellen
5. Merge-Anleitung und Release-Notes ausgeben (siehe `/deliver`)

Für Details zu PR-Merging, Releases und Hotfix-Workflows: siehe `CONTRIBUTING.md`.

## Regelungs-Engine

Lies `.claude/docs/regulation-concepts.md` bevor du an `regulation.py` oder `parameters.py` arbeitest.

## Brand-Assets

Lies `.claude/docs/brand-assets.md` bevor du an Logos/Icons arbeitest.

## Bekannte Eigenheiten

- **iOS Companion App**: `ha-entity-picker` crasht im iOS WebView (HA-Bug, nicht unser Code). Workaround: Browser nutzen.
- **Rate Limiting** (180s): Batterieschonung für Tado X TRVs – nicht verkürzen.
