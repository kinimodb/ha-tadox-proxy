> **[English version](TUNING.md)**  |  Deutsch (diese Seite)

# Tuning-Anleitung für Tado X Proxy

Diese Anleitung beschreibt, wie du die Regelung für einen neuen Raum einrichtest,
testest und anpasst. Sie richtet sich an Anwender ohne Regelungstechnik-Hintergrund.

---

## Inhaltsverzeichnis

1. [Voraussetzungen](#voraussetzungen)
2. [Ersteinrichtung eines Raums](#ersteinrichtung-eines-raums)
3. [Teststrategie (3 Phasen)](#teststrategie-3-phasen)
4. [Parameter-Referenz](#parameter-referenz)
5. [Diagnose: Was die Attribute verraten](#diagnose-was-die-attribute-verraten)
6. [Fehlerbehebung](#fehlerbehebung)

---

## Voraussetzungen

- Ein **Tado X Thermostat** (TRV), das in Home Assistant als `climate.*` Entity sichtbar ist.
- Ein **externer Raumsensor** (z.B. Aqara, Sonoff), der die *echte* Raumtemperatur misst.
  - Muss als `sensor.*` Entity mit `device_class: temperature` verfügbar sein.
  - Sollte **nicht** in der Nähe von Fenstern, Türen oder Heizkörpern platziert sein.
- Der Proxy ist über HACS installiert und für diesen Raum konfiguriert.

---

## Ersteinrichtung eines Raums

1. **Integration hinzufügen:** Einstellungen > Geräte & Dienste > Integration hinzufügen > *Tado X Proxy*.
2. **Source Entity:** Das Tado X Thermostat wählen (z.B. `climate.schlafzimmer`).
3. **External Sensor:** Den externen Raumsensor wählen.
4. **Name:** Einen eindeutigen Namen vergeben (z.B. "Schlafzimmer Proxy").
5. **Parameter belassen:** Die Default-Werte (Kp=0.8, Ki=0.003) sind ein guter Startpunkt.
6. **Tado X Modus:** Den Tado X Thermostat auf "Manuell" setzen. Die Tado-App darf keine
   eigene Zeitsteuerung aktiv haben, da der Proxy die Kontrolle übernimmt.

---

## Teststrategie (3 Phasen)

### Phase 1: Aufheiz-Test (1–2 Stunden)

**Ziel:** Prüfen, ob die Grundregelung funktioniert und kein starker Overshoot auftritt.

**Vorgehen:**
1. Setze den Proxy auf eine Zieltemperatur, die ~2–4°C über der aktuellen Raumtemperatur liegt.
2. Beobachte in den Entwicklerwerkzeugen (Zustände) die Proxy-Entity.
3. Warte, bis die Raumtemperatur den Sollwert erreicht.

**Worauf achten:**

| Attribut | Guter Wert | Problem |
|----------|-----------|---------|
| `feedforward_offset_c` | 1.0–5.0°C | < 0 deutet auf falschen Sensor hin |
| `error_c` | Geht Richtung 0 | Bleibt > 1°C nach 30 min → Kp zu niedrig |
| `i_correction_c` | < 0.3 während Aufheizen | > 1.0 → Integral baut sich auf (sollte nicht passieren) |
| `target_for_tado_c` | Sinkt wenn Raum wärmer wird | Bleibt bei 25°C → Raum zu groß oder Kp zu niedrig |

**Ergebnis bewerten:**
- **Overshoot < 0.5°C:** Alles gut → weiter zu Phase 2.
- **Overshoot 0.5–1.0°C:** Kp um 0.1–0.2 senken, erneut testen.
- **Overshoot > 1.0°C:** Kp auf 0.5 senken, ggf. Ki auf 0.001 reduzieren.
- **Raum wird nicht warm genug:** Kp um 0.2 erhöhen.

### Phase 2: Halte-Test (4–8 Stunden)

**Ziel:** Prüfen, ob die Temperatur stabil gehalten wird, ohne zu driften.

**Vorgehen:**
1. Lasse den Proxy bei der Zieltemperatur laufen (idealerweise tagsüber).
2. Exportiere die Historie über Entwicklerwerkzeuge oder HA Recorder.

**Worauf achten:**

| Metrik | Guter Wert | Maßnahme bei Abweichung |
|--------|-----------|------------------------|
| Schwankung um Sollwert | ±0.3–0.5°C | ±0.5°C ist normal für TRV-Regelung |
| Mittlere Abweichung | < 0.2°C | Wenn dauerhaft zu kalt: Ki leicht erhöhen (0.004–0.005) |
| Zyklusdauer (Heizen→Idle→Heizen) | 30–90 min | < 15 min = zu viel Oszillation → Kp senken |
| `i_correction_c` im Betrieb | −0.5 bis +0.5 | Wenn > 1.0 oder < −1.0: möglicherweise systematischer Fehler |

**Ergebnis bewerten:**
- **Stabil ±0.5°C:** Perfekt → weiter zu Phase 3.
- **Langsame Drift in eine Richtung:** Ki um 0.001 erhöhen.
- **Schnelle Oszillation:** Kp um 0.1–0.2 senken.

### Phase 3: Nacht-/Langzeit-Test (12–24 Stunden)

**Ziel:** Stabilität über längere Zeiträume bestätigen, inkl. Nachtabsenkung.

**Vorgehen:**
1. Lasse den Proxy über Nacht laufen.
2. Optional: Teste einen Sollwert-Wechsel (z.B. von 21°C auf 18°C abends, morgens zurück).

**Worauf achten:**
- Kein Drift über die Nacht (Temperatur bleibt im ±0.5°C-Band).
- Beim Sollwert-Wechsel: Neues Ziel wird innerhalb von 30–60 min erreicht.
- `i_correction_c` bleibt im Bereich −0.5 bis +0.5.

**Wenn Phase 3 bestanden ist:** Der Raum ist produktionsreif. Du kannst den nächsten Raum einrichten.

---

## Parameter-Referenz

### Kp (Proportional-Korrektur)

| Wert | Verhalten |
|------|-----------|
| 0.0 | Nur Feedforward, keine Korrektur bei Fehler |
| **0.5** | Sanft, wenig Overshoot, langsames Aufheizen |
| **0.8** | Default – guter Kompromiss |
| **1.2** | Aggressiver, schnelleres Aufheizen, mehr Overshoot-Risiko |
| 2.0+ | Nur für große Räume mit träger Heizung |

**Faustregel:** Kp senken = weniger Overshoot, Kp erhöhen = schnelleres Aufheizen.

### Ki (Integral-Korrektur)

| Wert | Verhalten |
|------|-----------|
| 0.0 | Keine Langzeitkorrektur (nur P + Feedforward) |
| **0.001** | Sehr langsam, Korrektur über Stunden |
| **0.003** | Default – Korrektur über ~30 min |
| **0.005** | Schneller, höheres Overshoot-Risiko |
| 0.01+ | Aggressiv – nur bei systematischem Offset |

**Faustregel:** Ki höher = Zieltemperatur wird genauer erreicht, aber Overshoot-Risiko steigt.

### Weitere interne Parameter (nicht über UI einstellbar)

Diese Werte sind in `parameters.py` definiert und wurden für Tado X optimiert:

| Parameter | Wert | Bedeutung |
|-----------|------|-----------|
| `integral_deadband_c` | 0.3°C | Integral sammelt nur, wenn Fehler < 0.3°C |
| `integral_decay` | 0.95 | Integral verliert 5% pro Zyklus, wenn Fehler > Deadband |
| `integral_min_c / max_c` | ±2.0°C | Absolutes Limit für Integral-Aufbau |
| `min_command_interval_s` | 180s | Mindestabstand zwischen Befehlen (Batterieschonung) |
| `min_change_threshold_c` | 0.3°C | Nur senden, wenn Differenz > 0.3°C |
| `min_target_c / max_target_c` | 5 / 25°C | Sicherheitsgrenzen für Tado-Befehle |

---

## Diagnose: Was die Attribute verraten

Öffne in Home Assistant: **Entwicklerwerkzeuge > Zustände** > suche deine Proxy-Entity.

### Gesunder Zustand (Beispiel)

```
feedforward_offset_c: 1.7       ← Tado misst 1.7°C mehr als Raum (normal)
p_correction_c: 0.08            ← kleiner Fehler, kleine Korrektur
i_correction_c: 0.06            ← Integral nahe 0 = stabil
error_c: 0.1                    ← Raum 0.1°C unter Ziel = gut
target_for_tado_c: 19.8         ← Befehl an Tado
is_saturated: false              ← nicht am Limit
regulation_reason: sent(normal_update)
```

### Problematischer Zustand

```
feedforward_offset_c: 8.5       ← ungewöhnlich hoch – Heizkörper extrem heiß
i_correction_c: 1.8             ← Integral sehr hoch = möglicher Overshoot
error_c: -0.8                   ← Raum 0.8°C über Ziel
target_for_tado_c: 25.0         ← am Maximum festgeklemmt
is_saturated: true
regulation_reason: rate_limited(95s)
```

**In diesem Fall:** Kp senken, prüfen ob der externe Sensor korrekt funktioniert.

---

## Fehlerbehebung

### Raum wird gar nicht warm

1. Prüfe `feedforward_offset_c`: Sollte positiv sein (typisch 1–5°C).
2. Prüfe `target_for_tado_c`: Sollte deutlich über der aktuellen Tado-Temperatur liegen.
3. Ist der Tado X Thermostat auf "Manuell"? Automatische Zeitpläne der Tado-App können stören.
4. Ist der externe Sensor erreichbar? Prüfe das Attribut `sensor_degraded` – wenn `true`, ist der Sensor ausgefallen und die Regelung nutzt den letzten gültigen Messwert.

### Starker Overshoot (> 1°C)

1. Kp senken (z.B. von 0.8 auf 0.5).
2. `i_correction_c` prüfen: Wenn > 1.0 während Aufheizen → mögliches Problem, bitte als Issue melden.
3. Raum-Thermik beachten: Fußbodenheizung hat mehr Trägheit als Heizkörper.

### Temperatur pendelt stark (Oszillation)

1. Kp senken (z.B. auf 0.5).
2. `min_command_interval_s` ist auf 180s gesetzt – häufigere Befehle würden die Batterie belasten.
3. Prüfe, ob die Tado-App eigene Zeitpläne aktiv hat (Konflikte).

### Externer Sensor fällt kurz aus

Die Integration überbrückt kurze Sensorausfälle automatisch (**Last-Valid-Bridging**,
Standard: 5 Minuten). Während dieser Zeit:

- Die Regelung läuft mit dem letzten gültigen Messwert weiter.
- Das Attribut `sensor_degraded` zeigt `true`.
- `room_temp_last_valid_age_s` zeigt das Alter des letzten Messwerts in Sekunden.

Wenn der Sensor länger als die Grace-Zeit ausfällt, pausiert die Regelung automatisch.

### Integration reagiert nicht

1. Home Assistant Logs prüfen (Einstellungen > System > Logs > "tadox_proxy").
2. Coordinator-Refresh prüfen: Die Daten werden alle 60s aktualisiert.
3. Dienste-Aufruf testen: Entwicklerwerkzeuge > Dienste > `climate.set_temperature` auf die Proxy-Entity.
