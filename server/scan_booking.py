"""Scan-Auswertung und Buchungs-Precheck/Commit (PLAN §6).

Ausgelagert aus `sessions.py` (Welle 6, s. dortiges Modul-Docstring). Herzstück
ist `evaluate_scan_for_booking()` — die read-only Vorabprüfung, die entscheidet,
ob ein gescannter Barcode überhaupt gebucht werden darf (Buch im Lager UND
bestellt/Reihe noch nicht ausgeliehen) — plus die beiden Wege, die danach
tatsächlich buchen: `process_scan` (Scanner/Schüler-WS) und `commit_book_safely`
(Host-Endpoint `/api/commit-book`).

`get_config`/`get_hub` werden bewusst NICHT direkt aus `.config`/`.hub`
importiert, sondern über die Fassade `sessions` zur Laufzeit aufgelöst
(`_sessions.get_config()`/`_sessions.get_hub()`) — Tests patchen diese
Collaborators als `sessions.get_config`/`sessions.get_hub` (Monkeypatch auf dem
Fassaden-Modul), und nur ein Aufruf über das Modul-Objekt selbst sieht einen
solchen Patch zur Laufzeit. Ein bloßes `from .config import get_config` würde
hier eine eigene, unpatchbare Kopie binden. Der Rückverweis `from . import
sessions` ist zirkulär (die Fassade importiert dieses Modul zurück), aber
unschädlich: Python trägt `sessions` schon vor dessen vollständigem Ladevorgang
in `sys.modules` ein, und der Name wird erst beim tatsächlichen Aufruf gelesen,
nicht beim Import.

`mark_book_done`/`invalidate_slip_after_scan` (book_visibility.py bzw.
loan_slip_flow.py) werden aus demselben Grund NICHT direkt aus ihrem
jeweiligen Modul importiert, sondern lokal aus `.sessions` — book_visibility.py
importiert seinerseits `booking_isbn_sets_from_info`/`expected_isbns_from_info`
von HIER auf Modul-Ebene, ein Rückimport dieses Moduls würde also einen
echten Zyklus schließen. Der Umweg über die Fassade bricht ihn UND bleibt
korrekt, unabhängig davon, ob `mark_book_done`/`invalidate_slip_after_scan`
gerade noch lokal in `sessions.py` stehen oder schon ausgelagert sind.
"""

from __future__ import annotations

import asyncio
import logging

from . import sessions as _sessions
from .state import AppState

log = logging.getLogger(__name__)


async def handle_scan(state: AppState, student_id: int, barcode: str) -> dict:
    """Barcode an die Playwright-Worker-Session des Schülers geben.

    Bleibt read-only/staged (kein Submit) — siehe automation/worker.py."""
    worker_session = state.student_worker_sessions.get(student_id)
    if not worker_session:
        return {"status": "error", "msg": "Worker-Session nicht bereit"}
    try:
        return await worker_session.submit_barcode(barcode)
    except Exception as e:  # noqa: BLE001 — Fehler dem Client melden
        log.exception("submit_barcode fehlgeschlagen")
        return {"status": "error", "msg": str(e)}


def expected_isbns_from_info(info: dict) -> set[str]:
    """ISBN-Menge der Bücher, die zu diesem Schüler gehören (Anmeldung + bereits
    ausgeliehen). Grundlage für die Vorab-Prüfung „gehört dieses Buch zu dir?"."""
    return {b["isbn"] for b in info.get("books", []) if b.get("isbn")}


