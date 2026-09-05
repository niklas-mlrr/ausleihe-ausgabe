"""Helfer-Warteschlange: Zuweisung, Booklist-Nachzug, Zuschauer (Modus A/B).

Ausgelagert aus `sessions.py` (Welle 6, s. dortiges Modul-Docstring). Deckt das
„Aufrufen" eines wartenden Schülers durch einen Helfer ab (`assign_student_to_
helper`, `advance_helper`, `assign_next_pending_to_helper`), das Zuschauen auf
einen bereits von einem ANDEREN Helfer bedienten Schüler (`spectate_student`)
und den Live-Nachzug der Bücherliste nach Reihenfolge-/Ausblendungs-Änderungen
(`repush_booklist`, `repush_for_changed_empty_isbns`). Sowohl Modus-A- als auch
Modus-B-Laden (`load_and_push_helper_student`/`load_and_push_paired_student`)
sitzen hier, weil beide `hydrate_student_info` (book_visibility.py) exakt
gleich aufrufen und densel­ben Worker-Aufbau-Ablauf spiegeln. Das eigentliche
Beenden/Entwerten eines Schülers (`end_student` & Co.) sitzt dagegen in
`session_lifecycle.py` — dort begründet, warum die Trennung an genau dieser
Stelle verläuft (Zyklus zwischen den beiden Modulen, an zwei Stellen in
`end_student`s Spectator-Beförderung lokal über die Fassade gebrochen).

`get_hub` wird über die Fassade `sessions` aufgelöst (`_sessions.get_hub()`)
statt direkt aus `.hub` importiert — s. `scan_booking.py`-Docstring für die
ausführliche Begründung (Tests patchen `sessions.get_hub`).
"""

from __future__ import annotations

import asyncio
import logging

from . import sessions as _sessions
from .book_visibility import all_books_already_loaned, hydrate_student_info
from .loan_slip_flow import slip_trigger_for
from .session_lifecycle import end_student, set_worker_session
from .state import AppState, SpectatorWaiter, StudentSessionB

log = logging.getLogger(__name__)


