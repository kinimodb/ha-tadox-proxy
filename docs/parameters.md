# Parameter Map — Hybrid Control Branch

Ziel dieses Dokuments: Klarheit darüber, **welche Parameter aktuell wirksam sind**, wo sie definiert werden,
und welche Werte nur aus Legacy-/UI-Kompatibilitätsgründen existieren.

Dieses Dokument ändert **kein Verhalten**. Es ist eine “Landkarte” für Tests, Tuning und neue Leser.

---

## 1) Ebenen

### A) Regler-Ebene (Hybrid-Regler)
Datei: `custom_components/tadox_proxy/hybrid_regulation.py`

Diese Parameter beeinflussen, **welchen Target-Setpoint der Regler idealerweise möchte**.

**Aktiv (wirksam):**
- `HybridConfig.kp` → proportionaler Verstärkungsfaktor
- `HybridConfig.ki_small` → kleiner Integrator (konservativ)
- `bias_*` → Bias-Estimator (Langzeit-Offset)
- `boost_*`, `hold_*`, `coast_*` → State Machine Schwellwerte/Ziele
- `trend_*`, `predict_horizon_s`, `overshoot_guard_c` → Trend / Overshoot-Guard
- `window_open_*` (falls in Regler aktiv genutzt) → Trend-basierte Window-Erkennung (im Proxy meist deaktiviert)

**Hinweis:** In diesem Branch wird Window Handling primär **sensor-basiert** im Sendepfad gemacht (siehe unten).

---

### B) Sendepfad / Command Policy
Datei: `custom_components/tadox_proxy/climate.py`

Diese Parameter beeinflussen, **wie aggressiv der gewünschte Regler-Target in echte Tado-Kommandos übersetzt wird**.

**Aktiv (wirksam):**
- `MIN_SEND_DELTA_C` → keine Mikro-Updates
- `MAX_STEP_UP_C` → Standard Step-Up-Limit
- `RegulationConfig.min_command_interval_s` → Standard Rate-Limit
- Fast-Recovery:
  - `FAST_RECOVERY_MIN_INTERVAL_S`
  - `FAST_RECOVERY_MAX_STEP_UP_C`
  - Trigger-Schwellen (z. B. `FAST_RECOVERY_ERROR_C`, `FAST_RECOVERY_TARGET_GAP_C`)
- Window Handling (sensor-basiert):
  - `window_open_enabled`
  - `window_sensor_entity_id`
  - `window_open_delay_min` / `window_close_delay_min` (in Optionen gespeichert, intern Sekunden)

**Telemetrie-Keys (nur Debug, nicht steuernd):**
- `tado_last_sent_*`, `command_effective_*`, `regulation_reason`, `hybrid_*`

---

### C) Options Flow / UI-Keys
Dateien: `custom_components/tadox_proxy/config_flow.py` + Translations/strings

Diese Parameter sind sicht-/editierbar im HA UI.

**Aktiv (wirksam, Mapping):**
- `kp` → Hybrid `kp`
- `ki` → Hybrid `ki_small` (konservativ begrenzt)

**Legacy (derzeit ohne Wirkung im Hybrid-Regler):**
- `kd` → UI-Key vorhanden, aktuell keine Hybrid-Verwendung

**Rationale:** Legacy Keys bleiben bis zum Abschluss von M1 (Stabilisierung), um UX und bestehende Installationen
nicht während der Tuning-Phase zu destabilisieren.

---

## 2) Aktive Defaults (Kurzreferenz)

> Achtung: Diese Liste beschreibt den *aktuellen Code-Stand* und kann sich im Verlauf ändern.
> Maßgeblich ist immer die jeweilige Datei.

- Standard Rate-Limit: `RegulationConfig.min_command_interval_s` (typisch 180s)
- Standard Step-Up: `MAX_STEP_UP_C` (typisch 0.5°C)
- Fast-Recovery: `FAST_RECOVERY_MIN_INTERVAL_S` (typisch 20s), `FAST_RECOVERY_MAX_STEP_UP_C` (typisch 2.0°C)
- Hybrid: `kp` (aus Options), `ki_small` (aus Options, konservativ)

---

## 3) Deprecation Plan (nach M1 Exit)

Nach erfolgreicher Stabilisierung (M1 Exit):
- Entfernen/Umbenennen von Legacy UI-Keys (`kd`)
- Zusammenführung/Externalisierung relevanter Parameter in Options Flow (nur wenn nötig)
- Entfernen nicht genutzter PID-Regelmodule/Dateien (Refactor-Release mit sauberer Versionierung)

---

<!-- Commit: docs: add parameter map for hybrid branch (active vs legacy) -->
