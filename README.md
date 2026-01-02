# Tado X Proxy Thermostat (HACS) — Hybrid Control

Eine **Home Assistant Custom Integration** für **Tado X Thermostate**, die als Proxy-Climate-Entity arbeitet:
- Regelung auf Basis eines **externen Raumtemperatursensors**
- Stellgröße ist der **an Tado gesendete Setpoint** (Ventil bleibt Black Box)
- Robustheit für träge Heizkörperstrecken (Nachlauf, Overshoot) und Störungen (z. B. Lüften)

## Status

- **Branch:** `feature/hybrid-control`
- **Reifegrad:** BETA / Testbetrieb (v0.3.x)
- **Hinweis:** `main` bleibt stabil; dieser Branch ist der Entwicklungsstand für die Hybrid-Regelstrategie.

Technische Leitdokumente:
- Regelstrategie-Spezifikation: `docs/control_strategy.md`
- ADR / Branching-Policy: `custom_components/tadox_proxy/docs/adr/0001-hybrid-control-strategy.md`

---

## Problem (Warum überhaupt Proxy?)

Tado X misst am Thermostatkopf nahe am Heizkörper. Typische Effekte:
1) **Hitzestau am TRV:** Ventil schließt zu früh, Raum bleibt zu kalt
2) **Oszillation:** Auf/zu-Auf/zu („Sägezahn“)
3) **Offset-Drift:** Systematische Abweichungen zwischen Raumfühler und TRV-Verhalten

---

## Lösung (Kurzfassung)

Der Proxy erstellt eine neue Climate-Entity (z. B. `climate.gz_proxy_thermostat`), die:
- den Raum-Sollwert in Home Assistant entgegennimmt
- die Regelung gegen den externen Sensor macht
- daraus einen geeigneten **Tado-Setpoint** ableitet und sendet

Die Hybrid-Regelung kombiniert:
- **Bias-Estimator** (langsames Offset-Lernen)
- **State Machine**: BOOST / HOLD / COAST
- **P (+ kleines I optional)**
- **Command Hygiene** (Min-Delta, Rate-Limit, Step-Limit)
- **Fast-Recovery** (situativ, um nach Frost/Lüften nicht „in 0.5°C-Schritten alle Minuten“ zu kriechen)

---

## Installation (HACS)

1) HACS → Integrationen → Custom Repositories → `https://github.com/kinimodb/ha-tadox-proxy`
2) Installieren → Home Assistant Neustart
3) Einstellungen → Geräte & Dienste → Integration hinzufügen → **Tado X Proxy Thermostat**

---

## Setup (Config Flow)

Beim Hinzufügen der Integration wählst du:
- **Source Entity:** das originale Tado X Thermostat (Climate)
- **External Temperature Entity:** dein Raumtemperatursensor (Sensor)

Optional (über Optionen / später konfigurierbar):
- External Humidity Entity (falls vorhanden)
- Window Handling (Fensterkontakt)

---

## Optionen (Options Flow)

### Fensterlogik (sensor-basiert)
- `window_open_enabled` (bool)
- `window_sensor_entity_id` (binary_sensor)
- `window_open_delay_min` (min): „wie lange offen, bis Frostschutz erzwungen wird“
- `window_close_delay_min` (min): „wie lange nach Schließen Frostschutz noch gehalten wird“

### Tuning (Legacy-UI-Keys, aber sinnvoll nutzbar)
Im Options Flow existieren `kp`, `ki`, `kd`.

**Aktuelles Mapping im Hybrid-Branch:**
- `kp` → **hybrid_kp** (wirksam)
- `ki` → **hybrid_ki_small** (wirksam, konservativ begrenzt)
- `kd` → **derzeit ohne Wirkung** im Hybrid-Regler (Legacy-Key)

Hinweis: Das vollständige Regelkonzept inkl. Parameterbedeutung ist in `docs/control_strategy.md` dokumentiert.

---

## Telemetry / Debug (wichtig für Tests)

Die Proxy-Entity stellt bewusst viele Attribute bereit. Für schnelle Diagnose reichen meist diese:

### Ground Truth & Send-Telemetrie
- `tado_setpoint_c`: aktueller Setpoint der Source-Entity
- `tado_last_sent_target_c`: letzter vom Proxy gesendeter Setpoint
- `tado_last_sent_age_s`: Alter des letzten Send in Sekunden
- `regulation_reason`: kompakter „Warum“-String (Rate-Limit, Min-Delta, Window, Fast-Recovery, …)

### Hybrid-Regler
- `hybrid_mode`: boost / hold / coast
- `hybrid_mode_reason`: Begründung des Zustandswechsels
- `hybrid_error_c`: Soll-Ist-Abweichung (°C)
- `hybrid_target_c`: vom Regler gewünschter Target-Setpoint (vor Sendepolicy)
- `hybrid_desired_target_c`: gewünschter Zielwert (nach Clamp/Override)
- `hybrid_command_target_c`: tatsächlich geplanter Sende-Zielwert (nach Hygiene/Policy)

### Command Policy
- `command_effective_min_interval_s`: effektives Rate-Limit (z. B. 180 normal / 20 fast recovery)
- `command_effective_max_step_up_c`: effektives Step-Up-Limit (z. B. 0.5 normal / 2.0 fast recovery)
- `command_fast_recovery_active` + `command_fast_recovery_reason`

### Window Handling
- `window_open`, `window_forced`, `window_open_pending`
- `window_open_delay_remaining_s`, `window_close_hold_remaining_s`

---

## Test-Workflow (empfohlen)

1) Stabiler Zielwert (z. B. 17°C), Raum mehrere Stunden beobachten
2) Lüften-Event (Fensterkontakt) und Verhalten prüfen:
   - Frostschutz erzwingen nach Delay
   - sauberes Resume (kein minutenlanges Hochkriechen)
3) CSV exportieren (History) und anhand von Setpoint/Temperaturen analysieren

---

## Credits

Inspiriert von „Versatile Thermostat“ (Patterns), aber spezialisiert auf die Eigenheiten der Tado X Hardware.

<!-- Commit: docs: refresh README for hybrid control branch -->
