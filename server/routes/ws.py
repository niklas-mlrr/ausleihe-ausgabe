from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Cookie, WebSocket, WebSocketDisconnect

from ..hub import get_hub
from ..print_queue import PrintJob
from ..print_queue import slip_name as _slip_name
from ..ratelimit import SlidingWindowLimiter
from ..sessions import (
    advance_helper,
    allowed_printers_for,
    assign_student_to_helper,
    broadcast_printer_displays,
    broadcast_scanner_result,
    broadcast_student_info_to_spectators,
    confirm_slip_received,
    displayed_printer_ids,
    eligible_drucker_scanners_for,
    end_student,
    gen_registration_code,
    hydrate_student_info,
    load_station_student,
    pending_vormerk_isbns_for,
    persist_helpers,
    persist_printer_displays,
    persist_printer_scanners,
    persist_scan_stations,
    process_scan,
    rebind_helper_to_context,
    release_station_student,
    release_student_worker,
    relevant_display_count,
    repush_for_changed_empty_isbns,
    resolve_station_code,
    send_display_update,
    send_printer_display_update,
    send_printer_scanner_update,
    send_scan_station_update,
    send_teacher_update,
    slip_signature_options_for,
    slip_trigger_for,
    spectate_student,
)
from ..state import (
    DisplaySession,
    PrinterDisplaySession,
    PrinterScannerSession,
    QueueStudent,
    ScanStationSession,
    get_state,
    own_print_defaults,
    pool_light,
)
from .booklists import _persist_booklist_settings

log = logging.getLogger(__name__)
router = APIRouter()

# Grace-Frist zwischen Scanner-Disconnect und Schüler-Teardown (Reconnect-Fenster).
# Abgesichert: tests/test_scanner_reconnect.py
_RECONNECT_GRACE_S = 3.0


async def safe_broadcast(hub, state) -> None:
    """Host-Broadcast, dessen Fehler bewusst verschluckt werden.

    Wird an mehreren Stellen (Deferred-Teardown, `finally`-Blöcke der WS-
    Handler) verwendet, an denen ein fehlgeschlagener Broadcast den
    umgebenden Ablauf (Teardown bzw. Verbindungsabbau) nicht stören darf.
    """
    try:
        await hub.broadcast_host(state.state_snapshot())
    except Exception:  # noqa: BLE001 — Broadcast-Fehler nicht propagieren
        pass


async def _take_over_ws(holder, websocket) -> None:
    """Übernimmt eine Reconnect-Verbindung auf `holder.ws` (Helfer oder Schüler-Session).

    Synchron übernehmen — VOR jedem await. So erkennt das `finally` des alten
    WS (das asynchron zum Reconnect läuft) an `holder.ws is websocket`, dass
    ein Reconnect übernommen hat, und löst KEINEN Teardown aus.
    """
    old_ws = holder.ws
    holder.ws = websocket
    # Reconnect: die alte Verbindung sauber schließen, statt sie verwaist offen
    # zu lassen.
    if old_ws is not None and old_ws is not websocket:
        try:
            await old_ws.close(code=4009, reason="Neue Verbindung")
        except Exception:
            pass


async def _deferred_end(state, hub, helper, student_id: int) -> None:
    """Verzögerter Teardown des Helfer-Schülers nach WS-Trennung (s. ws_scanner).

    Abgesichert: tests/test_scanner_reconnect.py::test_deferred_end_noop_on_reconnect,
    ::test_deferred_end_noop_on_student_changed"""
    try:
        await asyncio.sleep(_RECONNECT_GRACE_S)
    except asyncio.CancelledError:
        return
    # Re-Check 1: Helfer hat wieder eine Verbindung (Reconnect) → kein Teardown.
    if helper.ws is not None:
        return
    # Re-Check 2: Helfer wurde inzwischen weitergeschaltet/zurückgesetzt.
    if helper.student_id != student_id:
        return
    try:
        if helper.load_task is not None and not helper.load_task.done():
            helper.load_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await helper.load_task
            helper.load_task = None
        await end_student(
            state,
            hub,
            student_id,
            queue_status="pending",
            session_state="revoked",
        )
    except Exception:  # noqa: BLE001 — Sweeper-Loop-artige Robustheit: ein fehlgeschlagener Teardown darf den Task nicht crashen
        log.exception("deferred end_student für %d fehlgeschlagen", student_id)
    await safe_broadcast(hub, state)


@router.websocket("/ws/host")
async def ws_host(websocket: WebSocket, session_id: str | None = Cookie(default=None)) -> None:
    state = get_state()
    hub = get_hub()
    from ..config import get_config

    if not state.is_host_session_valid(session_id, get_config().host_session_ttl_s):
        await websocket.close(code=4003, reason="Nicht authentifiziert")
        return

    await websocket.accept()
    state.host_ws_connections.append(websocket)
    # sid→WS registrieren, damit Druck-Status-Popups gezielt an den Host gehen,
    # der den Druck gestartet hat (s. print_queue / state.host_ws_by_sid).
    if session_id:
        state.host_ws_by_sid.setdefault(session_id, []).append(websocket)
    try:
        await hub.send_websocket(websocket, state.state_snapshot())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            state.host_ws_connections.remove(websocket)
        except ValueError:
            pass
        if session_id:
            conns = state.host_ws_by_sid.get(session_id)
            if conns is not None:
                try:
                    conns.remove(websocket)
                except ValueError:
                    pass
                if not conns:
                    state.host_ws_by_sid.pop(session_id, None)


# ---------------------------------------------------------------------------
# Modus A — Helfer-Scanner: Dispatch-Tabelle für die Empfangsschleife
# ---------------------------------------------------------------------------


async def _handle_next(state, hub, helper, websocket, raw) -> None:
    # Aktuellen Schüler abschließen (kein Browser-Submit) und
    # nächsten Wartenden auf diesen Helfer setzen. `context_id`
    # (optional): Client hat vorgeschlagen, auf eine andere
    # (nicht-leere) Klasse umzuspringen, weil die eigene
    # Warteschlange leer ist — s. advance_helper.
    await advance_helper(state, hub, helper, context_id=raw.get("context_id"))


async def _handle_call(state, hub, helper, websocket, raw) -> None:
    # Helfer ruft einen konkreten Schüler aus der Warteschlangen-
    # Anzeige auf (Button bei wartenden, aktiven UND bereits fertigen
    # Schülern — Aktive werden Zuschauer und warten, bis der Schüler
    # frei ist; Fertige lassen sich so erneut aufrufen, z. B. um
    # nachträglich ein vergessenes Buch zu erfassen). Rein lokale
    # Zuweisung — kein IServ-/DB-Schreibzugriff. Bei 'pending'/'done'
    # erfolgt die Zuweisung direkt; zwischen Prüfung und Zuweisung
    # liegt kein Await, also atomar im Eventloop (kein Doppel-
    # Aufruf zweier Helfer auf denselben Schüler).
    sid = raw.get("student_id")
    target_pair = state.find_student_with_ctx(sid) if sid is not None else None
    target = target_pair[1] if target_pair else None
    # Selbst-Aufruf (Helfer ruft SEINEN EIGENEN aktiven Schüler erneut auf):
    # zählt bewusst wie ein neuer Zugriff, nicht wie ein reiner Refresh. Gibt
    # es eine Warteliste für diesen Schüler, gibt der Aufrufer seine
    # Aktivität ab (der Erste in der Liste wird per end_student-Beförderung
    # übernehmen) und stellt sich selbst hinten an — statt sich direkt
    # zurückzuholen (das wäre wieder ein Doppel-Aktiv-Fall). Ohne Warteliste
    # fällt dieser Fall unten in den normalen Reload-Pfad (end_student +
    # Neuzuweisen an denselben Helfer), der `loading` sendet und so auch das
    # Menü/Such-Panel schließt.
    self_recall_reload = False
    if target is not None and target.status == "active":
        owner = state.find_helper_for_student(sid)
        if owner is not None and owner.token == helper.token:
            if state.student_spectators.get(sid):
                await end_student(
                    state,
                    hub,
                    helper.student_id,
                    queue_status="pending",
                    session_state="revoked",
                    helper_notify={"type": "loading"},
                )
                await spectate_student(
                    state,
                    hub,
                    helper,
                    student_id=sid,
                    lastname=target.lastname,
                    firstname=target.firstname,
                    form=target.form,
                )
                return
            self_recall_reload = True
        else:
            # Schüler ist gerade aktiv, aber NICHT beim aufrufenden Helfer:
            # entweder bei einem ANDEREN Helfer (Queue-`call`/Lupe) ODER bei
            # einem Schülerclient (Modus B — dann ist `owner` None, weil
            # Modus-B-Pairing `status='active'` ohne `assigned_helper` setzt).
            # Statt eines Fehlers wird der Aufrufer Zuschauer (read-only
            # Bücherliste, live mitaktualisiert) und automatisch befördert,
            # sobald der Aktive den Schüler freigibt (end_student/
            # pop_next_spectator — Owner-unabhängig, greift auch beim
            # Selbst-Abschluss eines Schülerclients).
            await spectate_student(
                state,
                hub,
                helper,
                student_id=sid,
                lastname=target.lastname,
                firstname=target.firstname,
                form=target.form,
            )
            return
    if not self_recall_reload and (target is None or target.status not in ("pending", "done")):
        await hub.send_websocket(
            websocket,
            {
                "type": "error",
                "msg": "Schüler nicht (mehr) in der Warteschlange",
            },
        )
        # Queue sofort nachpushen, damit der Client die aktuelle
        # Liste sieht (z. B. zwischenzeitlich von anderem Helfer
        # aufgerufen) — statt auf den nächsten Broadcast zu warten.
        await hub.broadcast_queue_size(state)
        return
    # Aufrufen aus einer fremden Klasse (anderer Klassen-Tab im
    # Helfer-Menü) ist erlaubt: der Helfer wird dabei an die Klasse
    # des aufgerufenen Schülers gebunden (helper.context_id), sodass
    # „Nächster" danach aus dieser Klasse zieht (Workflow „ich
    # bediene jetzt diese Klasse"). `(aktive)`-Helfer (context_id
    # None) werden beim ersten Aufruf ebenfalls gebunden.
    if target_pair[0].id != helper.context_id:
        rebind_helper_to_context(helper, target_pair[0].id)
    if helper.student_id is not None:
        # Aufrufen aus der Peek-Ansicht (Menü): der alte Schüler wird
        # NICHT abgeschlossen, sondern als 'pending' zurück in die
        # Warteschlange gelegt (noch nicht bearbeitet). Der Worker-
        # Context schließt (revoked), der Schüler bleibt aber in der
        # Queue verfügbar — wie beim Disconnect-Teardown
        # (`_deferred_end`). Der „Weiter"-Button (`next`) dagegen
        # schließt den Schüler als 'done' ab (s. advance_helper).
        await end_student(
            state,
            hub,
            helper.student_id,
            queue_status="pending",
            session_state="revoked",
            helper_notify={"type": "loading"},  # Queue verbergen — neuer wird geladen
        )
    await assign_student_to_helper(state, hub, helper, target)


async def _handle_search_classes(state, hub, helper, websocket, raw) -> None:
    # Helfer-Lupe: Liste aller Klassen des gewählten Schuljahrs
    # (IServ, read-only). Schuljahrbezogen gecached, damit wieder-
    # holtes Öffnen der Suche keine IServ-Roundtrips auslöst.
    if state.iserv is None:
        await hub.send_websocket(websocket, {"type": "search_classes", "classes": []})
        return
    sy = state.selected_schoolyear
    cached = state.caches.class_names_cache.get(sy)
    if cached is None:
        try:
            cached = await state.iserv.get_class_names(sy)
        except Exception as e:  # noqa: BLE001
            log.warning("search_classes fehlgeschlagen: %s", e)
            await hub.send_websocket(
                websocket,
                {"type": "error", "msg": f"Klassen konnten nicht geladen werden: {e}"},
            )
            return
        state.caches.class_names_cache[sy] = cached
    await hub.send_websocket(websocket, {"type": "search_classes", "classes": cached})


