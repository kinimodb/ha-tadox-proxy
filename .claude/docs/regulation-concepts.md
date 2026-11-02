# Regulation Engine – Konzepte & Design-Entscheidungen

Dieses Dokument enthält die nicht-offensichtlichen Design-Entscheidungen der Regelungs-Engine.
Lies es bevor du an `regulation.py` oder `parameters.py` arbeitest.

## Feedforward

Kompensiert den Tado-Sensor-Offset (Heizkörper vs. Raum) sofort.
Das ist die Hauptstellgröße – der PI-Anteil korrigiert nur den Restfehler.

## PI-Korrektur

- **Bewusst klein** gehalten: Kp=0.8, Ki=0.001 (bis v1.5.0: 0.003).
- Integralzeit (1+Kp)/Ki = 30 min: so lange braucht das Integral bei gleichbleibender Abweichung, um so viel Schub zu addieren, wie die Abweichung selbst schon gibt. Räume reagieren in Stunden (#124).
- Die PI-Korrektur ist nur für Restfehler zuständig, nicht für die Hauptregelung.
- Größere Werte führen zu Overshoot wegen der Trägheit des Systems (Heizkörper → Raum).
- Das Thermostat reagiert auf u = Befehl − eigene Messung = (1+Kp)·Fehler + Integral; das Ventil ist offen, solange u > 0.

## Integral Deadband (0.3°C)

Das Integral sammelt **nur** wenn der Fehler innerhalb der Deadband liegt (nahe am Ziel).
Verhindert, dass bei großen Abweichungen (z.B. Kaltstart) ein riesiger Integral-Term aufgebaut wird.

## Anti-Windup

Drei Maßnahmen:
1. **Sättigungs-Block**: Integral wird nicht weiter aufgebaut wenn Output am Limit.
2. **Deadband-Gating mit Decay**: Außerhalb der Deadband wird der bestehende Integral-Term langsam abgebaut.
3. **Asymmetrische Grenzen** (`integral_min_c = -0.5`, `integral_max_c = +2.0`): Ein geschlossenes Ventil lässt sich nicht weiter schließen. Unterhalb von etwa −0.5 verzögert das Integral nur die nächste Heizphase, denn das Ventil öffnet erst wieder, wenn 1.8·e > −I (bei −2: ~1.1 K unter Ziel oder Stunden später; bei −0.5: spätestens ~0.28 K darunter). Oben bleibt +2: Kappen bei +0.5/+1 ließen Thermostate mit proportionalem Band (Ventil öffnet proportional zur Abweichung) zu kalt.

Ventil-bewusste bedingte Integration (kein negatives Integrieren, solange u unter einer Grenze liegt) wurde getestet und verworfen: Räume laufen warm, weil Thermostate, die um u = 0 takten, das Integral nach oben schaukeln. Zahlen und die übrigen verworfenen Varianten: `docs/adr/0002-integral-windup-low-gain-and-tight-floor.md`.

## Adaptive Gain Scheduling

Automatische Kp-Skalierung abhängig vom Betriebszustand:
- **Kaltstart** (große Abweichung): Multiplikator bis 1.5× für schnelleres Aufheizen.
- **Nahe Ziel**: Multiplikator 1.0× für stabiles Verhalten.
- Interpolation über den vollen Bereich zwischen den Zonenschwellen.
- Schwellen (cold-start/near-target) sind im Options-Flow konfigurierbar.

## Rate Limiting (180s)

Batterieschonung für Tado X TRVs. Die TRVs kommunizieren per Funk und jeder Befehl kostet Batterie.
**Nicht verkürzen** ohne triftigen Grund.
