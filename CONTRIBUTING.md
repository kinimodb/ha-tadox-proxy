# Contributing – Tado X Proxy

## PR mergen

### Variante A: GitHub Web (empfohlen)

1. PR-Link öffnen.
2. Tab "Files changed" prüfen.
3. **"Merge pull request"** → **"Confirm merge"**.
4. Optional: **"Delete branch"** zum Aufräumen.

### Variante B: Kommandozeile

```bash
git checkout main
git pull origin main
git merge origin/claude/<branch-name>
git push origin main
```

## Release erstellen

### Voraussetzungen
- Alle Änderungen auf `main` gemergt, Tests grün.
- `manifest.json` zeigt die neue Versionsnummer.

### Schritte

1. GitHub → Releases → **"Draft a new release"**
2. Tag: `v1.x.y` → **"Create new tag on publish"**
3. Target: `main`
4. Titel: `v1.x.y – Short Title`
5. Release-Notes einfügen (werden von Claude am Session-Ende bereitgestellt)
6. **"Publish release"**

HACS erkennt neue Releases automatisch (bis zu 1h Verzögerung).

## Pre-Release zum Testen (Beta)

Für tiefgreifende Änderungen: erst selbst ausprobieren, dann mergen.

1. Auf dem Feature-Branch `manifest.json` → `"version": "x.y.z-beta.n"` und das
   README-Badge mitziehen (im Badge-Text `-` als `--` schreiben).
2. PR gegen `main` als **Draft** öffnen – nicht mergen.
3. GitHub → Releases → **"Draft a new release"**: Tag `vx.y.z-beta.n`, Target: der
   Feature-Branch, Haken bei **"Set as a pre-release"**.
4. Installieren in Home Assistant:
   - Unter **Einstellungen → Geräte & Dienste → Entitäten** den Schalter
     **„Pre-release“** des Repositorys (von HACS angelegt, standardmäßig deaktiviert)
     aktivieren und einschalten.
   - HACS → Tado X Proxy → ⋮ → **Redownload** → unter „Need a different version?“
     die Beta wählen → Home Assistant neu starten.
5. Läuft der Test gut: Version auf `x.y.z`, PR mergen, reguläres Release von `main`.
   Zurück zur stabilen Version: gleicher Weg, ältere Version wählen.

Wer den Pre-release-Schalter nicht einschaltet, bekommt die Beta nicht angeboten.

## Hotfix auf main

1. Feature-Branch direkt von `main` erstellen.
2. Fix implementieren, Tests grün.
3. PR gegen `main`, mergen, Patch-Release erstellen.

## Branch-Hygiene

- Alle PRs gehen gegen `main` (es gibt keinen `dev`-Branch).
- Feature-Branches (`claude/*`) nach dem Merge löschen ("Delete branch" im PR).
