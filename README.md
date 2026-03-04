# Tado X Proxy Thermostat

Ein Home Assistant Custom Component (HACS), das einen virtuellen Proxy-Thermostaten
für Tado X Heizkörperthermostate (TRVs) erzeugt. Kern der Integration ist eine
Feedforward+PI-Regelung, die den Tado-internen Sensor mithilfe eines externen
Raumsensors kompensiert – für präzise Raumtemperaturregelung statt
Heizkörperoberflächentemperatur.

---

## Das Problem – und die Lösung

Tado X TRVs messen ihre eigene Oberflächentemperatur direkt am Heizkörper – nicht
die tatsächliche Raumtemperatur. Das führt dazu, dass die Heizung zu früh abschaltet
(der Heizkörper ist bereits warm, der Raum noch nicht). Ergebnis: dauerhaftes
Unterschwingen um 1–3 °C.

**Tado X Proxy** löst dieses Problem mit einem Feedforward-Ansatz:
- Ein externer Raumsensor (z. B. Zigbee-Temperatursensor) liefert die echte Raumtemperatur.
- Der Proxy berechnet aus der Differenz `Tado-intern – Raum` einen Korrekturoffset.
- Dieser Offset wird direkt auf den Sollwert aufaddiert – ohne Verzögerung.
- Eine PI-Regelung gleicht verbliebene Restfehler aus (Kp=0.8, Ki=0.003).

Ergebnis: ±0.3–0.5 °C Genauigkeit, bestätigt über 11+ Stunden Nachtbetrieb.

---

## Voraussetzungen

