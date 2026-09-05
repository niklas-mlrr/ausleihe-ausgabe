"""Persistenz über Serverneustarts: Helfer, Drucker-Displays, Scan-Stationen.

Ausgelagert aus `sessions.py` (Welle 6, s. dortiges Modul-Docstring). Letztes
der neun Module — `sessions.py` besteht ab hier nur noch aus der Fassade
(Re-Exports + `__all__`), keine eigene Logik mehr.

Gemeinsam von den jeweiligen Routen (bei jeder Konfigurationsänderung) UND von
`routes/ws.py` (beim ersten WS-Connect je Serverlauf) sowie der
`app.py`-Lifespan (Shutdown, s. dort) aufgerufen — daher hier statt in den
einzelnen `routes/*.py`, die sich sonst gegenseitig importieren müssten.

Zwei Verwerfungsregeln, unabhängig von der reinen Datei-IO in den
`*_store.py`-Modulen:
 1. Andere Server-IP als beim Speichern (`server_lan_ip()`) → beim nächsten
    Start wird GAR NICHT geladen (die alten Token stecken in URLs, die auf
    die alte IP zeigen — auf einem anderen Netz ohnehin nie erreichbar).
 2. Ein Eintrag, der in einem kompletten Serverlauf (Start bis Ende) nie per
    WS verbunden war (`connected_since_start`), wird gar nicht erst auf die
    Platte geschrieben — Karteileichen (z. B. ein wiederhergestellter, nie
    wieder angeschlossener Helfer) fallen so beim übernächsten Neustart
    automatisch raus, spätestens beim Shutdown-Aufruf in `app.py`.
    ABER: Diese Regel greift erst, wenn der Lauf länger als
    `PRUNE_MIN_UPTIME_S` (5 min) gedauert hat. Ein kurzer Lauf (Neustart
    direkt nach dem Start, Fehlstart, schnelles Durchstarten beim Aufbau) ist
    kein Beleg dafür, dass ein Gerät weg ist — die Handys/Displays hätten in
    der Zeit gar nicht zuverlässig reconnecten können. Solange der Lauf
    jünger ist, werden alle Einträge weitergeschrieben, damit sie beim
    nächsten Start wieder da sind.

`server_lan_ip` wird von den `persist_*`-Funktionen bewusst über die Fassade
`sessions` aufgelöst (`_sessions.server_lan_ip()`), obwohl es im selben Modul
definiert ist — Tests patchen `sessions.server_lan_ip` (Monkeypatch auf dem
Fassaden-Modul), s. `scan_booking.py`-Docstring für die ausführliche
Begründung dieses Musters.
"""

from __future__ import annotations

import logging
import time

from . import sessions as _sessions
from .state import AppState

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Persistenz über Serverneustarts: Helfer, Drucker-Displays, Scan-Stationen
# ---------------------------------------------------------------------------
#
# Gemeinsam von den jeweiligen Routen (bei jeder Konfigurationsänderung) UND
# von routes/ws.py (beim ersten WS-Connect je Serverlauf) sowie der
# `app.py`-Lifespan (Shutdown, s. dort) aufgerufen — daher hier statt in den
# einzelnen `routes/*.py`, die sich sonst gegenseitig importieren müssten.
#
# Zwei Verwerfungsregeln, unabhängig von der reinen Datei-IO in den
# `*_store.py`-Modulen:
#  1. Andere Server-IP als beim Speichern (`server_lan_ip()`) → beim nächsten
#     Start wird GAR NICHT geladen (die alten Token stecken in URLs, die auf
#     die alte IP zeigen — auf einem anderen Netz ohnehin nie erreichbar).
#  2. Ein Eintrag, der in einem kompletten Serverlauf (Start bis Ende) nie
#     per WS verbunden war (`connected_since_start`), wird gar nicht erst auf
#     die Platte geschrieben — Karteileichen (z. B. ein wiederhergestellter,
#     nie wieder angeschlossener Helfer) fallen so beim übernächsten Neustart
#     automatisch raus, spätestens beim Shutdown-Aufruf in `app.py`.
#     ABER: Diese Regel greift erst, wenn der Lauf länger als
#     `PRUNE_MIN_UPTIME_S` (5 min) gedauert hat. Ein kurzer Lauf (Neustart
#     direkt nach dem Start, Fehlstart, schnelles Durchstarten beim Aufbau)
#     ist kein Beleg dafür, dass ein Gerät weg ist — die Handys/Displays
#     hätten in der Zeit gar nicht zuverlässig reconnecten können. Solange
#     der Lauf jünger ist, werden alle Einträge weitergeschrieben, damit sie
#     beim nächsten Start wieder da sind.


def server_lan_ip() -> str | None:
    """LAN-IP dieses Servers — Fingerprint für die Persistenz-Verwerfungsregel
    (1). Dieselbe Auto-Erkennung wie für QR-/Join-URLs (`routes/_deps.py::
    _base_url`), aber ohne Request und unabhängig vom Tailscale-Toggle (der
    selbst nicht persistiert wird und bei jedem Neustart auf False steht) —
    reflektiert also schlicht, an welchem physischen Netz die Maschine hängt."""
    from .tls import primary_lan_ip

    return primary_lan_ip()


# Mindestlaufzeit eines Serverlaufs, ab der „war nie verbunden" als Beleg für
# ein verschwundenes Gerät gilt (s. Verwerfungsregel 2 oben).
PRUNE_MIN_UPTIME_S = 300.0


