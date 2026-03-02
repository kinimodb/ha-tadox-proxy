# TADOX-proxy – Roadmap

**Mission:** Ein lokaler Proxy-Regler für Tado X, der den internen Offset-Hitzestau der Hardware durch Feedforward-Kompensation eliminiert und präzise auf externe Raumsensoren regelt.

## Status (v0.4.1)

- **Architektur:** Feedforward + PI (arbeitet MIT Tados internem Regler).
- **Technik:** Python `async`, HA DataUpdateCoordinator.
- **Phase:** Beta-Test – ein Raum läuft stabil (±0.3–0.5°C, 11h+ Nachtbetrieb bestätigt).

---

## M1 – Core Stability & Validation (v0.4.0–v0.4.1) ✅

**Ziel:** Stabile, zuverlässige Kernregelung.

- [x] Feedforward-Kompensation für Tado-Sensor-Offset.
- [x] PI-Korrektur mit echtem Anti-Windup (Integral friert bei Sättigung ein).
- [x] Integral Deadband (v0.4.1) – Integral sammelt nur bei |error| < 0.3°C, decayed außerhalb.
- [x] Rate Limiting (180s) mit Batterieschonung + Urgent-Decrease-Bypass.
- [x] Safety Clamping (5–25°C).
- [x] Unit Tests für Regulation-Engine (16 Tests).
- [x] Real-World-Test Testraum: Aufheizen, Halten, Nachtbetrieb bestanden.
- [ ] Real-World-Test in weiteren Räumen (nach M3).

## M2 – Advanced Configuration ✅

**Ziel:** Parameter pro Thermostat anpassbar.

- [x] Options Flow: Kp, Ki über "Konfigurieren" einstellbar.
- [x] Options Flow: Externer Sensor wechselbar.
- [x] Live-Reload: Parameter-Änderungen ohne Neustart (OptionsFlowWithReload).

## M3 – Presets & Modes (nächster Meilenstein)

**Ziel:** Verschiedene Betriebsmodi für den Alltag.

| Preset | Beschreibung | Details |
|--------|-------------|---------|
| **Comfort** | Standard-Betrieb | Nutzt die konfigurierte Zieltemperatur. Ist der Default nach Einrichtung. |
| **Eco** | Energiesparen bei Anwesenheit | Reduziert den Sollwert um einen konfigurierbaren Wert (z.B. −2°C). |
| **Boost** | Schnelles Aufheizen | Setzt temporär max. Temperatur (25°C) für konfigurierbare Dauer (z.B. 30 min), danach zurück zu Comfort. |
| **Away** | Abwesenheit | Niedriger Sollwert (z.B. 16°C). Manuell oder per Automation aktivierbar. |
| **Vacation** | Urlaub / Frostschutz | Frostschutz-Temperatur (5°C), reduzierte Regelfrequenz. |

**Technische Umsetzung:**
- `ClimateEntityFeature.PRESET_MODE` in climate.py aktivieren.
- Preset-Temperaturen über Options Flow konfigurierbar.
- Boost mit Timer (auto-revert nach Ablauf).
- Presets werden per `RestoreEntity` über Neustarts hinweg gespeichert.

## M4 – Externe Trigger

**Ziel:** Automatische Reaktion auf Umgebungsbedingungen.

- [ ] **Fensterkontakt:** Sofort auf Frostschutz bei "offen", Restore bei "zu".
- [ ] **Präsenz-Sensor:** Auto-Wechsel auf Away/Eco bei Abwesenheit.

## M5 – Multi-Room & Community

**Ziel:** Erweiterung und Community-Feedback.

- [ ] Validierung der Default-Parameter in verschiedenen Raumtypen.
- [ ] Dokumentation erweitern basierend auf Community-Erfahrungen.
- [ ] Optional: Raum-Gruppierung (Zonen).

---

## Changelog

### v0.4.1
- **Fix:** Integral Deadband mit Decay – verhindert Overshoot beim Aufheizen.
- Overshoot reduziert von 0.6°C (v0.4.0) auf 0.3°C.
- 16 Unit Tests (inkl. Overshoot-Regression-Test).

### v0.4.0
- **Komplett-Rewrite:** PID (Kp=7.0) ersetzt durch Feedforward + PI (Kp=0.8, Ki=0.003).
- Neue Architektur arbeitet MIT Tados internem Regler statt dagegen.
- Ungenutzte Parameter entfernt, Code aufgeräumt.
- Unit Tests eingeführt.
- `@callback` + `async` Bug behoben.
