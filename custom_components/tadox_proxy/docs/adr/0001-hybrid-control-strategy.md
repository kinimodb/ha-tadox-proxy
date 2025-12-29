# ADR 0001 — Hybrid Control Strategy & Branching Policy

## Status
Accepted (2025-12-29)

## Kontext
Wir entwickeln eine Home Assistant Custom Integration (HACS) für Tado X.
Ziel ist eine deutlich robustere Regelung als der aktuelle Stand.

Randbedingungen:
- Stellgröße ist der an Tado gesendete Setpoint (Ventilregelung bleibt Black Box).
- Heizkörper/raumseitige Thermik ist träge (Nachlauf, Überschwingen).
- Störgrößenwechsel (z. B. Tür offen/zu) sind relevant.
- Stellbefehle dürfen nicht beliebig häufig gesendet werden (Schonung/Cloud/Mechanik).

## Entscheidung
1) Wir entwickeln eine neue Regelstrategie als “Hybrid Controller” (Bias-Estimator + Zustandslogik + Command Hygiene).
2) Die Entwicklung erfolgt ausschließlich im Branch `feature/hybrid-control`.
3) `main` bleibt der stabile Referenzstand und wird nicht für Experimente genutzt.
4) Wir implementieren in `feature/hybrid-control` keinen dauerhaften “Regler-Schalter” (PID vs Hybrid).
   Ziel ist, Hybrid als neue Standardstrategie zu etablieren und den Alt-Algorithmus später zu entfernen.

## Begründung
- Saubere Trennung und jederzeitiges Zurückspringen auf `main` ohne Risiko.
- Kein “Produktballast” durch dauerhafte Doppelstrategie im Code.
- Fokus auf Endzustand statt Zwischenlösungen.

## Praktische Test-/Rollout-Policy
- HACS nutzt bei Repos ohne Releases den Default-Branch. Deshalb können wir nicht davon ausgehen,
  dass man im Alltag in HACS sauber auf beliebige Branches umschalten kann.
- Für Tests/Rollback in Home Assistant wird eine der folgenden Strategien genutzt:
  A) GitHub Releases als Versionen (empfohlen): Hybrid-Stand wird als Release installiert; Rollback = ältere Release.
  B) Separates Test-Repository/Fork als eigenes HACS Custom Repository (Alternative).
- Temporäres Umstellen des Default-Branch wird vermieden.

## Dokumentations-Policy
- Dokumentation wird endzustandsorientiert erweitert.
- Zwischenstände gehören in ADRs oder Issues, nicht in das Haupt-README.
- Die zentrale Regelstrategie-Spezifikation liegt in `docs/control_strategy.md`.

## Ausblick (Funktionsumfang nach Stabilisierung der Regelbasis)
- Window-open / rapid heat loss detection
- Home/Away
- Presets (Boost, Eco, Comfort)
- Vacation mode (Zeitplan außer Kraft, Frostschutz)
