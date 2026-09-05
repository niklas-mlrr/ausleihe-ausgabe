"""Scan-Station (`/scan-station`): Gerät, Schüler-Laden, Zettel-Druck, TTL-Sweep.

Ausgelagert aus `sessions.py` (Welle 6, s. dortiges Modul-Docstring). Deckt die
komplette Lebensdauer einer Scan-Station ab: Registrierung/Zustands-Broadcast
(`send_scan_station_update`), Zettel-Code-Auflösung (`resolve_station_code`),
Schülerkartei-Laden (`load_station_student`, Spiegel von `load_and_push_
paired_student` in helper_queue.py — aber ohne Leihschein-/Druckmodus-Teil),
Freigabe (`release_station_student`) und den eigenen Zettel-Druck
(`print_station_sheet_for`, `_load_and_activate_station_student`,
`activate_station_student` — Spiegel von `print_loan_slip_for` in
loan_slip_flow.py, nur mit selbst gebautem PDF statt IServ-Leihschein) samt
30-Sekunden-Leerlauf-TTL-Sweeper (`sweep_scan_stations`).

`get_config`/`get_hub`/`get_state`/`get_book_order_for_form`/
`get_hidden_isbns_for_form` werden über die Fassade `sessions` aufgelöst
(`_sessions.get_...`) statt direkt aus ihren Ursprungsmodulen importiert — s.
`scan_booking.py`-Docstring für die ausführliche Begründung (Tests patchen
diese Collaborators als `sessions.get_config` usw.).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from . import sessions as _sessions
from .book_visibility import (
    all_books_already_loaned,
    apply_empty_stock_visibility,
    apply_hidden_books,
    hydrate_student_info,
    init_book_progress,
)
from .loan_slip_flow import (
    _download_slip_to_host,
    _prefetch_tasks,
    _station_sheet_filename,
    _station_sheet_label,
    _student_form,
)
from .session_lifecycle import release_student_worker, set_worker_session
from .state import AppState, QueueStudent

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scan-Station (`/scan-station`)
# ---------------------------------------------------------------------------

# Leerlauf einer belegten Station bis zum Rückfall auf „Zettel-Code scannen".
# Zählt erst ab `worker_ready` (s. `ScanStationSession`) — Warten auf einen
# freien Worker-Context darf den Schüler nicht unter den Händen wegräumen.
STATION_IDLE_TTL_S = 30.0

# Takt des Stations-Sweepers. Deutlich feiner als der 30-s-Takt des Modus-B-
# Sweepers, weil das Stations-TTL selbst nur 30 s beträgt — mit dem groben Takt
# wäre der tatsächliche Rückfall bis zu 60 s spät.
_STATION_SWEEP_TICK_S = 5.0


async def send_scan_station_update(state: AppState, station) -> None:
    """Gerätezustand an eine Scan-Station schicken: vor der Freischaltung nur
    den Registrierungs-Code, danach die Aufforderung „Zettel-Code scannen".

    Schülerdaten laufen bewusst NICHT hierüber — die pusht `load_station_student`
    (`student_info`/`worker_ready`), damit eine noch nicht freigeschaltete oder
    gerade leere Station niemals personenbezogene Daten sieht.
    """
    if station.ws is None:
        return
    if not station.authorized:
        msg = {
            "type": "registration",
            "code": station.registration_code,
            "station_id": station.station_id,
            "theme": station.theme,
            "input_mode": station.input_mode,
        }
    else:
        msg = {
            "type": "ready",
            "label": station.label,
            "theme": station.theme,
            "input_mode": station.input_mode,
            "idle_ttl_s": STATION_IDLE_TTL_S,
        }
    if not await _sessions.get_hub().send_websocket(station.ws, msg):
        station.ws = None


async def broadcast_scan_stations(state: AppState) -> None:
    """Gerätezustand an alle Stationen pushen (Theme/Name/Freischaltung)."""
    for station in list(state.scan_stations.values()):
        await send_scan_station_update(state, station)


def resolve_station_code(state: AppState, code: str) -> tuple[QueueStudent | None, str]:
    """Gescannten Zettel-Code auf einen Schüler auflösen.

    Liefert `(student, "")` bei Erfolg, sonst `(None, grund)` mit einem für den
    Schüler verständlichen Text. Streng bei Unsicherheit — die Station lädt nur,
    wenn der Schüler eindeutig frei ist:

    - Code unbekannt → kein Zugriff (Tippfehler oder fremder/alter Zettel).
    - Schüler nicht mehr in einer Queue (Klasse geschlossen) → abgelehnt.
    - Schüler bereits `done` → abgeschlossen, kein erneuter Zugriff.
    - Schüler einem Helfer zugewiesen oder mit eigener Modus-B-Session
      verbunden → er wird bereits woanders bedient; zwei parallele Scan-Wege
      auf derselben Playwright-Kartei sind ausgeschlossen.
    - Schüler an einer anderen Station aktiv → dort erst freigeben.
    """
    student_id = state.student_id_for_station_code(code)
    if student_id is None:
        return None, "Code unbekannt — bitte beim Betreuer melden."
    student = state.find_student(student_id)
    if student is None:
        return None, "Klasse nicht mehr geöffnet — bitte beim Betreuer melden."
    if student.status == "done":
        return None, "Vorgang bereits abgeschlossen."
    if student.assigned_helper is not None:
        return None, "Du wirst gerade von einem Helfer bedient."
    session = state.find_session_by_student(student_id)
    if session is not None and session.state == "paired":
        return None, "Du bist bereits mit deinem Handy angemeldet."
    for other in state.scan_stations.values():
        if other.student_id == student_id:
            return None, "Du bist bereits an einer anderen Station angemeldet."
    return student, ""


async def load_station_student(state: AppState, hub, station, student) -> None:
    """Schülerkartei für eine Scan-Station laden — Spiegel von
    `load_and_push_paired_student`, aber ohne Leihschein-/Druckmodus-Teil
    (die Station scannt nur Bücher, s. docs/PLAN.md „Scan-Station").

    Identität geht sofort raus (`student_info` ohne Bücher), die Bücherliste
    folgt mit `worker_ready`, sobald der Playwright-Worker steht. Erst dann
    beginnt auch das 30-s-Leerlauf-TTL.
    """
    station_id = station.station_id
    bound_student_id = student.student_id
    try:
        info = await state.iserv.get_student_info(bound_student_id, state.selected_schoolyear)
    except Exception as e:  # noqa: BLE001 — Ladefehler dem Schüler zeigen, Station freigeben
        log.exception("Scan-Station: Laden von %d fehlgeschlagen", bound_student_id)
        if station.ws is not None:
            await hub.send_websocket(
                station.ws, {"type": "code_error", "msg": f"Laden fehlgeschlagen: {e}"}
            )
        station.clear_student()
        await send_scan_station_update(state, station)
        return
    info = await hydrate_student_info(
        state, info, student.form or "", station, is_helper=False
    )
    books = info.get("books", [])
    if station.ws is not None:
        # Bewusst NUR Name und Klasse — anders als am eigenen Handy ist die
        # Station ein geteiltes Gerät; Zahl-/Anmeldestatus haben dort nichts
        # zu suchen (PLAN §3.7). Die Bücherliste folgt mit `worker_ready`.
        await hub.send_websocket(
            station.ws,
            {
                "type": "student_info",
                "student": {
                    "lastname": student.lastname,
                    "firstname": student.firstname,
                    "form": student.form,
                },
                # Klassenweite Reihenfolge der offenen Bücher — dieselbe
                # Sortierung wie im Schülerclient (kein Personenbezug).
                "book_order": info.get("book_order", []),
            },
        )

    def _stale() -> bool:
        """Station inzwischen weg/freigegeben/anderem Schüler zugeteilt?"""
        current = state.scan_stations.get(station_id)
        return current is not station or station.student_id != bound_student_id

    # Kurzschluss wie in Modus B: sind schon alle Bücher ausgeliehen, bringt ein
    # Worker-Context nichts — die Station zeigt die Liste und meldet „nichts
    # mehr offen", statt einen Platz im Pool zu blockieren.
    if state.worker_pool and not all_books_already_loaned(books):
        def _on_wait(position: int) -> None:
            # Wird synchron aus `open_student` gerufen, während dort auf einen
            # freien Context gewartet wird → Senden als Fire-and-forget-Task
            # (starke Referenz halten, s. `_prefetch_own_slip_task`).
            if station.ws is None:
                return
            t = asyncio.create_task(
                hub.send_websocket(station.ws, {"type": "worker_waiting", "position": position})
            )
            _prefetch_tasks.add(t)
            t.add_done_callback(_prefetch_tasks.discard)

        try:
            worker_session = await state.worker_pool.open_student(
                bound_student_id,
                f"{student.lastname}, {student.firstname}",
                priority="station",
                on_wait=_on_wait,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Scan-Station: Worker-Session für %d fehlgeschlagen", bound_student_id)
            if station.ws is not None:
                await hub.send_websocket(
                    station.ws,
                    {
                        "type": "code_error",
                        "msg": f"Kein freier Platz: {e}. Bitte beim Betreuer melden.",
                    },
                )
            station.clear_student()
            await send_scan_station_update(state, station)
            await hub.broadcast_host(state.state_snapshot())
            return
        # Stale-Guard wie in `load_and_push_paired_student`: Während des
        # `open_student`-`await` kann die Station freigegeben oder neu belegt
        # worden sein — dann gehört der frische Context niemandem mehr.
        if _stale():
            log.info(
                "Stale load_station_student für %d — Station nicht mehr gebunden, Context zurück.",
                bound_student_id,
            )
            try:
                await worker_session.close()
            except Exception:
                log.exception("Schließen des stale Worker-Contexts (Station) fehlgeschlagen")
            await hub.broadcast_host(state.state_snapshot())
            return
        # Bereits vorhandener Worker (Helfer/Handy) darf nicht überschrieben
        # werden — dann gehört uns der eigene nicht und wird sofort abgegeben.
        if state.student_worker_sessions.get(bound_student_id) is not None:
            station.owns_worker = False
            try:
                await state.worker_pool.release(worker_session)
            except Exception:
                log.exception("Doppelten Worker-Context (Station) freigeben fehlgeschlagen")
        else:
            set_worker_session(state, bound_student_id, worker_session)
            station.owns_worker = True

    if _stale():
        return
    station.worker_ready = True
    station.last_activity = datetime.now()  # ab hier zählt das 30-s-TTL
    if station.ws is not None:
        await hub.send_websocket(
            station.ws,
            {
                "type": "worker_ready",
                "books": books,
                "book_order": info.get("book_order", []),
            },
        )
    await hub.broadcast_host(state.state_snapshot())


async def release_station_student(state: AppState, station, *, reason: str) -> bool:
    """Schüler-Bindung einer Station lösen (Timeout, Knopf, Reload, Verbieten).

    Gibt den Worker-Context nur frei, wenn die Station ihn selbst geöffnet hat
    (`owns_worker`) — ein Helfer-/Handy-Worker desselben Schülers bleibt
    unangetastet. Idempotent: ohne gebundenen Schüler passiert nichts.
    """
    if station.student_id is None:
        return False
    student_id = station.student_id
    task = station.load_task
    if task is not None and not task.done():
        task.cancel()
    if station.owns_worker:
        release_student_worker(state, student_id)
    station.clear_student()
    log.info("Scan-Station %s freigegeben (%s)", station.station_id[:6], reason)
    if station.ws is not None:
        await _sessions.get_hub().send_websocket(station.ws, {"type": "released", "reason": reason})
    await send_scan_station_update(state, station)
    return True


async def _load_and_activate_station_student(
    state: AppState, student_id: int, *, reactivate_old_code: bool = True
):
    """Gemeinsamer Kern von `print_station_sheet_for` und
    `activate_station_student`: IServ-Stand holen (bei JEDEM Aufruf frisch —
    die Bücherliste auf dem Zettel ist also immer aktuell), Zettel-Code
    vergeben (stabil pro Schüler, `AppState.allocate_station_code` — derselbe
    Code über beliebig viele Nachdrucke hinweg, damit ein alter Zettel gültig
    bleibt), Schüler in den Zettel-/Stations-Fluss aktivieren.

    `reactivate_old_code`: nur relevant, wenn der Schüler gerade KEINEN
    aktiven Code hat (z. B. nach „Trennen" am Host, s. `end_student`) — dann
    reaktiviert `True` (Checkbox im Host-Druckdialog, Default an) den zuletzt
    entwerteten Code, `False` zieht einen frischen (s. `AppState.
    allocate_station_code`).
    `get_student_info` ist ein reiner GET (CLAUDE.md / PLAN §6) — kein
    Schreibzugriff auf IServ.

    Der Zettel-Druck (bzw. hier: das reine Aktivieren) ist das „Aufrufen"
    des Zettel-/Stations-Flusses: der Schüler gilt ab hier als aktiv
    (bislang wartende Schüler wechseln von „Wartend"), und die Host-Queue
    bekommt ihren X/Y-Fortschritt genau wie beim Aufrufen durch einen Helfer
    (`reset_baseline` — neue Zählung). `station_zettel_printed` bleibt
    danach auch über ein Ab-/Anmelden an der Station hinweg gesetzt (s.
    Feld-Doku in state.py) — der Name ist historisch: er markiert „im
    Zettel-/Stations-Fluss", nicht zwingend „ein Blatt kam aus dem Drucker"
    (s. `activate_station_student`, wo das gilt). Ein Nachdruck für einen
    bereits aktiven Schüler (Host-Knopf im „Aktuell in Ausgabe"-Kästchen,
    s. `web/host-render.js::reprintSheetBtn`) ist KEIN erneutes Aufrufen —
    nur `pending → active` setzt die Baseline neu, sonst würde der
    Nachdruck den seit dem echten Aufrufen laufenden Fortschritt der
    Host-Statuszeile zurücksetzen.

    Gibt `(student, form, info, code)` zurück.
    """
    if state.iserv is None:
        raise RuntimeError("Kein IServ-Client verfügbar — bitte zuerst eine Klasse laden")

    student = state.find_student(student_id)
    form = _student_form(state, student_id) or ""
    info = await state.iserv.get_student_info(student_id, state.selected_schoolyear)
    apply_hidden_books(info, await _sessions.get_hidden_isbns_for_form(state, form))
    code = state.allocate_station_code(student_id, reactivate_old=reactivate_old_code)

    if student is not None:
        is_first_call = student.status == "pending"
        student.station_zettel_printed = True
        if is_first_call:
            student.status = "active"
        init_book_progress(state, student_id, info, reset_baseline=is_first_call)
        await _sessions.get_hub().broadcast_host(state.state_snapshot())

    # Wie in `hydrate_student_info` (Modus B/Scan-Station): Bestand-leer-Reihen
    # verschwinden vom Zettel, solange sie nicht ausgeliehen sind — erst NACH
    # `init_book_progress`, damit der Host-„X/Y"-Zähler weiterhin mit der
    # vollen Liste rechnet.
    apply_empty_stock_visibility(info, state.caches.empty_isbns)

    return student, form, info, code


async def activate_station_student(
    state: AppState, student_id: int, *, reactivate_old_code: bool = True
) -> dict:
    """Schüler für den Zettel-/Stations-Fluss aktivieren OHNE einen Zettel zu
    drucken — Gegenstück zu `print_station_sheet_for` für den Host-Knopf
    „Erstellen" (im Pairing-Kasten, ohne „und Drucken"). Status/Code/
    Fortschritt werden identisch gesetzt; der physische Druck kann später
    jederzeit über den Zettel-Nachdruck-Knopf im „Aktuell in
    Ausgabe"-Kästchen nachgeholt werden (`printStationSheet` im Host,
    `/api/scan-station/print-sheet`) — der Zettel-Code bleibt dabei
    unverändert (stabil pro Schüler). `reactivate_old_code` s.
    `_load_and_activate_station_student`."""
    _student, _form, _info, code = await _load_and_activate_station_student(
        state, student_id, reactivate_old_code=reactivate_old_code
    )
    return {"ok": True, "code": code}


async def print_station_sheet_for(
    state: AppState,
    student_id: int,
    *,
    printer_name: str | None = None,
    reactivate_old_code: bool = True,
) -> dict:
    """Zettel für die Scan-Station bauen und lokal drucken.

    Spiegel von `print_loan_slip_for`, nur mit selbst gebautem PDF statt des
    IServ-Leihscheins. Aktivierung (Status/Code/Fortschritt) läuft über
    `_load_and_activate_station_student` — s. dort für Details, auch zu
    `reactivate_old_code`.
    """
    from .printing import print_pdf
    from .scan_station import build_sheet_pdf

    cfg = _sessions.get_config()
    student, form, info, code = await _load_and_activate_station_student(
        state, student_id, reactivate_old_code=reactivate_old_code
    )
    books = info.get("books", [])
    pending = [b for b in books if b.get("status") == "vorgemerkt"]
    # Wie in den Clients (`web/common.js::renderBookRows`): nach der
    # klassenweit konfigurierten Reihenfolge, unbekannte ISBNs ans Ende.
    book_order = await _sessions.get_book_order_for_form(state, form)
    order_index = {isbn: i for i, isbn in enumerate(book_order)}
    pending.sort(key=lambda b: order_index.get(b.get("isbn"), len(book_order)))
    # `books` liegt bereits nach Ausgabezeit absteigend sortiert vor
    # (`IservClient.get_student_info`), Filtern erhält diese Reihenfolge.
    lent = [b for b in books if b.get("status") == "ausgeliehen"]

    pdf = await asyncio.to_thread(
        build_sheet_pdf,
        form=form,
        lastname=student.lastname if student else None,
        firstname=student.firstname if student else None,
        code=code,
        lent_books=lent,
        pending_books=pending,
    )

    sheet_label = _station_sheet_label(state, student_id, form)

    # Entwickler-Toggle „PDF lokal speichern": in den Host-Browser laden statt
    # drucken (identisch zum Leihschein-Pfad).
    if state.settings.save_pdf_locally:
        filename = _station_sheet_filename(
            student.lastname if student else None, student.firstname if student else None, form
        )
        delivered = await _download_slip_to_host(
            state, student_id, pdf, pages=None, filename=filename
        )
        if delivered:
            log.info("Stations-Zettel an %d Host-Browser gesendet: student_id=%s",
                     delivered, student_id)
            return {"ok": True, "backend": "download", "code": code,
                    "detail": f"an {delivered} Host-Browser gesendet"}
        log.warning(
            "PDF-lokal aktiv, aber kein Host-Browser verbunden — Stations-Zettel "
            "für student_id=%s wird ins Ausgabeverzeichnis gespeichert", student_id,
        )
        result = await print_pdf(
            pdf, backend="file", output_dir=cfg.print_output_dir,
            label=sheet_label,
        )
        result["detail"] = "kein Host-Browser verbunden — " + result.get("detail", "")
        result["code"] = code
        return result

    result = await print_pdf(
        pdf,
        backend=cfg.print_backend,
        printer_name=printer_name or cfg.printer_name,
        sumatra_path=cfg.sumatra_path,
        output_dir=cfg.print_output_dir,
        label=sheet_label,
    )
    log.info(
        "Stations-Zettel gedruckt: student_id=%s backend=%s", student_id, result.get("backend")
    )
    result["code"] = code
    return result


def expired_scan_stations(state: AppState, now: datetime) -> list:
    """Stationen, deren belegter Schüler das 30-s-Leerlauf-TTL gerissen hat
    (rein rechnend, ohne Effekte — testbar wie `expired_student_sessions`).

    Nur Stationen mit `worker_ready` zählen: solange auf einen Worker gewartet
    oder die Kartei geladen wird, läuft die Uhr nicht. Ebenso ausgesetzt,
    solange ein blockierendes Buch-Hinweis-Modal offen ist (`book_alert_open`)
    — die Station wartet dann auf die Host-Freigabe per
    `/api/clear-book-alert`, die die Uhr erst wieder scharf stellt (s. dort).
    """
    return [
        s
        for s in state.scan_stations.values()
        if s.student_id is not None
        and s.worker_ready
        and not s.book_alert_open
        and (now - s.last_activity).total_seconds() > STATION_IDLE_TTL_S
    ]


async def sweep_scan_stations() -> None:
    """Hintergrund-Loop: belegte Scan-Stationen nach 30 s Leerlauf freigeben.

    Sicherheitsnetz zum clientseitigen Timer der Stationsseite — greift auch,
    wenn deren Browser eingefroren ist oder der Tab im Hintergrund schläft.
    """
    hub = _sessions.get_hub()
    while True:
        await asyncio.sleep(_STATION_SWEEP_TICK_S)
        try:
            state = _sessions.get_state()
            expired = expired_scan_stations(state, datetime.now())
            for station in expired:
                await release_station_student(state, station, reason="timeout")
            if expired:
                await hub.broadcast_host(state.state_snapshot())
        except asyncio.CancelledError:
            raise  # Shutdown — Loop sauber beenden.
        except Exception:
            log.exception("Scan-Station-Sweeper fehlgeschlagen (non-fatal)")
            continue
