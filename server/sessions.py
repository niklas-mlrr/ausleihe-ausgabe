"""Fassade über die Session-/Scan-/Druck-Module (Welle 6).

Bis 2026-09-05 vereinte diese Datei mit 2976 Zeilen und 92 Funktionen
mindestens sechs unabhängige Aufgaben — Token-Erzeugung, Buchungs-Precheck,
Bücherlisten-Sichtbarkeit, Leihschein-Druck, Geräte-Broadcast und den
Scan-Station-/Modus-B-Lebenszyklus — und war damit die Datei, die praktisch
jedes neue Feature anfassen musste (Vorbild für diesen Schnitt:
sba-dashboard/docs/architektur.md, „Aufteilung innerhalb von `app/`" —
„Eine Datei, eine Aufgabe"). Sie ist jetzt reine Fassade ohne eigene Logik:

```
session_tokens.py           Token-/Code-/QR-Erzeugung
scan_booking.py              Scan-Auswertung + Buchungs-Precheck/Commit
book_visibility.py           Bücherlisten-Hydration & Sichtbarkeitsfilter
loan_slip_flow.py             Leihschein-Druck (Modus A/B, telefonbasiert)
device_broadcast.py          iPad-/Drucker-Display(+Scanner)/Lehrkraft-Broadcast
session_lifecycle.py         Modus-A/B Session-/Worker-Lebenszyklus + Sweeper
helper_queue.py               Helfer-Warteschlange: Zuweisung, Booklist, Zuschauer
scan_station_session.py      Scan-Station: Gerät, Laden, Zettel-Druck, TTL-Sweep
device_persistence.py        Persistenz über Serverneustarts
```

Jedes Modul re-exportiert hierher, und diese Datei re-exportiert alles mit
einem expliziten `__all__` weiter — so bleiben `from .sessions import X` und
`from server.sessions import X` an allen ~40 bisherigen Importstellen (Routen,
Tests) unverändert gültig, ohne dass der Split sie in einem Commit mit
anfassen musste. Aus demselben Grund lösen die neuen Module gemeinsam
genutzte Collaborators (`get_hub`, `get_config`, `get_state`,
`get_book_order_for_form`, `get_hidden_isbns_for_form`, `server_lan_ip`,
`secrets`) zur Laufzeit über diese Fassade auf (`_sessions.get_hub()` usw.,
s. `scan_booking.py`-Docstring) statt sie direkt aus ihrem Ursprungsmodul zu
importieren — Tests patchen sie als `sessions.get_hub` & Co., und nur ein
Zugriff über das Fassaden-Modul-Objekt selbst sieht einen solchen Patch zur
Laufzeit.

Zwei echte Modul-Zyklen ließen sich beim Schnitt nicht vermeiden und werden
an ihrer jeweiligen Stelle lokal über genau diese Fassade gebrochen (Details
in den betroffenen Modulen): scan_booking.py <-> book_visibility.py/
loan_slip_flow.py, sowie session_lifecycle.py <-> helper_queue.py/
scan_station_session.py.

Sicherheitsmodell (PLAN §3): Der `session_token` ist der einzige
Daten-Zugangs-Credential (lang, kryptografisch zufällig). Der 4-stellige
`pairing_code` dient nur der menschlich vermittelten Zuordnung am Host und
gewährt für sich genommen NIE Datenzugriff. Schülerdaten fließen erst nach
Host-Bestätigung (`state == "paired"`). Beim Abschluss/Abbruch/Timeout wird
der Token hart entwertet: Worker-Context zu, WebSocket zu, Token aus dem RAM.
"""

from __future__ import annotations

import logging

# Tests patchen sessions.secrets direkt (dasselbe Modul-Objekt wie in
# server/session_tokens.py) — deshalb hier bewusst weiter importiert und in
# __all__ unten re-exportiert, obwohl sessions.py selbst secrets nicht mehr
# aufruft.
import secrets  # noqa: F401

