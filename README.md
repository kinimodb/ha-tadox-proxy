# Tado X Proxy Thermostat (HACS) — Hybrid Control

Eine **Home Assistant Custom Integration** für **Tado X Thermostate**, die als Proxy-Climate-Entity arbeitet:
- Regelung auf Basis eines **externen Raumtemperatursensors**
- Stellgröße ist der **an Tado gesendete Setpoint** (Ventil bleibt Black Box)
- Robustheit für träge Heizkörperstrecken (Nachlauf, Overshoot) und Störungen (z. B. Lüften)

## Status

- **Branch:** `feature/hybrid-control`
- **Reifegrad:** BETA / Testbetrieb (v0.3.x)
- **Hinweis:** `main` bleibt stabil; dieser Branch ist der Entwicklungsstand für die Hybrid-Regelstrategie.

## Docs Index (Start here)

- **Roadmap / Milestones:** `ROADMAP.md`
- **Regelstrategie (Hybrid + Window + Command Policy):** `docs/control_strategy.md`
- **Parameter Map (active vs legacy):** `docs/parameters.md`
- **ADR (Branching/Policy):** `docs/adr/0001-hybrid-control-strategy.md`

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
- **Window Handling** (sensor-basiert via binary_sensor)

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
- `kp` → Hybrid `kp` (wirksam)
- `ki` → Hybrid `ki_small` (wirksam, konservativ begrenzt)
- `kd` → derzeit **ohne Wirkung** im Hybrid-Regler (Legacy-Key)

Details: `docs/parameters.md`

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
- `hybrid_target_c`: Regler-Target (vor Sendepolicy)
- `hybrid_desired_target_c`: Reglerwunsch nach Clamp/Override
- `hybrid_command_target_c`: tatsächlich geplanter Sendezielwert (nach Hygiene/Policy)

### Command Policy
- `command_effective_min_interval_s`
- `command_effective_max_step_up_c`
- `command_fast_recovery_active` + `command_fast_recovery_reason`

### Window Handling
- `window_open`, `window_forced`, `window_open_pending`
- `window_open_delay_remaining_s`, `window_close_hold_remaining_s`

---

## Credits

Inspiriert von „Versatile Thermostat“ (Patterns), aber spezialisiert auf die Eigenheiten der Tado X Hardware.

<!-- Commit: docs: add docs index and fix references for hybrid branch -->
