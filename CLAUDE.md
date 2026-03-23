# CLAUDE.md – Projektanweisungen für Claude Code

## Projekt

Home Assistant Custom Component (HACS) – Proxy-Thermostat für Tado X TRVs.
Nutzt Feedforward + PI Regelung mit externem Raumsensor.

## Sprache

- Code und Kommentare: **Englisch**
- Dokumentation: **Englisch** (README.md, TUNING.md)
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
- Aktuell: 90 Tests in `tests/test_regulation.py`, `tests/test_controllers.py`, `tests/test_sensor_resilience.py`.

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

1. **README.md** – User-facing Dokumentation
2. **TUNING.md** – Bei Regelungs-/Parameter-Änderungen

## Versionierung

Bei jedem Release müssen **alle** Versionsnummern synchron aktualisiert werden:

| Datei | Feld/Stelle |
|-------|-------------|
| `manifest.json` | `"version": "x.y.z"` |
| `README.md` | Version-Badge (`img.shields.io/badge/version-x.y.z-blue`) |

**Wichtig:** Vor dem Commit prüfen, dass die Version überall konsistent ist.

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

## Aktueller Stand (v1.0.9)

- Alle Kernfeatures stabil (Presets, Externe Trigger, Sensor-Resilienz, Multi-Room)
- HVAC OFF wird an den Tado-TRV weitergeleitet; Fehler beim Senden werden korrekt behandelt
- Frontend-Polish: icons.json, Sektionen im Options-Flow, NumberSelector für alle Zahlenfelder
- Icon-Verbesserungen: neutrales Icon für Heat-Modus, Feuer-Icon nur bei aktiver Heizung
- Bereinigung: ungenutzter Root-`brand/`-Ordner entfernt, CLAUDE.md-Dokumentation überarbeitet
- 141 Tests grün, CI aktiv

## Bekannte offene Bugs

1. **iOS Companion App: EntitySelector-Crash** – HA-Frontend-Bug in `ha-entity-picker` (`ReferenceError: elementId` im iOS WebView). Nicht unser Code. **Workaround:** Konfiguration über den Browser.

---

## Git-Branching-Strategie

```
main  ─────────────────────────── stabil, Release-Branch
  │
  └── dev  ────────────────────── Entwicklung & Tests
        │
        └── claude/feature-xyz ── Feature-Branches (von Claude Code)
```

| Branch | Zweck | Wer pusht hierhin? |
|--------|-------|--------------------|
| `main` | Nur stabile, getestete Releases. HACS zieht Updates von hier. | Nur via Pull Request (PR) |
| `dev` | Entwicklungs- und Test-Branch. Hier werden neue Features integriert und getestet. | Claude Code pusht Feature-Branches, Nutzer mergt via PR |
| `claude/*` | Kurzlebige Feature-Branches, erstellt von Claude Code. | Claude Code (automatisch) |

**Wichtig:** Direkte Pushes auf `main` sind verboten. Alles geht über Pull Requests.

---

## Workflow: Feature entwickeln (für AI-Sessions)

Wenn ein Nutzer eine neue Funktion oder einen Fix anfordert:

1. **Kontext lesen:** Diese Datei (CLAUDE.md), ROADMAP.md, und relevante Code-Dateien lesen.
2. **Feature-Branch erstellen:** Claude Code erstellt automatisch einen Branch `claude/<beschreibung>-<id>` basierend auf dem aktuellen Stand.
3. **Implementieren:** Code ändern, Tests schreiben/anpassen.
4. **Tests ausführen:**
   ```bash
   python -m pytest tests/ -v
   ```
   Alle Tests müssen grün sein.
5. **Commit & Push:**
   ```bash
   git add <dateien>
   git commit -m "feat: kurze Beschreibung"
   git push -u origin claude/<branch-name>
   ```
6. **PR erstellen:** Claude Code erstellt einen PR gegen `main` (für Hotfixes) oder `dev` (für Features).
7. **Merge-Anleitung ausgeben:** Dem Nutzer Schritt für Schritt erklären, wie er den PR mergt.

---

## Workflow: PR mergen (Schritt-für-Schritt für den Nutzer)

### Variante A: Über GitHub Web (empfohlen)

1. Öffne den PR-Link, den Claude dir gegeben hat.
2. Lies die Änderungen durch (Tab "Files changed").
3. Klicke auf den grünen Button **"Merge pull request"**.
4. Klicke auf **"Confirm merge"**.
5. Optional: Klicke auf **"Delete branch"** um den Feature-Branch aufzuräumen.

### Variante B: Über die Kommandozeile