async def load_and_push_helper_student(state: AppState, hub, student, helper) -> None:
    """Modus A: Schülerinfo laden, an den Scanner pushen, Worker-Context öffnen.

    Reihenfolge bewusst: erst `student_info` an den Scanner (sofort sichtbar),
    dann der (langsamere) Worker-Aufbau.
    """
    # Identität festhalten, bevor wir awaiten — end_student/skip kann während
    # open_student helper.student_id auf None (oder einen neuen Schüler) setzen.
    assigned_student_id = student.student_id
    try:
        info = await state.iserv.get_student_info(student.student_id, state.selected_schoolyear)
    except Exception as e:  # noqa: BLE001
        log.exception("Schülerinfo für %d konnte nicht geladen werden", student.student_id)
        await hub.send_scanner(helper.token, {"type": "error", "msg": f"IServ-Fehler: {e}"})
        return

    info = await hydrate_student_info(
        state, info, getattr(student, "form", ""), helper, is_helper=True
    )
    # Modus A: Bücherliste sofort sichtbar. `worker_ready` (ohne Bücher) folgt,
    # sobald der Worker buchungsbereit ist — bis dahin zeigt der Helferclient
    # „Warten…" und ignoriert Scans (clientseitig). Der Host bleibt bis zum
    # fertigen Worker-Laden auf „Lädt"; der erste Broadcast bei der Zuweisung
    # hat den aktiven Schüler samt Helfer bereits an die übrigen Clients gesendet.
    await hub.send_scanner(helper.token, {"type": "student_info", "student": info})

    if state.worker_pool:
        try:
            worker_session = await state.worker_pool.open_student(
                student.student_id,
                f"{student.lastname}, {student.firstname}",
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Worker-Session für Schüler %d fehlgeschlagen", student.student_id)
            await hub.send_scanner(
                helper.token,
                {"type": "error", "msg": f"Playwright-Fehler: {e}. Buchung manuell."},
            )
            # Kein `worker_ready`: Worker nie bereit → Scans bleiben am Client
            # ignoriert, Status zeigt den Fehler. Bücherliste ist bereits da.
            return
        # Stale-Guard: helper.student_id muss noch dem hier zugewiesenen
        # student_id entsprechen, bevor der Context registriert wird.
        # Abgesichert: tests/test_stale_guards.py
        #   ::test_load_and_push_helper_student_stale_guard_closes_orphan_context
        if helper.student_id != assigned_student_id:
            log.info(
                "Stale load_and_push_helper_student für %d — Helfer nicht mehr "
                "zugewiesen (helper.student_id=%r), Context zurück.",
                assigned_student_id,
                helper.student_id,
            )
            try:
                await worker_session.close()
            except Exception:
                log.exception("Schließen des stale Worker-Contexts fehlgeschlagen")
            return
        set_worker_session(state, student.student_id, worker_session)
    # Worker bereit (oder Degraded-Modus ohne worker_pool): Helferclient flippt
    # von „Warten…" auf „Scanner bereit" und gibt Scans frei.
    await hub.send_scanner(helper.token, {"type": "worker_ready"})
    await hub.broadcast_host(state.state_snapshot())


async def repush_booklist(
    state: AppState, hub, student_id: int, target, *, helper: bool
) -> None:
    """Live-Nachzug der Bücherliste nach einer jahrgangsweiten Reihenfolge-/
    Ausblendungs-Änderung (Einstellungen-Dialog, „Ausblenden"-Toggle).

    Holt die Schülerinfo frisch, filtert ausgeblendete Reihen neu
    (`hydrate_student_info` → `apply_hidden_books`), rechnet die ISBN-Vorabmengen
    (expected/vormerk/lent/lent_codes) auf ``target`` auf und schickt dem Client
    eine ``booklist_update``-Nachricht mit der neuen gefilterten Liste +
    Reihenfolge. Bewusst KEINE volle ``student_info``: diese löst clientseitig
    ``resetScannedState`` aus und würde den Scan-Fortschritt im Helfer-/
    Schüler-Client löschen. ``booklist_update`` ersetzt nur die Bücherliste und
    rendert neu — ``scannedIsbns``/``scanOrder`` bleiben erhalten (ein
    ausgeblendetes, bereits gescanntes Buch fällt einfach aus der Liste, ein
    wieder eingeblendetes taucht mit seinem IServ-Status auf).

    Der Worker-Context wird NICHT angefasst (im Gegensatz zu
    `load_and_push_helper_student`/`load_and_push_paired_student`).

    ``target``: HelperSession (Modus A, ``helper=True`` → Versand via
    `send_scanner`) oder StudentSessionB (Modus B, ``helper=False`` → Versand
    via `send_websocket`). Beide tragen die vier ISBN-Mengen, die
    `hydrate_student_info` aktualisiert; ``student_id`` gehört zu ``target``.
    """
    student = state.find_student(student_id)
    if student is None:
        return
    # Session-Scan-Fortschritt (X/Y-Zählung auf dem Host) bewahren:
    # hydrate_student_info → init_book_progress setzt done_isbns auf die
    # „ausgeliehen"-ISBNs aus info zurück; in dieser Session gescannte Bücher
    # wären sonst aus der Zählung weg. Nur Bücher, die nach dem Ausblenden noch
    # in der Liste stehen, behalten ihren Fortschritt (ein ausgeblendetes Buch
    # fällt aus Y UND aus X — die Quote stimmt).
    prev_done = set(student.done_isbns) if student.done_isbns else set()
    try:
        info = await state.iserv.get_student_info(student_id, state.selected_schoolyear)
    except Exception:  # noqa: BLE001
        log.exception("Schülerinfo für booklist_update (%d) fehlgeschlagen", student_id)
        return
    info = await hydrate_student_info(
        state, info, getattr(student, "form", ""), target, reset_baseline=False, is_helper=helper
    )
    new_isbns = {b.get("isbn") for b in info.get("books", []) if b.get("isbn")}
    student.done_isbns |= prev_done & new_isbns
    msg = {
        "type": "booklist_update",
        "books": info.get("books", []),
        "book_order": info.get("book_order", []),
    }
    if helper:
        await hub.send_scanner(target.token, msg)
    elif target.ws is not None:
        await hub.send_websocket(target.ws, msg)


async def repush_for_changed_empty_isbns(state: AppState, hub, changed: set[str]) -> None:
    """Alle Helfer-/Schüler-/Scan-Station-Sessions live nachziehen, deren
    aktuell erwartete Bücher (`expected_isbns`) von einer Bestand-leer-
    Änderung betroffen sind.

    Global statt jahrgangsgebunden (anders als `set_booklist_hidden`'s
    `_student_in_grade`-Filter in `routes/booklists.py`): `empty_isbns` ist
    kein Pro-Jahrgang-Set, ein Mehrjahresband-ISBN kann mehrere Jahrgänge
    gleichzeitig betreffen. Gemeinsam genutzt vom Admin-Endpoint
    (`POST /api/booklist-empty`) und den Helfer-WS-Handlern
    (`mark_empty_stock`/`clear_empty_stock`)."""
    if not changed:
        return
    tasks: list[asyncio.Task] = []
    for helper in state.helper_sessions.values():
        if helper.student_id is None or helper.ws is None:
            continue
        if helper.expected_isbns & changed:
            tasks.append(
                asyncio.create_task(
                    repush_booklist(state, hub, helper.student_id, helper, helper=True)
                )
            )
    for session in state.student_sessions.values():
        if session.student_id is None or session.ws is None or session.state != "paired":
            continue
        if session.expected_isbns & changed:
            tasks.append(
                asyncio.create_task(
                    repush_booklist(state, hub, session.student_id, session, helper=False)
                )
            )
    for station in state.scan_stations.values():
        if station.student_id is None or station.ws is None:
            continue
        if station.expected_isbns & changed:
            tasks.append(
                asyncio.create_task(
                    repush_booklist(state, hub, station.student_id, station, helper=False)
                )
            )
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        await hub.broadcast_host(state.state_snapshot())


async def advance_helper(state: AppState, hub, helper, context_id: str | None = None) -> dict:
    """Helfer auf den nächsten Wartenden setzen.

    Zwei klar getrennte Schritte (analog zur Cleanup-Reihenfolge in
    `/api/helper/{token}` DELETE — erst abschließen, dann neu zuweisen):

      1. Aktuellen Schüler abschließen (`end_student` → Worker-Context zu,
         KEIN Browser-Submit/keine Buchung).
      2. Nächsten Pending aus der Queue diesem Helfer zuweisen.

    `context_id` (optional): Client-Vorschlag „eigene Warteschlange ist leer,
    aber ein Reiter weiter hinten hat noch Wartende" (s. scan.js,
    `suggestedQueueContext`) — der Helfer wird dabei auf diese Klasse
    umgebunden, statt aus seiner (leeren) eigenen Klasse zu ziehen.
    """
    if helper.student_id is not None:
        await end_student(
            state,
            hub,
            helper.student_id,
            queue_status="done",
            session_state="completed",
            helper_notify={"type": "loading"},  # Queue verbergen — nächster wird geladen
        )

    if context_id is not None and context_id in state.contexts and context_id != helper.context_id:
        rebind_helper_to_context(helper, context_id)

    return await assign_next_pending_to_helper(state, hub, helper)


async def assign_next_pending_to_helper(state: AppState, hub, helper) -> dict:
    """Nächsten wartenden Schüler (falls vorhanden) diesem Helfer zuweisen.

    Setzt voraus, dass der Helfer aktuell KEINEN aktiven Schüler mehr hat
    (vom Aufrufer sicherzustellen — z. B. `advance_helper` ruft vorher
    `end_student` für den bisherigen Schüler). Stößt das (langsamere) Laden
    von Schülerinfo + Worker-Context als Hintergrund-Task an
    (`load_and_push_helper_student`), ohne darauf zu warten.
    """
    student = state.next_pending(helper.context_id)
    if not student:
        await hub.send_scanner(
            helper.token,
            {
                "type": "waiting",
                "msg": "Warteschlange leer",
                "queue_size": state.pending_count(helper.context_id),
                "queue": state.pending_queue_as_list(helper.context_id),
                "queue_all": state.queue_as_list(helper.context_id),
            },
        )
        return {"ok": False, "reason": "empty"}

    return await assign_student_to_helper(state, hub, helper, student)


def rebind_helper_to_context(helper, context_id: str | None) -> None:
    """Helfer an eine Klasse binden — genutzt beim „Aufrufen" aus einem fremden
    Klassen-Tab im Helfer-Menü: ``helper.context_id`` wird auf die Klasse des
    aufgerufenen Schülers gesetzt, sodass „Nächster" danach aus dieser Klasse
    zieht (Workflow „ich bediene jetzt diese Klasse"). Ein bisheriger
    `(aktive)`-Helfer (``context_id`` None) wird so beim ersten Aufruf ebenfalls
    an eine konkrete Klasse gebunden. Rein transient — kein IServ-/DB-Zustand.
    """
    helper.context_id = context_id


async def assign_student_to_helper(
    state: AppState, hub, helper, student, *, via_search: bool = False
) -> dict:
    """Gezielten (wartenden) Schüler diesem Helfer zuweisen.

    Genutzt von `assign_next_pending_to_helper` („nächster") und vom
    `call`-Handler im Scanner-WS („aufrufen": Helfer wählt einen konkreten
    Schüler aus der Warteschlange). Setzt den Schüler auf 'active', ordnet
    ihn dem Helfer zu und stößt das Laden von Schülerinfo + Worker-Context
    als Hintergrund-Task an. Rein lokale Zuweisung — kein IServ-/DB-Schreib.
    Der Aufrufer stellt sicher, dass `student.status` `'pending'` oder
    `'done'` ist (erneutes Aufrufen eines fertigen Schülers) und der Helfer
    keinen aktiven Schüler mehr hat.

    ``via_search=True`` markiert eine Zuweisung per Helfer-Lupe
    (`_handle_search_call`) — der Host zeigt dann in der Helferliste die
    Klasse in Klammern hinter dem Namen (bei Queue-Aufrufen nicht). Wird bei
    der Beförderung aus einer Spectator-Warteliste vom Spectator vererbt.
    """
    if helper.spectating_student_id is not None:
        # Helfer bekommt jetzt einen ECHTEN (Worker-)Schüler zugewiesen — eine
        # noch offene Zuschauer-Registrierung (anderer Schüler) wäre sonst eine
        # Karteileiche in dessen Warteliste (z. B. Spectator klickt „Nächster"
        # statt weiter zu warten, oder wird selbst befördert).
        state.remove_spectator(helper.spectating_student_id, helper.token)
        helper.spectating_student_id = None
    student.status = "active"
    student.assigned_helper = helper.token
    helper.student_id = student.student_id
    helper.student_form = student.form  # für Reconnect, falls Schüler nicht in Queue (Lupe)
    # Name am Helfer hinterlegen — für die Helferliste im Host, bes. bei
    # transienten Lupe-Schülern (stehen in KEINER Queue, sonstige Namens-
    # quelle `findStudentInState` greift nicht).
    helper.student_lastname = student.lastname
    helper.student_firstname = student.firstname
    helper.student_via_search = via_search
    helper.peeking = False  # neuer Schüler → keine Queue-Ansicht mehr
    await hub.broadcast_host(state.state_snapshot())
    # Client in den Lade-Zustand versetzen: Queue verbergen, „wird geladen …"
    # zeigen — bevor der (langsame) IServ-Fetch + Worker-Aufbau läuft. Deckt
    # auch den Fall ab, dass der Helfer keinen alten Schüler hatte (Host-
    # „Nächster", „Aufrufen" aus der Queue-Anzeige) → hier gibt es kein
    # `end_student`-`loading`, dieser Send ist das einzige Signal.
    await hub.send_scanner(helper.token, {"type": "loading"})
    helper.load_task = asyncio.create_task(
        load_and_push_helper_student(state, hub, student, helper)
    )
    return {"ok": True, "student_id": student.student_id}


async def spectate_student(
    state: AppState,
    hub,
    helper,
    *,
    student_id: int,
    lastname: str,
    firstname: str,
    form: str,
    via_search: bool = False,
) -> None:
    """Helfer als Zuschauer (Spectator) auf einen bei einem ANDEREN Helfer
    bereits aktiven Schüler registrieren: Bücherliste read-only anzeigen
    (live mitaktualisiert — s. `_handle_scan`s Fan-out an Spectator-Tokens in
    ws.py), aber KEIN eigener Worker-Context — der bleibt exklusiv beim
    aktiven Helfer (es gibt ohnehin nur einen Worker pro `student_id`).
    FIFO-Warteliste je Schüler (`state.student_spectators`); endet der
    Schüler beim aktiven Helfer, übernimmt automatisch der am längsten
    Wartende (`end_student`s Beförderungs-Zweig, ruft dafür wieder
    `assign_student_to_helper` auf — das räumt die Zuschauer-Registrierung
    dann selbst ab).

    Aufgerufen aus `_handle_call`/`_handle_search_call` in ws.py, sobald
    `state.find_helper_for_student(student_id)` einen ANDEREN Helfer als
    aktuellen Besitzer liefert.
    """
    if helper.student_id is not None:
        # Helfer hatte selbst noch einen aktiven Schüler — wie beim direkten
        # `call`/`search_call` erst regulär beenden, bevor er zuschaut.
        await end_student(
            state,
            hub,
            helper.student_id,
            queue_status="pending",
            session_state="revoked",
            helper_notify={"type": "loading"},
        )
    if helper.spectating_student_id is not None:
        state.remove_spectator(helper.spectating_student_id, helper.token)
    helper.spectating_student_id = student_id
    # Form am Helfer hinterlegen — symmetrisch zu `helper.student_form` beim
    # echten Besitzer, für den Reconnect-Wiederherstellungs-Zweig in
    # ws_scanner (dort steht sonst keine Form zur Verfügung).
    helper.student_form = form
    state.add_spectator(
        student_id, SpectatorWaiter(helper.token, lastname, firstname, form, via_search)
    )
    await hub.send_scanner(helper.token, {"type": "loading"})
    try:
        info = await state.iserv.get_student_info(student_id, state.selected_schoolyear)
    except Exception as e:  # noqa: BLE001 — Fehler dem Client melden
        log.exception("Spectator-Schülerinfo für %d konnte nicht geladen werden", student_id)
        state.remove_spectator(student_id, helper.token)
        helper.spectating_student_id = None
        await hub.send_scanner(helper.token, {"type": "error", "msg": f"IServ-Fehler: {e}"})
        return
    info = await hydrate_student_info(state, info, form, helper, is_helper=True)
    await hub.send_scanner(
        helper.token, {"type": "student_info", "student": info, "spectator": True}
    )


async def broadcast_student_info_to_spectators(
    state: AppState, hub, student_id: int, info: dict
) -> None:
    """Bereits geladenes (hydriertes) `info`-Dict an alle Spectators eines
    Schülers spiegeln — für Live-Refresh der Bücherliste, wenn der aktive
    Helfer seine Seite neu lädt (Reconnect in `ws_scanner`)."""
    waiters = state.student_spectators.get(student_id)
    if not waiters:
        return
    payload = {"type": "student_info", "student": info, "spectator": True}
    for waiter in list(waiters):
        await hub.send_scanner(waiter.token, payload)


async def load_and_push_paired_student(
    state: AppState, hub, session: StudentSessionB, student, info: dict
) -> None:
    """Nach erfolgreichem Pairing: Identität sofort ans Handy pushen, Worker danach.

    `info` ist bereits im Endpoint geladen. Modus B trennt bewusst Identität und
    Bücherliste: Name/Klasse/Bezahlstatus gehen sofort in `student_info` (ohne
    Bücher), die Bücherliste folgt mit `worker_ready`, sobald der Worker
    buchungsbereit ist. Bis dahin zeigt der Schülerclient „Wird geladen…" und
    ignoriert Scans. Das Öffnen der Playwright-Worker-Session (`open_student` →
    Browser-Navigation, mehrere Sekunden) blockiert die Identitäts-Anzeige nicht.
    """
    # Identität + Session-State festhalten — invalidate_session kann während
    # open_student die Session auf "revoked" setzen und aus student_sessions
    # poppen. Ohne Stale-Gard registriert der Task danach den Context für einen
    # student_id, der schon nicht mehr zur Session gehört → Worker-Orphan.
    paired_student_id = student.student_id
    info = await hydrate_student_info(
        state, info, getattr(student, "form", ""), session, is_helper=False
    )
    # Bücher erst mit `worker_ready` senden — Identität (inkl. book_order) sofort.
    books = info.get("books", [])
    if session.ws is not None:
        await _sessions.get_hub().send_websocket(
            session.ws,
            {
                "type": "student_info",
                "student": {**info, "books": []},
                "payment_overridden": session.payment_overridden,
            },
        )

    # Kurzschluss: sind beim Pairing bereits alle Bücher ausgeliehen, bringt das
    # Öffnen eines Playwright-Workers nichts — er würde sofort wieder
    # freigegeben (Client geht mit `worker_ready` direkt in den Druckmodus und
    # meldet `print_mode` zurück, s. `routes/ws.py`). Direkt `worker_ready`
    # senden, auch ohne freien Worker — so kommt der Schüler in den Druckmodus,
    # selbst wenn gerade kein Worker frei ist.
    if state.worker_pool and not all_books_already_loaned(books):
        try:
            worker_session = await state.worker_pool.open_student(
                student.student_id,
                f"{student.lastname}, {student.firstname}",
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Worker-Session (Modus B) für %d fehlgeschlagen", student.student_id)
            if session.ws is not None:
                await _sessions.get_hub().send_websocket(
                    session.ws,
                    {"type": "error", "msg": f"Playwright-Fehler: {e}. Buchung manuell."},
                )
            # Kein `worker_ready`: Worker nie bereit → Bücherliste bleibt aus,
            # Scans ignoriert. Host muss den Schüler überspringen/manuell lösen.
            await hub.broadcast_host(state.state_snapshot())
            return
        # Stale-Guard: Session muss noch demselben student_id "paired" sein,
        # bevor der Context registriert wird.
        # Abgesichert: tests/test_stale_guards.py
        #   ::test_load_and_push_paired_student_stale_guard_closes_orphan_context
        if session.student_id != paired_student_id or session.state != "paired":
            log.info(
                "Stale load_and_push_paired_student für %d — Session nicht mehr "
                "paired (state=%r, student_id=%r), Context zurück.",
                paired_student_id,
                session.state,
                session.student_id,
            )
            try:
                await worker_session.close()
            except Exception:
                log.exception("Schließen des stale Worker-Contexts (Modus B) fehlgeschlagen")
            await hub.broadcast_host(state.state_snapshot())
            return
        set_worker_session(state, student.student_id, worker_session)
    # Worker bereit (oder Degraded-Modus ohne worker_pool): Bücherliste an den
    # Schüler pushen + Client flippt von „Wird geladen…" auf „Scanner bereit".
    if session.ws is not None:
        await _sessions.get_hub().send_websocket(
            session.ws,
            {
                "type": "worker_ready",
                "books": books,
                "slip_trigger": slip_trigger_for(state, student.student_id),
                "slip_mode": session.loan_slip_mode,
                "slip_recipient": session.loan_slip_recipient,
                "slip_printed": student.slip_printed,
                "slip_printer": student.slip_printer,
                "slip_printer_label": student.slip_printer_label,
            },
        )

    await hub.broadcast_host(state.state_snapshot())
