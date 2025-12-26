# TADUX-proxy – Roadmap

**Mission:** Ein lokaler PID-Regler für Tado X, der den internen Offset-Hitzestau der Hardware durch "Continuous Holding" eliminiert und präzise auf externe Raumsensoren regelt.

## Status (v0.3.x)
- **Architektur:** Continuous Holding PID (Kein Hard Deadband).
- **Technik:** Python `async`, HA DataUpdateCoordinator.
- **Aktuelle Phase:** Beta-Test & PID-Tuning.

---

## 🚀 M1 – Core Stability & Validation (Aktuell)
**Ziel:** Beweisen, dass der "Continuous PID" Ansatz das "Sägezahn"-Problem und das "Einschlafen" der Tado-Ventile löst.
- [x] Refactoring auf Stateless PID Class.
- [x] Fix DataUpdateCoordinator (KeyError Abstürze).
- [x] Implementierung "Soft Deadband" (I-Anteil läuft weiter).
- [ ] **Validation:** Analyse von Real-World Daten (History Stats) aus Testräumen.

## ⚙️ M2 – Advanced Configuration (Options Flow)
**Ziel:** Jeder Raum ist anders (Größe, Dämmung, Heizkörper). Hardcodierte Parameter funktionieren nicht universell.
- [ ] **UI für PID-Parameter:** Kp, Ki, Kd über "Konfigurieren" einstellbar machen.
- [ ] **UI für Limits:** Min/Max Temperaturen und Deadband einstellbar machen.
- [ ] Live-Reload: Parameter-Änderungen ohne Neustart wirksam machen.

## 🎛 M3 – Presets & Modes (Spezifikation)
Hier definieren wir das Verhalten der geplanten Modi:

1.  **Comfort (Standard):**
    - Nutzt die konfigurierten PID-Werte (Kp/Ki/Kd).
    - Ziel: Präzises Halten der Temperatur.
2.  **Eco (Energiesparen):**
    - Reduzierter Setpoint (z. B. -2°C).
    - *Optional:* Sanfteres Regelverhalten (niedrigerer Kp), um Überschwingen strikt zu vermeiden.
3.  **Boost (Schnellaufheizen):**
    - Ignoriert PID kurzzeitig.
    - Sendet `Max_Temp` (z. B. 25°C) an Tado für X Minuten oder bis `Ist > Soll`.
    - Danach Rückfall in Comfort.
4.  **Away (Abwesend):**
    - Wie Eco, aber meist tieferer Setpoint (konfigurierbar).
    - Aktiviert durch Präsenz-Sensor oder manuell.
5.  **Urlaub (Vacation):**
    - Frostschutz (z. B. 5°C oder "Off").
    - Deaktiviert regelmäßige PID-Berechnungen, um Batterie zu sparen (nur Sicherheits-Check alle 60 Min).

## 🔌 M4 – Externe Trigger
- [ ] Fensterkontakt (Sofort "Off" bei offen, Restore bei zu).
- [ ] Präsenz (Auto-Eco bei Abwesenheit).

---

## 📚 PID-Tuning Guide: Wie finde ich meine Werte?
*(Konzept für Dokumentation / Helper-Text in der UI)*

Da jeder Raum physikalisch anders ist (Größe, Heizkörperleistung, Dämmung), gibt es keine "One Size Fits All" Werte.
**Vorgehen:**
1.  **Start:** Mit Defaults beginnen (`Kp=7.0`, `Ki=0.005`, `Kd=600`).
2.  **Test:** 24h laufen lassen und Home Assistant History (`history.csv`) beobachten.
3.  **Analyse & Anpassung:**
    * **Problem:** Temperatur schwingt stark über und unter das Ziel (Sägezahn).
        * *Lösung:* `Kp` senken (Regler ist zu nervös).
    * **Problem:** Es dauert ewig, bis der Raum warm wird.
        * *Lösung:* `Kp` erhöhen (Regler gibt zu wenig Gas).
    * **Problem:** Temperatur ist stabil, liegt aber dauerhaft *unter* dem Ziel.
        * *Lösung:* `Ki` leicht erhöhen (Regler lernt den Offset zu langsam).
    * **Problem:** Temperatur ist stabil, liegt aber dauerhaft *über* dem Ziel.
        * *Lösung:* `Kp` senken oder `Ki` verringern (Offset hat sich zu stark aufgebaut).