def booking_isbn_sets_from_info(info: dict) -> tuple[set[str], set[str], set[str]]:
    """Zerlegt die Buchliste in (vorgemerkt, ausgeliehen, ausgeliehene Codes) —
    für die Buchungs-Vorabprüfung.

    `vorgemerkt` = bestellt UND von der Reihe ist noch KEIN Buch auf den Schüler
    ausgeliehen (genau die buchbaren ISBNs — `get_student_info` setzt den Status
    einer Reihe auf „ausgeliehen", sobald ein Exemplar verliehen ist).
    `ausgeliehen` = Reihe bereits auf den Schüler ausgeliehen (für die
    Fehlermeldung „Reihe/Buch schon ausgeliehen").
    `ausgeliehene Codes` = die Barcodes der konkret ausgeliehenen Exemplare —
    unterscheidet in `evaluate_scan_for_booking` zwischen „genau DIESES
    Exemplar bereits an dich verliehen" (`book_already_lent`) und „ein ANDERES
    Exemplar derselben Reihe" (`series_already_lent`).
    """
    vormerk: set[str] = set()
    lent_from_books: set[str] = set()
    for b in info.get("books", []):
        isbn = b.get("isbn")
        if not isbn:
            continue
        if b.get("status") == "vorgemerkt":
            vormerk.add(isbn)
        elif b.get("status") == "ausgeliehen":
            lent_from_books.add(isbn)
    # `lent` autoritativ aus `info["current_books"]` (UNGEFILTERT —
    # `apply_hidden_books` entfernt nur `info["books"]`): eine ausgeblendete
    # Reihe, die der Schüler bereits hat, muss trotzdem als „an dich selbst
    # verliehen" erkannt werden — sonst fällt der Scan zu `not_in_stock` und
    # deklariert das eigene Exemplar als „verliehen an jemand anderes".
    # `current_books` ist in echten `info`-Payloads immer vorhanden
    # (`get_student_info`); fehlt es (Unit-Test), wird auf die status-basierte
    # Menge aus `info["books"]` zurückgefallen (dann ohne Code-Info).
    current = info.get("current_books")
    lent: set[str] = (
        {b.get("isbn") for b in current if b.get("isbn")}
        if current is not None
        else lent_from_books
    )
    lent_codes: set[str] = (
        {b.get("code") for b in current if b.get("code")} if current is not None else set()
    )
    return vormerk, lent, lent_codes


