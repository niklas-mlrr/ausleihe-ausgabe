"""Leihschein-Druck-Orchestrierung (Modus A/B, telefonbasiert).

Ausgelagert aus `sessions.py` (Welle 6, s. dortiges Modul-Docstring). Deckt den
kompletten Weg vom Druckauftrag bis zur Bestätigung ab: `print_loan_slip_for`
holt das PDF (read-only GET) und druckt lokal, `_mark_slip_printed` markiert
den OS-Druckjob als fertig, `confirm_slip_received` schaltet nach der
Schüler-Bestätigung in den Unterschriften- bzw. Abschluss-Modus. Der Zettel der
Scan-Station lebt dagegen in `scan_station_session.py` (eigener Druckweg ohne
IServ-PDF) — nur die kleinen, für beide Zettelarten identischen Dateinamens-
Helfer (`_station_sheet_label`/`_station_sheet_filename`) sind hier mit
untergebracht, weil sie den gleichen sicheren Dateinamens-Baustein
(`_UNSAFE_LABEL_CHARS`) wie `_slip_print_label` teilen.

`get_config`/`get_hub` werden über die Fassade `sessions` aufgelöst
(`_sessions.get_...`) statt direkt aus `.config`/`.hub` importiert — s.
`scan_booking.py`-Docstring für die ausführliche Begründung (Tests patchen
diese Collaborators als `sessions.get_config`/`sessions.get_hub`).

`end_student` (für den Auto-Fertig-Zweig in `_mark_slip_printed`/
`confirm_slip_received`) wird bewusst NUR lokal — UND über die Fassade
`.sessions` statt direkt aus `.session_lifecycle` — importiert:
`session_lifecycle.py` importiert seinerseits (ebenfalls lokal, über dieselbe
Fassade) `assign_student_to_helper` aus `helper_queue.py`, und
`scan_station_session.py` importiert auf Modul-Ebene aus DIESEM Modul
(`_download_slip_to_host`) UND aus `session_lifecycle.py` — ein Import dieses
Moduls auf Modul-Ebene durch `session_lifecycle.py` zurück würde also einen
echten Zyklus schließen. Der Umweg über `.sessions` bricht ihn UND bleibt
korrekt, unabhängig davon, ob `end_student` gerade noch lokal in
`sessions.py` steht oder schon nach `session_lifecycle.py` ausgelagert ist.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from datetime import datetime

from . import sessions as _sessions
from .state import AppState, StudentSessionB

log = logging.getLogger(__name__)

# Starke Referenzen auf in-flight Schülerleihschein-Prefetch-Tasks (asyncio
# hält Tasks selbst nur schwach) — auch von `scan_station_session.py`
# mitgenutzt (dortiger `_on_wait`-Fortschritts-Push beim Warten auf einen
# Worker-Context), daher hier zentral statt je Modul dupliziert.
_prefetch_tasks: set[asyncio.Task] = set()


def invalidate_slip_after_scan(state: AppState, student_id: int) -> None:
    """Make an already started/finished slip stale after a new book scan.

    The old print job is not cancelled because it may already be in the OS
    queue. It is marked stale in ``PrintQueue`` and therefore no longer drives
    queue/status UI or the ``slip_printed`` marker. The next print request is a
    fresh job for the now-current book set.
    """
    print_queue = getattr(state, "print_queue", None)
    if print_queue is not None:
        print_queue.invalidate_student(student_id)
    student = state.find_student(student_id)
    if student is None:
        return
    student.slip_printed = False
    student.slip_printer = None
    student.slip_printer_label = None
    student.slip_signing = False
    student.slip_generation += 1
    session = state.find_session_by_student(student_id)
    if session is not None:
        session.loan_slip_mode = False
        session.loan_slip_recipient = None
        session.slip_receipt_confirmed = False


def _student_form(state: AppState, student_id: int) -> str | None:
    """Echte Klasse des Schülers ermitteln — aus seinem Queue-Eintrag (sucht
    über alle Klassen-Kontexte, da der Schüler in genau einem lebt), sonst
    die aktive Klasse. Für den Leihschein-Klassen-Toggle. Gibt None zurück,
    wenn nichts bekannt ist (dann bleibt der Leihschein unverändert)."""
    s = state.find_student(student_id)
    if s and s.form:
        return s.form
    ctx = state.active_context
    return ctx.form if ctx and ctx.form else None


_UNSAFE_LABEL_CHARS = re.compile(r'[<>:"/\\|?*\s]+')


def _slip_print_label(state: AppState, student_id: int) -> str:
    """Druckjob-/Dateinamen für den Leihschein bauen: `Leihschein_<Klasse>_
    <Nachname>_<Vorname>`. `print_pdf` verwendet `label` als Präfix der
    Temp-PDF, deren Dateiname lp/SumatraPDF unverändert als Job-/Dokumentname
    an die OS-Druckerwarteschlange durchreichen (s. printing.py) — damit ein
    Leihschein dort im Fehlerfall erkennbar ist, statt nur an der student_id.
    Ohne bekannte Schülerdaten (Klasse/Name fehlen) Fallback auf die bisherige
    `leihschein_<student_id>`-Form. Greift defensiv über `getattr` auf
    Klasse/Name zu (statt `_student_form`), damit Test-Doubles ohne diese
    Attribute nicht crashen — anders als der Klassen-Korrektur-Toggle läuft
    dieser Label-Aufbau unbedingt bei jedem Druck."""
    student = state.find_student(student_id)
    form = getattr(student, "form", None) if student else None
    if not form:
        ctx = getattr(state, "active_context", None)
        form = getattr(ctx, "form", None) if ctx else None
    lastname = getattr(student, "lastname", None) if student else None
    firstname = getattr(student, "firstname", None) if student else None
    safe_parts = [
        _UNSAFE_LABEL_CHARS.sub("_", p).strip("_") for p in (form, lastname, firstname) if p
    ]
    if not safe_parts:
        return f"leihschein_{student_id}"
    return "Leihschein_" + "_".join(safe_parts)


def _station_sheet_label(state: AppState, student_id: int, form: str | None) -> str:
    """Analog zu `_slip_print_label`, für den Scan-Stations-Zettel: `Scanstation_
    <Klasse>_<Nachname>_<Vorname>` als Druckjob-/Dateiname (file-Backend/Temp-PDF).
    Fallback auf `scan_station_<student_id>` ohne bekannte Schülerdaten."""
    student = state.find_student(student_id)
    lastname = getattr(student, "lastname", None) if student else None
    firstname = getattr(student, "firstname", None) if student else None
    safe_parts = [
        _UNSAFE_LABEL_CHARS.sub("_", p).strip("_") for p in (form, lastname, firstname) if p
    ]
    if not safe_parts:
        return f"scan_station_{student_id}"
    return "Scanstation_" + "_".join(safe_parts)


def slip_trigger_for(state: AppState, student_id: int) -> str:
    """`slip_trigger` der Klasse eines Schülers — bestimmt den Druckmodus am
    Schülerclient (Modus B), sobald alle vorgemerkten Bücher gescannt sind.
    Sucht den besitzenden Klassen-Kontext via `find_student_with_ctx`. Ohne
    Kontext → ``"auto"`` (Default). Rein lesend."""
    found = state.find_student_with_ctx(student_id)
    if found is None:
        return "auto"
    ctx, _s = found
    return ctx.slip_trigger


def slip_signature_options_for(state: AppState, student_id: int) -> tuple[bool, bool]:
    """Leihschein-Workflow der Klasse eines Schülers.

    Liefert `(done_signed, done_collected)`. Ohne Klassenkontext oder bei
    alten/inkonsistenten Zuständen gilt der normale Abschluss nach dem Druck.
    `done_collected` wird nur als Lehrer-Ziel verwendet, wenn auch
    `done_signed` aktiv ist.
    """
    found = state.find_student_with_ctx(student_id)
    if found is None:
        return False, False
    ctx, _s = found
    return ctx.done_signed, ctx.done_collected if ctx.done_signed else False


async def _missing_stock_subjects_for(state: AppState, student_id: int) -> list[str]:
    """Fächer der Buchreihen, die für diesen Schüler vorgemerkt, aber noch
    NICHT ausgeliehen sind UND als „Bestand leer" markiert sind — für den
    Fehlt-Hinweis auf Seite 1 des Leihscheins (s. loan_slip.overlay_missing_stock_note).
    Reihenfolge wie in `info["books"]`, Dubletten entfernt. Rein lesend gegen
    IServ; Fehler liefern eine leere Liste (Druck darf nie daran scheitern)."""
    caches = getattr(state, "caches", None)
    if caches is None or not caches.empty_isbns or state.iserv is None:
        return []
    try:
        info = await state.iserv.get_student_info(student_id, state.selected_schoolyear)
    except Exception:  # noqa: BLE001 — Hinweis ist Kosmetik, Druck nie blockieren
        log.debug(
            "Bestand-leer-Fächer für Leihschein: Schülerinfo fehlgeschlagen (student_id=%s)",
            student_id,
            exc_info=True,
        )
        return []
    subjects: list[str] = []
    seen: set[str] = set()
    for b in info.get("books", []):
        isbn = b.get("isbn")
        if not isbn or isbn not in state.caches.empty_isbns or b.get("status") == "ausgeliehen":
            continue
        subject = (b.get("subject") or "").strip()
        if subject and subject not in seen:
            seen.add(subject)
            subjects.append(subject)
    return subjects


async def print_loan_slip_for(
    state: AppState,
    student_id: int,
    *,
    variant: str = "student-always_school-auto",
    pages: str | None = "1",
    printer_name: str | None = None,
) -> dict:
    """Leihschein eines Schülers holen (read-only GET) und lokal drucken.

    Geholt wird stets der 2-seitige Beleg (Seite 1 = immer gedruckt, Seite 2 =
    Schüler-Leihschein). `pages` wählt den zu druckenden Bereich: ``"1"`` nur die
    erste Seite (Default), ``None`` beide Seiten. `printer_name` = der Drucker,
    an den dieser Auftrag geht (vom Pool-Scheduler zugewiesen); ``None`` = der
    Standarddrucker des Geräts (``cfg.printer_name`` bzw. OS-Default).

    Gemeinsame Orchestrierung für den Host-Endpoint (`/api/print-loan-slip`)
    und den Scanner (WS `print`). Kein Schreibzugriff auf IServ — `get_loan_slip_pdf`
    ist ein reiner GET, das Drucken passiert lokal am Laptop/Macbook
    (siehe server/printing.py).

    Gibt `{ok, backend, detail, [path]}` zurück oder wirft bei Fehlern eine
    Exception (vom Aufrufer in eine Client-Antwort zu wandeln).
    """
    from .printing import print_pdf

    if state.iserv is None:
        # Passiert, wenn noch keine Klasse/Ausgabe aktiv war (der IServ-Client
        # wird erst dabei gesetzt) — ohne diesen Guard würde ein AttributeError
        # auf `None.get_loan_slip_pdf` geworfen (Aufrufer fängt es zwar generisch
        # ab, aber mit einer unklaren Meldung statt eines aussagekräftigen Texts).
        raise RuntimeError("Kein IServ-Client verfügbar — bitte zuerst eine Klasse laden")

    cfg = _sessions.get_config()
    pdf = await state.iserv.get_loan_slip_pdf(student_id, variant=variant)
    # Schülerleihschein (Eigenabruf) im Hintergrund vorladen, damit der
    # Übergang zu „abgeschlossen" (Schülerleihscheinmodus) nicht auf einen
    # IServ-Fetch wartet. Fire-and-forget; Fehler fallen auf den Frisch-Fetch
    # in `_send_own_slip_download` zurück. Nur Modus B (Session vorhanden)
    # relevant — Modus-A-Drucke haben keine Session und tun nichts.
    _prefetch_own_slip_task(state, student_id)
    # Experimenteller Toggle „Klasse auf Leihschein korrigieren": den (teils
    # falschen) Klassen-Code auf dem IServ-PDF lokal durch die echte Klasse des
    # Schülers ersetzen. Rein lokale PDF-Bearbeitung, kein IServ-Write.
    if getattr(state.settings, "fix_class_on_slip", False):
        form = _student_form(state, student_id)
        if form:
            from .loan_slip import override_class_on_slip

            pdf = await asyncio.to_thread(override_class_on_slip, pdf, form)
        else:
            log.warning(
                "Klasse-Korrektur aktiv, aber keine Klasse für student_id=%s "
                "ermittelbar — Leihschein wird unverändert gedruckt",
                student_id,
            )

    # „Bestand leer": Fächer vorgemerkter, noch nicht ausgeliehener Reihen mit
    # leerem Bestand — Hinweiszeile auf Seite 1 (Schul-Kopie), s.
    # loan_slip.overlay_missing_stock_note. Rein lokale PDF-Bearbeitung.
    missing_subjects = await _missing_stock_subjects_for(state, student_id)
    if missing_subjects:
        from .loan_slip import overlay_missing_stock_note

        pdf = await asyncio.to_thread(overlay_missing_stock_note, pdf, missing_subjects)

    # Entwickler-Toggle „PDF lokal speichern": nicht drucken, sondern das PDF in
    # den Browser des Host-Rechners herunterladen (Download-Prompt) — die
    # Anzeige/Weiterverarbeitung passiert dort lokal, kein IServ-Write.
    if state.settings.save_pdf_locally:
        delivered = await _download_slip_to_host(state, student_id, pdf, pages=pages)
        if delivered:
            log.info(
                "Leihschein an %d Host-Browser gesendet: student_id=%s pages=%s",
                delivered,
                student_id,
                pages or "alle",
            )
            return {
                "ok": True,
                "backend": "download",
                "detail": f"an {delivered} Host-Browser gesendet",
            }
        # Kein Host-Browser verbunden → Download unmöglich. Als Sicherheitsnetz
        # ins Ausgabeverzeichnis schreiben, damit der Leihschein nicht verloren geht.
        log.warning(
            "PDF-lokal aktiv, aber kein Host-Browser verbunden — student_id=%s "
            "wird ins Ausgabeverzeichnis gespeichert",
            student_id,
        )
        result = await print_pdf(
            pdf,
            backend="file",
            output_dir=cfg.print_output_dir,
            label=_slip_print_label(state, student_id),
            pages=pages,
        )
        result["detail"] = "kein Host-Browser verbunden — " + result.get("detail", "")
        return result

    result = await print_pdf(
        pdf,
        backend=cfg.print_backend,
        printer_name=printer_name or cfg.printer_name,
        sumatra_path=cfg.sumatra_path,
        output_dir=cfg.print_output_dir,
        label=_slip_print_label(state, student_id),
        pages=pages,
    )
    log.info(
        "Leihschein gedruckt: student_id=%s backend=%s pages=%s",
        student_id,
        result.get("backend"),
        pages or "alle",
    )
    return result


async def _mark_slip_printed(
    state: AppState,
    student_id: int,
    *,
    printer: str | None = None,
    printer_label: str | None = None,
) -> None:
    """Queue-Eintrag nach abgeschlossenem Druck als „Leihschein gedruckt"
    markieren und die Hosts aktualisieren — daraus wird im Host-Client das
    Badge „Leihschein" (zwischen „Aktiv" und „Fertig"). Kein Queue-Eintrag
    (transienter Lupe-Schüler) → still nichts tun.

    Diese Funktion wird ausschließlich nach der Druckerwarteschlange aufgerufen,
    wenn der OS-Druckauftrag abgeschlossen ist. Das bloße Absenden an den
    Drucker darf den Modus-B-Schüler noch nicht abschließen — der eigentliche
    Wechsel (Unterschriften-Modus bzw. Auto-Fertig) passiert erst in
    `confirm_slip_received`, sobald der Schülerclient „Leihschein erhalten"
    bestätigt. So zeigt ein Reload des Schülerclients zwischen physischem
    Druckende und dieser Bestätigung weiterhin den Druckmodus (mit dem
    Button) statt fälschlich schon den nächsten Schritt.

    `printer`/`printer_label` (vom aufrufenden `PrintQueue._mark_slip_printed_
    after_completion`) werden auf dem Schüler gemerkt (statt nur im
    transienten `print_result`-Frame), damit ein Reload zwischen Druckende und
    Bestätigung weiterhin „Leihschein von X gedruckt." statt nur „Leihschein
    gedruckt." zeigen kann (s. `worker_ready`-Felder `slip_printer*`).
    """
    student = state.find_student(student_id)
    if student is None or student.slip_printed:
        return
    student.slip_printed = True
    student.slip_printer = printer
    student.slip_printer_label = printer_label
    # Scan-Station-Schüler haben keine Modus-B-Session und damit keinen Weg,
    # „Leihschein erhalten" zu bestätigen (kein eigener Client dafür) — der
    # gedruckte Zettel selbst ist der Nachweis. Anders als beim Phone-Zweig
    # oben (der bewusst auf die Bestätigung wartet) wird ihr Status deshalb
    # HIER direkt weitergeschaltet, sobald der Leihschein physisch fertig
    # ist: in den Unterschriften-Modus (Klasse mit `done_signed`) oder gleich
    # auf „abgeschlossen" — Mirror des Modus-B-Zweigs in
    # `confirm_slip_received`, nur ohne auf eine Bestätigung zu warten, die
    # nie kommt. Erkennung über einen vergebenen Zettel-Code (überlebt auch
    # ein zwischenzeitliches Trennen von der Station); greift unabhängig vom
    # `slip_trigger`, der den Druck ausgelöst hat (Automatisch/Betreuer-/
    # Schülerauslöser über den Drucker-Scanner).
    if student_id in state.station_code_by_student:
        done_signed, _done_collected = slip_signature_options_for(state, student_id)
        if done_signed:
            student.slip_signing = True
        else:
            try:
                # Lokaler Import über die Fassade bricht den Zyklus
                # loan_slip_flow <-> session_lifecycle (s. Modul-Docstring oben).
                from .sessions import end_student

                await end_student(
                    state, _sessions.get_hub(), student_id,
                    queue_status="done", session_state="completed",
                )
            except Exception:  # noqa: BLE001 — Auto-Fertig darf den Druck-Marker nicht widerrufen
                log.debug(
                    "Auto-Fertig (Scan-Station) nach Leihschein-Druck fehlgeschlagen",
                    exc_info=True,
                )
            return
    try:
        await _sessions.get_hub().broadcast_host(state.state_snapshot())
    except Exception:  # noqa: BLE001 — Druck darf an einem Broadcast nicht scheitern
        log.debug("Host-Broadcast nach Leihschein-Druck fehlgeschlagen", exc_info=True)


async def confirm_slip_received(state: AppState, student_id: int) -> None:
    """Schülerclient bestätigt „Leihschein erhalten" (WS `slip_received`,
    nach `_mark_slip_printed`). Erst hier — nicht schon beim physischen
    Druckende — wechselt der Modus-B-Schüler in den Unterschriften-Modus
    (Klasse mit `done_signed`) bzw. schließt automatisch ab. Kein Effekt, wenn
    noch nicht gedruckt, keine passende Session (mehr) existiert, oder bereits
    bestätigt wurde (Idempotenz, s. `loan_slip_received`/`slip_receipt_confirmed`).

    `done_collected` wählt dabei nur den angezeigten Empfänger (Betreuer oder
    Lehrer) und wird nicht als Schüler-/Lehrkraftname an den Client gegeben.
    """
    student = state.find_student(student_id)
    if student is None or not student.slip_printed:
        return
    session = state.find_session_by_student(student_id)
    if session is None or session.state != "paired" or session.slip_receipt_confirmed:
        return
    session.slip_receipt_confirmed = True
    done_signed, done_collected = slip_signature_options_for(state, student_id)
    if done_signed:
        session.loan_slip_mode = True
        session.loan_slip_recipient = "teacher" if done_collected else "helper"
        student.slip_signing = True
        if session.ws is not None:
            await _sessions.get_hub().send_websocket(
                session.ws,
                {
                    "type": "slip_mode",
                    "recipient": session.loan_slip_recipient,
                },
            )
        # Host-Queue live nachziehen: der Status wechselt erst hier auf
        # „Unterschrift" (s. host-render.js) — ohne Broadcast bliebe er bis
        # zum nächsten Seiten-Reload auf „Leihschein gedruckt".
        await _sessions.get_hub().broadcast_host(state.state_snapshot())
        # Helfer-Client (scan.html) live nachziehen: der „Leihschein
        # unterschreiben"-Button in der Klassenliste (s.
        # real_contexts_summary) muss erscheinen, sobald der Schüler in
        # den Unterschriften-Modus gewechselt ist. Ohne verbundene Helfer
        # entfällt der Aufwand komplett.
        if state.helper_sessions:
            try:
                await _sessions.get_hub().broadcast_queue_size(state)
            except Exception:  # noqa: BLE001 — analog Host-Broadcast oben
                log.debug(
                    "Helfer-Broadcast nach Leihschein-Bestätigung fehlgeschlagen", exc_info=True
                )
        return
    # Ohne Unterschriftenmodus geht der Modus-B-Schüler nach der Bestätigung
    # automatisch auf „abgeschlossen" — unabhängig davon, wer den Druck
    # ausgelöst hat. Modus-A-Schüler haben keine Modus-B-Session und
    # bleiben unberührt (Host beendet sie wie bisher).
    try:
        # Lokaler Import über die Fassade: gleicher Zyklus-Bruch wie in
        # _mark_slip_printed oben.
        from .sessions import end_student

        await end_student(
            state, _sessions.get_hub(), student_id,
            queue_status="done", session_state="completed",
        )
    except Exception:  # noqa: BLE001 — Auto-Fertig darf die Bestätigung nicht widerrufen
        log.debug("Auto-Fertig (Modus B) nach Leihschein-Bestätigung fehlgeschlagen", exc_info=True)


async def _download_slip_to_host(
    state: AppState, student_id: int, pdf: bytes, *, pages: str | None, filename: str | None = None
) -> int:
    """Leihschein-PDF an alle verbundenen Host-Browser zum Download pushen.

    Beschränkt das PDF auf denselben Seitenbereich, der sonst gedruckt würde,
    und schickt es base64-kodiert über die Host-WebSocket. Gibt die Anzahl der
    erreichten Host-Browser zurück (0 = keiner verbunden).

    `filename`: Download-Dateiname; ohne Angabe der bisherige generische
    Leihschein-Name (Fallback für Aufrufer ohne Klasse-/Namens-Kontext)."""
    from .loan_slip import select_pages

    pdf = await asyncio.to_thread(select_pages, pdf, pages)
    if filename is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"leihschein_{student_id}_{ts}.pdf"
    msg = {
        "type": "loan_slip_download",
        "filename": filename,
        "size": len(pdf),
        "data_b64": base64.b64encode(pdf).decode("ascii"),
    }

    return await _sessions.get_hub().send_all_hosts(msg, state)


async def _prefetch_own_slip(state: AppState, student_id: int) -> None:
    """Schülerleihschein (Eigenabruf, Aktionen der letzten 3 Monate) im
    Hintergrund holen und auf der Modus-B-Session cachen, damit der Übergang
    zu „abgeschlossen" (Schülerleihscheinmodus) nicht auf einen IServ-Fetch
    wartet. Rein lesend; Fehler sind kosmetisch — der Abschluss fällt dann
    auf den Frisch-Fetch in `_send_own_slip_download` zurück."""
    session = state.find_session_by_student(student_id)
    if session is None or session.own_slip_data_b64 is not None or state.iserv is None:
        return
    try:
        pdf = await state.iserv.get_loan_slip_pdf(
            student_id, variant="student", start_reporting_period="3months"
        )
    except Exception:  # noqa: BLE001 — Prefetch ist Kosmetik, nie fatal
        log.debug(
            "Schülerleihschein-Prefetch fehlgeschlagen (student_id=%s)",
            student_id,
            exc_info=True,
        )
        return
    student = state.find_student(student_id)
    lastname = (student.lastname if student else "").strip()
    firstname = (student.firstname if student else "").strip()
    form = _student_form(state, student_id)
    session.own_slip_data_b64 = base64.b64encode(pdf).decode("ascii")
    session.own_slip_filename = _own_slip_filename(lastname, firstname, form)


def _station_sheet_filename(lastname: str | None, firstname: str | None, form: str | None) -> str:
    """Dateiname für den Scan-Stations-Zettel (Download bzw. `save_pdf_locally`):
    `Scanstation <Klasse> <Nachname>, <Vorname>` — Klasse vorneweg (wie auf dem
    Zettel selbst), analog zu `_own_slip_filename`. Fehlende Teile fallen
    einfach weg, statt eine leere Lücke zu hinterlassen."""
    form_clean = (form or "").removeprefix("Klasse ").strip()
    name_part = ", ".join(p for p in ((lastname or "").strip(), (firstname or "").strip()) if p)
    parts = [p for p in (form_clean, name_part) if p]
    return "Scanstation " + " ".join(parts) + ".pdf" if parts else "Scanstation.pdf"


def _own_slip_filename(lastname: str, firstname: str, form: str | None) -> str:
    """Dateiname für den Download-Button des eigenen (Schüler-)Leihscheins:
    `Schülerleihschein <Nachname>, <Vorname>, <Klasse>` — Klasse zusätzlich zu
    Name/Vorname, damit der Download im Browser/Downloads-Ordner eindeutig
    zuordenbar ist. Ohne bekannte Klasse fällt der Klassen-Teil einfach weg."""
    name_part = f"{lastname}, {firstname}"
    if form:
        name_part += f", {form}"
    return f"Schülerleihschein {name_part}.pdf"


def _prefetch_own_slip_task(state: AppState, student_id: int) -> None:
    """Prefetch als Fire-and-forget-Task starten (starke Referenz halten —
    asyncio hält Tasks nur schwach, ein unreferenzierter Task kann mid-Coroutine
    GC't werden)."""
    t = asyncio.create_task(_prefetch_own_slip(state, student_id))
    _prefetch_tasks.add(t)
    t.add_done_callback(_prefetch_tasks.discard)


async def _send_own_slip_download(state: AppState, ws, session: StudentSessionB) -> None:
    """Eigenen Leihschein (Aktionen der letzten 3 Monate) einmalig an den
    Schülerclient pushen, kurz bevor dessen Modus-B-Session regulär endet
    (Schülerleihscheinmodus — der Abschluss-Screen mit Download-Button, s.
    `invalidate_session`). Rein lesend gegen IServ; Fehler (kein IServ-Client,
    Netzproblem) dürfen den Abschluss der Session nicht verzögern/verhindern
    — der Client zeigt dann einfach keinen funktionierenden Button.

    Bevorzugt wird der beim Leihschein-Druck vorab geladene Cache
    (`_prefetch_own_slip`) — so ist der Übergang zu „abgeschlossen"
    verzögerungsfrei. Fehlt er (Prefetch noch nicht fertig / fehlgeschlagen),
    wird frisch geholt."""
    if state.iserv is None:
        return
    if session.own_slip_data_b64 is not None:
        await _sessions.get_hub().send_websocket(
            ws,
            {
                "type": "own_slip_download",
                "filename": session.own_slip_filename,
                "data_b64": session.own_slip_data_b64,
            },
        )
        return
    student_id = session.student_id
    try:
        pdf = await state.iserv.get_loan_slip_pdf(
            student_id, variant="student", start_reporting_period="3months"
        )
        info = await state.iserv.get_student_info(student_id, state.selected_schoolyear)
    except Exception:  # noqa: BLE001 — Download ist Kosmetik, Abschluss nie blockieren
        log.debug(
            "Eigener-Leihschein-Download vor Session-Ende fehlgeschlagen (student_id=%s)",
            student_id,
            exc_info=True,
        )
        return
    lastname = (info.get("lastname") or "").strip()
    firstname = (info.get("firstname") or "").strip()
    form = _student_form(state, student_id)
    filename = _own_slip_filename(lastname, firstname, form)
    await _sessions.get_hub().send_websocket(
        ws,
        {
            "type": "own_slip_download",
            "filename": filename,
            "data_b64": base64.b64encode(pdf).decode("ascii"),
        },
    )
