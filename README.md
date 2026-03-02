# Tado X Proxy Thermostat (HACS)

Eine **Home Assistant Custom Component**, die als intelligenter Proxy-Regler für **Tado X Thermostate** fungiert.

## Status: BETA (v0.5.0)

Diese Integration befindet sich in aktiver Entwicklung. Aktuell wird ein Testraum langzeiterprobt.
Nutzung auf eigene Gefahr.

---

## Das Problem

Tado X Thermostate (TRV) messen die Temperatur direkt am heißen Heizkörper. Das führt zu:

1. **Hitzestau:** Das Ventil schließt zu früh, weil der Sensor am Heizkörper schon warm ist – der Raum bleibt kalt.
2. **Oszillation:** Ventil öffnet/schließt im Wechsel (Sägezahn-Kurve).
3. **Offset-Drift:** Der von Tado intern gelernte Offset passt oft nicht zur Realität.

Ein externer Raumsensor (z.B. Aqara, Sonoff) kennt die *echte* Raumtemperatur.
Diese Integration nutzt ihn, um Tado intelligent zu steuern.

## Die Lösung: Feedforward + PI

Anstatt einen zweiten PID-Regler zu bauen, der **gegen** Tados internen Regler kämpft,
arbeitet dieser Proxy **mit** Tado zusammen:

```
Tado-Offset  = Tado-Sensor − Raum-Sensor          (z.B. 24°C − 19°C = 5°C)
Basis-Ziel   = Wunsch-Temperatur + Tado-Offset     (z.B. 21°C + 5°C = 26°C)
Korrektur    = Kp × Fehler + Ki × ∫Fehler dt       (kleiner Feinabgleich)
Befehl       = Basis-Ziel + Korrektur               (geclampt auf 5–25°C)
```

**Warum funktioniert das?**
- Der **Feedforward**-Term kompensiert den Sensor-Offset sofort (kein Warten auf Fehleraufbau).
- Der **PI-Korrekturfaktor** ist bewusst klein – er gleicht nur noch Restfehler aus.
- **Dual Anti-Windup** verhindert Overshoot: Das Integral baut sich nur auf, wenn die Raumtemperatur nahe am Ziel ist (Deadband).

### Getestete Ergebnisse (v0.4.1)

| Metrik | Ergebnis |
|--------|----------|
| Regelgenauigkeit | ±0.3–0.5°C um den Sollwert |
| Overshoot | max. 0.3°C |
| Stabilität | 11+ Stunden Nachtbetrieb ohne Drift |
| Integral im Betrieb | nahe 0 (typisch 0.02–0.10°C) |

---

## Installation (HACS)

1. **HACS** > Integrationen > **Custom Repositories** > `https://github.com/kinimodb/ha-tadox-proxy`
2. Installieren & Home Assistant neu starten.
3. **Einstellungen** > Geräte & Dienste > **Integration hinzufügen** > *Tado X Proxy*.
4. Konfiguriere:
   - **Source Entity:** Das originale Tado X Thermostat (z.B. `climate.wohnzimmer`).
   - **External Sensor:** Dein externer Raumsensor (z.B. `sensor.wohnzimmer_temperatur`).

---

## Presets (v0.5.0)

Der Proxy unterstützt 5 Betriebsmodi, die über die HA-Oberfläche oder per Automation umgeschaltet werden:

| Preset | Verhalten | Typischer Einsatz |
|--------|-----------|-------------------|
| **Comfort** | Nutzt die eingestellte Zieltemperatur | Standard-Modus, wenn man im Raum ist |
| **Eco** | Zieltemperatur minus Eco-Offset (z.B. −2°C) | Energiesparen bei Anwesenheit |
| **Boost** | Maximale Temperatur für X Minuten, dann auto-zurück zu Comfort | Schnelles Aufheizen |
| **Away** | Feste niedrige Temperatur (z.B. 16°C) | Bei Abwesenheit |
| **Vacation** | Frostschutz-Temperatur (z.B. 5°C) | Urlaub |