async def evaluate_scan_for_booking(
    state: AppState,
    vormerk_isbns: set[str],
    lent_isbns: set[str],
    lent_codes: set[str],
    barcode: str,
) -> dict:
    """Buchungs-Vorabprüfung (read-only) VOR jedem Eintippen ins Feld.

    Gebucht (Enter) wird nur, wenn ALLE Bedingungen erfüllt sind — sonst wird
    der Barcode gar nicht erst ins Feld gefüllt.

      1. Buch im Lager: `available and not distributed and not deleted`.
      2. Schüler hat das Buch bestellt UND von der Reihe ist noch keins auf ihn
         ausgeliehen (= ISBN ∈ vormerk_isbns).

    Prüf-Reihenfolge (Bedingung 1 VOR 2, damit ein verliehenes/ausgemustertes
    Buch immer als solches angezeigt wird, auch wenn der Schüler es gar nicht
    bestellt hat): deleted → book_already_lent/series_already_lent →
    nicht-im-Lager → nicht bestellt. „Bereits an dich ausgeliehen" greift vor
    der Lager-Prüfung, da das Exemplar an dich selbst verliehen sein kann
    (distributed). Zwei Fälle werden unterschieden (`lent_codes` = Barcodes der
    konkret ausgeliehenen Exemplare): scannt der Schüler genau das Exemplar,
    das schon auf ihn läuft (Barcode ∈ `lent_codes`) → `book_already_lent`
    („dieses Buch"). Scannt er ein ANDERES Exemplar derselben Reihe (ISBN ∈
    `lent_isbns`, Barcode aber nicht in `lent_codes`) → `series_already_lent`
    („diese Buchreihe").

    Streng bei Unsicherheit: fehlender API-Client, noch nicht geladene Buchliste
    oder ein Lookup-Fehler → `ok=False` (NICHT buchen). Bewusst strenger als eine
    reine „gehört das Buch zu dir?"-Prüfung: da wir bei Erfolg automatisch Enter
    drücken (Buchung gegen Produktion), muss die Vorabprüfung sicher sein.

    Gibt `{"ok": True, "isbn", "title", "code"}` bei Buchbarkeit, sonst
    `{"ok": False, "status", "msg", ...}`. Reiner Read-Pfad.
    """
    if state.iserv is None:
        return {"ok": False, "status": "error", "msg": "Kein IServ-Client"}
    if not vormerk_isbns and not lent_isbns:
        # Buchliste noch nicht geladen → keine sichere Aussage möglich, nicht buchen.
        return {
            "ok": False,
            "status": "not_ready",
            "msg": "Buchliste noch nicht geladen — bitte erneut scannen",
        }
    try:
        book = await state.iserv.get_book_by_code(barcode)
    except Exception as e:  # noqa: BLE001 — bei Lookup-Fehler NICHT buchen
        log.warning("Buch-Lookup für %s fehlgeschlagen: %s", barcode, e)
        return {"ok": False, "status": "error", "msg": f"Buch-Lookup fehlgeschlagen: {e}"}

    if book is None:
        return {"ok": False, "status": "unknown_book", "msg": "Buch unbekannt"}

    isbn = book["isbn"]
    title = book.get("title") or isbn

    # Ausgemustert-Prüfung ZUERST — ein ausgemustertes Buch wird immer als
    # solches erkannt, egal ob bestellt oder verliehen (s. Ersatzanspruch).
    if book["deleted"]:
        # Ausgemustert, aber noch mit einem Schüler verknüpft (student_id !=
        # null, z. B. [not_timely]/[unusable]) → loaned_to/loaned_to_id
        # durchreichen. Host + Helfer zeigen daraus den Ersatzanspruch-Hinweis,
        # der Schüler-Client bekommt die Felder via process_send als None
        # (siehe dort: `if source != "student"`). msg bleibt name-frei.
        return {
            "ok": False,
            "status": "book_deleted",
            "msg": f"Buch ausgemustert: {title}",
            "isbn": isbn,
            "title": title,
            "loaned_to": book.get("loaned_to"),
            "loaned_to_id": book.get("loaned_to_id"),
            "loaned_to_firstname": book.get("loaned_to_firstname"),
            "loaned_to_lastname": book.get("loaned_to_lastname"),
            "loaned_to_form": book.get("loaned_to_form"),
        }

    # „Bereits an dich ausgeliehen": ISBN steht schon auf dem Schüler als
    # ausgeliehen. VOR der Lager-Prüfung, denn das Buch kann an dich selbst
    # verliehen (distributed) ODER ein anderweitig lagerndes Exemplar
    # derselben ISBN sein — beides „nicht nochmal ausleihen", unabhängig vom
    # Lager-Status dieses Exemplars. Barcode-Abgleich gegen `lent_codes`
    # unterscheidet „dieses Exemplar" von „ein anderes Exemplar der Reihe".
    if isbn in lent_isbns:
        if book["code"] in lent_codes:
            return {
                "ok": False,
                "status": "book_already_lent",
                "msg": f"Bereits an dich verliehen: {title}",
                "isbn": isbn,
                "title": title,
            }
        return {
            "ok": False,
            "status": "series_already_lent",
            "msg": f"Reihe bereits ausgeliehen: {title}",
            "isbn": isbn,
            "title": title,
        }

    # Lager-Prüfung vor der Bestell-Prüfung: ein verliehenes Buch soll als
    # solches gemeldet werden, auch wenn der Schüler es nicht bestellt hat.
    if book["distributed"] or not book["available"]:
        # Buch aktuell an jemand anders verliehen. Den Namen des Ausleihers
        # (read-only aus /books/:code, siehe get_book_by_code) halten wir
        # bewusst AUSSERHALB der `msg` — er wandert nur als eigenes `loaned_to`-
        # Feld in die Payloads. `process_scan` steuert dann, wer ihn sieht:
        # Host (immer) + Helfer-Scanner (Modus A), aber NICHT den Schüler-Client
        # (Modus B) — der Schüler sieht nur „Buch noch verliehen", ohne WEM.
        loaned_to = book.get("loaned_to")
        loaned_to_id = book.get("loaned_to_id")
        return {
            "ok": False,
            "status": "not_in_stock",
            "msg": f"Nicht im Lager (verliehen): {title}",
            "isbn": isbn,
            "title": title,
            "loaned_to": loaned_to,
            "loaned_to_id": loaned_to_id,
            "loaned_to_firstname": book.get("loaned_to_firstname"),
            "loaned_to_lastname": book.get("loaned_to_lastname"),
            "loaned_to_form": book.get("loaned_to_form"),
        }

    # Bedingung 2: bestellt UND Reihe noch nicht ausgeliehen.
    if isbn not in vormerk_isbns:
        return {
            "ok": False,
            "status": "not_enrolled",
            "msg": f"Nicht bestellt: {title}",
            "isbn": isbn,
            "title": title,
        }

    return {"ok": True, "isbn": isbn, "title": title, "code": book["code"]}


async def process_scan(
    state: AppState,
    student_id: int,
    vormerk_isbns: set[str],
    lent_isbns: set[str],
    lent_codes: set[str],
    barcode: str,
    source: str = "student",
) -> dict:
    """Vollständige Scan-Verarbeitung, gemeinsam für Scanner (Modus A) und
    Schüler (Modus B). Returnt das scan_result-Payload (ohne `type`/`barcode`).

    Ablauf:
      1. Buchungs-Vorabprüfung (read-only). Nicht erfüllt → Feld wird NICHT
         berührt, Grund zurückmelden.
      2. Erfüllt UND `ALLOW_BOOKING=true` → tatsächlich buchen (Enter).
      3. Erfüllt, aber Gate aus (Default) → nur stagen (fill, kein Enter) —
         Standardbetrieb bleibt read-only, bis explizit scharfgeschaltet.

    ``source`` ("helper" Modus A / "student" Modus B) steuert, ob der Host für
    die Meldung einen Schließen-Button bekommt: am Helfer-Scanner schließt der
    Helfer das eigene Modal selbst (Button im Client), am Schüler-Client hat
    der Client keinen Schließen-Button → nur der Host darf freigeben.
    """
    # The precheck and the actual browser submit form one critical section.
    # Without this lock two scans/host commits can both pass the check and use
    # the same Playwright page concurrently.
    async with _booking_lock_for(state, student_id):
        return await _process_scan_locked(
            state,
            student_id,
            vormerk_isbns,
            lent_isbns,
            lent_codes,
            barcode,
            source,
        )


