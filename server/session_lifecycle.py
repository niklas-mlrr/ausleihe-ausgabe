"""Modus-A/B Session- und Worker-Lebenszyklus (create/invalidate/end) + Timeout-Sweeper.

Ausgelagert aus `sessions.py` (Welle 6, s. dortiges Modul-Docstring). Umfasst
das Anlegen und harte Entwerten einer Modus-B-Session (`create_student_session`,
`invalidate_session`), das gemeinsame Beenden eines Schülers für Modus A UND B
(`end_student`, inkl. Spectator-Beförderung und Helfer-Loslösung) sowie den
Worker-Pool-Rücklauf (`release_worker`/`release_student_worker`/
`set_worker_session`). Der Timeout-Sweeper (`sweep_expired_sessions`) hängt
direkt an `end_student`/`invalidate_session` und lebt deshalb hier statt in
einem eigenen Modul. Das „Aufrufen"/Zuweisen eines Schülers an einen Helfer
(`assign_student_to_helper` & Co.) sitzt dagegen in `helper_queue.py` — dort
begründet, warum die Trennung an genau dieser Stelle verläuft.

`get_config`/`get_hub`/`get_state` werden über die Fassade `sessions`
aufgelöst (`_sessions.get_...`) statt direkt aus `.config`/`.hub`/`.state`
importiert — s. `scan_booking.py`-Docstring für die ausführliche Begründung
(Tests patchen diese Collaborators als `sessions.get_config` usw.).

Zwei Namen werden aus demselben Grund lokal — UND über die Fassade
`.sessions` statt direkt aus dem Zielmodul — importiert, um echte
Modul-Zyklen zu brechen, die die inhaltliche Aufteilung sonst erzwingen
würde:
  - `assign_student_to_helper` (in `end_student`s Spectator-Beförderung,
    zwei Stellen) — liegt in `helper_queue.py`, das seinerseits
    `end_student` von HIER auf Modul-Ebene importiert.
  - `release_station_student` (in `end_student`s Trennen-/Abschluss-Zweig,
    zwei Stellen) — liegt in `scan_station_session.py`, das seinerseits
    `set_worker_session`/`release_student_worker` von HIER auf Modul-Ebene
    importiert.
Der Umweg über `.sessions` bricht den Zyklus UND bleibt korrekt, unabhängig
davon, ob diese Namen gerade noch lokal in `sessions.py` stehen oder schon
in ihr Zielmodul ausgelagert sind.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from datetime import datetime

from . import sessions as _sessions
from .config import Config
from .device_broadcast import broadcast_printer_displays
from .loan_slip_flow import _send_own_slip_download
from .ratelimit import join_limiter
from .session_tokens import gen_pairing_code, gen_session_token
from .state import AppState, QueueStudent, StudentSessionB

log = logging.getLogger(__name__)


def release_worker(state: AppState, worker) -> None:
    """Worker-Context nach Abschluss zurück in den Pool (statt ihn zu verlieren).

    Fällt auf reines Schließen zurück, falls kein Pool verfügbar ist. Die
    Release-Coroutine wird als Task mit starker Referenz gehalten — asyncio
    hält Tasks selbst nur schwach (WeakSet), ein unreferenzierter Task kann
    daher mid-Coroutine GC't werden, bevor er den Context zurückgibt.
    Abgesichert (Bookkeeping, nicht das GC-Verhalten selbst):
    tests/test_sessions.py::test_release_worker_holds_task_in_flight_and_discards_after
    """
    pool = state.worker_pool
    coro = (
        pool.release(worker) if (pool is not None and hasattr(pool, "release")) else worker.close()
    )
    t = asyncio.create_task(coro)
    _release_tasks.add(t)
    t.add_done_callback(_release_tasks.discard)


# Starke Referenzen auf in-flight Release-Tasks (asyncio hält Tasks nur schwach).
_release_tasks: set[asyncio.Task] = set()


def release_student_worker(state: AppState, student_id: int) -> bool:
    """Den Worker eines Schülers aus der aktiven Zuordnung lösen.

    Der Schüler kann danach weiter über seine Modus-B-Session drucken; nur die
    Playwright-Kartei wird nicht mehr benötigt. Der Vorgang ist idempotent, weil
    Druckmodus-Nachrichten bei Reconnects erneut eintreffen können.
    """
    worker = state.student_worker_sessions.pop(student_id, None)
    if worker is None:
        return False
    release_worker(state, worker)
    return True


def set_worker_session(state: AppState, student_id: int, worker_session) -> None:
    """Worker-Session eines Schülers registrieren — vorhandene zuvor freigeben.

    Ohne diese Freigabe würde ein Überschreiben (z. B. zwei `open_student`-Läufe
    für denselben Schüler) den alten Context aus dem Pool verlieren — bei nur
    wenigen Contexts (Default 2) sind so nach kurzer Zeit alle weg.
    """
    old = state.student_worker_sessions.get(student_id)
    if old is not None and old is not worker_session:
        release_worker(state, old)
    state.student_worker_sessions[student_id] = worker_session


# ---------------------------------------------------------------------------
# Modus-B-Session-Lebenszyklus
# ---------------------------------------------------------------------------


def create_student_session(state: AppState) -> StudentSessionB:
    session = StudentSessionB(
        session_token=gen_session_token(),
        pairing_code=gen_pairing_code(state),
    )
    state.student_sessions[session.session_token] = session
    log.info("Modus-B-Session angelegt (Code %s)", session.pairing_code)
    return session


async def invalidate_session(
    state: AppState, session: StudentSessionB, new_state: str, *, reason: str = ""
) -> None:
    """Harter Zugriffsentzug: Worker zu, WS zu, Token aus dem RAM (PLAN §3.2)."""
    if session.state in ("completed", "expired", "revoked"):
        return
    session.state = new_state  # type: ignore[assignment]

    # In-flight Lade-Task abbrechen — sonst leakt der Worker-Context, wenn
    # open_student noch in load_card steckt (s. end_student / worker.py).
    # Der Await erzwingt, dass die CancelledError den Task tatsächlich trifft
    # (bzw. der Task über den Stale-Guard in load_and_push_paired_student
    # sauber zurückkehrt), BEVOR wir unten den Worker poppen — sonst raced das
    # pop() gegen das nachträgliche set_worker_session und der Context wird
    # trotzdem als orphan für den toten student_id registriert. Kein Lock
    # wird hier gehalten → kein Deadlock-Risiko.
    if session.load_task is not None and not session.load_task.done():
        session.load_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await session.load_task
    session.load_task = None

    # Worker-Context zurück in den Pool (falls vorhanden).
    if session.student_id is not None:
        release_student_worker(state, session.student_id)

    # Schüler-WS informieren und schließen.
    ws = session.ws
    session.ws = None
    if ws is not None:
        # Regulärer Abschluss (Schülerleihscheinmodus): der eigene Leihschein
        # mit den Aktionen der letzten drei Monate muss VOR dem `closed`/Close
        # raus — direkt danach wird der Token unten hart entwertet, ein
        # Nachfordern über die (dann tote) Session ist nicht mehr möglich.
        if new_state == "completed" and session.student_id is not None:
            await _send_own_slip_download(state, ws, session)
        await _sessions.get_hub().send_websocket(ws, {"type": "closed", "reason": new_state})
        try:
            await ws.close(code=4006)
        except Exception:
            pass

    # Token endgültig entwerten.
    state.student_sessions.pop(session.session_token, None)
    # Token niemals loggen — auch nicht als Präfix (PLAN §3.7). Stattdessen
    # ein nicht-reversibler 8-Zeichen-Hash als Korrelationshandle.
    token_handle = hashlib.sha256(session.session_token.encode()).hexdigest()[:8]
    log.info("Modus-B-Session %s → %s (%s)", token_handle, new_state, reason)


async def _detach_helper(state: AppState, hub, helper, helper_notify: dict | None) -> None:
    """Helfer von seinem (gerade beendeten) Schüler lösen — gemeinsame Logik für
    beide Zweige von `end_student` (echter Queue-Schüler über
    `student.assigned_helper` bzw. transienter Lupe-Schüler über
    `find_helper_for_student`). Der Aufrufer stellt sicher, dass `helper`
    tatsächlich (noch) diesem Schüler zugeordnet ist.

    In-flight Lade-Task abbrechen, damit ein noch laufendes open_student seinen
    Worker-Context zurückgibt (BaseException-Handler in worker.py).

    Reihenfolge nicht verändern: `student_id` muss VOR dem Await auf None
    gesetzt werden, damit der Stale-Guard in load_and_push_helper_student
    greift.
    """
    if helper.load_task is not None and not helper.load_task.done():
        helper.load_task.cancel()
    helper.student_id = None
    helper.student_form = None
    helper.student_lastname = None
    helper.student_firstname = None
    helper.student_via_search = False
    helper.expected_isbns = set()
    helper.vormerk_isbns = set()
    helper.lent_isbns = set()
    helper.lent_codes = set()
    helper.peeking = False  # Schüler weg → Queue-Ansicht hinfällig
    if helper.load_task is not None and not helper.load_task.done():
        with contextlib.suppress(asyncio.CancelledError):
            await helper.load_task
    helper.load_task = None
    # Scanner sonst ohne jede Rückmeldung mit dem alten (getrennten) Schüler
    # stehen — der Helfer sieht dann weder Trennung noch neuen Wartezustand
    # ("Alle Verbindungen trennen" wirkte sonst nur am Host). Default: Idle-
    # `waiting` (Queue anzeigen). Beim Advance übergibt der Aufrufer
    # `{"type":"loading"}` → Client verbirgt die Queue, während der nächste
    # Schüler geladen wird. Die Queue des Helfer-Kontexts (Klasse, an die er
    # gebunden ist) — sonst würde ein Helfer einer anderen Klasse die falsche
    # Warteschlange sehen.
    await hub.send_scanner(
        helper.token,
        helper_notify
        or {
            "type": "waiting",
            "msg": "Warte auf Schüler-Zuweisung",
            "queue_size": state.pending_count(helper.context_id),
            "queue": state.pending_queue_as_list(helper.context_id),
            "queue_all": state.queue_as_list(helper.context_id),
        },
    )


async def end_student(
    state: AppState,
    hub,
    student_id: int,
    *,
    queue_status: str,
    session_state: str,
    broadcast: bool = True,
    helper_notify: dict | None = None,
) -> None:
    """Schüler beenden (Abschluss/Skip/Abbruch) für Modus A UND B.

    Setzt den Queue-Status, löst die Helfer-Zuordnung (Modus A) und entwertet
    eine etwaige Modus-B-Session hart. Schließt in jedem Fall den Worker-Context.

    `broadcast=False` unterdrückt den Host-Snapshot-Push — für Batch-Aufrufe
    (disconnect-all/reset-queue), die am Ende einmal selbst broadcasten.

    `helper_notify`: Nachricht an den bisherigen Helfer-Scanner (Modus A).
    Default `None` → Idle-`waiting` (Queue wird im Client angezeigt, Helfer ist
    frei). Beim *Advance* („Weiter"/„Nächster"/„Aufrufen") übergibt der Aufrufer
    `{"type": "loading"}`, damit der Client die Queue **nicht** zeigt, während
    der nächste Schüler geladen wird — statt eines Idle-`waiting`, das die Queue
    aufblitzen ließe (s. `assign_student_to_helper` für den zweiten `loading`-
    Push beim Zuweisen).
    """
    student = state.find_student(student_id)
    if student:
        old_helper = student.assigned_helper
        # Beförderung: wartet ein Spectator auf genau diesen Schüler, übernimmt
        # er ihn sofort statt ihn wirklich frei zu geben. Reihenfolge bewusst
        # synchron (kein Await zwischen `pop_next_spectator` und dem Aufruf von
        # `assign_student_to_helper`, dessen Mutationen selbst VOR ihrem ersten
        # Await laufen) — sonst könnte ein dritter Helfer den Schüler in der
        # Lücke regulär „callen", bevor die Beförderung feststeht.
        waiter = state.pop_next_spectator(student_id)
        promoted = state.helper_sessions.get(waiter.token) if waiter else None
        if promoted is not None:
            student.assigned_helper = None
            # Lokaler Import über die Fassade bricht den Zyklus session_lifecycle
            # <-> helper_queue (s. Modul-Docstring oben).
            from .sessions import assign_student_to_helper

            await assign_student_to_helper(
                state, hub, promoted, student, via_search=waiter.via_search
            )
        else:
            student.status = queue_status  # type: ignore[assignment]
            student.assigned_helper = None
            # Zurück in die Warteschlange (Trennen/Reset) = neuer Durchlauf →
            # Buch-Zähler und Leihschein-Marker fallen zurück auf Null. Bei
            # done/skipped bleiben sie stehen (Host sieht, wie weit es kam).
            if queue_status == "pending":
                student.reset_progress()
                # Trennen (Einzeln/Alle/Reset) entwertet auch eine etwaige
                # Scan-Station-Bindung: an der Station angemeldet → dort
                # abmelden (fällt auf „Zettel-Code scannen" zurück), UND der
                # bisherige Zettel-Code wird an der Station nicht mehr
                # angenommen (`invalidate_station_code`). Der Code bleibt als
                # Vorschlag für eine Reaktivierung beim nächsten „Erstellen"
                # gemerkt (Checkbox im Host-Druckdialog, s. `AppState.
                # allocate_station_code`).
                # Lokaler Import über die Fassade bricht den Zyklus session_lifecycle
                # <-> scan_station_session (s. Modul-Docstring oben).
                from .sessions import release_station_student

                station = state.find_station_by_student(student_id)
                if station is not None:
                    await release_station_student(state, station, reason="host disconnected")
                state.invalidate_station_code(student_id)
            elif queue_status in ("done", "skipped", "absent"):
                # Abschließen/Überspringen beendet den Durchlauf final — die
                # Station wird (wie beim Trennen) abgemeldet und fällt auf
                # „Zettel-Code scannen" zurück, statt den beendeten Schüler
                # weiter stale anzuzeigen. Der Zettel-Code bleibt hier
                # unangetastet: ein Re-Scan wird via `resolve_station_code` am
                # `done`-Status ohnehin abgelehnt, und nach einem Reset-Queue
                # (done → pending) ist der Code ohne Neudruck wieder nutzbar.
                # Lokaler Import über die Fassade: gleicher Zyklus-Bruch wie oben im
                # pending-Zweig.
                from .sessions import release_station_student

                station = state.find_station_by_student(student_id)
                if station is not None:
                    await release_station_student(state, station, reason="student finished")
        if old_helper and old_helper in state.helper_sessions:
            h = state.helper_sessions[old_helper]
            await _detach_helper(state, hub, h, helper_notify)
    else:
        # Transienter Such-Schüler (Helfer-Lupe): bewusst NICHT in eine Queue
        # eingetragen („Schnellsprung" zu beliebigem IServ-Schüler), aber ein
        # Helfer kann ihn trotzdem zugewiesen haben. `find_student` findet ihn
        # nicht → ohne diesen Zweig bliebe `helper.student_id` stale und ein
        # noch laufendes `open_student` (load_task) leakte den Worker-Context.
        # Gleiche Aufräumung wie oben, nur Helfer via find_helper_for_student.
        old_transient_helper = state.find_helper_for_student(student_id)
        waiter = state.pop_next_spectator(student_id)
        promoted = state.helper_sessions.get(waiter.token) if waiter else None
        if promoted is not None:
            transient = QueueStudent(
                student_id=student_id,
                lastname=waiter.lastname,
                firstname=waiter.firstname,
                form=waiter.form,
                status="active",
                assigned_helper=promoted.token,
            )
            # Lokaler Import über die Fassade: gleicher Zyklus-Bruch wie oben im
            # Queue-Zweig.
            from .sessions import assign_student_to_helper

            await assign_student_to_helper(
                state, hub, promoted, transient, via_search=waiter.via_search
            )
        if old_transient_helper is not None and old_transient_helper.student_id == student_id:
            await _detach_helper(state, hub, old_transient_helper, helper_notify)

    session = state.find_session_by_student(student_id)
    if session:
        await invalidate_session(state, session, session_state, reason=queue_status)
    else:
        release_student_worker(state, student_id)

    if broadcast:
        await hub.broadcast_host(state.state_snapshot())
        # Ein abgeschlossener/übersprungener Schüler kann die Schülerauftrag-
        # Bedingung eines Drucker-Displays kippen (s.
        # `AppState._printer_display_students_only`) — Displays live nachziehen.
        await broadcast_printer_displays(state)


async def teardown_students(
    state: AppState,
    hub,
    student_ids: set[int] | list[int],
    *,
    reason: str,
    clear_unbound_sessions: bool = False,
) -> None:
    """End several live student flows without route-local state mutation.

    Batch resets must cancel in-flight loads and release Modus-A worker pages,
    not merely clear the helper/session fields.  Intermediate broadcasts and
    spectator promotion are intentionally suppressed for this terminal flow.
    """
    ids = set(student_ids)
    for helper in state.helper_sessions.values():
        if helper.student_id is not None and (
            helper.student_id in ids or clear_unbound_sessions
        ):
            ids.add(helper.student_id)
    for session in state.student_sessions.values():
        if session.student_id is not None and (
            session.student_id in ids or clear_unbound_sessions
        ):
            ids.add(session.student_id)

    for student_id in ids:
        state.student_spectators.pop(student_id, None)
    for student_id in ids:
        await end_student(
            state,
            hub,
            student_id,
            queue_status="pending",
            session_state="revoked",
            broadcast=False,
        )
    if clear_unbound_sessions:
        for session in list(state.student_sessions.values()):
            if session.state in ("pending_pairing", "paired") and session.student_id is None:
                await invalidate_session(state, session, "revoked", reason=reason)


# ---------------------------------------------------------------------------
# Timeout-Sweeper (harter Zugriffsentzug bei Inaktivität)
# ---------------------------------------------------------------------------


def sweep_helper_scan_secrets(
    state: AppState, ttl_s: int, now: datetime | None = None
) -> None:
    """Einmalige Helfer-Scan-Secrets aufräumen (ungenutzter QR verfällt)."""
    now = now or datetime.now()
    for secret, (_sid, created) in list(state.helper_scan_secrets.items()):
        if (now - created).total_seconds() > ttl_s:
            del state.helper_scan_secrets[secret]


def expired_student_sessions(
    state: AppState, cfg: Config, now: datetime
) -> list[StudentSessionB]:
    """Modus-B-Sessions, deren TTL abgelaufen ist (rein rechnend, ohne Effekte).

    Ausgelagert aus `sweep_expired_sessions`, damit die TTL-Regeln testbar
    sind — s. tests/test_session_ttl.py.
    """
    expired: list[StudentSessionB] = []
    for session in list(state.student_sessions.values()):
        if session.state == "pending_pairing":
            if (now - session.created_at).total_seconds() > cfg.pending_pairing_ttl_s:
                expired.append(session)
        elif session.state == "paired":
            # Verbundene Sessions verfallen NICHT: ein offener Socket ist der
            # Liveness-Beweis. `last_activity` taugt dafür nicht — ein Schüler,
            # der 20 min in der Schlange steht oder den Bildschirm ausschaltet,
            # sendet keine Frames und flog früher mitten im Vorgang raus
            # (→ neuer Pairing-Code). Tote Sockets erkennt Uvicorns
            # WS-Ping/Pong-Keepalive (~40 s) und schließt sie, womit
            # `disconnected_at` gesetzt wird und das Offline-TTL greift.
            if session.ws is not None:
                continue
            since = session.disconnected_at or session.last_activity
            if (now - since).total_seconds() > cfg.paired_idle_ttl_s:
                expired.append(session)
    return expired


async def sweep_expired_sessions() -> None:
    """Hintergrund-Loop: pending/paired Sessions nach TTL hart entwerten."""
    cfg = _sessions.get_config()
    hub = _sessions.get_hub()
    while True:
        await asyncio.sleep(30)
        # Einzelne Iteration darf den Loop nie töten — eine flüchtige Exception
        # (z. B. transienter IServ-Fehler in end_student) würde sonst den
        # Sweeper dauerhaft killen → unbegrenztes Session-Wachstum.
        try:
            join_limiter.sweep()  # Rate-Limit-Buckets aufräumen (kein unbegrenztes Wachstum)
            state = _sessions.get_state()
            expired_host_sids = state.sweep_host_sessions(cfg.host_session_ttl_s)
            for host_sid in expired_host_sids:
                await hub.close_host_session(host_sid, state)
            now = datetime.now()
            sweep_helper_scan_secrets(state, cfg.helper_scan_ttl_s, now)
            expired = expired_student_sessions(state, cfg, now)
            # broadcast=False — einmal am Ende bündeln (wie /api/disconnect-all),
            # sonst N Snapshots pro Sweep.
            for session in expired:
                sid = session.student_id
                if sid is not None:
                    await end_student(
                        state,
                        hub,
                        sid,
                        queue_status="pending",
                        session_state="expired",
                        broadcast=False,
                    )
                else:
                    await invalidate_session(state, session, "expired", reason="timeout")
        except asyncio.CancelledError:
            raise  # Shutdown — Loop sauber beenden.
        except Exception:
            log.exception("Sweeper iteration fehlgeschlagen (non-fatal)")
            continue
        if expired:
            await hub.broadcast_host(state.state_snapshot())
