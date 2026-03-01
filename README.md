# Tado X Proxy Integration (HACS)

Eine **Home Assistant Custom Component**, die als intelligenter Proxy-Regler für **Tado X Thermostate** fungiert.

## Status: BETA (v0.4.0)
Diese Integration befindet sich in der Testphase. Nutzung auf eigene Gefahr.

## Das Problem
Tado X Thermostate messen die Temperatur direkt am heißen Heizkörper. Das führt oft zu:
1.  **Hitzestau:** Ventil schließt zu früh, Raum bleibt kalt.
2.  **Oszillation:** Ventil macht auf/zu/auf/zu (Sägezahn-Kurve).
3.  **Offset-Drift:** Der von Tado gelernte Offset passt oft nicht zur Realität.

## Die Lösung: Feedforward + PI
Dieser Proxy erstellt eine neue Climate-Entität (z.B. `climate.wohnzimmer_proxy`), die:
1.  Einen **externen Raumsensor** als alleinige Wahrheit nutzt.
2.  Den **Sensor-Offset** zwischen Tado und Raum misst und sofort kompensiert (Feedforward).
3.  Einen sanften **PI-Korrekturfaktor** für verbleibende Fehler berechnet.
4.  **Mit** Tados internem Regler zusammenarbeitet, anstatt gegen ihn zu kämpfen.

### Wie es funktioniert
```
Tado-Offset = Tado-Sensor − Raum-Sensor          (z.B. 24°C − 19°C = 5°C)
Basis-Ziel  = Wunsch-Temperatur + Tado-Offset     (z.B. 21°C + 5°C = 26°C)
Korrektur   = Kp × Fehler + Ki × ∫Fehler dt       (kleiner Feinabgleich)
Befehl      = Basis-Ziel + Korrektur               (geclampt auf 5−25°C)
```

## Installation (HACS)

1.  HACS > Integrationen > Custom Repositories > `https://github.com/kinimodb/ha-tadox-proxy`
2.  Installieren & HA Neustarten.
3.  **Einstellungen > Geräte & Dienste > Integration hinzufügen > Tado X Proxy**.
4.  Wähle:
    * **Source Entity:** Das originale Tado X Thermostat.
    * **External Sensor:** Dein Raumsensor (Aqara, Sonoff, etc.).

## Konfiguration & Tuning

Über **Einstellungen > Geräte & Dienste > Tado X Proxy > Konfigurieren** kannst du anpassen:

| Parameter | Default | Beschreibung |
|-----------|---------|-------------|
| **Kp (Proportional)** | 0.8 | Stärke der sofortigen Fehlerkorrektur |
| **Ki (Integral)** | 0.003 | Geschwindigkeit der Langzeit-Drift-Korrektur |

**Tuning-Tipps:**
* **Temperatur schwingt:** Kp senken (z.B. 0.5)
* **Aufheizen dauert zu lang:** Kp erhöhen (z.B. 1.2)
* **Temperatur liegt dauerhaft unter/über Ziel:** Ki anpassen

## Credits
Inspiriert von `Versatile Thermostat`, aber spezialisiert auf die Eigenheiten der Tado X Hardware (Matter/Thread).
