# Tado X Proxy Integration (HACS)

Eine **Home Assistant Custom Component**, die als intelligenter "Man-in-the-Middle" Regler für **Tado X Thermostate** fungiert.

## ⚠️ Status: BETA / TESTPHASE
Diese Integration befindet sich im aktiven Refactoring (v0.3.x). Nutzung auf eigene Gefahr.
**Aktuelles Ziel:** Testen des neuen "Continuous PID" Algorithmus in verschiedenen Räumen.

## Das Problem
Tado X Thermostate messen die Temperatur direkt am heißen Heizkörper. Das führt oft zu:
1.  **Hitzestau:** Ventil schließt zu früh, Raum bleibt kalt.
2.  **Oszillation:** Ventil macht auf/zu/auf/zu (Sägezahn-Kurve).
3.  **Offset-Drift:** Der von Tado gelernte Offset passt oft nicht zur Realität.

## Die Lösung: "Continuous Holding PID"
Dieser Proxy erstellt eine neue Climate-Entität (z. B. `climate.wohnzimmer_proxy`), die:
1.  Einen **externen Raumsensor** als alleinige Wahrheit nutzt.
2.  Einen eigenen **PID-Regler** berechnet.
3.  Das Tado-Ventil **permanent aktiv steuert**, indem es die Zieltemperatur dynamisch anpasst (z. B. "Stelle Tado auf 24°C", damit es effektiv 21°C im Raum hält).
4.  Das Ventil nie ganz "schlafen" lässt (Soft Deadband), um die Temperatur stabil zu halten.

## Installation (HACS)

1.  HACS > Integrationen > Custom Repositories > `https://github.com/kinimodb/ha-tadox-proxy`
2.  Installieren & HA Neustarten.
3.  **Einstellungen > Geräte & Dienste > Integration hinzufügen > Tado X Proxy**.
4.  Wähle:
    * **Source Entity:** Das originale Tado X Thermostat.
    * **External Sensor:** Dein Raumsensor (Aqara, Sonoff, etc.).

## Konfiguration & Tuning (Roadmap)
Aktuell sind die PID-Werte (`Kp=7.0`, `Ki=0.005`) noch als "Safe Defaults" im Code hinterlegt.
Da jeder Raum (Volumen, Heizkörpergröße) eigene physikalische Eigenschaften hat, wird **Milestone 2** eine Benutzeroberfläche (Options Flow) einführen, um diese Werte pro Thermostat anzupassen.
Eine Anleitung zum Ermitteln der perfekten Werte findest du in der [ROADMAP](ROADMAP.md).

## Credits
Inspiriert von `Versatile Thermostat`, aber spezialisiert auf die Eigenheiten der Tado X Hardware (Matter/Thread).