- Home Assistant (aktuelle Version empfohlen)
- [HACS](https://hacs.xyz) installiert
- Mindestens ein Tado X TRV als `climate.*`-Entity in HA
- Ein Temperatur-Sensor (`sensor.*`, `device_class: temperature`) im Raum

---

## Installation

1. HACS öffnen → **Integrationen** → Menü (drei Punkte oben rechts) → **Benutzerdefinierte Repositories**
2. URL eintragen: `https://github.com/kinimodb/ha-tadox-proxy`
3. Kategorie: **Integration** → **Hinzufügen**
4. **Tado X Proxy Thermostat** in HACS suchen und installieren
5. Home Assistant neu starten
6. **Einstellungen** → **Geräte & Dienste** → **Integration hinzufügen** → *Tado X Proxy Thermostat*

---

## Konfiguration

Beim erstmaligen Einrichten werden drei Felder abgefragt:

| Feld | Beschreibung |
|------|-------------|
| Quell-Climate-Entity | Der echte Tado X TRV (`climate.*`) |
| Externer Temperatursensor | Ein `sensor.*` mit `device_class: temperature` im Raum |
| Name | Anzeigename des Proxy-Thermostaten |

---

## Presets

Der Proxy-Thermostat kennt sechs Betriebsmodi:

| Preset | Beschreibung |
|--------|-------------|
| **Komfort** | Zieltemperatur aus der Komfort-Einstellung (konfigurierbar) |
| **Eco** | Feste Eco-Temperatur (konfigurierbar, Standard: 19 °C) |
| **Boost** | Kurzzeitiges Hochheizen auf Boost-Temperatur, dann automatisch zurück |
| **Abwesend** | Reduzierte Temperatur für kurze Abwesenheit |
| **Frostschutz** | Minimale Temperatur für Frostschutz (z.B. bei Fensteröffnung oder langer Abwesenheit) |
| **Manuell** | Freie Temperaturwahl über den Slider – kein Preset aktiv |

Der **Manuell**-Modus wird automatisch aktiviert, wenn der Temperatur-Slider verschoben
wird, ohne ein Preset auszuwählen. Die manuell gesetzte Temperatur verändert **nicht**
die gespeicherte Komfort-Temperatur.

Der **Boost**-Timer schaltet nach der konfigurierten Dauer (Standard: 30 Minuten)
automatisch zurück auf Komfort. Ein aktiver Boost-Modus wird beim HA-Neustart
aus Sicherheitsgründen ebenfalls auf Komfort zurückgesetzt.

---

## Preset-Temperaturen als Entitäten

Jede Preset-Temperatur ist als `number.*`-Entität in HA verfügbar und kann direkt
in Automationen verwendet werden:

| Entität | Beschreibung | Standard |
|---------|-------------|---------|
| `number.*_comfort_temperature` | Komfort-Zieltemperatur | 20.0 °C |
| `number.*_eco_temperature` | Eco-Zieltemperatur | 19.0 °C |
| `number.*_boost_temperature` | Boost-Zieltemperatur | 25.0 °C |
| `number.*_away_temperature` | Abwesend-Zieltemperatur | 16.0 °C |
| `number.*_frost_protection_temperature` | Frostschutz-Temperatur | 5.0 °C |

Alle Entitäten sind im Bereich 5–30 °C in 0.5-°C-Schritten einstellbar.

---

## Schalter: Physischem Thermostat folgen

Der Schalter `switch.*_follow_physical_thermostat` aktiviert einen optionalen Modus:

Wenn jemand am physischen Tado TRV direkt eine neue Temperatur einstellt
(Differenz > 1.5 °C zur zuletzt gesendeten Solltemperatur), übernimmt der Proxy
diese Änderung automatisch und wechselt in den **Manuell**-Modus.

> Standardmäßig **deaktiviert**. Muss bewusst eingeschaltet werden.

---

## Fenstererkennung

Ein optionaler Binärsensor (z. B. Fensterkontakt) kann konfiguriert werden.
Wenn das Fenster geöffnet wird (`state: on`):

1. Ein konfigurierbarer Timer startet (Standard: 30 Sekunden).
2. Nach Ablauf: Wechsel auf das **Frostschutz**-Preset – Temperatur wird auf Frostschutz-Niveau gesenkt.
3. Beim Schließen des Fensters: Vorheriges Preset wird automatisch **wiederhergestellt**.

Wenn das Fenster vor Ablauf des Timers wieder geschlossen wird, wird der Timer
abgebrochen – kein Eingriff in die Heizung.

**Konfiguration** (in den Optionen der Integration):
- *Fenstersensor*: `binary_sensor.*` (optional)
- *Verzögerung Fenstererkennung*: 0–3600 Sekunden

Das Attribut `window_open_active` zeigt den aktuellen Zustand an.

---

## Präsenzsensor

Ein optionaler Präsenzsensor (z. B. Personen-Tracker) kann konfiguriert werden.
Wenn niemand mehr zu Hause ist (`state: off`):

1. Ein konfigurierbarer Timer startet (Standard: 30 Minuten).
2. Nach Ablauf: Wechsel auf das **Abwesend**-Preset.
3. Wenn jemand zurückkommt (`state: on`): Automatische Rückkehr auf das **vorherige Preset**.

Wenn jemand vor Ablauf des Timers zurückkommt, wird der Timer abgebrochen.

**Konfiguration** (in den Optionen der Integration):
- *Präsenzsensor*: `binary_sensor.*` (optional)
- *Verzögerung Abwesenheit*: 0–7200 Sekunden

Das Attribut `presence_away_active` zeigt den aktuellen Zustand an.

> Fenster- und Präsenzsensor sind **unabhängig voneinander**: Das Fenster steuert
> das Preset (Frostschutz), der Präsenzsensor steuert das Preset
> (Abwesend). Beide können gleichzeitig aktiv sein.

---

## Regelparameter (Optionen)

Über **Einstellungen → Geräte & Dienste → Tado X Proxy → Konfigurieren** anpassbar:

| Parameter | Standard | Bereich | Beschreibung |
|-----------|---------|---------|-------------|
| **Kp (Proportional)** | 0.8 | 0.0–5.0 | Stärke der sofortigen Fehlerkorrektur |
| **Ki (Integral)** | 0.003 | 0.0–0.1 | Geschwindigkeit der Langzeit-Drift-Korrektur |

> Für Details zur Feinjustierung siehe [TUNING.md](TUNING.md).

---

## Diagnose-Attribute

Die Proxy-Entität stellt folgende Attribute bereit (sichtbar unter **Entwicklerwerkzeuge → Zustände**):

| Attribut | Beschreibung |
|----------|-------------|
| `room_temp` | Aktuelle Raumtemperatur (externer Sensor) |
| `tado_internal_temp` | Tado-interne Temperaturmessung |
| `correction_applied` | Aktuell angewendete Korrektur (°C) |
| `integral` | Aktueller Integral-Wert der PI-Regelung |
| `last_sent_setpoint` | Zuletzt an Tado gesendeter Sollwert |
| `window_open_active` | Fenstererkennung aktiv (`true`/`false`) |
| `presence_away_active` | Präsenz-Abwesenheit aktiv (`true`/`false`) |

---

## Projekt-Dateien

| Datei | Zweck |
|-------|-------|
| [TUNING.md](TUNING.md) | Detaillierte Tuning-Anleitung für neue Räume |
| [ROADMAP.md](ROADMAP.md) | Feature-Roadmap und Meilensteine |
| [CONTEXT.md](CONTEXT.md) | Technischer Kontext und Architekturentscheidungen |

---

## Lizenz

MIT License – siehe [LICENSE](LICENSE)
