# Regulation Engine – Konzepte & Design-Entscheidungen

Dieses Dokument enthält die nicht-offensichtlichen Design-Entscheidungen der Regelungs-Engine.
Lies es bevor du an `regulation.py` oder `parameters.py` arbeitest.

## Feedforward

Kompensiert den Tado-Sensor-Offset (Heizkörper vs. Raum) sofort.
Das ist die Hauptstellgröße – der PI-Anteil korrigiert nur den Restfehler.

## PI-Korrektur

- **Bewusst klein** gehalten: Kp=0.8, Ki=0.003.
- Integralzeit (1+Kp)/Ki = 10 min: so lange braucht das Integral bei gleichbleibender Abweichung, um so viel Schub zu addieren, wie die Abweichung selbst schon gibt.
- Die Vorabversionen von v1.6.0 liefen mit Ki=0.001 (Integralzeit 30 min). Das verlangsamte im Feldtest in einem klaren Fall das letzte Zehntel, und der Gewinn für Kessel auf Anforderung hielt in den echten Läufen des Testers nicht; v1.6.0 ist zurück bei 0.003 (`docs/adr/0003-ki-default-back-to-0003.md`). Heizt ein Ventil nach langsamer Annäherung weiter, folgt der Kessel dem Braucht-Wärme-Sensor, und der Raum bekommt bei Bedarf einen eigenen kleineren Ki (TUNING.md). Keinen Default für alle wegen eines Setups ändern, und ein Update ändert nie einen gespeicherten Wert (keine Migration, die Optionen löscht oder umschreibt).
- Die PI-Korrektur ist nur für Restfehler zuständig, nicht für die Hauptregelung.
- Größere Werte führen zu Overshoot wegen der Trägheit des Systems (Heizkörper → Raum).
- Arbeitsmodell, kein bewiesenes Ventilgesetz: u = Befehl − eigene Messung des Thermostats = (1+Kp)·Fehler + Integral. Bei u > 0 verlangt der Befehl, sobald er gesendet ist, dass das Ventil öffnet (Sollwert über der eigenen Messung). Das Tado X meldet sein Ventil nicht (keine Ventilstellung, kein Heizbedarf, kein `hvac_action`); bestätigt ist das Gesetz also nicht.

## Integral Deadband (0.3°C)

Das Integral sammelt **nur** wenn der Fehler innerhalb der Deadband liegt (nahe am Ziel).
Verhindert, dass bei großen Abweichungen (z.B. Kaltstart) ein riesiger Integral-Term aufgebaut wird.

## Anti-Windup

Vier Maßnahmen:
1. **Sättigungs-Block**: Integral wird nicht weiter aufgebaut wenn Output am Limit.
2. **Deadband-Gating mit Decay**: Außerhalb der Deadband wird der bestehende Integral-Term langsam abgebaut.
3. **Asymmetrische Grenzen** (`integral_min_c = -0.5`, `integral_max_c = +2.0`): Ein geschlossenes Ventil lässt sich nicht weiter schließen. Unterhalb von etwa −0.5 verzögert das Integral nur die nächste Heizphase, denn nach dem Arbeitsmodell öffnet das Ventil erst wieder, wenn 1.8·e > −I (bei −2: ~1.1 K unter Ziel oder Stunden später; bei −0.5: etwa 0.3 K darunter – rechnerisch 0.28, doch der Befehl wird auf 0.1 gerundet und erst gesendet, wenn er sich um mindestens 0.3 ändert). Die Wartezeit sinkt, ist aber nicht weg: ein sehr gut gedämmter Raum kühlt so langsam aus, dass er auch dann noch Stunden braucht. Oben bleibt +2: Kappen bei +0.5/+1 ließen Thermostate mit proportionalem Band (Ventil öffnet proportional zur Abweichung) zu kalt.
4. **Leck in der Deadband** (`integral_leak_time_s = 900`, Option „Integral leak time“ in Minuten, 0 = aus): Innerhalb der Deadband verliert das Integral je Zyklus `Integral × dt / Leckzeit`. Ein anhaltender Fehler e hält es bei Ki × Leckzeit × e (2.7·e) statt beim Deckel. Grund: Raumsensoren melden in 0.2-K-Schritten; ein Ziel zwischen zwei Schritten liest sich stundenlang als 0.1 K Fehler, das Integral lief bis +2 und der Raum schoss 0.5–0.9 K über (Gästezimmer 25×, WC 17× in knapp drei Wochen; Schlafzimmer 23.11.). Preis: Räume, die einen dauerhaften Schub brauchen, bleiben im Modell bis 0.15 K (bei 0.6 K Bedarf) bzw. 0.25 K (bei 1.4 K) unter Ziel. Verworfen: Totzone |e| < 0.15 (hilft nicht, wo die Sensorschritte das Ziel treffen, schaltet das Integral sonst ganz ab, unter L1 warm), Deckel P1/P2, längere Lecks. `docs/adr/0004-integral-leak-near-target.md`.

Ventil-bewusste bedingte Integration (kein negatives Integrieren, solange u unter einer Grenze liegt) wurde getestet und verworfen: Räume laufen warm, weil Thermostate, die um u = 0 takten, das Integral nach oben schaukeln. Zahlen und die übrigen verworfenen Varianten: `docs/adr/0002-integral-windup-low-gain-and-tight-floor.md`; dessen Ki-Teil ersetzt `docs/adr/0003-ki-default-back-to-0003.md`, die Untergrenze −0.5 gilt weiter.

## Adaptive Gain Scheduling

Automatische Kp-Skalierung abhängig vom Betriebszustand:
- **Kaltstart** (große Abweichung): Multiplikator bis 1.5× für eine stärkere Reaktion (ob der Raum dadurch schneller warm wird, hängt vom Heizkörper ab und ist nicht zugesagt).
- **Nahe Ziel**: Multiplikator 1.0× für stabiles Verhalten.
- Interpolation über den vollen Bereich zwischen den Zonenschwellen.
- Schwellen (cold-start/near-target) sind im Options-Flow konfigurierbar.

## Rate Limiting (180s)

Batterieschonung für Tado X TRVs. Die TRVs kommunizieren per Funk und jeder Befehl kostet Batterie.
**Nicht verkürzen** ohne triftigen Grund.