async def _handle_search_students(state, hub, helper, websocket, raw) -> None:
    # Helfer-Lupe: alle Schüler einer Klasse (IServ, read-only),
    # schuljahrbezogen gecached (Key "schoolyear|form").
    form = str(raw.get("form") or "").strip()
    if state.iserv is None:
        await hub.send_websocket(
            websocket,
            {"type": "search_students", "form": form, "students": []},
        )
        return
    sy = state.selected_schoolyear
    key = f"{sy}|{form}"
    cached = state.caches.form_students_cache.get(key)
    if cached is None:
        try:
            cached = await state.iserv.get_students_for_form(form, sy)
        except Exception as e:  # noqa: BLE001
            log.warning("search_students fehlgeschlagen: %s", e)
            await hub.send_websocket(
                websocket,
                {"type": "error", "msg": f"Schüler konnten nicht geladen werden: {e}"},
            )
            return
        state.caches.form_students_cache[key] = cached
    await hub.send_websocket(
        websocket,
        {"type": "search_students", "form": form, "students": cached},
    )


async def _handle_search_call(state, hub, helper, websocket, raw) -> None:
    # Helfer-Lupe: gezielt einen beliebigen IServ-Schüler laden
    # (Schnellsprung — der Schüler muss NICHT in der Warteschlange
    # stehen). Aktuellen Schüler wie beim Peek-`call` auf 'pending'
    # zurückgeben, dann den Schüler via assign_student_to_helper laden.
    # Steht er bereits in einer Klassen-Queue, wird dieser ECHTE Eintrag
    # übernommen (s. Claim-Kommentar unten) — sonst ein transienter
    # QueueStudent (bewusst NICHT in eine Queue eingetragen). Read-only:
    # IServ/DB werden nur gelesen (get_student_info), kein Write.
    sid = raw.get("student_id")
    form = str(raw.get("form") or "").strip()
    lastname = str(raw.get("lastname") or "").strip()
    firstname = str(raw.get("firstname") or "").strip()
    if sid is None or not form:
        await hub.send_websocket(websocket, {"type": "error", "msg": "Schüler/Klasse fehlt"})
        return
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        await hub.send_websocket(websocket, {"type": "error", "msg": "Ungültige Schüler-ID"})
        return
    # Guard gegen Doppel-Öffnen: anders als `call` (nur pending/done aus der
    # eigenen Queue) kann die Lupe JEDEN Schüler treffen — auch einen, der
    # gerade bei einem ANDEREN Helfer/Client aktiv ist (Queue-`call` oder
    # ebenfalls Lupe). `find_helper_for_student` erkennt das unabhängig davon,
    # ob der Ziel-Schüler ein echter Queue-Eintrag oder selbst ein transienter
    # Lupe-Schüler ist (der steht in KEINER Queue, `find_student` würde ihn
    # also nicht als „belegt" erkennen). Statt eines Fehlers wird der
    # Aufrufer Zuschauer (s. spectate_student) und automatisch befördert,
    # sobald der aktive Helfer den Schüler beendet.
    owner = state.find_helper_for_student(sid)
    if owner is not None and owner.token == helper.token:
        # Selbst-Aufruf (Helfer sucht/ruft SEINEN EIGENEN aktiven Schüler per
        # Lupe erneut auf): zählt wie ein neuer Zugriff, s. Kommentar in
        # _handle_call. Gibt es eine Warteliste, gibt der Aufrufer seine
        # Aktivität ab (Erster in der Liste übernimmt) und stellt sich selbst
        # hinten an; sonst fällt der Fall unten in den normalen Reload-Pfad.
        if state.student_spectators.get(sid):
            await end_student(
                state,
                hub,
                helper.student_id,
                queue_status="pending",
                session_state="revoked",
                helper_notify={"type": "loading"},
            )
            await spectate_student(
                state,
                hub,
                helper,
                student_id=sid,
                lastname=lastname,
                firstname=firstname,
                form=form,
                via_search=True,
            )
            return
    elif owner is not None:
        await spectate_student(
            state, hub, helper, student_id=sid, lastname=lastname, firstname=firstname, form=form,
            via_search=True,
        )
        return
    else:
        # Kein Helfer-Owner — aber der Schüler kann trotzdem aktiv sein, wenn
        # er gerade per Schülerclient (Modus B) geladen wurde: Modus-B-Pairing
        # setzt `status='active'` OHNE `assigned_helper`, sodass
        # `find_helper_for_student` None liefert. Ohne diesen Guard würde
        # unten ein transienter Schüler erzeugt und per
        # `assign_student_to_helper` übernommen → Doppel-Aktiv-Konflikt mit
        # dem Schülerclient. Stattdessen Zuschauer werden und warten, bis der
        # Schüler frei ist (Owner-unabhängige Beförderung via end_student).
        queued = state.find_student(sid)
        if queued is not None and queued.status == "active":
            await spectate_student(
                state,
                hub,
                helper,
                student_id=sid,
                lastname=lastname,
                firstname=firstname,
                form=form,
                via_search=True,
            )
            return
    if helper.student_id is not None:
        await end_student(
            state,
            hub,
            helper.student_id,
            queue_status="pending",
            session_state="revoked",
            helper_notify={"type": "loading"},  # Queue verbergen — neuer wird geladen
        )
    # Claim-Logik: steht der Ziel-Schüler bereits in einer Klassen-Queue
    # (pending/done/skipped), wird genau dieser ECHTE Eintrag übernommen
    # (active + zugewiesen) statt ein transienter Doppelgänger erzeugt. Ein
    # transienter Doppelgänger ließe den echten Queue-Eintrag unangetastet
    # stehen — bei 'pending' bliebe er für einen zweiten Helfer regulär
    # aufrufbar: sein Queue-`call` sieht `status == "pending"` (NICHT
    # "active"), übersieht den transienten Besitzer und übernimmt den
    # Schüler → derselbe Schüler wäre von zwei Helfern gleichzeitig aktiv
    # geladen. Den echten Eintrag übernehmen macht ihn "active", sodass der
    # Queue-`call` des zweiten Helfers sauber in die Spectator-Warteliste
    # läuft (s. _handle_call). Zugleich findet `end_student` beim Abschluss
    # den aktiven Eintrag und löst den Helfer sauber (ohne Claim fände er
    # nur den noch-pending-Eintrag mit `assigned_helper == None` und ließe
    # den Helfer mit stale `student_id`/leakendem Worker zurück). Steht der
    # Schüler in KEINER Queue (Schnellsprung zu beliebigem IServ-Schüler),
    # bleibt es beim transienten Eintrag — bewusst nicht in eine Queue
    # eingetragen.
    existing = state.find_student_with_ctx(sid)
    if existing is not None:
        student = existing[1]
    else:
        student = QueueStudent(
            student_id=sid,
            lastname=lastname,
            firstname=firstname,
            form=form,
            status="active",
            assigned_helper=helper.token,
        )
    await assign_student_to_helper(state, hub, helper, student, via_search=True)


async def _handle_peek_queue(state, hub, helper, websocket, raw) -> None:
    # Menü-Toggle: Helfer schaltet auf die Warteschlangen-Ansicht,
    # während sein Schüler im Hintergrund verbunden bleibt (kein
    # Trennen, kein IServ-/DB-Zugriff). Peek-Flag setzen, damit
    # nachfolgende `broadcast_queue_size`-Updates diesen Helfer
    # erreichen, und sofort die aktuelle Queue pushen (für ein
    # unmittelbares Rendern, ohne auf den nächsten Broadcast zu
    # warten).
    helper.peeking = True
    await hub.send_scanner(
        helper.token,
        {
            "type": "queue_update",
            "queue_size": state.pending_count(helper.context_id),
            "queue": state.pending_queue_as_list(helper.context_id),
            "queue_all": state.queue_as_list(helper.context_id),
        },
    )
    # Frische Kontext-Übersicht (alle offenen Klassen + eigene) für
    # die Klassen-Reiter — ein Helfer mit aktivem Schüler bekommt
    # sonst keine Live-contexts_update (broadcast_queue_size erreicht
    # ihn nur, weil hier gerade peeking=True gesetzt wurde; das eigene
    # peek_queue sendet sie aber bewusst sofort, ohne auf den nächsten
    # Broadcast zu warten).
    await hub.send_scanner(
        helper.token,
        {
            "type": "contexts_update",
            "contexts": state.real_contexts_summary(),
            "own_context_id": helper.context_id,
        },
    )


async def _handle_peek_close(state, hub, helper, websocket, raw) -> None:
    # Menü-Toggle zurück zur Schüler-Ansicht. Kein Push nötig — der
    # Client stellt die Bücherliste lokal wieder her.
    helper.peeking = False


async def _handle_clear_book_alert(state, hub, helper, websocket, raw) -> None:
    # Helfer schließt sein Ausgemustert-Hinweis-Modal selbst (Button)
    # → Host-Meldung für diesen Schüler ebenfalls aufräumen, damit das
    # Now-Serving-Kästchen wieder normal angezeigt wird. Read-only,
    # kein IServ-/DB-Zugriff; nur ein Host-Broadcast.
    sid = helper.student_id
    if sid is not None:
        await hub.broadcast_host(
            {
                "type": "book_alert",
                "student_id": sid,
                "cleared": True,
            }
        )


async def _handle_mark_empty_stock(state, hub, helper, websocket, raw) -> None:
    """Helfer markiert Buchreihen als „Bestand leer" (Checkbox im Next-/
    Print-Warn-Modal, s. web/scan-render.js). Nur ISBNs des AKTUELL
    zugewiesenen Schülers dürfen markiert werden (`helper.expected_isbns`) —
    Defense in Depth gegen einen buggy/kompromittierten Client, der beliebige
    ISBNs fremder Schüler flaggen wollte."""
    isbns = raw.get("isbns")
    if not isinstance(isbns, list):
        return
    valid = {i for i in isbns if isinstance(i, str) and i in helper.expected_isbns}
    if not valid:
        return
    state.caches.empty_isbns |= valid
    _persist_booklist_settings(state)
    await hub.broadcast_settings()
    await repush_for_changed_empty_isbns(state, hub, valid)


async def _handle_clear_empty_stock(state, hub, helper, websocket, raw) -> None:
    """Helfer bestätigt im Rescan-Popup „Ja, wieder da" — Bestand-leer-
    Markierung dieser einen ISBN entfernen. Nicht-blockierend: die Buchung
    selbst ist zu diesem Zeitpunkt bereits abgeschlossen (s.
    `_process_scan_locked`'s `was_empty_stock`-Flag)."""
    isbn = raw.get("isbn")
    if not isinstance(isbn, str) or isbn not in state.caches.empty_isbns:
        return
    state.caches.empty_isbns.discard(isbn)
    _persist_booklist_settings(state)
    await hub.broadcast_settings()
    await repush_for_changed_empty_isbns(state, hub, {isbn})