async def _process_scan_locked(
    state: AppState,
    student_id: int,
    vormerk_isbns: set[str],
    lent_isbns: set[str],
    lent_codes: set[str],
    barcode: str,
    source: str,
) -> dict:
    """Locked implementation of :func:`process_scan`."""
    decision = await evaluate_scan_for_booking(
        state, vormerk_isbns, lent_isbns, lent_codes, barcode
    )
    if not decision["ok"]:
        # Ausgemustert ODER anderweitig verliehen (nicht im Lager) → am Host
        # sichtbar machen, inkl. student_id für die Zuordnung im „Aktuell in
        # Ausgabe"-Kästchen der betreffenden Person.
        if decision["status"] in ("book_deleted", "not_in_stock"):
            student = state.find_student(student_id)
            await _sessions.get_hub().broadcast_host(
                {
                    "type": "book_alert",
                    "kind": decision["status"],
                    "source": source,
                    "student_id": student_id,
                    "barcode": barcode,
                    "isbn": decision.get("isbn"),
                    "title": decision.get("title"),
                    "msg": decision.get("msg"),
                    "student": f"{student.lastname}, {student.firstname}" if student else None,
                    # „currently lent to someone else"/Ersatzanspruch: Name (+ bei
                    # book_deleted Klasse) des Ausleihers (read-only, PLAN §3.7 —
                    # nicht loggen). loaned_to_form nur bei book_deleted belegt.
                    "loaned_to": decision.get("loaned_to"),
                    "loaned_to_id": decision.get("loaned_to_id"),
                    "loaned_to_firstname": decision.get("loaned_to_firstname"),
                    "loaned_to_lastname": decision.get("loaned_to_lastname"),
                    "loaned_to_form": decision.get("loaned_to_form"),
                }
            )
        return {
            "status": decision["status"],
            "msg": decision["msg"],
            "isbn": decision.get("isbn"),
            "title": decision.get("title"),
            # Name/Klasse des Ausleihers NUR für den Helfer-Scanner (Modus A) —
            # der Schüler-Client (Modus B) bekommt sie bewusst nicht (Privatheit:
            # der Schüler sieht nur „Buch noch verliehen"/"ausgemustert", nicht
            # WEM es gehört). Der Host erhält sie immer über den book_alert-
            # Broadcast.
            "loaned_to": decision.get("loaned_to") if source != "student" else None,
            "loaned_to_id": decision.get("loaned_to_id") if source != "student" else None,
            "loaned_to_firstname": (
                decision.get("loaned_to_firstname") if source != "student" else None
            ),
            "loaned_to_lastname": (
                decision.get("loaned_to_lastname") if source != "student" else None
            ),
            "loaned_to_form": decision.get("loaned_to_form") if source != "student" else None,
        }
    if _sessions.get_config().allow_booking:
        result = await handle_commit(state, student_id, barcode)
    else:
        result = await handle_scan(state, student_id, barcode)
    result.setdefault("isbn", decision.get("isbn"))
    # Erfolgreiche Buchung (Enter) macht die Reihe auf dem Schüler ausgeliehen:
    # ISBN von `vormerk` nach `lent` umhängen, damit ein erneuter Scan derselben
    # Reihe in dieser Session korrekt als „an dich selbst verliehen" erkannt
    # wird. Die Mengen sind die Session-Sets (Mutables, passed-by-reference) —
    # das Update greift direkt am Helper-/Schüler-Session-State.
    if result.get("status") == "booked" and decision.get("isbn"):
        lent_isbns.add(decision["isbn"])
        vormerk_isbns.discard(decision["isbn"])
        if decision.get("code"):
            lent_codes.add(decision["code"])
    # Fortschrittszähler der Host-Queue mitziehen — 'staged' zählt bewusst mit,
    # sonst stünde im read-only Regelbetrieb (ALLOW_BOOKING=false) dauerhaft
    # X=0, während die Client-Bücherliste die Reihen längst als erledigt zeigt.
    if result.get("status") in ("booked", "staged"):
        # Lokale Imports über die Fassade (nicht direkt aus book_visibility/
        # loan_slip_flow) — s. Modul-Docstring oben.
        from .sessions import invalidate_slip_after_scan, mark_book_done

        invalidate_slip_after_scan(state, student_id)
        isbn = result.get("isbn")
        # Nur für den Helfer-Client relevant (löst dort die nicht-blockierende
        # Ja/Nein-Rückfrage aus, s. web/scan-render.js) — der Schüler-Client
        # liest dieses Feld schlicht nicht aus.
        if isbn and isbn in state.caches.empty_isbns:
            result["was_empty_stock"] = True
        mark_book_done(state, student_id, isbn)
    return result