**Boost-Timer:** Boost schaltet automatisch nach der konfigurierten Dauer (Default: 30 min) zurück auf Comfort.
Beim HA-Neustart wird ein aktiver Boost-Modus aus Sicherheitsgründen ebenfalls auf Comfort zurückgesetzt.

---

## Konfiguration & Tuning

Über **Einstellungen > Geräte & Dienste > Tado X Proxy > Konfigurieren** kannst du anpassen:

### Regelparameter

| Parameter | Default | Bereich | Beschreibung |
|-----------|---------|---------|-------------|
| **Kp (Proportional)** | 0.8 | 0.0–5.0 | Stärke der sofortigen Fehlerkorrektur |
| **Ki (Integral)** | 0.003 | 0.0–0.1 | Geschwindigkeit der Langzeit-Drift-Korrektur |

### Preset-Temperaturen

| Parameter | Default | Bereich | Beschreibung |
|-----------|---------|---------|-------------|
| **Eco-Absenkung** | −2.0°C | −5.0–0.0 | Offset von der Komfort-Temperatur |
| **Boost-Ziel** | 25.0°C | 20.0–25.0 | Feste Temperatur im Boost-Modus |
| **Boost-Dauer** | 30 min | 5–120 | Auto-Rückkehr zu Comfort nach X Minuten |
| **Away-Ziel** | 16.0°C | 5.0–20.0 | Feste Temperatur bei Abwesenheit |
| **Vacation-Ziel** | 5.0°C | 5.0–15.0 | Frostschutz im Urlaubsmodus |

> **Detaillierte Tuning-Anleitung:** Siehe [TUNING.md](TUNING.md) für eine Schritt-für-Schritt-Anleitung mit Beispielen.

### Schnell-Referenz

| Symptom | Maßnahme |
|---------|----------|
| Temperatur schwingt (±1°C+) | Kp senken (z.B. 0.5) |
| Aufheizen dauert zu lang | Kp erhöhen (z.B. 1.2) |
| Temperatur dauerhaft unter Ziel | Ki leicht erhöhen (z.B. 0.005) |
| Temperatur dauerhaft über Ziel | Ki senken (z.B. 0.001) |
| Overshoot beim Aufheizen | Kp senken, Ki unverändert lassen |

---

## Diagnose-Attribute

In den **Entwicklerwerkzeugen > Zustände** findest du unter der Proxy-Entity:

| Attribut | Beschreibung |
|----------|-------------|
| `effective_setpoint_c` | Effektiver Sollwert nach Preset-Berechnung |
| `feedforward_offset_c` | Gemessener Sensor-Offset (Tado − Raum) |
| `p_correction_c` | Aktuelle proportionale Korrektur |
| `i_correction_c` | Aktuelle integrale Korrektur |
| `error_c` | Aktueller Fehler (Sollwert − Raumtemperatur) |
| `target_for_tado_c` | Befehl, der an Tado gesendet wird |
| `is_saturated` | `true` wenn Befehl am Limit (5°C oder 25°C) |
| `regulation_reason` | Letzter Entscheidungsgrund (z.B. `sent(normal_update)`, `rate_limited(42s)`) |

---

## Projekt-Dateien

| Datei | Zweck |
|-------|-------|
| [TUNING.md](TUNING.md) | Detaillierte Tuning-Anleitung für neue Räume |
| [ROADMAP.md](ROADMAP.md) | Feature-Roadmap und Meilensteine |
| [CONTEXT.md](CONTEXT.md) | Technischer Kontext und Architektur-Entscheidungen |

---

## Credits

Inspiriert von [Versatile Thermostat](https://github.com/jmcollin78/versatile_thermostat), aber spezialisiert auf die Eigenheiten der Tado X Hardware (Matter/Thread).