async def _handle_print(state, hub, helper, websocket, raw) -> None:
    # Leihschein des aktuell zugewiesenen Schülers drucken — über die interne
    # Druckerwarteschlange (Rollen-Rangfolge, 2-in-flight). Read-only PDF-Abruf
    # + lokaler Druck (kein IServ-Submit). Status/Position/Ergebnis kommen live
    # via `print_progress`/`print_result` vom Worker, nicht synchron hier.
    if helper.student_id is None:
        await hub.send_websocket(
            websocket,
            {"type": "print_result", "ok": False, "msg": "Kein Schüler zugewiesen"},
        )
        return
    # Leerer Drucker-Pool → Druck verweigern (Auftrag würde sonst endlos in der
    # Warteschlange hängen, da der Scheduler nichts verteilt).
    if not state.settings.printers:
        await hub.send_websocket(
            websocket,
            {"type": "print_result", "ok": False, "msg": "Kein Drucker konfiguriert"},
        )
        return
    # Vom Helfer im Druck-Dialog gewählte Drucker. `printers` als Schlüsser
    # vorhanden → ausschließlich diese nutzen (leer = blockieren, s. u.);
    # Schlüsser fehlt (alt/Tests) → Fallback auf die Klassen-Allowlist.
    pool_ids = {p.id for p in state.settings.printers}
    selected = raw.get("printers")
    if selected is not None:
        selected_ids = {pid for pid in selected if pid in pool_ids}
        if not selected_ids:
            await hub.send_websocket(
                websocket,
                {"type": "print_result", "ok": False,
                 "msg": "Bitte mindestens einen Drucker auswählen"},
            )
            return
        allowed = selected_ids
    else:
        # Drucker-Allowlist der Klasse des Schülers (Snapshot zum Enqueue-
        # Zeitpunkt). `None` = alle Pool-Drucker; explizite Menge ohne Treffer
        # im Pool → verweigern.
        allowed = allowed_printers_for(state, helper.student_id)
        if allowed is not None and not (allowed & pool_ids):
            await hub.send_websocket(
                websocket,
                {
                    "type": "print_result",
                    "ok": False,
                    "msg": "Kein erlaubter Drucker im Pool für diese Klasse",
                },
            )
            return
    # Seite 1 wird immer gedruckt; Seite 2 (Schüler-Leihschein) nur,
    # wenn der Helfer sie im Druck-Dialog aktiviert hat.
    second_page = bool(raw.get("second_page"))
    pages = None if second_page else "1"
    # Name/Klasse für die eigene Statusanzeige (Helfer bekommt kein Popup, nur
    # Statustext — `name` dient dort als Fallback, falls der Worker es liefert).
    student = state.find_student(helper.student_id)
    if student is not None:
        name = _slip_name(student.lastname, student.firstname, student.form)
    else:
        name = _slip_name(helper.student_lastname, helper.student_firstname, helper.student_form)
    job = PrintJob.create(
        role="helper",
        student_id=helper.student_id,
        pages=pages,
        name=name,
        helper_token=helper.token,
        request_id=str(raw.get("request_id") or "") or None,
        allowed_printers=allowed,
    )
    await state.print_queue.enqueue(job)


async def _handle_update_print_printers(state, hub, helper, websocket, raw) -> None:
    """Erlaubte Drucker eines eigenen, noch wartenden (nicht dispatchten)
    Druckauftrags ändern — Nachfrage-Menü, wenn alle erlaubten Drucker
    fehlerhaft sind, bzw. proaktives Umbuchen, solange der Auftrag noch
    unzugewiesen in der zentralen Warteschlange steht. Der Job behält dabei
    seine Warteposition (`PrintQueue.update_job_printers` mutiert in-place)."""
    job_id = raw.get("job_id")
    printers = raw.get("printers")
    if not isinstance(job_id, str) or not isinstance(printers, list):
        return
    result = await state.print_queue.update_job_printers(
        job_id, ("helper", helper.token), printers
    )
    if result == "ok":
        return
    msg = {
        "not_waiting": "Auftrag wurde bereits einem Drucker zugewiesen",
        "forbidden": "Kein eigener Druckauftrag",
        "empty": "Bitte mindestens einen Drucker auswählen",
    }.get(result, "Aktualisieren fehlgeschlagen")
    await hub.send_websocket(
        websocket, {"type": "print_printers_update_result", "ok": False, "msg": msg}
    )


async def _handle_print_for_student(state, hub, helper, websocket, raw) -> None:
    """Betreuerauslöser: ein Helfer druckt stellvertretend für einen ANDEREN
    Schüler aus der Klassenliste — anders als `_handle_print` (immer der
    eigene zugewiesene `helper.student_id`). Nur erlaubt, wenn die Klasse des
    Schülers „Betreuerauslöser" gewählt hat UND der Schüler selbst bereits in
    den Druckmodus gewechselt ist (`QueueStudent.print_mode`, gesetzt beim WS
    `print_mode`) — der Helfer-Client blendet den Button serverseitig
    validiert genauso ein/aus (Client-Gate ist nur UI, kein Vertrauen).

    Der Auftrag wird bewusst wie ein Schüler-Auftrag angelegt (`role=
    "student"`, kein `helper_token`) — Anforderung: der Leihschein soll auf
    dem Drucker-Display OHNE Helfername erscheinen, genau wie beim
    automatischen/Schülerauslöser-Druck. `student_token` (falls die Modus-B-
    Session noch verbunden ist) sorgt zusätzlich dafür, dass der Schüler auf
    seinem eigenen Bildschirm den Druckfortschritt sieht.

    Der `in_flight_student_ids()`-Check unmittelbar vor dem Enqueue verhindert
    einen doppelten Druck, wenn zwei Helfer gleichzeitig auf den Button
    klicken: da zwischen der Prüfung und `enqueue()` kein `await` liegt, ist
    der Check-then-enqueue-Schritt gegen andere WS-Handler-Tasks atomar
    (kooperatives asyncio-Scheduling) — der zweite Klick sieht den Auftrag des
    ersten bereits in `in_flight_student_ids()` und wird abgewiesen. Der
    Broadcast an alle Helfer (`PrintQueue._notify_all` → `broadcast_queue_size`)
    blendet den Button danach fleet-weit aus."""
    async def _reject(msg: str) -> None:
        await hub.send_websocket(
            websocket, {"type": "print_for_student_result", "ok": False, "msg": msg}
        )

    student_id = raw.get("student_id")
    if not isinstance(student_id, int):
        await _reject("Ungültiger Schüler")
        return
    found = state.find_student_with_ctx(student_id)
    if found is None:
        await _reject("Schüler nicht gefunden")
        return
    ctx, target = found
    if (
        ctx.slip_trigger != "helper"
        or target.status != "active"
        or not target.print_mode
        or target.slip_printed
        or student_id in state.print_queue.in_flight_student_ids()
    ):
        await _reject("Leihschein bereits gesendet oder nicht bereit")
        return
    if not state.settings.printers:
        await _reject("Kein Drucker konfiguriert")
        return
    pool_ids = {p.id for p in state.settings.printers}
    selected = raw.get("printers")
    if selected is not None:
        selected_ids = {pid for pid in selected if pid in pool_ids}
        if not selected_ids:
            await _reject("Bitte mindestens einen Drucker auswählen")
            return
        allowed = selected_ids
    else:
        allowed = allowed_printers_for(state, student_id)
        if allowed is not None and not (allowed & pool_ids):
            await _reject("Kein erlaubter Drucker im Pool für diese Klasse")
            return
    second_page = bool(raw.get("second_page"))
    pages = None if second_page else "1"
    name = _slip_name(target.lastname, target.firstname, target.form)
    session = state.find_session_by_student(student_id)
    job = PrintJob.create(
        role="student",
        student_id=student_id,
        pages=pages,
        name=name,
        student_token=session.session_token if session is not None else None,
        allowed_printers=allowed,
    )
    await state.print_queue.enqueue(job)
    await hub.send_websocket(
        websocket, {"type": "print_for_student_result", "ok": True, "detail": "gesendet"}
    )


async def _handle_finish_signed(state, hub, helper, websocket, raw) -> None:
    """„Leihschein unterschreiben"-Button in der Klassenliste des Helfer-
    Clients (analog dem Betreuerauslöser-Druckbutton): der Helfer bestätigt
    damit, dass der bereits gedruckte, physische Leihschein unterschrieben und
    entgegengenommen wurde, und schließt den Schüler ab — identisch zum
    Host-Button „Abschließen" (`/api/finish`). Nur erlaubt, wenn die Klasse
    des Schülers „Leihschein unterschreiben" aktiv hat (`ClassContext.
    done_signed`) UND der Schüler selbst im Unterschriften-Modus ist
    (`QueueStudent.slip_signing`, gesetzt in `confirm_slip_received`) — der
    Helfer-Client blendet den Button serverseitig validiert genauso ein/aus
    (Client-Gate ist nur UI, kein Vertrauen)."""
    async def _reject(msg: str) -> None:
        await hub.send_websocket(
            websocket, {"type": "finish_signed_result", "ok": False, "msg": msg}
        )

    student_id = raw.get("student_id")
    if not isinstance(student_id, int):
        await _reject("Ungültiger Schüler")
        return
    found = state.find_student_with_ctx(student_id)
    if found is None:
        await _reject("Schüler nicht gefunden")
        return
    ctx, target = found
    if not ctx.done_signed or target.status != "active" or not target.slip_signing:
        await _reject("Schüler ist nicht im Unterschriften-Modus")
        return
    await end_student(
        state, hub, student_id, queue_status="done", session_state="completed",
    )
    if state.helper_sessions:
        await hub.broadcast_queue_size(state)
    await hub.send_websocket(
        websocket, {"type": "finish_signed_result", "ok": True, "detail": "abgeschlossen"}
    )


async def _handle_scan(state, hub, helper, websocket, raw) -> None:
    barcode = str(raw.get("value", "")).strip()
    if not barcode:
        return

    helper.last_scan = barcode
    # Helper tokens are bearer credentials embedded in scanner URLs; never put
    # them in logs.  The short hash is sufficient to correlate a scan locally.
    log.info("Scan von Helper-Handle %s: %s", _token_handle(helper.token), barcode)

    student_id = helper.student_id
    if student_id is None:
        await hub.send_websocket(
            websocket,
            {
                "type": "scan_result",
                "barcode": barcode,
                "status": "error",
                "msg": "Kein Schüler zugewiesen",
            },
        )
        return

    # Scan verarbeiten: Buchungs-Vorabprüfung (im Lager? bestellt? Reihe
    # noch nicht ausgeliehen?) → buchen (Enter) oder — Gate aus — stagen.
    # Nicht erfüllt → Feld wird NICHT berührt.
    result = await process_scan(
        state,
        student_id,
        helper.vormerk_isbns,
        helper.lent_isbns,
        helper.lent_codes,
        barcode,
        source="helper",
    )
    # ISBN mitgeben, damit der Helferclient das gescannte Buch in seiner
    # Liste markieren kann.
    await hub.send_websocket(websocket, {"type": "scan_result", "barcode": barcode, **result})
    await hub.broadcast_host(state.state_snapshot())
    # Zuschauer (Spectator, s. spectate_student) bekommen denselben Scan
    # gespiegelt, damit ihre Bücherliste live mit dem aktiven Helfer
    # mitläuft — `spectator: True` unterdrückt clientseitig Statuszeile/
    # Alert-Modal (die bleiben „Warten bis Schüler frei…").
    spectators = state.student_spectators.get(student_id)
    if spectators:
        spectator_payload = {"type": "scan_result", "barcode": barcode, **result, "spectator": True}
        for waiter in list(spectators):
            await hub.send_scanner(waiter.token, spectator_payload)