def _booking_lock_for(state: AppState, student_id: int) -> asyncio.Lock:
    """Get the production lock, with a small compatibility seam for test fakes."""
    method = getattr(state, "booking_lock", None)
    if method is not None:
        return method(student_id)
    locks = getattr(state, "_booking_locks", None)
    if locks is None:
        locks = {}
        state.__dict__["_booking_locks"] = locks
    return locks.setdefault(student_id, asyncio.Lock())


def booking_sets_for_student(
    state: AppState, student_id: int
) -> tuple[set[str], set[str], set[str]] | None:
    """Return the live precheck sets for the student's current owner.

    The host recovery endpoint must not accept arbitrary state supplied by the
    request.  Only a paired Modus-B session or currently assigned Modus-A
    helper owns a safely hydrated book list.
    """
    session = state.find_session_by_student(student_id)
    if session is not None and session.state == "paired":
        return session.vormerk_isbns, session.lent_isbns, session.lent_codes
    helper = state.find_helper_for_student(student_id)
    if helper is not None and helper.student_id == student_id:
        return helper.vormerk_isbns, helper.lent_isbns, helper.lent_codes
    return None


async def commit_book_safely(state: AppState, student_id: int, barcode: str) -> dict:
    """Precheck and commit a host-confirmed barcode without a bypass.

    This is intentionally separate from the UI scan handler, but has the same
    fail-closed precheck and state transition.  It is the only function the
    host ``/api/commit-book`` route may call.
    """
    async with _booking_lock_for(state, student_id):
        sets = booking_sets_for_student(state, student_id)
        if sets is None:
            return {"status": "not_ready", "msg": "Kein aktiver Schüler mit geladener Buchliste"}
        vormerk_isbns, lent_isbns, lent_codes = sets
        decision = await evaluate_scan_for_booking(
            state, vormerk_isbns, lent_isbns, lent_codes, barcode
        )
        if not decision["ok"]:
            return {
                "status": decision["status"],
                "msg": decision["msg"],
                "isbn": decision.get("isbn"),
                "title": decision.get("title"),
            }
        result = await handle_commit(state, student_id, barcode)
        result.setdefault("isbn", decision.get("isbn"))
        if result.get("status") == "booked":
            isbn = decision.get("isbn")
            if isbn:
                lent_isbns.add(isbn)
                vormerk_isbns.discard(isbn)
                if decision.get("code"):
                    lent_codes.add(decision["code"])
                # Lokaler Import über die Fassade: gleicher Zyklus-Bruch wie in
                # _process_scan_locked oben.
                from .sessions import mark_book_done

                mark_book_done(state, student_id, isbn)
        return result


async def handle_commit(state: AppState, student_id: int, barcode: str) -> dict:
    """Barcode tatsächlich BUCHEN (Enter auf der Counter-Seite).

    Erste Prüfung ist das Gate: ohne `allow_booking` wird der Worker NICHT
    berührt (kein Enter, kein Produktionskontakt). Der Aufruf dieses Pfads ist
    zusätzlich auf den Host-Endpoint `/api/commit-book` (+ confirm)
    beschränkt — Buchung nur nach Freigabe Niklas + Lukas (CLAUDE.md / PLAN §6).
    """
    if not _sessions.get_config().allow_booking:
        return {"status": "blocked", "msg": "Buchung gesperrt (ALLOW_BOOKING=false)"}
    worker_session = state.student_worker_sessions.get(student_id)
    if not worker_session:
        return {"status": "error", "msg": "Worker-Session nicht bereit"}
    try:
        return await worker_session.commit_barcode(barcode)
    except Exception as e:  # noqa: BLE001
        log.exception("commit_barcode fehlgeschlagen")
        return {"status": "error", "msg": str(e)}