def _prunes_unconnected(state: AppState) -> bool:
    """True, wenn dieser Serverlauf lang genug lief, um nie verbundene
    Einträge aus der Persistenz zu entfernen. Bei kürzeren Läufen bleibt alles
    erhalten."""
    return (time.monotonic() - state.started_at_monotonic) >= PRUNE_MIN_UPTIME_S


def persist_helpers(state: AppState) -> None:
    """Helfer, die in DIESEM Serverlauf mindestens einmal verbunden waren, auf
    die Server-Persistenz (`data/helpers.json`) wegschreiben. Non-fatal —
    Schreibfehler werden geloggt, der In-Memory-State bleibt Leading."""
    from .helper_store import save as save_helpers

    prune = _prunes_unconnected(state)
    try:
        save_helpers(
            [
                h
                for h in state.helper_sessions.values()
                if h.connected_since_start or not prune
            ],
            _sessions.server_lan_ip(),
        )
    except Exception:
        log.exception("Speichern der Helfer-Persistenz fehlgeschlagen (non-fatal)")


def persist_printer_displays(state: AppState) -> None:
    """Freigeschaltete Drucker-Displays, die in DIESEM Serverlauf mindestens
    einmal verbunden waren, auf die Server-Persistenz
    (`data/printer_displays.json`) wegschreiben. Non-fatal. Zugewiesene
    Drucker werden über ihren `name` referenziert (laufzeitstabile Pool-`id`
    s. `printer_store.py`)."""
    from .printer_display_store import save as save_printer_displays

    prune = _prunes_unconnected(state)
    try:
        printer_names_by_id = {p.id: p.name for p in state.settings.printers}
        entries = []
        for d in state.printer_displays.values():
            if not (d.authorized and (d.connected_since_start or not prune)):
                continue
            # Gemeinsame Drucker+Scanner-Reihenfolge (`item_order`, s.
            # AppState._ordered_display_items): Drucker über `name` remappen
            # (wie `assigned_printer_names`), Scanner über die stabile
            # `scanner_id` direkt. Verwaiste Drucker-Einträge (Name nicht mehr
            # im Pool) fallen weg — beim nächsten Laden hängen sie ohnehin
            # stabil ans Ende, kein Datenverlust.
            item_order = []
            for key in d.item_order or []:
                kind, _, ident = key.partition(":")
                if kind == "printer" and ident in printer_names_by_id:
                    item_order.append({"kind": "printer", "name": printer_names_by_id[ident]})
                elif kind == "scanner":
                    item_order.append({"kind": "scanner", "id": ident})
            entries.append(
                {
                    "display_id": d.display_id,
                    "label": d.label,
                    "theme": d.theme,
                    "assigned_printer_names": (
                        None
                        if d.assigned_printer_ids is None
                        else [
                            printer_names_by_id[pid]
                            for pid in d.assigned_printer_ids
                            if pid in printer_names_by_id
                        ]
                    ),
                    # Scanner-Zuordnung braucht KEIN Namens-Remapping wie bei
                    # Druckern: `scanner_id` ist — wie `display_id`/`station_id`
                    # — der persistierte URL-Token selbst, über Neustarts hinweg
                    # stabil (anders als die laufzeitstabilen, aber pro Lauf neu
                    # vergebenen Pool-`id`s der Drucker).
                    "assigned_scanner_ids": (
                        None if d.assigned_scanner_ids is None else list(d.assigned_scanner_ids)
                    ),
                    "item_order": item_order,
                }
            )
        save_printer_displays(entries, _sessions.server_lan_ip())
    except Exception:
        log.exception("Speichern der Drucker-Display-Persistenz fehlgeschlagen (non-fatal)")


def persist_printer_scanners(state: AppState) -> None:
    """Freigeschaltete Drucker-Scanner, die in DIESEM Serverlauf mindestens
    einmal verbunden waren, auf die Server-Persistenz
    (`data/printer_scanners.json`) wegschreiben. Non-fatal."""
    from .printer_scanner_store import save as save_printer_scanners

    prune = _prunes_unconnected(state)
    try:
        entries = [
            {
                "scanner_id": s.scanner_id,
                "label": s.label,
                "theme": s.theme,
                "input_mode": s.input_mode,
            }
            for s in state.printer_scanners.values()
            if s.authorized and (s.connected_since_start or not prune)
        ]
        save_printer_scanners(entries, _sessions.server_lan_ip())
    except Exception:
        log.exception("Speichern der Drucker-Scanner-Persistenz fehlgeschlagen (non-fatal)")


def persist_scan_stations(state: AppState) -> None:
    """Freigeschaltete Scan-Stationen, die in DIESEM Serverlauf mindestens
    einmal verbunden waren, auf die Server-Persistenz
    (`data/scan_stations.json`) wegschreiben. Non-fatal."""
    from .scan_station_store import save as save_scan_stations

    prune = _prunes_unconnected(state)
    try:
        entries = [
            {
                "station_id": s.station_id,
                "label": s.label,
                "theme": s.theme,
                "input_mode": s.input_mode,
            }
            for s in state.scan_stations.values()
            if s.authorized and (s.connected_since_start or not prune)
        ]
        save_scan_stations(entries, _sessions.server_lan_ip())
    except Exception:
        log.exception("Speichern der Scan-Station-Persistenz fehlgeschlagen (non-fatal)")