_SCANNER_HANDLERS = {
    "next": _handle_next,
    "call": _handle_call,
    "search_classes": _handle_search_classes,
    "search_students": _handle_search_students,
    "search_call": _handle_search_call,
    "peek_queue": _handle_peek_queue,
    "peek_close": _handle_peek_close,
    "clear_book_alert": _handle_clear_book_alert,
    "print": _handle_print,
    "update_print_printers": _handle_update_print_printers,
    "print_for_student": _handle_print_for_student,
    "finish_signed": _handle_finish_signed,
    "scan": _handle_scan,
    "mark_empty_stock": _handle_mark_empty_stock,
    "clear_empty_stock": _handle_clear_empty_stock,
}


@router.websocket("/ws/scanner/{token}")
async def ws_scanner(websocket: WebSocket, token: str) -> None:
    state = get_state()
    hub = get_hub()

    if token not in state.helper_sessions:
        await websocket.close(code=4004, reason="Ungültiger Token")
        return

    await websocket.accept()
    helper = state.helper_sessions[token]
    # Erste WS-Verbindung dieses Helfers in DIESEM Serverlauf (auch bei einem
    # aus der Persistenz wiederhergestellten Helfer) → jetzt erst persistenz-
    # würdig (s. sessions.persist_helpers). Reconnects danach sind No-ops.
    if not helper.connected_since_start:
        helper.connected_since_start = True
        persist_helpers(state)
    # Frische Verbindung → Peek-Zustand ist clientseitig nicht mehr gesetzt.
    helper.peeking = False
    # Reconnect (Seite erneut geöffnet): einen noch laufenden Grace-Teardown-
    # Task des gerade getrennten alten WS abräumen (sonst würde er nach der
    # Frist den soeben neugeladenen Schüler doch noch abbrechen).
    t = helper.end_task
    helper.end_task = None
    if t is not None and not t.done():
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t
    await _take_over_ws(helper, websocket)

    # Schüler bereits zugewiesen? Info sofort schicken. Die Reihenfolge wird
    # anhand des Jahrgangs *dieses* Schülers ermittelt (nicht einer globalen
    # Kontext-Reihenfolge) — sonst würde die direkt danach folgende `settings`-
    # Nachricht sie bei klassenübergreifenden Warteschlangen wieder überschreiben.
    # Fallback (ohne Schüler): Reihenfolge des Helfer-Kontexts — `[]`, wenn der
    # Helfer keiner Klasse zugewiesen ist (kein stiller Rückfall auf eine
    # zufällig aktive fremde Klasse, s. `AppState.book_order_of`).
    book_order = state.book_order_of(helper.context_id)
    if helper.student_id is not None and state.iserv is not None:
        student = state.find_student(helper.student_id)
        # Form: aus dem QueueStudent, falls vorhanden (call/next); sonst aus
        # helper.student_form — der Lupe-Schüler (search_call) steht bewusst
        # NICHT in einer Queue, seine Form wurde darum beim Zuweisen am Helfer
        # hinterlegt. So wird auch der Lupe-Schüler beim Reconnect wiederher-
        # gestellt (inkl. Worker-Reload) statt als `waiting` zu verfallen.
        form = student.form if student is not None else (helper.student_form or "")
        try:
            info = await state.iserv.get_student_info(helper.student_id, state.selected_schoolyear)
            # reset_baseline=False: Reload derselben Verbindung soll die „seit
            # Aufrufen"-Baseline (loaned_at_load) NICHT neu setzen — die läuft
            # erst beim nächsten echten Aufrufen (assign_student_to_helper)
            # neu an. done_isbns wird trotzdem aus dem aktuellen IServ-Stand
            # aufgefrischt.
            info = await hydrate_student_info(
                state, info, form, helper, reset_baseline=False, is_helper=True
            )
            book_order = info["book_order"]
            helper.last_scan = None  # Worker-Page wird ggf. neu geladen → Feld leer
            # Modus A: Bücherliste sofort. Sends über das Hub-Lock
            # (send_websocket), damit sie nicht mit den Sends des In-Flight-
            # Lade-Tasks (send_scanner auf denselben neuen WS) interleaven.
            await hub.send_websocket(websocket, {"type": "student_info", "student": info})
            # Lädt der aktive Helfer seine Seite neu, soll sich die
            # Bücherliste auch bei allen Spectators dieses Schülers
            # aktualisieren (deren Ansicht läuft sonst mit einem veralteten
            # Stand weiter, bis der nächste Scan kommt).
            await broadcast_student_info_to_spectators(state, hub, helper.student_id, info)
            load_inflight = helper.load_task is not None and not helper.load_task.done()
            worker_session = state.student_worker_sessions.get(helper.student_id)
            worker_present = worker_session is not None
            if worker_present:
                # Worker war bereits bereit → Seite im Worker neu laden
                # (read-only GET-Reload auf dem bestehenden Context, kein
                # neuer Context). Identität danach re-checken: wurde der
                # Worker während des Reloads freigegeben (z. B. /api/skip),
                # KEIN `worker_ready` senden, sondern Fehler.
                ws_ref = worker_session
                reload_ok = False
                try:
                    await ws_ref.reload()
                    reload_ok = state.student_worker_sessions.get(helper.student_id) is ws_ref
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "Worker-Reload (Reconnect) für %d fehlgeschlagen: %s", helper.student_id, e
                    )
                    reload_ok = False
                if reload_ok:
                    await hub.send_websocket(websocket, {"type": "worker_ready"})
                else:
                    await hub.send_websocket(
                        websocket, {"type": "error", "msg": "Worker-Reload fehlgeschlagen"}
                    )
            elif load_inflight:
                # Worker wird gerade erst geöffnet (open_student läuft).
                # KEIN `worker_ready` senden — der In-Flight-Lade-Task
                # (`load_and_push_helper_student`) liefert ihn über
                # send_scanner(token) an den neuen WS. student_info steht
                # schon (oben gesendet), ggf. doppelt (vom In-Flight-Task) —
                # harmlos.
                pass
            else:
                # Degraded-Modus (kein worker_pool) oder Worker nie
                # bereit: sofort `worker_ready` senden.
                await hub.send_websocket(websocket, {"type": "worker_ready"})
        except Exception as e:
            await hub.send_websocket(websocket, {"type": "error", "msg": str(e)})
    elif helper.spectating_student_id is not None and state.iserv is not None:
        # Zuschauer (Spectator) lädt seine Seite neu: Warteposition bleibt
        # erhalten (die Disconnect-Behandlung entfernt ihn NICHT aus
        # `state.student_spectators` — er wartet also mit seiner bisherigen
        # Wartezeit weiter, statt sich hinten anzustellen). Nur die Ansicht
        # wiederherstellen, kein neuer Eintrag in der Warteliste.
        try:
            info = await state.iserv.get_student_info(
                helper.spectating_student_id, state.selected_schoolyear
            )
            info = await hydrate_student_info(
                state, info, helper.student_form or "", helper, reset_baseline=False, is_helper=True
            )
            book_order = info["book_order"]
            await hub.send_websocket(
                websocket, {"type": "student_info", "student": info, "spectator": True}
            )
        except Exception as e:
            await hub.send_websocket(websocket, {"type": "error", "msg": str(e)})
    else:
        await hub.send_websocket(
            websocket,
            {
                "type": "waiting",
                "msg": "Warte auf Schüler-Zuweisung",
                "queue_size": state.pending_count(helper.context_id),
                "queue": state.pending_queue_as_list(helper.context_id),
                "queue_all": state.queue_as_list(helper.context_id),
            },
        )

    # Host-Default „Schüler-Leihschein" (Druck-Dialog) + Bücher-Reihenfolge.
    await hub.send_websocket(
        websocket,
        {
            "type": "settings",
            "slip_second_page": state.settings.slip_second_page_default,
            "book_order": book_order,
            # Drucker-Pool + Vorauswahl für den Druck-Dialog (s. hub.broadcast_settings).
            "printers": pool_light(state),
            "print_default_ids": own_print_defaults(state, helper),
        },
    )

    # Kontext-Übersicht (alle offenen Klassen + eigene Klasse) schicken, damit
    # ein Idle-Helfer sofort die Klassen-Reiter der Warteschlangen-Ansicht hat.
    await hub.send_websocket(
        websocket,
        {
            "type": "contexts_update",
            "contexts": state.real_contexts_summary(),
            "own_context_id": helper.context_id,
        },
    )

    await hub.broadcast_host(state.state_snapshot())

    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                # Malformedes Frame (kein valides JSON) — nicht tödlich, Client
                # bleibt verbunden. Loggen und ignorieren, statt die Schleife
                # mit einem rohen Traceback sterben zu lassen.
                log.warning(
                    "Ungültiges JSON-Frame vom Scanner-WS (token_handle=%s) — ignoriert",
                    _token_handle(token),
                )
                continue
            # A reconnect may have replaced this socket while a frame was
            # buffered.  Never let the former owner execute it.
            if helper.ws is not websocket:
                break
            mtype = raw.get("type")

            handler = _SCANNER_HANDLERS.get(mtype)
            if handler is not None:
                await handler(state, hub, helper, websocket, raw)
            # Unbekannter/nicht behandelter Typ → ignorieren (wie bisher).

    except WebSocketDisconnect:
        pass
    finally:
        # WS-Referenz nur lösen, wenn keine neue Verbindung übernommen hat.
        # Abgesichert: tests/test_ws_scanner.py::test_finally_noop_when_ownership_stolen
        if helper.ws is websocket:
            helper.ws = None
            if helper.student_id is not None:
                # Eventuell noch laufenden Grace-Task der vorigen Trennung
                # abräumen (z. B. zweite Trennung während der Frist) — synchron
                # lesen+nullen, damit ein konkurrierender Reconnect nicht den
                # neu gesetzten Task überschreibt.
                t0 = helper.end_task
                helper.end_task = None
                if t0 is not None and not t0.done():
                    t0.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await t0
                helper.end_task = asyncio.create_task(
                    _deferred_end(state, hub, helper, helper.student_id)
                )
            # Absichtlich KEIN Aufräumen von `spectating_student_id`/
            # `student_spectators` hier: ein Zuschauer soll seine Warteposition
            # über einen Reconnect (Seiten-Reload) hinweg behalten (nicht
            # hinten anstellen). `pop_next_spectator` überspringt tote
            # Einträge (ws is None) bei der Beförderung ohnehin defensiv —
            # ein endgültig verlassener Spectator räumt sich so spätestens
            # bei seiner eigenen Beförderung selbst auf, ohne den Platz eines
            # echten Reconnects vorzeitig freizugeben.
        # else: Reconnect hat übernommen — nichts tun.
        await safe_broadcast(hub, state)


# ---------------------------------------------------------------------------
# Modus B — iPad-Display (nur QR, keine Schülerdaten)
# ---------------------------------------------------------------------------


@router.websocket("/ws/display")
async def ws_display(websocket: WebSocket) -> None:
    state = get_state()
    hub = get_hub()

    await websocket.accept()
    display = DisplaySession(
        display_id=uuid.uuid4().hex[:12], registration_code=gen_registration_code()
    )
    state.displays[display.display_id] = display
    display.ws = websocket
    await send_display_update(state, display)  # zeigt zunächst den Registrierungscode
    await hub.broadcast_host(state.state_snapshot())

    try:
        while True:
            # Display sendet nichts Inhaltliches; receive dient der Trennungserkennung.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.displays.pop(display.display_id, None)
        await safe_broadcast(hub, state)


# ---------------------------------------------------------------------------
# Drucker-Display (Warteschlangen-Anzeige neben den Druckern)
# ---------------------------------------------------------------------------