```bash
# 1. Zum Ziel-Branch wechseln
git checkout main        # oder: git checkout dev
git pull origin main     # oder: git pull origin dev

# 2. Feature-Branch mergen
git merge origin/claude/<branch-name>

# 3. Push
git push origin main     # oder: git push origin dev
```

---

## Workflow: Release erstellen (Schritt-für-Schritt)

Wenn eine neue Version veröffentlicht werden soll:

### Voraussetzungen
- Alle gewünschten Änderungen sind auf `main` gemergt.
- Tests sind grün.
- `manifest.json` und `pyproject.toml` zeigen die neue Versionsnummer.

### Schritte

1. **Auf GitHub gehen:** https://github.com/kinimodb/ha-tadox-proxy
2. **Releases öffnen:** Rechte Seitenleiste → "Releases" → **"Draft a new release"**
3. **Tag erstellen:**
   - Im Feld "Choose a tag" eintippen: `v1.0.0` (oder die neue Version)
   - Klick auf **"Create new tag: v1.0.0 on publish"**
4. **Target branch:** `main` auswählen
5. **Release title:** `v1.0.0 – Community Release` (oder passender Titel)
6. **Beschreibung:** Die Release-Notes einfügen (werden am Ende jeder Session von Claude bereitgestellt)
7. **Veröffentlichen:** Klick auf **"Publish release"**

### Nach dem Release
- HACS erkennt neue Releases automatisch (kann bis zu 1h dauern).
- Nutzer sehen das Update unter HACS → Integrationen → Tado X Proxy → "Update".

---

## Workflow: Hotfix auf main (für dringende Bugfixes)

Falls ein kritischer Bug direkt auf `main` gefixt werden muss (ohne den Umweg über `dev`):

1. Claude Code erstellt einen Feature-Branch direkt von `main`.
2. Fix implementieren, Tests grün.
3. PR gegen `main` erstellen.
4. Nutzer mergt den PR (siehe "Workflow: PR mergen").
5. Neuen Patch-Release erstellen (z.B. `v1.0.1`).
6. **Wichtig:** Danach `dev` aktualisieren:
   ```bash
   git checkout dev
   git pull origin main
   git push origin dev
   ```

---

## Workflow: dev-Branch auf den neuesten Stand bringen

Falls `main` Änderungen hat, die noch nicht in `dev` sind (z.B. nach einem Hotfix):

```bash
git checkout dev
git pull origin main
git push origin dev
```

---

## Session-Übergabe

Wenn eine neue AI-Session beginnt (neuer Claude Code Chat), sollte die AI:

1. **CLAUDE.md lesen** – enthält alle Projektregeln, Architektur, Workflows.
2. **README.md lesen** – User-facing Doku, aktuelle Features.
4. **Git-Status prüfen:**
   ```bash
   git branch -a          # Welche Branches existieren?
   git log --oneline -10  # Letzte Commits?
   git status             # Offene Änderungen?
   ```
5. **Tests ausführen** um sicherzustellen, dass alles grün ist:
   ```bash
   python -m pytest tests/ -v
   ```

### Checkliste für die neue Session
- [ ] Auf welchem Branch bin ich? (`git branch`)
- [ ] Gibt es ungespeicherte Änderungen? (`git status`)
- [ ] Sind alle Tests grün? (`python -m pytest tests/ -v`)
- [ ] Was ist die aktuelle Version? (`manifest.json` prüfen)
- [ ] Was ist die Aufgabe des Nutzers?

---

## Brand-Assets (Logo/Icon)

Es gibt **zwei unabhängige Systeme**, die Logos anzeigen:

### 1. Home Assistant Integrationsseite (ab HA 2026.3)

HA sucht lokal in `custom_components/<domain>/brand/` nach Icons (Brands Proxy API).

```
custom_components/tadox_proxy/
└── brand/
    ├── icon.png      (256×256)
    ├── icon@2x.png   (512×512)
    └── logo.png      (256×256)
```

→ Funktioniert **ohne Internet und ohne PR**. ✅

### 2. HACS Store-Ansicht

HACS löst Icons über das CDN `https://brands.home-assistant.io/` auf.
Das CDN wird aus dem `home-assistant/brands`-Repo gespeist.
Für Custom Integrations: `custom_integrations/<domain>/` im brands-Repo.

→ Erfordert einen **PR ans `home-assistant/brands`-Repo** (Legacy, aber nötig für HACS).

**Wichtig:**
- Ein `brand/`-Ordner im **Repository-Root** wird von **niemandem** ausgelesen – nicht von HA, nicht von HACS.
- Keine `logo.png` direkt in `custom_components/tadox_proxy/` ablegen (nur im `brand/` Unterordner).
