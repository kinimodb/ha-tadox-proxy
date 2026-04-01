# Contributing – Tado X Proxy

## PR mergen

### Variante A: GitHub Web (empfohlen)

1. PR-Link öffnen.
2. Tab "Files changed" prüfen.
3. **"Merge pull request"** → **"Confirm merge"**.
4. Optional: **"Delete branch"** zum Aufräumen.

### Variante B: Kommandozeile

```bash
git checkout main        # oder: git checkout dev
git pull origin main     # oder: git pull origin dev
git merge origin/claude/<branch-name>
git push origin main     # oder: git push origin dev
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

## Hotfix auf main

1. Feature-Branch direkt von `main` erstellen.
2. Fix implementieren, Tests grün.
3. PR gegen `main`, mergen, Patch-Release erstellen.
4. Danach `dev` aktualisieren:
   ```bash
   git checkout dev && git pull origin main && git push origin dev
   ```

## dev-Branch aktualisieren

Falls `main` Änderungen hat, die nicht in `dev` sind:

```bash
git checkout dev && git pull origin main && git push origin dev
```