@router.websocket("/ws/drucker-display")
async def ws_drucker_display(websocket: WebSocket, token: str | None = None) -> None:
    """Drucker-Display (`/drucker-display`). Unauthentifiziert (öffentlich im
    LAN), zeigt aber vorab nur den Registrierungs-Code — Schülerdaten kommen
    erst nach Host-Freischaltung (Namen-Eingabe) + Drucker-Zuweisung. Push bei
    Druck-Übergängen (``print_queue._notify_all``) und Pool-Mutationen
    (``_after_pool_change``).

    Identität = Token in der URL (``?token=<12-hex>``), den die Seite beim Öffnen
    per Redirect zugewiesen bekommt. Ein Reload liefert denselben Token → dieselbe
    Session (gleicher Code, gleicher Freigabe-/Drucker-Stand) wird wiederverwen-
    det — für EINGESCHALTETE (autorisierte) Displays. NICHT eingeschaltete
    Displays werden beim Trennen der Verbindung ganz entfernt (s. ``finally``-
    Block), ein Reload legt sie also mit frischem Code neu an. Per × am Host
    verbotene Token erhalten eine ``forbidden``-Antwort und keine Session."""
    state = get_state()
    hub = get_hub()

    await websocket.accept()

    # Token-Validierung (12-stelliger Hex, wie display_id).
    if not token or len(token) != 12 or any(c not in "0123456789abcdef" for c in token.lower()):
        await websocket.close(code=1008, reason="Token fehlt/ungültig")
        return
    token = token.lower()

    # Verbotenes Display (× am Host) → sperren, keine Session.
    if token in state.banned_printer_display_tokens:
        await get_hub().send_websocket(websocket, {"type": "forbidden"})
        await websocket.close(code=4009, reason="Display verboten")
        return

    # Bestehende Session wiederverwenden (Reload) oder neu anlegen. Neue
    # Displays starten OHNE Drucker (assigned_printer_ids=[]); der Host weiht
    # sie explizit zu.
    display = state.printer_displays.get(token)
    if display is None:
        display = PrinterDisplaySession(
            display_id=token,
            registration_code=gen_registration_code(),
            assigned_printer_ids=[],
        )
        state.printer_displays[token] = display
    display.ws = websocket
    # Erste WS-Verbindung dieses Displays in DIESEM Serverlauf (auch bei einem
    # aus der Persistenz wiederhergestellten Display) → jetzt erst
    # persistenzwürdig (s. sessions.persist_printer_displays). Ein noch nicht
    # freigeschaltetes Display wird dadurch nicht gespeichert (Filter dort
    # verlangt zusätzlich `authorized`).
    if not display.connected_since_start:
        display.connected_since_start = True
        persist_printer_displays(state)
    await send_printer_display_update(state, display)  # Code bzw. Queue-Sicht
    # Ein (wieder) verbundenes Display kann vorher unsichtbare erlaubte
    # Drucker zurückbringen — Scheduler wecken, damit ein pausierter
    # Scan-Station-Druckermodus-Auftrag (`PrintJob.station_display_gate`)
    # sofort geclaimt werden kann, statt auf den nächsten Trigger zu warten.
    state.print_queue.wake()
    await hub.broadcast_host(state.state_snapshot())

    try:
        while True:
            # Display sendet nichts Inhaltliches; receive dient der Trennungserkennung.
            # Beim Navigieren/Schließen benachrichtigt die Seite den Server zusätzlich
            # aktiv via sendBeacon (s. /api/drucker-display/departed), weil der
            # Close-Frame bei Navigation unzuverlässig ankommt.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        # Beim Trennen die ws-Referenz lösen. Wurde das Display noch nicht
        # eingeschaltet (nicht autorisiert), wird die Session GANZ entfernt —
        # der Reiter verschwindet am Host. Das deckt zwei Fälle zusammen:
        #  (1) das Display wurde vor dem Einschalten geschlossen (Tab zu), und
        #  (2) das Display wurde durch einen erneuten /drucker-display-Aufruf
        #      abgelöst: die neue Seite erhält einen frischen Token (Redirect)
        #      und öffnet eine neue Session; die alte WS trennt dabei.
        # Eingeschaltete (autorisierte) Displays bleiben als getrennter Reiter
        # stehen (grauer Punkt, ``connected=False``); ein Reload mit demselben
        # Token verbindet sie wieder (grün). Nur lösen/entfernen, wenn zwischen-
        # zeitlich KEIN Reconnect denselben Token übernommen hat (``d.ws`` ist
        # dann ein anderer Socket als der gerade trennende).
        d = state.printer_displays.get(token)
        if d is not None and d.ws is websocket:
            d.ws = None
            if not d.authorized:
                state.printer_displays.pop(token, None)
        await safe_broadcast(hub, state)


# ---------------------------------------------------------------------------
# Drucker-Scanner (`/drucker-scan`)
# ---------------------------------------------------------------------------

# Scan-Ergebnis gilt 10s (s. PLAN-Absprache mit dem Nutzer) — dieselbe Frist,
# die die Scanner-Karte am Drucker-Display für ihre Rückfall-Animation nutzt
# UND die der Drucker-Scanner selbst nutzt, um die Kamera währenddessen zu
# pausieren (s. web/drucker-scan.js).
_SCANNER_RESULT_TTL_S = 10.0


@router.websocket("/ws/drucker-scan")
async def ws_drucker_scan(websocket: WebSocket, token: str | None = None) -> None:
    """Drucker-Scanner (`/drucker-scan`). Pairing-Lebenszyklus identisch zu
    `ws_drucker_display`. Empfängt `{"type": "scan", "code": "..."}` (Kamera
    und manuelle Eingabe münden client-seitig in dieselbe Nachricht) und wertet
    den Code aus (s. `_handle_drucker_scan`) — das Ergebnis geht NICHT an den
    Scanner selbst zurück, sondern an die Drucker-Display(s), denen er
    zugeordnet ist (s. `broadcast_scanner_result`)."""
    state = get_state()
    hub = get_hub()

    await websocket.accept()

    if not token or len(token) != 12 or any(c not in "0123456789abcdef" for c in token.lower()):
        await websocket.close(code=1008, reason="Token fehlt/ungültig")
        return
    token = token.lower()

    if token in state.banned_printer_scanner_tokens:
        await hub.send_websocket(websocket, {"type": "forbidden"})
        await websocket.close(code=4009, reason="Scanner verboten")
        return

    scanner = state.printer_scanners.get(token)
    if scanner is None:
        scanner = PrinterScannerSession(scanner_id=token, registration_code=gen_registration_code())
        state.printer_scanners[token] = scanner
    scanner.ws = websocket
    if not scanner.connected_since_start:
        scanner.connected_since_start = True
        persist_printer_scanners(state)
    await send_printer_scanner_update(state, scanner)
    await hub.broadcast_host(state.state_snapshot())

    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except (WebSocketDisconnect, RuntimeError):
                break
            except json.JSONDecodeError:
                log.warning(
                    "Ungültiges JSON-Frame von Drucker-Scanner (%s) — ignoriert", token[:6]
                )
                continue
            if state.printer_scanners.get(token) is not scanner or scanner.ws is not websocket:
                break
            if raw.get("type") != "scan" or not scanner.authorized:
                continue
            code = str(raw.get("code", "")).strip().strip("*")
            if not code:
                continue
            await _handle_drucker_scan(state, hub, scanner, code)
    except WebSocketDisconnect:
        pass
    finally:
        s = state.printer_scanners.get(token)
        if s is not None and s.ws is websocket:
            s.ws = None
            if not s.authorized:
                state.printer_scanners.pop(token, None)
        await safe_broadcast(hub, state)


async def _handle_drucker_scan(state, hub, scanner: PrinterScannerSession, code: str) -> None:
    """Einen am Drucker-Scanner gescannten Code auswerten und klassifizieren.

    Reihenfolge (s. Absprache mit dem Nutzer):
      1. Code auflösen — erst Zettel-Code (`student_id_for_station_code`),
         sonst Buchcode (`get_book_by_code`, read-only) → `loaned_to_id`, falls
         dieser selbst einen Zettel-Code hat.
      2. Unaufgelöst / Schüler unbekannt / Status „fertig" → "unknown".
      3. Bereits ein Druckauftrag (wartet/druckt) oder Leihschein schon gedruckt
         → "already" (Sub-Status im Payload).
      4. Noch offene vorgemerkte Bücher (ohne ausgeblendete/Bestand-leer-Reihen)
         → "pending_books".
      5. Sonst → "ready": Druckauftrag anlegen (Mirror Auto-Pfad in
         `print_mode`-Handler oben), `station_display_gate=True`.

    Setzt das Ergebnis (5s TTL) auf der Scanner-Session und pusht es an die
    zugeordneten Drucker-Display(s) — der Scanner selbst bleibt stumm."""
    scanner.last_scan_status = "checking"
    scanner.last_scan_code = code
    scanner.last_scan_payload = None
    scanner.last_scan_expires_at = datetime.now() + timedelta(seconds=_SCANNER_RESULT_TTL_S)
    await broadcast_scanner_result(state, scanner)

    student_id = state.student_id_for_station_code(code)
    if student_id is None and state.iserv is not None:
        try:
            book = await state.iserv.get_book_by_code(code)
        except Exception:  # noqa: BLE001 — Lookup darf den Scanner nicht stören
            book = None
        if book is not None:
            loaned_to_id = book.get("loaned_to_id")
            if loaned_to_id is not None and state.slip_codes.has_active_code(loaned_to_id):
                student_id = loaned_to_id

    qs = state.find_student(student_id) if student_id is not None else None
    status: str
    payload: dict | None

    if qs is None or qs.status == "done":
        status, payload = "unknown", None
    else:
        # Klassen-Präfix „Klasse " abschneiden — wie `slip_name` es für die
        # Druckerwarteschlange schon tut (IServ liefert teils „Klasse 5a",
        # gezeigt wird nur „5a"); `qs.form` selbst bleibt unverändert.
        form_clean = (qs.form or "").removeprefix("Klasse ").strip()
        job_states = state.print_queue.print_job_states()
        job_status = job_states.get(student_id)
        if job_status is not None or qs.slip_printed:
            status = "already"
            payload = {
                "form": form_clean,
                "lastname": qs.lastname,
                "firstname": qs.firstname,
                "job_status": job_status or "printed",
                # Für den Doppel-Scan-Hinweis am Drucker-Display: das schon
                # bestehende Kästchen in Warteschlange/Drucker-Karte einmalig
                # gelb umranden (Mirror des grünen „fertig"-Blitzers), statt
                # ein zweites Kästchen zu zeigen. `None`, wenn der Auftrag
                # bereits fertig gedruckt UND aus der Warteschlange
                # verschwunden ist (kein Kästchen mehr zum Markieren).
                "job_id": state.print_queue.active_job_id_for_student(student_id),
            }
        else:
            vormerk = await pending_vormerk_isbns_for(state, student_id)
            if vormerk is None or vormerk:
                status = "pending_books"
                payload = {"form": form_clean, "lastname": qs.lastname, "firstname": qs.firstname}
            else:
                allowed = allowed_printers_for(state, student_id)
                done_signed, done_collected = slip_signature_options_for(state, student_id)
                if not qs.print_mode:
                    qs.print_mode = True
                name = _slip_name(qs.lastname, qs.firstname, qs.form)
                pages = None if state.settings.slip_second_page_default else "1"
                job = PrintJob.create(
                    role="student",
                    student_id=student_id,
                    pages=pages,
                    name=name,
                    allowed_printers=allowed,
                    station_display_gate=True,
                )
                await state.print_queue.enqueue(job)
                await hub.broadcast_host(state.state_snapshot())
                status = "ready"
                payload = {
                    "form": form_clean,
                    "lastname": qs.lastname,
                    "firstname": qs.firstname,
                    "done_signed": done_signed,
                    "recipient": "teacher" if done_collected else "helper",
                    # Für den Drucker-Display-Client: das Namens-Kästchen
                    # „reist" nach Ablauf der Anzeigezeit von der Scanner-
                    # Karte an die echte Position dieses Auftrags in der
                    # Warteschlange/auf einem Drucker (derselbe FLIP-
                    # Mechanismus, kein zweites Kästchen).
                    "job_id": job.id,
                }

    scanner.last_scan_status = status
    scanner.last_scan_payload = payload
    scanner.last_scan_expires_at = datetime.now() + timedelta(seconds=_SCANNER_RESULT_TTL_S)
    await broadcast_scanner_result(state, scanner)


