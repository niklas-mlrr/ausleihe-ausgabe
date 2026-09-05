"""Bücherlisten-Hydration und Sichtbarkeitsfilter auf dem Schüler-Info-Dict.

Ausgelagert aus `sessions.py` (Welle 6, s. dortiges Modul-Docstring). Alle
Funktionen hier arbeiten auf dem `info`-Dict aus `IservClient.get_student_info`
(bzw. dem `QueueStudent`/Session-Fortschritt daneben) — Ausblenden/Filtern von
Buchreihen, den „X/Y Bücher"-Fortschrittszähler der Host-Queue und die drei
ISBN-Mengen für die Buchungs-Vorabprüfung (letztere kommen aus
`scan_booking.py`, s. dort für die Begründung des Zuschnitts).

`get_book_order_for_form`/`get_hidden_isbns_for_form` werden über die Fassade
`sessions` aufgelöst (`_sessions.get_...`), nicht direkt aus `.book_order`
importiert — Tests patchen sie als `sessions.get_book_order_for_form`/
`sessions.get_hidden_isbns_for_form`, s. `scan_booking.py`-Docstring für die
ausführliche Begründung dieses Musters.
"""

from __future__ import annotations

import logging

from . import sessions as _sessions
from .scan_booking import booking_isbn_sets_from_info, expected_isbns_from_info
from .state import AppState

log = logging.getLogger(__name__)


async def hydrate_student_info(
    state: AppState, info: dict, form: str, target, *, reset_baseline: bool = True, is_helper: bool
) -> dict:
    """Reiner Hydrations-Teil, gemeinsam für alle vier Lade-/Reconnect-Pfade
    (Modus A: `load_and_push_helper_student`, `ws_scanner`-Reconnect; Modus B:
    `load_and_push_paired_student`, `ws_student`-Reconnect).

    Setzt `info["form"]`/`info["book_order"]`, filtert ausgeblendete Reihen aus
    `info["books"]` und füllt die drei ISBN-Mengen (`expected_isbns`,
    `vormerk_isbns`, `lent_isbns`) auf `target` (HelperSession oder
    StudentSessionB — beide tragen diese drei Attribute). Das Laden von `info`
    selbst (`get_student_info`) und die Fehlerbehandlung bleiben beim jeweiligen
    Aufrufer — die vier Stellen unterscheiden sich darin (bereits geladenes
    `info` vs. eigener Fetch, unterschiedliche Error-Payloads/Sends).

    Gibt `info` zurück, damit der Aufrufer `info["book_order"]` weiterverwenden
    kann (z. B. für die `settings`-Nachricht im Scanner-Reconnect).

    `reset_baseline` (Default True): siehe `init_book_progress`. Reconnect-
    Aufrufer (Seiten-Reload derselben Verbindung) übergeben False, damit die
    „seit Aufrufen"-Baseline über den Reload hinweg stehen bleibt.

    `is_helper` (Pflicht, kein Default): steuert `apply_empty_stock_flag` —
    True für Modus A (Helfer sieht die „Bestand leer"-Markierung), False für
    Modus B (Schüler bekommt sie NIE, s. server/sessions.py::apply_empty_stock_flag)
    UND `apply_empty_stock_visibility` (Bestand-leer-Reihen werden aus
    `info["books"]` entfernt, solange nicht ausgeliehen — Modus B, Scan-Station
    und deren gemeinsamer Rückgabewert für den Scan-Station-Zettel). Bewusst
    KEIN `isinstance`-Check auf `target` (bricht bei Duck-Typed-Fakes in
    Tests) — jeder der vier Call-Sites kennt seinen Modus ohnehin."""
    info["form"] = form
    helper = state.find_helper_for_student(getattr(target, "student_id", None))
    info["helper_name"] = helper.name if helper is not None else None
    info["book_order"] = await _sessions.get_book_order_for_form(state, form)
    apply_hidden_books(info, await _sessions.get_hidden_isbns_for_form(state, form))
    apply_empty_stock_flag(info, state.caches.empty_isbns, visible=is_helper)
    target.expected_isbns = expected_isbns_from_info(info)
    target.vormerk_isbns, target.lent_isbns, target.lent_codes = booking_isbn_sets_from_info(info)
    init_book_progress(
        state, getattr(target, "student_id", None), info, reset_baseline=reset_baseline
    )
    if not is_helper:
        apply_empty_stock_visibility(info, state.caches.empty_isbns)
    return info


