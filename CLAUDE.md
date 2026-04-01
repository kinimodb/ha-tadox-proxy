# CLAUDE.md – Projektanweisungen

## Projekt

Home Assistant Custom Component (HACS) – Proxy-Thermostat für Tado X TRVs.
Feedforward + PI-Regelung mit externem Raumsensor.

## Sprache

- Kommunikation mit dem Nutzer: **Deutsch**
- Code, Kommentare, Commits, Doku: **Englisch**

## Architektur

```
parameters.py  →  regulation.py  →  climate.py  ←  __init__.py (Coordinator)
(keine HA-Dep)    (keine HA-Dep)    (HA-Bridge)     config_flow.py / const.py
```

Neue Features immer erst in `parameters.py` / `regulation.py` (testbar, keine HA-Abhängigkeit), dann `climate.py`.

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

Versionen in `manifest.json` und README-Badge müssen synchron sein.

## Dokumentation

Bei Feature-Änderungen aktualisieren:
- **README.md** – User-facing Doku
- **TUNING.md** – Bei Regelungs-/Parameter-Änderungen

## Git-Branching

| Branch | Zweck |
|--------|-------|
| `main` | Stabile Releases. **Nie direkt pushen** – nur via PR. |
| `dev` | Entwicklung & Integration. |
| `claude/*` | Kurzlebige Feature-Branches von Claude Code. |

Feature-Branches basieren auf `dev`, Hotfixes auf `main`.
PRs gegen `dev` (Features) oder `main` (Hotfixes).

## Workflow

1. Feature-Branch erstellen: `claude/<beschreibung>`
2. Implementieren, Tests grün
3. Commit & Push
4. PR erstellen (gegen `dev` oder `main`)
5. Merge-Anleitung und Release-Notes ausgeben (siehe `/deliver`)

Für Details zu PR-Merging, Releases und Hotfix-Workflows: siehe `CONTRIBUTING.md`.

## Regelungs-Engine

Lies `.claude/docs/regulation-concepts.md` bevor du an `regulation.py` oder `parameters.py` arbeitest.

## Brand-Assets

Lies `.claude/docs/brand-assets.md` bevor du an Logos/Icons arbeitest.

## Bekannte Eigenheiten

- **iOS Companion App**: `ha-entity-picker` crasht im iOS WebView (HA-Bug, nicht unser Code). Workaround: Browser nutzen.
- **Rate Limiting** (180s): Batterieschonung für Tado X TRVs – nicht verkürzen.
