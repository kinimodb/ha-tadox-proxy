# Control Strategy (Hybrid) — Tado X Proxy Thermostat

## Ziel
Alltagstaugliche Raumtemperaturregelung über Tado X, wobei die einzige Stellgröße die an Tado gesendete Zieltemperatur (Tado-Setpoint) ist. Die Strategie soll:
- stabil sein (wenig Überschwingen),
- robust gegenüber Störungen (z. B. Tür offen/zu) sein,
- mit begrenzter Stellfrequenz funktionieren (Rate-Limit),
- Setpoint-Nervosität minimieren,
- den systematischen Sensor-/Montage-Offset zwischen Raumfühler und TRV (Thermostat) ausgleichen.

## Grundidee
Die Regelung besteht aus 4 Bausteinen:
1) **Bias-Estimator (langsames Offset-Lernen)**: lernt einen dauerhaften Setpoint-Offset, der nötig ist, um den Raum-Sollwert zu treffen (kompensiert TRV-/Montagebias).
2) **Schneller Komfort-Regler (P + optional kleine I-Komponente)**: reagiert auf aktuelle Abweichungen.
3) **Zustandsautomat (BOOST / HOLD / COAST)**: bildet Nichtlinearitäten und Lastsprünge robust ab (Tür-offen/zu, Nachlauf der Heizung).
4) **Command Hygiene**: Rate-Limit + Step-Limit + Mindeständerung, um Flattern zu reduzieren und Gerät/Cloud zu schonen.

Damit trennen wir:
- langsames „Offset-Lernen“ (Bias) von
- schneller „Komfort-Reaktion“ (P, ggf. kleine I)
und kapseln Lastsprünge über Zustände statt nur über einen universellen Regler.

---

## Signale und Definitionen

### Eingänge
- `T_room` (°C): Raumtemperatur (Primärsensor)
- `T_set_room` (°C): gewünschter Raum-Sollwert (vom Nutzer)
- `T_trv` (°C): interne Tado-Temperatur (optional für Diagnose, nicht zwingend für Regelkern)
- `hvac_mode`: heat/off
- optional: `door_open` (bool) / `window_open` (bool) / presence (nicht vorausgesetzt)

### Abgeleitete Größen
- Fehler: `e = T_set_room - T_room`
- Trend (°C/s): `dTdt = EMA( (T_room - T_room_prev) / dt )`
  - EMA-Glättung: `dTdt = alpha * raw + (1-alpha) * dTdt_prev`

### Stellgröße
- `T_tado_target` (°C): an Tado zu sendende Zieltemperatur

---

## Parameter (Defaults)
> Werte sind Startwerte; Tuning erfolgt empirisch.

### General
- `control_interval_s = 60`
- `tado_min_temp = 7.0`
- `tado_max_temp = 35.0`

### Command Hygiene
- `min_command_interval_s = 180`  (Rate-Limit)
- `min_send_delta_c = 0.2`        (Mindeständerung, sonst nicht senden)
- `max_step_delta_c = 0.5`        (Step-Limit pro Send)
- `max_offset_c = 8.0`            (Clamp für Gesamt-Offset relativ zu Raum-Soll)

### Bias-Estimator (langsames Lernen)
- `bias_tau_s = 4 * 3600`         (Zeitkonstante 4h)
- `bias_deadband_c = 0.1`         (nur lernen, wenn |e| klein genug / stabil)
- `bias_rate_limit_c_per_h = 0.5` (zusätzliche Sicherheit)
- `bias_min_c = -5.0`, `bias_max_c = +5.0`

### Komfort-Regler (schnell)
- `kp = 5.0`                      (Startwert)
- `ki_small = 0.0002`             (optional; kann 0 sein, wenn Bias genügt)
- `i_small_min_c = -2.0`, `i_small_max_c = +2.0`

### Trend / Prädiktion
- `dTdt_alpha = 0.25`
- `trend_cool_threshold_c_per_min = +0.03`   (steigend)
- `trend_drop_threshold_c_per_min = -0.03`   (fallend)
- `predict_horizon_s = 900`                  (15 min)
- `overshoot_guard_c = 0.2`                  (Puffer)

### Zustandsautomat (Schwellwerte)
- `boost_error_on_c = 0.6`       (BOOST, wenn e groß positiv)
- `boost_error_off_c = 0.2`      (BOOST endet, wenn e klein)
- `coast_error_on_c = -0.3`      (COAST, wenn e negativ)
- `coast_error_off_c = -0.1`     (COAST endet, wenn e nur leicht negativ)
- `hold_deadband_c = 0.1`        (HOLD nahe Soll)

### BOOST-Setpoint
- `boost_target_c = 25.0`        (oder min(tado_max_temp, T_set_room + max_offset_c))
- `boost_max_minutes = 30`

### COAST-Setpoint
- `coast_target_c = 7.0`         (min temp), alternativ `T_set_room - 2.0` geklemmt

---

## Zustandsautomat

### Zustände
- `BOOST`: schnelle Aufheizung bei großem Wärmebedarf oder schnellem Temperaturabfall
- `HOLD`: stabil nahe Soll, minimale Aktivität
- `COAST`: Nachlauf/Überschwingen abbauen, Heizung „auslaufen lassen“

### Übergänge (ohne Türsensor)
1) In `BOOST`, wenn:
   - `e >= boost_error_on_c`
   - ODER `dTdt <= trend_drop_threshold` (Temperatur fällt schnell)