# ---------------------------------------------------------------------------
# Scan-Station (`/scan-station`)
# ---------------------------------------------------------------------------

# Drosselung der Zettel-Code-Versuche je Station: 10 pro Minute. Vier Stellen
# sind 10 000 Möglichkeiten — ohne Deckel wäre ein Durchprobieren an einer
# freigeschalteten Station denkbar. Zehn Versuche pro Minute reichen für
# Vertipper bequem aus und machen das Raten unpraktikabel.
_station_code_limiter = SlidingWindowLimiter(max_hits=10, window_s=60.0)


@router.websocket("/ws/scan-station")
async def ws_scan_station(websocket: WebSocket, token: str | None = None) -> None:
    """Scan-Station (`/scan-station`) — festes Scan-Gerät für Schüler ohne Handy.

    Unauthentifiziert wie das Drucker-Display (öffentlich im LAN), zeigt aber
    vorab nur den Registrierungs-Code. Erst nach der Host-Freischaltung nimmt
    die Station Zettel-Codes an; erst nach einem gültigen Code fließen
    Schülerdaten.

    Identität = Token in der URL (``?token=<12-hex>``), den die Seite beim
    Öffnen per Redirect bekommt. Ein Reload liefert denselben Token → dieselbe
    (freigeschaltete) Station. Ein noch angemeldeter Schüler wird beim
    Verbindungsaufbau bewusst freigegeben: Ein Reload ist ein Neustart des
    Geräts, danach steht die Station wieder für den Nächsten bereit.
    """
    state = get_state()
    hub = get_hub()

    await websocket.accept()

    if not token or len(token) != 12 or any(c not in "0123456789abcdef" for c in token.lower()):
        await websocket.close(code=1008, reason="Token fehlt/ungültig")
        return
    token = token.lower()

    if token in state.banned_scan_station_tokens:
        await hub.send_websocket(websocket, {"type": "forbidden"})
        await websocket.close(code=4009, reason="Station verboten")
        return

    station = state.scan_stations.get(token)
    if station is None:
        station = ScanStationSession(
            station_id=token, registration_code=gen_registration_code()
        )
        state.scan_stations[token] = station
    else:
        # Reload/Reconnect: eine noch offene Schüler-Bindung gehört nicht zur
        # neuen Sitzung — freigeben, damit kein fremder Worker-Context und
        # keine fremden Daten an der Station hängen bleiben.
        await release_station_student(state, station, reason="station reconnected")
    station.ws = websocket
    # Erste WS-Verbindung dieser Station in DIESEM Serverlauf (auch bei einer
    # aus der Persistenz wiederhergestellten Station) → jetzt erst
    # persistenzwürdig (s. sessions.persist_scan_stations).
    if not station.connected_since_start:
        station.connected_since_start = True
        persist_scan_stations(state)
    await send_scan_station_update(state, station)
    await hub.broadcast_host(state.state_snapshot())

    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except (WebSocketDisconnect, RuntimeError):
                break
            except json.JSONDecodeError:
                log.warning(
                    "Ungültiges JSON-Frame von Scan-Station (%s) — ignoriert", token[:6]
                )
                continue
            # Besitzprüfung wie im Schüler-WS: ein nach Reconnect/Verbot noch
            # gepuffertes Frame des alten Sockets darf nichts mehr auslösen.
            if state.scan_stations.get(token) is not station or station.ws is not websocket:
                break
            mtype = raw.get("type")

            if mtype == "student_code":
                if not station.authorized:
                    await hub.send_websocket(
                        websocket,
                        {"type": "code_error", "msg": "Station noch nicht freigeschaltet."},
                    )
                    continue
                if station.student_id is not None:
                    continue  # schon belegt — erst freigeben
                code = str(raw.get("value", "")).strip().strip("*")
                if not code:
                    continue
                if not _station_code_limiter.hit(token):
                    await hub.send_websocket(
                        websocket,
                        {"type": "code_error", "msg": "Zu viele Versuche — bitte kurz warten."},
                    )
                    continue
                if state.student_id_for_station_code(code) is None:
                    # Der Wert passt zu KEINEM aktuell vergebenen Zettel-Code
                    # (Client sendet inzwischen jeden gescannten Wert hierher,
                    # nicht mehr nur 4-stellige — s. scan-station.js). Bevor
                    # pauschal "ungültig" gemeldet wird: rein lesend prüfen,
                    # ob es sich überhaupt um einen Buch-Barcode handelt (GET
                    # /books/{code}, PLAN §6 read-only) — egal welches Buch,
                    # nur ob IServ den Code kennt. Dann gezielt "erst
                    # Schülercode scannen" statt "ungültig". Codes NIE loggen
                    # (auch nicht abgelehnte) — sie sind der Zugangs-
                    # Credential des Zettels (PLAN §3.7).
                    book = None
                    if state.iserv is not None:
                        try:
                            book = await state.iserv.get_book_by_code(code)
                        except Exception:  # noqa: BLE001 — Lookup darf die Station nicht stören
                            book = None
                    if book is not None:
                        await hub.send_websocket(
                            websocket,
                            {
                                "type": "code_error",
                                "msg": "Bitte zunächst Schülercode scannen",
                                "kind": "book",
                            },
                        )
                    else:
                        await hub.send_websocket(
                            websocket,
                            {"type": "code_error", "msg": "Ungültiger Code", "kind": "invalid"},
                        )
                    continue
                student, reason = resolve_station_code(state, code)
                if student is None:
                    # Codes NIE loggen (auch nicht abgelehnte) — sie sind der
                    # Zugangs-Credential des Zettels (PLAN §3.7).
                    await hub.send_websocket(
                        websocket, {"type": "code_error", "msg": reason}
                    )
                    continue
                station.student_id = student.student_id
                station.student_lastname = student.lastname
                station.student_firstname = student.firstname
                station.student_form = student.form
                station.worker_ready = False
                station.last_activity = datetime.now()
                station.load_task = asyncio.create_task(
                    load_station_student(state, hub, station, student)
                )
                await hub.broadcast_host(state.state_snapshot())

            elif mtype == "scan":
                barcode = str(raw.get("value", "")).strip()
                if not barcode:
                    continue
                station.last_activity = datetime.now()
                if station.student_id is None or not station.worker_ready:
                    await hub.send_websocket(
                        websocket,
                        {
                            "type": "scan_result",
                            "barcode": barcode,
                            "status": "error",
                            "msg": "Noch nicht bereit",
                        },
                    )
                    continue
                if station.book_alert_open:
                    # Blockierendes Hinweis-Modal offen (ausgemustert/anderweitig
                    # verliehen) — wie am Handy erst der Host per
                    # `/api/clear-book-alert` freigeben lassen. Scan ignorieren,
                    # kein eigener Schließen-Weg am Client.
                    continue

                # Zunächst prüfen, ob der gescannte Wert eigentlich der
                # Zettel-Code EINES ANDEREN Schülers ist — dann Stationswechsel
                # statt Buch-Scan-Versuch (ein Schüler scannt an einer gerade
                # belegten Station seinen eigenen Zettel). Der Treffer-Check
                # selbst läuft UNABHÄNGIG von Länge/Ziffernform (ein einfacher
                # Dict-Lookup — billig, und ein Buch-Barcode trifft praktisch
                # nie zufällig einen der wenigen vergebenen 4-stelligen Codes),
                # damit ein echter Treffer nie an einer zu strengen Formannahme
                # über den vom Scanner gelieferten Wert scheitert. Das
                # Rate-Limit bleibt an die 4-stellige Form gebunden (Buch-
                # Barcodes sind länger, s. Code.PNG) — es greift für JEDEN
                # 4-stelligen Versuch (Treffer wie Fehlversuch), sonst liefe
                # ein Durchprobieren fremder Codes während einer laufenden
                # Sitzung am Limiter vorbei.
                stripped = barcode.strip("*")
                code_shaped = len(stripped) == 4 and stripped.isdigit()
                if code_shaped and not _station_code_limiter.hit(token):
                    await hub.send_websocket(
                        websocket,
                        {"type": "code_error", "msg": "Zu viele Versuche — bitte kurz warten."},
                    )
                    continue
                other_id = state.student_id_for_station_code(stripped)
                if other_id is not None and other_id != station.student_id:
                    other_student, reason = resolve_station_code(state, stripped)
                    if other_student is None:
                        # Wechsel abgelehnt (z. B. Zielschüler inzwischen
                        # fertig) — der aktuell angemeldete Schüler bleibt
                        # unangetastet, nur die Fehlermeldung wird gezeigt.
                        await hub.send_websocket(
                            websocket, {"type": "code_error", "msg": reason}
                        )
                        continue
                    await release_station_student(state, station, reason="switched")
                    station.student_id = other_student.student_id
                    station.student_lastname = other_student.lastname
                    station.student_firstname = other_student.firstname
                    station.student_form = other_student.form
                    station.worker_ready = False
                    station.last_activity = datetime.now()
                    station.load_task = asyncio.create_task(
                        load_station_student(state, hub, station, other_student)
                    )
                    await hub.broadcast_host(state.state_snapshot())
                    continue
                # Kein bekannter FREMDER Code (eigener Code erneut oder
                # unbekannt) → normal als Buch-Barcode weiterprüfen (landet
                # unten ggf. bei `unknown_book`).

                station.last_scan = barcode
                # Identische Verarbeitung wie am Handy (Modus B): Vorabprüfung,
                # dann stagen bzw. — nur mit ALLOW_BOOKING — buchen.
                result = await process_scan(
                    state,
                    station.student_id,
                    station.vormerk_isbns,
                    station.lent_isbns,
                    station.lent_codes,
                    barcode,
                )
                payload = {"type": "scan_result", "barcode": barcode, **result}
                # Ausgemustert ODER an jemand anderen verliehen → blockierendes
                # Hinweis-Modal wie am Handy, nur der Host gibt per
                # `/api/clear-book-alert` frei (kein eigener Schließen-Weg).
                if result.get("status") in ("book_deleted", "not_in_stock"):
                    station.book_alert_open = True
                await hub.send_websocket(websocket, payload)
                await hub.broadcast_host(state.state_snapshot())

            elif mtype == "print_mode":
                # Druckermodus an der Scan-Station: alle vorgemerkten Bücher
                # des angemeldeten Schülers sind erledigt (Client-seitig via
                # `maybeEnterDruckmodus()` erkannt, analog `ws_student`s
                # `print_mode`/`print_request`). „Automatisch" druckt sofort
                # (unten). „Selbstauslöser" (`trigger == "student"`) zeigt
                # stattdessen — sofern mindestens ein Drucker-Scanner
                # erreichbar ist (s. `eligible_drucker_scanners_for`) — den
                # Hinweis, dort den Schülercode zu scannen; der Druckauftrag
                # entsteht erst dort (s. `routes/ws.py::ws_drucker_scan`).
                # Ohne erreichbaren Scanner fällt „Selbstauslöser" auf das
                # Auto-Verhalten zurück (derselbe Code-Pfad unten). Die
                # Station meldet sich nach der Antwort unconditional nach 15 s
                # ab (Client-Timer); es gibt daher keinen laufenden
                # Progress-Kanal zur Station — die Sichtbarkeit danach läuft
                # ausschließlich über den Host (`PrintQueue.
                # station_gate_snapshot`, state_snapshot).
                if station.student_id is None or not station.worker_ready:
                    continue
                if release_student_worker(state, station.student_id):
                    await hub.broadcast_host(state.state_snapshot())
                qs = state.find_student(station.student_id)
                if qs is not None and not qs.print_mode:
                    qs.print_mode = True
                    await hub.broadcast_host(state.state_snapshot())
                    if state.helper_sessions:
                        await hub.broadcast_queue_size(state)

                trigger = slip_trigger_for(state, station.student_id)
                # "Leihschein unterschreiben" ist eine Klassenoption
                # (ClassContext.done_signed/done_collected), unabhängig vom
                # `slip_trigger` — die Station hängt den Hinweis an JEDEN
                # Statustext an (außer beim Barcode-Platzhalter, s. unten),
                # da so oder so irgendwann physisch gedruckt wird, egal wer
                # den Auftrag letztlich auslöst.
                done_signed, done_collected = slip_signature_options_for(
                    state, station.student_id
                )
                signing_fields = {
                    "done_signed": done_signed,
                    "recipient": "teacher" if done_collected else "helper",
                }
                if trigger not in ("auto", "student"):
                    # "helper" → der Host-/Helfer-Druckbutton wird durch
                    # `qs.print_mode` oben sichtbar (studentClientPrint,
                    # host-render.js) — die Station zeigt nur den Hinweis.
                    # "barcode" → Platzhalter, kein Verhalten.
                    await hub.send_websocket(
                        websocket,
                        {"type": "print_mode_result", "trigger": trigger, **signing_fields},
                    )
                    continue

                if trigger == "student":
                    eligible = eligible_drucker_scanners_for(state, station.student_id)
                    if eligible:
                        # Kein Auftrag jetzt — der entsteht erst beim Scan am
                        # Drucker-Scanner (s. ws_drucker_scan unten).
                        await hub.send_websocket(
                            websocket,
                            {
                                "type": "print_mode_result",
                                "trigger": trigger,
                                "printer_available": True,
                                "scanner_names": [
                                    (e["label"] or e["scanner_id"][:6]) for e in eligible
                                ],
                                **signing_fields,
                            },
                        )
                        continue
                    # Kein erreichbarer Scanner → wie "auto" verfahren
                    # (derselbe Code-Pfad unten, inkl. `station_print_needs_host`
                    # als Fallback, falls auch kein Drucker sichtbar ist).

                # Verfügbarkeit VOR dem Erzeugen eines Auftrags prüfen: ist zu
                # Beginn schon kein erlaubter Drucker auf einem Display
                # sichtbar, wird bewusst KEIN Druckauftrag angelegt (kein
                # "kein erlaubter Drucker verfügbar"-Hinweis am Host für
                # diesen Fall — der gelbe Hinweis samt Übernahme-Menü ist nur
                # für einen bereits WARTENDEN Auftrag gedacht, s. `PrintQueue.
                # station_gate_snapshot`/`web/host-render.js`). Die Station
                # zeigt stattdessen "bitte beim Host melden"; der Host druckt
                # ganz normal über den Druckbutton in "Aktuell in Ausgabe"
                # (`station_print_needs_host` schaltet ihn frei, unabhängig
                # vom Klassen-`slip_trigger`).
                allowed = allowed_printers_for(state, station.student_id)
                pool_ids = {p.id for p in state.settings.printers}
                printer_available = bool(state.settings.printers) and (
                    allowed is None or bool(allowed & pool_ids)
                )
                if printer_available:
                    shown = displayed_printer_ids(state)
                    printer_available = bool(shown if allowed is None else (allowed & shown))

                if not printer_available:
                    if qs is not None:
                        qs.station_print_needs_host = True
                        await hub.broadcast_host(state.state_snapshot())
                    await hub.send_websocket(
                        websocket,
                        {
                            "type": "print_mode_result",
                            "trigger": trigger,
                            "printer_available": False,
                            **signing_fields,
                        },
                    )
                    continue

                if qs is not None:
                    qs.station_print_needs_host = False
                    name = _slip_name(qs.lastname, qs.firstname, qs.form)
                else:
                    name = _slip_name(
                        station.student_lastname, station.student_firstname,
                        station.student_form,
                    )
                pages = None if state.settings.slip_second_page_default else "1"
                job = PrintJob.create(
                    role="student",
                    student_id=station.student_id,
                    pages=pages,
                    name=name,
                    allowed_printers=allowed,
                    station_display_gate=True,
                )
                await state.print_queue.enqueue(job)
                await hub.broadcast_host(state.state_snapshot())
                # Singular/Plural für den Stationstext ("Bitte achte auf die
                # Druckeranzeige(n)."): wie viele Displays zeigen gerade
                # mindestens einen für diesen Auftrag erlaubten Drucker?
                display_count = relevant_display_count(state, allowed)
                await hub.send_websocket(
                    websocket,
                    {
                        "type": "print_mode_result",
                        "trigger": trigger,
                        "printer_available": True,
                        "display_count": display_count,
                        **signing_fields,
                    },
                )

            elif mtype == "release":
                # „Fertig"-Knopf bzw. abgelaufener Client-Timer.
                if await release_station_student(state, station, reason="station"):
                    await hub.broadcast_host(state.state_snapshot())

            elif mtype == "ping":
                # Aktivitäts-Signal der Stationsseite (Tippen/Scannen) — hält
                # das 30-s-TTL offen, ohne selbst etwas auszulösen.
                station.last_activity = datetime.now()
    except WebSocketDisconnect:
        pass
    finally:
        # Spiegel des Drucker-Display-`finally`: ws-Referenz lösen, nicht
        # freigeschaltete Stationen ganz entfernen (Reiter verschwindet am
        # Host). Ein angemeldeter Schüler wird freigegeben, damit sein
        # Worker-Context nicht an der toten Station hängen bleibt.
        st = state.scan_stations.get(token)
        if st is not None and st.ws is websocket:
            # ws-Referenz ZUERST lösen: die Verbindung ist bereits weg, und
            # `release_station_student` würde sonst versuchen, `released`/`ready`
            # auf den toten Socket zu schicken. Ohne Empfänger ist das im besten
            # Fall verschwendet und im schlechtesten ein Hänger im ASGI-Layer,
            # der das Aufräumen (Schüler + Worker!) verzögert.
            st.ws = None
            await release_station_student(state, st, reason="station disconnected")
            if not st.authorized:
                state.scan_stations.pop(token, None)
        await safe_broadcast(hub, state)


