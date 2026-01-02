# Control Strategy (Hybrid) — Tado X Proxy Thermostat

Dieses Dokument beschreibt die **aktuelle Regelstrategie** im Branch `feature/hybrid-control`.
Es ist bewusst praxisorientiert: Welche Signale nutzen wir, welche Logik läuft wo, und wie liest man die Telemetrie.

> Wichtig: Der Tado X TRV wird als **Aktuator-Blackbox** betrachtet.
> Stellgröße ist der an Tado gesendete **Setpoint**. Das Ventilverhalten ergibt sich daraus indirekt.

---

## 1) Architektur in 3 Ebenen (entscheidend)

### Ebene A — Hybrid-Regler (Reglerlogik)
**Datei:** `custom_components/tadox_proxy/hybrid_regulation.py`

Liefert den **idealen Target-Setpoint**, um den Raum Richtung Sollwert zu bringen:
- Bias-Estimator (Langzeit-Offset)
- State Machine: BOOST / HOLD / COAST
- P (+ kleines I optional)
- Trend/Prediction (Overshoot-Guard)

Output: `hybrid_target_c` (bzw. intern der “Reglerwunsch”)

---

### Ebene B — Window Handling (Störung / Override)
**Datei:** `custom_components/tadox_proxy/climate.py` (sensor-basiert)

Window Handling ist in diesem Branch **deterministisch und sensorbasiert** (binary_sensor):
- Open-Delay: erst nach X Sekunden/Minuten „offen“ wird Frostschutz erzwungen
- Close-Hold: nach dem Schließen wird Frostschutz für Y Sekunden/Minuten gehalten

**Wenn Window-forced aktiv ist, ist das ein Output-Override:**  
→ Tado-Setpoint wird auf Frostschutz gesetzt, unabhängig vom Reglerwunsch.

---

### Ebene C — Command Policy (Sendepfad / Hygiene)
**Datei:** `custom_components/tadox_proxy/climate.py`

Übersetzt den Reglerwunsch in **tatsächliche Tado-Kommandos**, ohne Flattern und ohne unnötige Cloud-Last:
- Min-Delta Guard
- Rate-Limit (normal)
- Step-Up Limit (normal)
- Urgent Decrease (runter sofort)
- Fast-Recovery (bounded), wenn es wirklich kalt ist / großer Gap / BOOST
- Window-Resume: kontrolliertes Wiederanlaufen nach Frostschutz

Output: `hybrid_command_target_c` + tatsächliches Sendeverhalten (`tado_last_sent_*`)

---

## 2) Signale / Inputs

### Raumtemperatur (Ist)
- Primär: externer Raumtemperatursensor (Proxy `current_temperature`)
- Das ist die Regelgröße.

### Raum-Sollwert
- Proxy `temperature` (vom Nutzer/Automationen gesetzt)

### Tado Telemetrie (nur Aktuatorfeedback)
- `tado_internal_temperature_c` (Temperatur am Thermostatkopf)
- `tado_setpoint_c` (aktueller Setpoint am TRV / Source Entity)

**Interpretation:**  
TRV-intern kann deutlich über Raumtemperatur liegen (Heizkörper-/Kopf-Nachlauf). Das ist normal.

---

## 3) Hybrid-Regler im Detail (Ebene A)

### 3.1 Bias-Estimator (Langzeitlernen)
Ziel: systematische Abweichungen ohne Integrator-Windup korrigieren.

- `hybrid_bias_c`: langsam lernender Offset
- Lernen findet nur in „ruhigen“ Phasen statt (nahe steady state, Trend klein).

### 3.2 State Machine
- **BOOST**: Raum deutlich zu kalt oder Temperatur fällt schnell → aggressiver öffnen
- **HOLD**: nahe Soll → geringe Aktivität, stabil halten
- **COAST**: zu warm / Overshoot erwartet → Ventil schließen (Target runter)

Telemetrie:
- `hybrid_mode` und `hybrid_mode_reason`

