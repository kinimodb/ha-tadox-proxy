# ROADMAP — Hybrid Control Branch

Diese Roadmap beschreibt den Entwicklungsstand und die geplanten Schritte im Branch `feature/hybrid-control`.
Fokus: **stabile, erklärbare Regelung** für Tado X (externes Raumthermometer, TRV als Black Box).

## Prinzipien

1) **Test-first:** Keine Feature-Explosion vor stabiler Regelung (Tuning + Telemetrie).
2) **Deterministische Störbehandlung:** Fensterlogik via Sensor (binary_sensor), nicht via Trend-Raten im Feld.
3) **Command Hygiene:** Flattern vermeiden (Min-Delta, Rate-Limit, Step-Limit), aber “Fast Recovery” für echte Kälte/Recovery.
4) **Branching:** `main` bleibt stabil; dieser Branch ist BETA/Test.

---

## M0 — Basisfunktion & Observability (DONE)

- [x] Proxy-Climate-Entity mit externem Raumtemperatursensor
- [x] Hybrid-Regler (Bias + BOOST/HOLD/COAST)
- [x] Telemetrie-Attribute (`hybrid_*`, `regulation_reason`)
- [x] Command Hygiene (Min-Delta / Rate-Limit / Step-Up-Limit)
- [x] Fast-Recovery (bounded) für schnelle Recovery bei BOOST/hoher Abweichung
- [x] Window Handling via Sensor + Open-Delay + Close-Hold
- [x] “Ground Truth” Send-Telemetrie (`tado_last_sent_*`)

---

## M1 — Stabilisierung & Tuning (IN PROGRESS)

Ziel: “Dauerbetrieb ohne Überraschungen”, reproduzierbar anhand von CSV-Exports.

- [ ] Testmatrix definieren (Szenarien + erwartete Eigenschaften)
  - Stabil halten (HOLD) über mehrere Stunden
  - Lüften / Window open/close: Frostschutz + sauberes Resume
  - Kälteeinbruch (Drop): BOOST + Fast-Recovery, danach Ramp-Down ohne Sägezahn
- [ ] Parameter-Tuning (datenbasiert):
  - BOOST-Trigger/Exit, HOLD-Deadband, COAST-Trigger
  - Fast-Recovery thresholds
  - Bias-Lernparameter (tau, deadband)
- [ ] Telemetrie-Konsistenz prüfen (keine missverständlichen Attribute)

**Exit-Kriterium M1:**  
Mindestens 48h Testbetrieb ohne “stuck frost / stuck boost / sawtooth flapping” und mit nachvollziehbaren `regulation_reason`-Strings.

---

## M2 — Komfortfeatures (BLOCKED bis M1 Exit)

Features erst nach stabiler Regelung.

- [ ] Presence / Home-Away (optional enable + entity + away setpoint)
- [ ] Presets (Boost, Eco, Comfort)
- [ ] Vacation mode (Zeitplan außer Kraft, Frostschutz/Komfort definierbar)
- [ ] Erweiterte Fensterlogik (sekundenbasierte Delays, optional)

---

## M3 — UI/UX (BLOCKED bis M1 Exit)

- [ ] Dashboard Card (eigene UI Card, optional)
- [ ] Icons/Branding (Integration + Card)
- [ ] Dokumentation für Nutzer (Konfiguration, Tuning, Troubleshooting)

---

## M4 — Release Hardening (später)

- [ ] Diagnostics / Debug endpoints (HA Diagnostics)
- [ ] Robustheit gegen Sensor-Ausfälle (Fallbacks, klare States)
- [ ] CI/Quality (lint, tests, hassfest, hacs action)

---

<!-- Commit: docs: align roadmap with hybrid strategy and test-first milestones -->
