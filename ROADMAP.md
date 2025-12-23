# TADUX-proxy – Roadmap

Ziel: Proxy-Climate für Tado X, der *externe Sensorik* (Temp/optional Feuchte) nutzt und eine *präzisere, stabilere und sparsamere* Regelung als Standard-Setups ermöglicht.

## Grundprinzipien (Constraints)
- Primär **lokal** arbeiten, wenn möglich (Matter/HA-Entity), um Cloud-Abhängigkeiten und API-Quoten zu vermeiden. :contentReference[oaicite:0]{index=0}
- Externer Temperatursensor ist die maßgebliche **Ist-Temperatur**.
- Jede Regelstrategie muss **Überheizen (Overshoot)** und **Takten** reduzieren; Hydronik/Heizkörper haben relevante **thermische Trägheit** (Nachwärme). :contentReference[oaicite:1]{index=1}
- Tado X interne Messlogik kann z. B. gemittelte Werte nutzen; wir behandeln das als sekundär/Backup. :contentReference[oaicite:2]{index=2}

---

## M0 – Lauffähiges Fundament (so klein wie möglich, aber testbar)
**Lieferumfang**
- 1× `ClimateEntity` als Proxy (Setpoint schreiben/lesen) auf Basis einer bestehenden Tado-X-Entität in HA (Matter oder vorhandene Integration).
- Konfiguration: Auswahl externe Temperatur-Entity (Pflicht).
- Logging + saubere Schichten (Setup / Adapter / Entity).

**DoD**
- Proxy lässt sich per UI anlegen (Config Entry) und setzt zuverlässig Solltemperaturen.
- Externe Ist-Temperatur wird im Proxy angezeigt.

---

## M1 – Regelkern “Besser als Versatile”: Forschung → Entwurf → Implementierung
### M1a – Research-Dossier (kompakt, belastbar)
**Erarbeiten und dokumentieren**
- Tado X Ansteuerungs-Realität in HA: lokal vs. Cloud, Latenzen, Quoten, Nebenwirkungen häufiger Setpoint-Änderungen. :contentReference[oaicite:3]{index=3}
- Hydronik/Ölzentralheizung: Trägheit, Nachwärme, sinnvolle Mindestlaufzeiten/Stillstandszeiten, typische Overshoot-Ursachen. :contentReference[oaicite:4]{index=4}

**DoD**
- 1–2 Seiten “Design Notes” direkt in dieser Datei unter “Regelstrategie”.

### M1b – Control v1: stabiler Kern (ohne Presets/Window/Presence)
**Regelidee (robust, sparsam)**
- Hysterese/Deadband + Mindeststellzeit (Anti-Taktung).
- Temperaturänderungsrate **ΔT/Δt** (wie schnell steigt/fällt die Temperatur) zur *Vorsteuerung*: früher drosseln, wenn Trend auf Ziel zuläuft (Overshoot-Reduktion).
- Schutz: Rate-Limit für Setpoint-Kommandos (insb. relevant bei Cloud/Quoten). :contentReference[oaicite:5]{index=5}

**DoD**
- In einem Testraum wird ein Sollwert über 24h mit weniger Overshoot und weniger “Setpoint-Spam” gehalten (Logs belegen Eingriffe).

### M1c – Control v2: Tuning & Kalibrierung (Tado X / Ölzentralheizung)
- Parameter-Autotuning (z. B. Deadband, Mindeststellzeit) anhand gemessener Trägheit/Trend.
- “Safe Defaults” + optional “Aggressiv/Schonend”-Regelprofil.

**DoD**
- Parameter lassen sich nachvollziehbar einstellen; Defaults funktionieren ohne manuelles Feintuning.

---

## M2 – Presets (Komfort/ECO/Abwesend/Custom)
- Presets setzen Zieltemperatur + Regelprofil (z. B. “Schonend” für ECO).
**DoD:** Preset-Wechsel ist deterministisch und protokolliert.

## M3 – Fensterkontakt
- Fenster offen → nach Verzögerung Absenken/Aus; Fenster zu → Restore (mit Flatter-Schutz).
**DoD:** Kein “Ping-Pong” bei wackeligen Kontakten.

## M4 – Präsenz
- Präsenz aus → ECO/Abwesend; Präsenz an → Komfort/letzter Modus; Cooldown gegen Sprünge.
**DoD:** Verhalten ist stabil bei wechselnden Presence-States.

## M5 – Feuchte (optional, nur mit klarer Wirkung)
- Feuchte als Signal (z. B. Lüftungsempfehlung / optionaler ECO-Boost), nicht als primärer Regler.
**DoD:** Feature ist deaktivierbar und verursacht keine Reglerinstabilität.

---

## Regelstrategie (Design Notes)
(Platzhalter für M1a – wird ergänzt: Systemverhalten, Mess-/Stellgrößen, Parameter, Schutzlogiken, Testkriterien)