### 3.3 P (+ kleines I)
- `hybrid_error_c = setpoint - room_temp`
- `hybrid_p_term_c = kp * error`
- `hybrid_i_small_c` optional (sehr konservativ)
- Der Reglerwunsch wird als `hybrid_target_c` (bzw. `hybrid_desired_target_c`) sichtbar.

### 3.4 Prediction (Overshoot-Guard)
- `hybrid_predicted_temp_c` ist eine einfache lineare Projektion:
  `T_pred = T_now + dTdt_ema * horizon`
- Wird genutzt, um frühzeitig COAST zu wählen, wenn Overshoot wahrscheinlich ist.
- In Window-forced Phasen ist Prediction nicht maßgeblich (Override).

---

## 4) Window Handling (Ebene B)

### 4.1 Sensorzustand
- `window_open` zeigt den aktuellen Fensterkontakt-Status (binary_sensor on/off).

### 4.2 Delay/Timers
- `window_open_delay_min`: wie lange „offen“, bis Frostschutz erzwungen wird
- `window_close_delay_min`: wie lange nach „zu“ Frostschutz gehalten wird

Telemetrie:
- `window_open_pending`, `window_open_delay_remaining_s`
- `window_close_hold_remaining_s`

### 4.3 Override Semantik
- `window_forced: true` bedeutet:
  - Kommandiert wird Frostschutz (z. B. 5°C)
  - Reglerzustand wird nicht „kaputtgerechnet“ (Output Override)

---

## 5) Command Policy / Hygiene (Ebene C)

### 5.1 Zielwerte (wichtigste Unterscheidung)
- `hybrid_desired_target_c`: Reglerwunsch nach Clamp/Override
- `hybrid_command_target_c`: tatsächlich geplanter Sendezielwert (nach Policy)
- `tado_setpoint_c`: aktueller Setpoint am TRV (Source)
- `tado_last_sent_target_c`: letzter vom Proxy gesendeter Setpoint

### 5.2 Guards / Limits
- Min-Delta Guard: keine Sends unter `command_min_send_delta_c` (z. B. 0.2°C)
- Rate-Limit: normaler Mindestabstand zwischen Sends (`command_effective_min_interval_s`, typ. 180s)
- Step-Up Limit: Erhöhungen werden begrenzt (`command_effective_max_step_up_c`, typ. 0.5°C)

Decreases sind “urgent” (runter sofort), um schnell schließen zu können.

### 5.3 Fast-Recovery (bounded)
Fast-Recovery ist **keine Reglerlogik**, sondern eine Sendepfad-Policy:
- verkürzt das Rate-Limit (z. B. 20s)
- erhöht Step-Up Limit (z. B. 2.0°C)
- aktiviert bei:
  - BOOST, oder
  - großem room_error, oder
  - großem gap zwischen desired_target und aktuellem Tado-Setpoint

Telemetrie:
- `command_fast_recovery_active`
- `command_fast_recovery_reason`

### 5.4 Window-Resume
Nach Window-Frostschutz darf die Policy **kontrolliert** “schneller wieder einsteigen”, damit kein minutenlanges Hochkriechen entsteht.
Dieses Verhalten ist als Reason sichtbar (`regulation_reason` enthält entsprechende Marker).

---

## 6) Reading Guide: Welche Attribute zuerst?

Wenn du schnell debuggen willst:

1) Window:
   - `window_open`, `window_forced`, `window_open_pending`
2) Tado Ground Truth vs Send:
   - `tado_setpoint_c` vs `tado_last_sent_target_c` (+ `tado_last_sent_age_s`)
3) Reglerwunsch vs Command:
   - `hybrid_desired_target_c` vs `hybrid_command_target_c`
4) Warum-String:
   - `regulation_reason`

---

## 7) Parameterübersicht / Single Source of Truth

Für eine kompakte Karte “aktiv vs legacy vs deprecated” siehe:
- `docs/parameters.md`

---

<!-- Commit: docs: update control strategy to match current hybrid + command policy + window handling -->