def init_book_progress(
    state: AppState, student_id: int | None, info: dict, *, reset_baseline: bool = True
) -> None:
    """Startwerte für den „X/Y Bücher"-Zähler der Host-Queue setzen.

    Y = alle angemeldeten Bücher des Schülers ohne die ausgeblendeten Reihen
    (`info["books"]` ist hier bereits durch `apply_hidden_books` gelaufen),
    X = die davon bereits ausgeliehenen. Kein Queue-Eintrag (transienter
    Lupe-Schüler) → nichts zu tun.

    `reset_baseline` steuert, ob `loaned_at_load` (die „beim Aufrufen"-Basis
    für die session-bezogene Host-Anzeige, s. unten) neu gesetzt wird. True
    nur beim echten „Aufrufen" (`assign_student_to_helper` →
    `load_and_push_helper_student`/`load_and_push_paired_student`) — dort
    beginnt eine neue Zählung. Reconnects (Seiten-Reload derselben
    Verbindung, `ws_scanner`/`ws_student`) und reine Booklist-Refreshes
    (`repush_booklist`) übergeben False: `done_isbns` wird trotzdem aus dem
    aktuellen IServ-Stand aufgefrischt (Scans anderswo bleiben sichtbar),
    aber die Baseline bleibt stehen, bis der Schüler erneut aufgerufen wird."""
    if student_id is None:
        return
    student = state.find_student(student_id)
    if student is None:
        return
    # Anmelde-/Zahlstatus gleich mit auffrischen — `info` trägt ihn ohnehin,
    # und beim Laden des Schülers ist er aktueller als der vom Klassen-Öffnen.
    student.set_info_flags(info)
    books = info.get("books", [])
    student.books_total = len(books)
    student.done_isbns = {
        b["isbn"] for b in books if b.get("isbn") and b.get("status") == "ausgeliehen"
    }
    student.books_empty_outstanding = sum(
        1
        for b in books
        if b.get("isbn") in state.caches.empty_isbns and b["isbn"] not in student.done_isbns
    )
    if reset_baseline:
        # Bei Laden bereits ausgeliehene Bücher — Grundlage für den session-
        # basierten Fortschritt in der Host-Status-Spalte („seit Aufrufen … /
        # beim Aufrufen offene …"). Entspricht `done_isbns` zum Ladezeitpunkt.
        student.loaned_at_load = len(student.done_isbns)


def mark_book_done(state: AppState, student_id: int, isbn: str | None) -> None:
    """Ein erfolgreich gescanntes/gebuchtes Buch im Queue-Fortschritt zählen —
    dieselbe „erledigt"-Definition wie in den Clients (`isBookDone`):
    ausgeliehen ODER in dieser Session gescannt (auch nur gestaged, solange
    `ALLOW_BOOKING=false`)."""
    if not isbn:
        return
    student = state.find_student(student_id)
    if student is not None:
        was_open = isbn not in student.done_isbns
        student.done_isbns.add(isbn)
        # Klammer-Anzeige `X/Y (Z)` verschwindet, sobald das Buch tatsächlich
        # gescannt wird — unabhängig von der Ja/Nein-Rückfrage im Helfer-Client
        # (die betrifft nur `empty_isbns` selbst, nicht diesen Zähler).
        if was_open and isbn in state.caches.empty_isbns and student.books_empty_outstanding > 0:
            student.books_empty_outstanding -= 1


def all_books_already_loaned(books: list[dict]) -> bool:
    """True, wenn jedes Buch der Liste bereits ausgeliehen ist (leere Liste: False).

    Spiegelt `allVorgemerkteDone` in `web/student.js` für den Fall direkt nach
    dem Pairing, wenn noch nichts in dieser Session gescannt wurde
    (`scannedIsbns` ist dort leer) — dann reduziert sich „alle vorgemerkten
    erledigt" auf „alle Bücher haben Status ausgeliehen"."""
    return bool(books) and all(b.get("status") == "ausgeliehen" for b in books)