# ---------------------------------------------------------------------------
# Lehrkraft-Statusansicht (`/teacher`)
# ---------------------------------------------------------------------------


@router.websocket("/ws/teacher")
async def ws_teacher(websocket: WebSocket, token: str | None = None) -> None:
    """Lehrkraft-Statusansicht einer einzelnen Modus-B-Klasse. Unauthentifiziert
    im üblichen Sinn (kein Host-Cookie) — der lange, zufällige Token IST der
    Zugangs-Credential (analog `/ws/student/{session_token}`). Vor der
    Host-Freischaltung liefert die Verbindung nur den Registrierungscode
    (`send_teacher_update`), danach den klassenscharften `teacher_state`.
    Ein unbekannter Token (nie gemintet, oder bereits entwertet — neuer QR,
    explizites Trennen, Klassen-Schließen/Schuljahreswechsel) wird sofort mit
    `forbidden` abgewiesen; ein Reload kann den Zugang dann nicht mehr
    wiederherstellen (PLAN-Abnahmekriterium)."""
    state = get_state()
    hub = get_hub()

    await websocket.accept()

    if not token or len(token) < 32:
        await websocket.close(code=1008, reason="Token fehlt/ungültig")
        return

    session = state.teacher_sessions.get(token)
    if session is None:
        await hub.send_websocket(websocket, {"type": "forbidden"})
        await websocket.close(code=4009, reason="Unbekannter oder entwerteter Token")
        return

    await _take_over_ws(session, websocket)
    await send_teacher_update(state, session)
    # Der Host muss den Verbindungswechsel sofort sehen, damit ein dort
    # geöffnetes Lehrer-QR-Modal nach dem Scan schließen kann (analog zu
    # `/ws/display` und `/ws/drucker-display`).
    await hub.broadcast_host(state.state_snapshot())

    try:
        while True:
            # Die Lehrkraft-Ansicht sendet nichts Inhaltliches über die WS
            # (Statuswechsel laufen über /api/teacher/skip|undo-skip); receive
            # dient nur der Trennungserkennung.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        # Session bleibt bestehen (Reconnect mit demselben Token funktioniert
        # weiter, solange sie nicht entwertet wurde) — nur die ws-Referenz
        # lösen, und auch nur, wenn zwischenzeitlich kein Reconnect denselben
        # Token übernommen hat (Spiegel des Drucker-Display-Musters).
        current = state.teacher_sessions.get(token)
        if current is not None and current.ws is websocket:
            current.ws = None
            await safe_broadcast(hub, state)


# ---------------------------------------------------------------------------
# Modus B — Schüler-Session (Pairing-Gate + Scan)
# ---------------------------------------------------------------------------