from .book_order import get_book_order_for_form, get_hidden_isbns_for_form
from .book_visibility import (
    all_books_already_loaned,
    apply_empty_stock_flag,
    apply_empty_stock_visibility,
    apply_hidden_books,
    hydrate_student_info,
    init_book_progress,
    mark_book_done,
    pending_vormerk_isbns_for,
)
from .config import get_config
from .device_broadcast import (
    allowed_printers_for,
    broadcast_displays,
    broadcast_printer_displays,
    broadcast_printer_scanners,
    broadcast_scanner_result,
    broadcast_teacher_sessions,
    displayed_printer_ids,
    eligible_drucker_scanners_for,
    relevant_display_count,
    revoke_all_teacher_sessions,
    revoke_teacher_session,
    revoke_teacher_sessions_for_context,
    send_display_update,
    send_printer_display_update,
    send_printer_scanner_update,
    send_teacher_update,
)
from .device_persistence import (
    PRUNE_MIN_UPTIME_S,
    _prunes_unconnected,
    persist_helpers,
    persist_printer_displays,
    persist_printer_scanners,
    persist_scan_stations,
    server_lan_ip,
)
from .helper_queue import (
    advance_helper,
    assign_next_pending_to_helper,
    assign_student_to_helper,
    broadcast_student_info_to_spectators,
    load_and_push_helper_student,
    load_and_push_paired_student,
    rebind_helper_to_context,
    repush_booklist,
    repush_for_changed_empty_isbns,
    spectate_student,
)
from .hub import get_hub
from .loan_slip_flow import (
    _download_slip_to_host,
    _mark_slip_printed,
    _missing_stock_subjects_for,
    _own_slip_filename,
    _prefetch_own_slip,
    _prefetch_own_slip_task,
    _send_own_slip_download,
    _slip_print_label,
    _station_sheet_filename,
    _station_sheet_label,
    _student_form,
    confirm_slip_received,
    invalidate_slip_after_scan,
    print_loan_slip_for,
    slip_signature_options_for,
    slip_trigger_for,
)
from .scan_booking import (
    _booking_lock_for,
    booking_isbn_sets_from_info,
    booking_sets_for_student,
    commit_book_safely,
    evaluate_scan_for_booking,
    expected_isbns_from_info,
    handle_commit,
    handle_scan,
    process_scan,
)
from .scan_station_session import (
    STATION_IDLE_TTL_S,
    _load_and_activate_station_student,
    activate_station_student,
    broadcast_scan_stations,
    expired_scan_stations,
    load_station_student,
    print_station_sheet_for,
    release_station_student,
    resolve_station_code,
    send_scan_station_update,
    sweep_scan_stations,
)
from .session_lifecycle import (
    _detach_helper,
    _release_tasks,
    create_student_session,
    end_student,
    expired_student_sessions,
    invalidate_session,
    release_student_worker,
    release_worker,
    set_worker_session,
    sweep_expired_sessions,
    sweep_helper_scan_secrets,
    teardown_students,
)
from .session_tokens import (
    gen_join_secret,
    gen_pairing_code,
    gen_registration_code,
    gen_session_token,
    make_qr_data_url,
)
from .state import StudentSessionB, get_state

log = logging.getLogger(__name__)

# Vollständiger Re-Export aller neun ausgelagerten Module (s. Modul-Docstring
# oben) — jede Zeile, gruppiert nach Herkunftsmodul, in derselben Reihenfolge
# wie die Imports oben.
__all__ = [
    "secrets",
    "gen_session_token",
    "gen_join_secret",
    "gen_registration_code",
    "gen_pairing_code",
    "make_qr_data_url",
    "handle_scan",
    "expected_isbns_from_info",
    "booking_isbn_sets_from_info",
    "evaluate_scan_for_booking",
    "process_scan",
    "_booking_lock_for",
    "booking_sets_for_student",
    "commit_book_safely",
    "handle_commit",
    "hydrate_student_info",
    "init_book_progress",
    "mark_book_done",
    "all_books_already_loaned",
    "apply_hidden_books",
    "apply_empty_stock_flag",
    "apply_empty_stock_visibility",
    "pending_vormerk_isbns_for",
    "invalidate_slip_after_scan",
    "_student_form",
    "_slip_print_label",
    "_station_sheet_label",
    "slip_trigger_for",
    "slip_signature_options_for",
    "_missing_stock_subjects_for",
    "print_loan_slip_for",
    "_mark_slip_printed",
    "confirm_slip_received",
    "_download_slip_to_host",
    "_prefetch_own_slip",
    "_station_sheet_filename",
    "_own_slip_filename",
    "_prefetch_own_slip_task",
    "_send_own_slip_download",
    "allowed_printers_for",
    "displayed_printer_ids",
    "relevant_display_count",
    "eligible_drucker_scanners_for",
    "send_display_update",
    "broadcast_displays",
    "send_printer_display_update",
    "broadcast_printer_displays",
    "send_printer_scanner_update",
    "broadcast_printer_scanners",
    "broadcast_scanner_result",
    "send_teacher_update",
    "broadcast_teacher_sessions",
    "revoke_teacher_session",
    "revoke_teacher_sessions_for_context",
    "revoke_all_teacher_sessions",
    "release_worker",
    "_release_tasks",
    "release_student_worker",
    "set_worker_session",
    "create_student_session",
    "invalidate_session",
    "_detach_helper",
    "end_student",
    "teardown_students",
    "sweep_helper_scan_secrets",
    "expired_student_sessions",
    "sweep_expired_sessions",
    "load_and_push_helper_student",
    "repush_booklist",
    "repush_for_changed_empty_isbns",
    "advance_helper",
    "assign_next_pending_to_helper",
    "rebind_helper_to_context",
    "assign_student_to_helper",
    "spectate_student",
    "broadcast_student_info_to_spectators",
    "load_and_push_paired_student",
    "StudentSessionB",
    "send_scan_station_update",
    "broadcast_scan_stations",
    "resolve_station_code",
    "load_station_student",
    "release_station_student",
    "_load_and_activate_station_student",
    "activate_station_student",
    "print_station_sheet_for",
    "expired_scan_stations",
    "sweep_scan_stations",
    "STATION_IDLE_TTL_S",
    # Collaborators, die nicht mehr bare-name in sessions.py selbst aufgerufen
    # werden (alle Aufrufer sind nach der Aufteilung in eigene Module
    # gewandert und lösen sie über `_sessions.get_...()` auf), aber weiterhin
    # hier importiert bleiben müssen: Tests patchen sie als `sessions.get_...`
    # (Monkeypatch auf dem Fassaden-Modul) — s. scan_booking.py-Docstring.
    "get_hub",
    "get_config",
    "get_state",
    "get_book_order_for_form",
    "get_hidden_isbns_for_form",
    "server_lan_ip",
    "PRUNE_MIN_UPTIME_S",
    "_prunes_unconnected",
    "persist_helpers",
    "persist_printer_displays",
    "persist_printer_scanners",
    "persist_scan_stations",
]
