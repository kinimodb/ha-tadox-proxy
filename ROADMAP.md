# TADOX-proxy – Roadmap

**Mission:** Ein lokaler Proxy-Regler für Tado X, der den internen Offset-Hitzestau der Hardware durch Feedforward-Kompensation eliminiert und präzise auf externe Raumsensoren regelt.

## Status (v0.4.0)
- **Architektur:** Feedforward + PI (kein voller PID, arbeitet MIT Tados internem Regler).
- **Technik:** Python `async`, HA DataUpdateCoordinator.
- **Aktuelle Phase:** Beta-Test.

---

## M1 – Core Stability & Validation (v0.4.0) ✓
**Ziel:** Stabile, zuverlässige Kernregelung.
- [x] Feedforward-Kompensation für Tado-Sensor-Offset.
- [x] PI-Korrektur mit echtem Anti-Windup (Integral friert bei Sättigung ein).
- [x] Rate Limiting (180s) mit Batterieschonung.
- [x] Safety Clamping (5–25°C).
- [x] Unit Tests für Regulation-Engine.
- [ ] **Validation:** Real-World-Test in verschiedenen Räumen.

## M2 – Advanced Configuration ✓
**Ziel:** Jeder Raum ist anders. Parameter müssen pro Thermostat anpassbar sein.
- [x] Options Flow: Kp, Ki über "Konfigurieren" einstellbar.
- [x] Options Flow: Externer Sensor wechselbar.
- [x] Live-Reload: Parameter-Änderungen ohne Neustart.

## M3 – Presets & Modes
1.  **Comfort (Standard):** Nutzt die konfigurierten Werte.
2.  **Eco:** Reduzierter Setpoint (z.B. −2°C).
3.  **Boost:** Max-Temperatur für X Minuten, danach Comfort.
4.  **Away:** Wie Eco, tieferer Setpoint. Aktiviert durch Präsenz-Sensor.
5.  **Vacation:** Frostschutz, reduzierte Regelfrequenz.

## M4 – Externe Trigger
- [ ] Fensterkontakt (sofort "Off" bei offen, Restore bei zu).
- [ ] Präsenz (Auto-Eco bei Abwesenheit).