2) Aus `BOOST` nach `HOLD`, wenn:
   - `e <= boost_error_off_c` UND `dTdt > trend_drop_threshold` (Abfall vorbei)
   - ODER `boost_max_minutes` überschritten
3) In `COAST`, wenn:
   - `e <= coast_error_on_c`
   - ODER Prädiktion: `T_room + dTdt * predict_horizon_s >= T_set_room + overshoot_guard_c`
4) Aus `COAST` nach `HOLD`, wenn:
   - `e >= coast_error_off_c` (nicht mehr deutlich zu warm)
5) In `HOLD` sonst, zusätzlich gilt:
   - Wenn `|e| <= hold_deadband_c` und Trend klein: HOLD beibehalten

> Optional mit Türsensor:
- Wenn `door_open == true`, BOOST-Schwelle senken (früher boosten) und COAST erschweren.
- Wenn `door_open` von true→false wechselt: Bias-Lernen kurz einfrieren (z. B. 30 min), um Windup zu vermeiden.

---

## Bias-Estimator (Offset-Lernen)

### Ziel
`bias` bildet den langfristigen Setpoint-Offset ab, der nötig ist, um den Raum-Sollwert zu erreichen, ohne dass schnelle Lastsprünge (Tür) den Integrator „aufladen“.

### Update-Regel
Nur aktualisieren, wenn:
- `hvac_mode == heat`
- `state != BOOST` (sonst lernt Bias fälschlich die Boost-Phase)
- `|e| <= bias_deadband_c`
- `|dTdt|` klein (z. B. < 0.01 °C/min), damit stationär

Dann:
- `bias += (e / bias_tau_s) * dt`
- zusätzlich begrenzen:
  - `bias = clamp(bias, bias_min_c, bias_max_c)`
  - pro Stunde Änderungsrate limitieren (`bias_rate_limit_c_per_h`)

Interpretation:
- ist Raum dauerhaft 0.3°C zu kalt nahe Soll, bias steigt langsam → Tado-Setpoint wird langfristig etwas höher gefahren.

---

## Komfort-Regler (P + optional kleine I)

### Motivation
P reagiert schnell. Eine kleine I-Komponente kann Restfehler ausgleichen, wird aber bewusst klein gehalten, weil Bias den statischen Anteil trägt.

Berechnung:
- `p = kp * e`
- `i_small += e * ki_small * dt` (nur in HOLD, nur wenn nicht gesättigt)
- `i_small = clamp(i_small, i_small_min_c, i_small_max_c)`

Anti-Windup:
- `i_small` einfrieren, wenn:
  - aktueller Output gesättigt ist (`T_tado_target` am Clamp)
  - Zustand BOOST oder COAST aktiv ist

---

## Stellwertbildung

1) Basis:
- `base = T_set_room + bias`

2) Zustandsabhängige Logik:
- Wenn `BOOST`:
  - `raw_target = max(boost_target_c, base + p)` (einfach robust)
- Wenn `COAST`:
  - `raw_target = coast_target_c` (oder clamp(base - 2.0))
- Wenn `HOLD`:
  - `raw_target = base + p + i_small`

3) Clamp:
- `raw_target = clamp(raw_target, tado_min_temp, tado_max_temp)`
- zusätzlicher Clamp relativ zu Raum-Soll:
  - `raw_target = clamp(raw_target, T_set_room - max_offset_c, T_set_room + max_offset_c)`

---

## Command Hygiene (Senden an Tado)

Wir senden nur, wenn alle Bedingungen erfüllt sind:
- seit letztem Senden: `now - last_send >= min_command_interval_s`
- UND Änderung groß genug: `|raw_target - last_sent_target| >= min_send_delta_c`

Zusätzlich Step-Limit:
- `target = last_sent_target + clamp(raw_target - last_sent_target, -max_step_delta_c, +max_step_delta_c)`

Damit verhindern wir:
- kleine, häufige Änderungen (min_send_delta)
- große Sprünge (step limit)
- zu häufiges Senden (rate limit)

Sonderfall „Urgent“ (optional, sehr vorsichtig):
- Bei sehr großem positiven Fehler (`e >= 1.2°C`) darf einmalig das Rate-Limit verkürzt werden (z. B. auf 60–90s), aber nur in Richtung HEIZEN (Boost), nie als Dauerzustand.

---

## Logging / Telemetrie (Pflichtfelder)
Für Diagnose und Tuning sollen pro control cycle geloggt werden:
- `T_room`, `T_set_room`, `e`, `dTdt`
- `state` (BOOST/HOLD/COAST)
- `bias`, `p`, `i_small`
- `raw_target`, `sent_target`, `send_reason` (rate-limited / step-limited / deadband / urgent)
- `tado_internal_temp` (optional)
- `hvac_action` / `hvac_mode`

---

## Tuning-Leitfaden (Kurz)
1) **Command Hygiene zuerst** (rate/step/min_delta) so einstellen, dass Setpoints nicht flattern.
2) `bias_tau` so wählen, dass Bias nicht auf Tür-Events reagiert (Stundenbereich).
3) `kp` erhöhen bis Reaktion gut, aber ohne Überschwingen/Setpoint-Jitter.
4) `ki_small` erst zuletzt (oder 0 lassen), wenn Bias + P nicht reichen.
5) Schwellen der Zustandslogik so anpassen, dass:
   - BOOST früh genug bei Tür offen greift,
   - COAST früh genug bei Nachlauf greift,
   - HOLD die meiste Zeit aktiv ist.

