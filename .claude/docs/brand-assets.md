# Brand-Assets (Logo/Icon)

Zwei unabhängige Systeme zeigen Logos an:

## 1. Home Assistant Integrationsseite (ab HA 2026.3)

HA sucht lokal in `custom_components/<domain>/brand/`:

```
custom_components/tadox_proxy/
└── brand/
    ├── icon.png      (256×256)
    ├── icon@2x.png   (512×512)
    └── logo.png      (256×256)
```

Funktioniert ohne Internet und ohne PR.

## 2. HACS Store-Ansicht

HACS löst Icons über `https://brands.home-assistant.io/` auf (CDN aus `home-assistant/brands`-Repo).
Für Custom Integrations: `custom_integrations/<domain>/` im brands-Repo.
Erfordert einen PR ans `home-assistant/brands`-Repo.

## Wichtig

- Ein `brand/`-Ordner im **Repository-Root** wird von niemandem ausgelesen.
- Keine `logo.png` direkt in `custom_components/tadox_proxy/` – nur im `brand/`-Unterordner.
