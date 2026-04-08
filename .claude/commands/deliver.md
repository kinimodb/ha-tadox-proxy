Dieser Slash-Command wird nach dem Abschluss einer Feature-Session aufgerufen.
Voraussetzungen: Tests grün, Änderungen committed, Branch gepusht, PR erstellt.

Dem Nutzer zwei Dinge ausgeben:

1. **Merge-Anleitung** – Schritt-für-Schritt was der Nutzer auf GitHub tun muss
   (PR reviewen, mergen, ggf. Branch löschen). Details siehe `CONTRIBUTING.md`.

2. **Release-Beschreibung** – Englisch, Markdown, copy-pasteable, nach folgendem
   Format. Wird direkt in die GitHub-Release-Notes eingefügt:

```
## v{VERSION} – Short Title

### New
- Bullet points

### Fixes
- Bullet points

### Breaking Changes (if any)
- What changed and what users need to do

### Installation
Via HACS → Integrations → Tado X Proxy → Update.
```
