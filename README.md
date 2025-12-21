# ha-tadox-proxy

Home Assistant Custom Integration, die eine **Climate-Proxy-Entität** bereitstellt.  
Die Proxy-Entität spiegelt Werte (z. B. Ist-/Solltemperatur, HVAC-Modus) einer bestehenden Climate-Entität und leitet Steuerbefehle an diese Quelle weiter.

## Was ist das?

Du hast bereits eine funktionierende Climate-Entität (z. B. `climate.gz_tado_thermostat`).  
Diese Integration erzeugt zusätzlich eine Proxy-Entität (z. B. `climate.gz_proxy_thermostat`), die:

- Zustände/Attribute der Quelle anzeigt (Mirror)
- `climate.set_temperature` an die Quelle weiterleitet
- `climate.set_hvac_mode` an die Quelle weiterleitet

Damit kannst du Automationen, Dashboards oder Logik auf der Proxy-Entität aufbauen, ohne direkt die Original-Entität zu verwenden.

## Voraussetzungen

- Home Assistant (mit UI-Konfiguration / Config Entries)
- Eine **existierende Climate-Entität** als Quelle (z. B. TadoX/Tado-Thermostat aus einer anderen Integration)
- Optional: HACS (empfohlen)

## Installation (empfohlen): HACS Custom Repository

1. In Home Assistant: **HACS → Integrationen**
2. Oben rechts **⋮ → Custom repositories**
3. Repository-URL hinzufügen:
   - `https://github.com/kinimodb/ha-tadox-proxy`
4. Kategorie: **Integration**
5. **Hinzufügen**
6. Repo in HACS öffnen → **Installieren**
7. **Home Assistant neu starten**

### Updates über HACS

- HACS → Integrationen → `ha-tadox-proxy`
- **Update** (falls angeboten)
- Danach **Home Assistant neu starten**

## Manuelle Installation (ohne HACS)

1. Kopiere den Ordner:
   - `custom_components/tadox_proxy/`
2. nach:
   - `/config/custom_components/tadox_proxy/`
3. Home Assistant neu starten.

## Konfiguration (UI)

1. **Einstellungen → Geräte & Dienste → Integration hinzufügen**
2. Suche nach: **Tado X Proxy Thermostat** (oder Domain `tadox_proxy`)
3. Wähle die **Quelle** (Source Entity), z. B. `climate.gz_tado_thermostat`
4. Vergib einen Namen für den Proxy
5. Danach erscheint eine neue Entity, z. B.:
   - `climate.gz_proxy_thermostat`

## Nutzung

- Verwende die Proxy-Entität in Automationen/Blueprints/Dashboards.
- Änderungen an Solltemperatur/HVAC-Modus werden an die Quelle weitergeleitet.
- Die Proxy-Entität enthält als Attribut:
  - `source_entity_id` (hilfreich fürs Debugging)

## Troubleshooting

### Proxy ist `unavailable` oder `restored: true`
- Das bedeutet meist: Die Integration/Platform wurde nicht korrekt geladen oder ist beim Start mit einem Fehler abgebrochen.
- Prüfe: **Einstellungen → System → Protokolle** und suche nach `tadox_proxy`.

### Proxy-Status ist `unknown`
- Das tritt auf, wenn der HVAC-Modus nicht korrekt aus der Quelle abgeleitet werden kann.
- Stelle sicher, dass du die neueste Version installiert hast (HACS Update + Neustart).

### Aktionen steuern nichts
- Nicht über „Zustände setzen“ testen (das verändert nur die Anzeige).
- Nutze stattdessen **Entwicklerwerkzeuge → Aktionen**:
  - `climate.set_temperature` mit `temperature: 18`
  - `climate.set_hvac_mode` mit `hvac_mode: "off"`

## Lizenz

Siehe `LICENSE`.