@router.websocket("/ws/student/{session_token}")
async def ws_student(websocket: WebSocket, session_token: str) -> None:
    state = get_state()
    hub = get_hub()

    session = state.student_sessions.get(session_token)
    if not session or session.state not in ("pending_pairing", "paired"):
        # Ungültiger/entwerteter Token → neutrale "Vorgang abgeschlossen"-Seite.
        # accept() VOR close(), damit der Browser den 4006-Code zuverlässig
        # erhält (sonst sieht er nur 1006 und kann Token-Tod nicht erkennen).
        await websocket.accept()
        await websocket.close(code=4006, reason="Session ungültig")
        return

    await websocket.accept()
    # Reconnect: vorherige Verbindung derselben Session sauber schließen.
    await _take_over_ws(session, websocket)
    session.last_activity = datetime.now()
    # Verbunden → der Idle-Sweeper lässt die Session in Ruhe (s. state.py::
    # StudentSessionB.disconnected_at).
    session.disconnected_at = None

    if session.state == "pending_pairing":
        await hub.send_websocket(
            websocket, {"type": "pending", "pairing_code": session.pairing_code}
        )
    elif session.state == "paired" and session.student_id is not None:
        # Reconnect nach Pairing: Identität (ohne Bücher) erneut senden; die
        # Bücherliste kommt mit `worker_ready` — sofort, wenn der Worker bereits
        # steht, sonst liefert sie der noch laufende Lade-Task.
        try:
            info = await state.iserv.get_student_info(session.student_id, state.selected_schoolyear)
            qs = state.find_student(session.student_id)
            # reset_baseline=False: Reload derselben Verbindung — die „seit
            # Aufrufen"-Baseline (loaned_at_load) bleibt stehen, s. ws_scanner.
            info = await hydrate_student_info(
                state, info, qs.form if qs else "", session, reset_baseline=False, is_helper=False
            )
            books = info.get("books", [])
            await hub.send_websocket(
                websocket,
                {
                    "type": "student_info",
                    "student": {**info, "books": []},
                    "payment_overridden": session.payment_overridden,
                },
            )
            load_inflight = session.load_task is not None and not session.load_task.done()
            worker_present = state.student_worker_sessions.get(session.student_id) is not None
            if not load_inflight or worker_present:
                await hub.send_websocket(
                    websocket,
                    {
                        "type": "worker_ready",
                        "books": books,
                        "slip_trigger": slip_trigger_for(state, session.student_id),
                        "slip_mode": session.loan_slip_mode,
                        "slip_recipient": session.loan_slip_recipient,
                        # Gedruckt, aber noch nicht bestätigt (Reload
                        # zwischen Druckende und „Leihschein erhalten") — der
                        # Client soll den Druckmodus mit sichtbarem Button
                        # fortsetzen, nicht erneut drucken.
                        "slip_printed": bool(qs and qs.slip_printed),
                        "slip_printer": qs.slip_printer if qs else None,
                        "slip_printer_label": qs.slip_printer_label if qs else None,
                    },
                )
            # Blockierendes Ausgemustert-Hinweis-Modal überlebt einen Reconnect
            # (z. B. Seiten-Reload) — erst der Host darf es per Button schließen.
            if session.book_alert_open and session.book_alert_payload:
                await hub.send_websocket(websocket, session.book_alert_payload)
        except Exception as e:
            await hub.send_websocket(websocket, {"type": "error", "msg": str(e)})

    await hub.broadcast_host(state.state_snapshot())

    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except (WebSocketDisconnect, RuntimeError):
                # WebSocketDisconnect: Client hat die Verbindung getrennt.
                # RuntimeError: der Server hat die Verbindung von einer anderen
                # Coroutine aus geschlossen — invalidate_session → ws.close()
                # setzt den ASGI-State auf DISCONNECTED, worauf receive_json()
                # „WebSocket is not connected" (statt WebSocketDisconnect) wirft.
                # Regulärer Pfad für Modus-B-Dismiss / Timeout-Sweeper /
                # Modus-B-Close einer verbundenen pending-Session: sauber
                # beenden statt eines ASGI-Tracebacks. s. sessions.invalidate_session.
                break
            except json.JSONDecodeError:
                # Malformedes Frame — loggen und weiterlauschen, statt die
                # Schleife mit Traceback sterben zu lassen.
                log.warning(
                    "Ungültiges JSON-Frame vom Schüler-WS (session_handle=%s) — ignoriert",
                    _token_handle(session_token),
                )
                continue
            # The old socket can still yield an already buffered frame after a
            # reconnect or invalidation.  Ownership is checked before any
            # command, especially scan/finish.
            if (
                state.student_sessions.get(session_token) is not session
                or session.ws is not websocket
            ):
                break
            session.last_activity = datetime.now()
            mtype = raw.get("type")

            if mtype == "scan":
                barcode = str(raw.get("value", "")).strip()
                if not barcode:
                    continue
                if session.state != "paired" or session.student_id is None:
                    await hub.send_websocket(
                        websocket,
                        {
                            "type": "scan_result",
                            "barcode": barcode,
                            "status": "error",
                            "msg": "Noch nicht freigegeben",
                        },
                    )
                    continue
                if session.book_alert_open:
                    # Blockierendes Hinweis-Modal (ausgemustertes Buch) noch offen —
                    # erst der Host darf per Button freigeben. Barcode ignorieren.
                    continue
                session.last_scan = barcode
                # Scan verarbeiten: Buchungs-Vorabprüfung → buchen (Enter) oder
                # — Gate aus — stagen. Nicht erfüllt → Feld wird NICHT berührt.
                result = await process_scan(
                    state,
                    session.student_id,
                    session.vormerk_isbns,
                    session.lent_isbns,
                    session.lent_codes,
                    barcode,
                )
                payload = {"type": "scan_result", "barcode": barcode, **result}
                # Ausgemustert ODER an jemand anderen verliehen → blockierendes
                # Hinweis-Modal am Schüler-Client (kein eigener Schließen-Button,
                # Host gibt per /api/clear-book-alert frei). „An sich selbst
                # verliehen" (book_already_lent/series_already_lent) ist nur ein
                # Hinweis und nicht blockierend — der Schüler schließt ihn selbst.
                if result.get("status") in ("book_deleted", "not_in_stock"):
                    session.book_alert_open = True
                    session.book_alert_payload = payload
                await hub.send_websocket(websocket, payload)
                await hub.broadcast_host(state.state_snapshot())

            elif mtype == "print_mode":
                # Sobald alle Bücher erledigt sind, braucht der Schülerclient
                # die Playwright-Kartei nicht mehr. Die Modus-B-Session und der
                # WebSocket bleiben für den Druckstatus bewusst bestehen.
                if session.state == "paired" and session.student_id is not None:
                    released = release_student_worker(state, session.student_id)
                    qs = state.find_student(session.student_id)
                    # Betreuerauslöser: der Helfer-Client (scan.html) zeigt ab
                    # hier in der Klassenliste den Druckbutton für diesen
                    # Schüler (s. real_contexts_summary). Nur beim ERSTEN
                    # Eintritt broadcasten, damit ein doppelt gesendetes
                    # `print_mode`-Frame (Reconnect) keinen unnötigen
                    # Zusatz-Broadcast auslöst.
                    entered_print_mode = qs is not None and not qs.print_mode
                    if qs is not None:
                        qs.print_mode = True
                    if released:
                        await hub.broadcast_host(state.state_snapshot())
                    if entered_print_mode and state.helper_sessions:
                        await hub.broadcast_queue_size(state)

            elif mtype == "print_request":
                # Druckmodus am Schülerclient (Modus B): Schüler hat alle
                # vorgemerkten Bücher gescannt und löst den Leihschein-Druck
                # aus („Automatisch" sendet sofort, „Schülerauslöser" per
                # Button). Enqueue über die Druckerwarteschlange mit der
                # Klassen-Druck-Allowlist; Progress/Result kommen via
                # `print_progress`/`print_result` zurück (Routing via
                # `student_token`). Read-only PDF-Abruf + lokaler Druck, kein
                # IServ-Submit. Nach erfolgreichem Druck UND bestätigtem
                # `slip_received` auto-fertig via `confirm_slip_received`
                # (sendet `closed`).
                if session.state != "paired" or session.student_id is None:
                    await hub.send_websocket(
                        websocket,
                        {"type": "print_result", "ok": False, "msg": "Noch nicht freigegeben"},
                    )
                    continue
                # Sicherheitsnetz: Der Client sendet `print_mode` beim Wechsel
                # in die Ansicht. Falls dieses Frame wegen eines Reconnects
                # verloren ging, darf der Druckauftrag den Worker trotzdem
                # nicht weiter belegen.
                if release_student_worker(state, session.student_id):
                    await hub.broadcast_host(state.state_snapshot())
                # Auch beim Betreuerauslöser darf der Schülerclient den Auftrag
                # selbst auslösen. Er wird wie ein Helfer-Betreuerauslöser als
                # `student`-Job eingeordnet (kein Host-/Helfername), nicht als
                # automatischer oder eigener Host-Druck.
                student_trigger = slip_trigger_for(state, session.student_id)
                if student_trigger not in ("auto", "student", "helper"):
                    msg = "Dieser Druckmodus ist noch nicht verfügbar"
                else:
                    msg = None
                if msg is not None:
                    await hub.send_websocket(
                        websocket,
                        {
                            "type": "print_result",
                            "ok": False,
                            "msg": msg,
                        },
                    )
                    continue
                if not state.settings.printers:
                    await hub.send_websocket(
                        websocket,
                        {"type": "print_result", "ok": False, "msg": "Kein Drucker konfiguriert"},
                    )
                    continue
                pool_ids = {p.id for p in state.settings.printers}
                allowed = allowed_printers_for(state, session.student_id)
                if allowed is not None and not (allowed & pool_ids):
                    await hub.send_websocket(
                        websocket,
                        {
                            "type": "print_result",
                            "ok": False,
                            "msg": "Kein erlaubter Drucker im Pool für diese Klasse",
                        },
                    )
                    continue
                # Seite 1 immer; Seite 2 (Schüler-Leihschein) nur, wenn der
                # globale Host-Default es vorgibt (am Schülerclient gibt es keine
                # Einzelauswahl).
                pages = None if state.settings.slip_second_page_default else "1"
                qs = state.find_student(session.student_id)
                if qs is not None:
                    name = _slip_name(qs.lastname, qs.firstname, qs.form)
                else:
                    name = _slip_name(
                        getattr(session, "lastname", ""),
                        getattr(session, "firstname", ""),
                        getattr(session, "form", ""),
                    )
                job = PrintJob.create(
                    role="student",
                    student_id=session.student_id,
                    pages=pages,
                    name=name,
                    student_token=session_token,
                    allowed_printers=allowed,
                )
                await state.print_queue.enqueue(job)
                await hub.broadcast_host(state.state_snapshot())

            elif mtype == "finish_signed":
                # Der Schülerclient darf den eigenen Leihschein nach der
                # Unterschrift ebenfalls abschließen. Das ist derselbe Gate
                # wie beim Helfer-Button, nur ohne fremde student_id.
                found = (
                    state.find_student_with_ctx(session.student_id)
                    if session.student_id is not None else None
                )
                if (
                    session.state != "paired"
                    or session.student_id is None
                    or not session.loan_slip_mode
                    or found is None
                    or not found[0].done_signed
                    or found[1].status != "active"
                    or not found[1].slip_signing
                ):
                    await hub.send_websocket(
                        websocket,
                        {"type": "finish_signed_result", "ok": False,
                         "msg": "Nicht im Unterschriften-Modus"},
                    )
                    continue
                await end_student(
                    state,
                    hub,
                    session.student_id,
                    queue_status="done",
                    session_state="completed",
                )
                if state.helper_sessions:
                    await hub.broadcast_queue_size(state)
                break

            elif mtype == "slip_received":
                # Schüler bestätigt „Leihschein erhalten" im Druckmodus (Modus
                # B). Zwei unabhängige Effekte: (1) löscht — falls noch
                # vorhanden — den „Gedruckt"-Marker im Drucker-Display für
                # diesen Schüler (dritter Entfernungsweg neben „nächster
                # Auftrag fertig" und 30s-TTL; idempotent, falls dort längst
                # nichts mehr angezeigt wird); (2) erst hier, nicht schon beim
                # physischen Druckende, wechselt die Session in den
                # Unterschriften-Modus bzw. schließt automatisch ab
                # (`confirm_slip_received`) — ein Reload zwischen Druckende
                # und Bestätigung darf den Wechsel nicht vorwegnehmen.
                if session.student_id is not None:
                    cleared = await state.print_queue.clear_last_printed_for_student(
                        session.student_id
                    )
                    if cleared:
                        await broadcast_printer_displays(state)
                    await confirm_slip_received(state, session.student_id)

            elif mtype == "finish":
                # Schüler schließt selbst ab → harter Zugriffsentzug.
                if session.student_id is not None:
                    await end_student(
                        state,
                        hub,
                        session.student_id,
                        queue_status="done",
                        session_state="completed",
                    )
                else:
                    from ..sessions import invalidate_session

                    await invalidate_session(state, session, "completed", reason="self-finish")
                break

    except WebSocketDisconnect:
        pass
    finally:
        # WS-Referenz nur lösen, wenn es noch unsere Verbindung ist.
        if session.ws is websocket:
            session.ws = None
            # Ab jetzt läuft das Offline-TTL (Handy aus, Netz weg).
            session.disconnected_at = datetime.now()
        await safe_broadcast(hub, state)


def _token_handle(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:8]