def apply_hidden_books(info: dict, hidden_isbns: set[str]) -> None:
    """Ausgeblendete Buchreihen (Einstellungen-Dialog) aus `info["books"]"`
    entfernen, bevor sie als vorgemerkt/erwartet gilt. Muss vor
    `expected_isbns_from_info`/`booking_isbn_sets_from_info` laufen, sonst
    tauchen ausgeblendete Reihen weiter als vorgemerkt bzw. buchbar auf."""
    if not hidden_isbns:
        return
    info["books"] = [b for b in info.get("books", []) if b.get("isbn") not in hidden_isbns]


def apply_empty_stock_flag(info: dict, empty_isbns: set[str], *, visible: bool) -> None:
    """Markiert Buchzeilen mit leerem Bestand (`b["bestand_leer"] = True`) —
    NUR für den Helfer-Client (`visible=True`). Bei `visible=False`
    (Schüler-Client, Modus B) passiert nichts: der Schüler bekommt weder eine
    Markierung noch eine Rückfrage, das Buch bleibt für ihn ein ganz normales,
    einscannbares Buch.

    Anders als `apply_hidden_books` wird die Zeile NIE entfernt — „Bestand
    leer" bleibt vorgemerkt und buchbar (`evaluate_scan_for_booking` bleibt
    unberührt), es ist nur eine zusätzliche Info-Markierung."""
    if not visible or not empty_isbns:
        return
    for b in info.get("books", []):
        if b.get("isbn") in empty_isbns:
            b["bestand_leer"] = True


def apply_empty_stock_visibility(info: dict, empty_isbns: set[str]) -> None:
    """Entfernt Buchreihen mit leerem Bestand aus `info["books"]`, SOLANGE sie
    noch nicht ausgeliehen sind — für Schüler-Client (Modus B), Scan-Station-
    Client und den Scan-Station-Zettel. Bereits ausgeliehene Reihen bleiben
    sichtbar (Status „ausgeliehen"), sonst würde eine später doch ausgegebene
    Reihe für den Schüler spurlos verschwinden.

    MUSS nach der Buchungsmengen-Berechnung
    (`expected_isbns_from_info`/`booking_isbn_sets_from_info`) und nach
    `init_book_progress` laufen: die Reihe bleibt weiterhin regulär buchbar
    (dieser Filter betrifft nur die Anzeige) und der Host-„X/Y Bücher"-Zähler
    (inkl. `books_empty_outstanding`) rechnet weiterhin mit der vollen Liste."""
    if not empty_isbns:
        return
    info["books"] = [
        b
        for b in info.get("books", [])
        if b.get("isbn") not in empty_isbns or b.get("status") == "ausgeliehen"
    ]


async def pending_vormerk_isbns_for(state: AppState, student_id: int) -> set[str] | None:
    """Noch offene vorgemerkte Bücher eines Schülers — ausgeblendete Reihen
    (Einstellungen-Dialog) und Reihen mit leerem Bestand (solange nicht
    ausgeliehen) zählen NICHT mit (Mirror `routes/classes.py::
    _load_student_flags`, dortiger `"all_lent"`-Zweig, hier zusätzlich um
    `apply_empty_stock_visibility` ergänzt). `None` = Status konnte nicht
    sicher ermittelt werden (IServ-Lookup fehlgeschlagen) — vom Aufrufer wie
    „noch nicht bereit" zu behandeln, NICHT wie „keine offenen Bücher".
    Rein lesend."""
    qs = state.find_student(student_id)
    if qs is None or state.iserv is None:
        return None
    try:
        info = await state.iserv.get_student_info(student_id, state.selected_schoolyear)
    except Exception:
        log.exception("Bücherstatus für Schüler %s konnte nicht geladen werden", student_id)
        return None
    apply_hidden_books(info, await _sessions.get_hidden_isbns_for_form(state, qs.form))
    apply_empty_stock_visibility(info, state.caches.empty_isbns)
    vormerk, _lent, _lent_codes = booking_isbn_sets_from_info(info)
    return vormerk
