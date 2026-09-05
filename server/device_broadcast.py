"""Broadcast-/Autorisierungslogik für angeschlossene Zusatzgeräte.

Ausgelagert aus `sessions.py` (Welle 6, s. dortiges Modul-Docstring). Vier
Geräte-/Ansichtsfamilien teilen hier dasselbe Muster (Registrierungs-Code vor
der Freischaltung, danach ein gefilterter Zustands-Push, `send_websocket`
liefert `False` statt zu werfen, Cleanup dediziert am Rückgabewert): das
iPad-Display (`/display`, Modus-B-QR), das Drucker-Display samt zugehörigem
Drucker-Scanner (`/drucker-display`), und die Lehrkraft-Statusansicht
(`/teacher`). Die Drucker-Eligibility-Funktionen (`allowed_printers_for` &
Co.) stehen mit im selben Modul, weil sie exakt die Datengrundlage berechnen,
die `send_printer_display_update`/`eligible_drucker_scanners_for` unten
weiterreichen.

`get_hub` wird über die Fassade `sessions` aufgelöst (`_sessions.get_hub()`)
statt direkt aus `.hub` importiert — s. `scan_booking.py`-Docstring für die
ausführliche Begründung (Tests patchen `sessions.get_hub`).
"""

from __future__ import annotations

from . import sessions as _sessions
from .state import AppState, DisplaySession, TeacherSession


def allowed_printers_for(state: AppState, student_id: int) -> set[str] | None:
    """Drucker-Allowlist der Klasse eines Schülers — für den Druckauftrag
    (`PrintJob.allowed_printers`). Sucht den besitzenden Klassen-Kontext via
    `find_student_with_ctx` (Schüler lebt in genau einem). `None` = kein Filter
    (alle Pool-Drucker erlaubt, Default); eine Menge beschränkt auf diese IDs.
    Ohne Kontext (Schüler in keiner Klasse-Queue) → `None` (alle). Rein lesend."""
    found = state.find_student_with_ctx(student_id)
    if found is None:
        return None
    ctx, _s = found
    # None bleibt None (alle); kopieren, damit der Snapshot im Job stabil ist,
    # auch wenn die Klasse nach dem Enqueue umkonfiguriert wird.
    return None if ctx.allowed_printer_ids is None else set(ctx.allowed_printer_ids)


def displayed_printer_ids(state: AppState) -> set[str]:
    """Pool-Drucker-IDs, die gerade auf mindestens einem angemeldeten
    Drucker-Display (authorisiert + per WS verbunden) sichtbar sind. Für ein
    Display ohne explizite Zuweisung (`assigned_printer_ids is None`) zählen
    alle aktuellen Pool-Drucker als sichtbar. Grundlage für das
    Scan-Station-Druckermodus-Gate (`PrintJob.station_display_gate`) — ein
    solcher Auftrag darf nur auf einem Drucker aus dieser Menge gedruckt
    werden. Rein lesend, kein Lock nötig (Anzeige-Konsistenz reicht, analog
    `PrintQueue.pool_printers`)."""
    all_pool_ids = {p.id for p in state.settings.printers}
    shown: set[str] = set()
    for d in state.printer_displays.values():
        if not d.authorized or d.ws is None:
            continue
        if d.assigned_printer_ids is None:
            shown |= all_pool_ids
        else:
            shown |= set(d.assigned_printer_ids) & all_pool_ids
    return shown


def relevant_display_count(state: AppState, allowed: set[str] | None) -> int:
    """Anzahl der aktuell angemeldeten, verbundenen Drucker-Displays, die
    mindestens einen für `allowed` erlaubten Drucker zeigen (`allowed=None` =
    jeder gezeigte Drucker zählt als erlaubt). Für den Scan-Station-
    Druckermodus-Hinweis "Bitte achte auf die Druckeranzeige(n)" — Singular
    bei genau einem in Frage kommenden Display, sonst Plural."""
    all_pool_ids = {p.id for p in state.settings.printers}
    count = 0
    for d in state.printer_displays.values():
        if not d.authorized or d.ws is None:
            continue
        shown = all_pool_ids if d.assigned_printer_ids is None else (
            set(d.assigned_printer_ids) & all_pool_ids
        )
        if not shown:
            continue
        if allowed is None or (shown & allowed):
            count += 1
    return count


def eligible_drucker_scanners_for(state: AppState, student_id: int) -> list[dict]:
    """Drucker-Scanner, an denen dieser Schüler gerade seinen Leihschein-Druck
    selbst auslösen könnte: autorisierte, verbundene Scanner, die einem
    autorisierten, verbundenen Drucker-Display zugeordnet sind, das seinerseits
    mindestens einen für die Klasse dieses Schülers erlaubten Drucker zeigt
    (dieselbe „shown"-Berechnung wie in `displayed_printer_ids`, hier pro
    Display statt aggregiert). Liefert `[{"scanner_id", "label"}, ...]`,
    dedupliziert über mehrere qualifizierende Displays. Rein lesend."""
    allowed = allowed_printers_for(state, student_id)
    all_pool_ids = {p.id for p in state.settings.printers}
    seen: set[str] = set()
    out: list[dict] = []
    for d in state.printer_displays.values():
        if not d.authorized or d.ws is None:
            continue
        shown = all_pool_ids if d.assigned_printer_ids is None else (
            set(d.assigned_printer_ids) & all_pool_ids
        )
        if not shown or (allowed is not None and not (shown & allowed)):
            continue
        scanner_ids = (
            list(state.printer_scanners.keys())
            if d.assigned_scanner_ids is None
            else d.assigned_scanner_ids
        )
        for sid in scanner_ids:
            if sid in seen:
                continue
            scanner = state.printer_scanners.get(sid)
            if scanner is None or not scanner.authorized or scanner.ws is None:
                continue
            seen.add(sid)
            out.append({"scanner_id": scanner.scanner_id, "label": scanner.label})
    return out


async def send_display_update(state: AppState, display: DisplaySession) -> None:
    """Aktuellen Zustand an ein Display schicken: Reg-Code, QR oder 'geschlossen'."""
    if display.ws is None:
        return
    if not display.authorized:
        msg = {
            "type": "registration",
            "code": display.registration_code,
            "display_id": display.display_id,
        }
    elif state.modus_b_open and state.modus_b_join_qr and state.modus_b_paused:
        msg = {"type": "paused"}
    elif state.modus_b_open and state.modus_b_join_qr:
        msg = {"type": "qr", "qr": state.modus_b_join_qr, "url": state.modus_b_join_url}
    else:
        msg = {"type": "closed"}
    # `send_websocket` gibt False statt zu werfen — Cleanup dediziert am
    # Rückgabewert, nicht mehr per except (s. Wave-2-Konvertierung).
    if not await _sessions.get_hub().send_websocket(display.ws, msg):
        display.ws = None


async def broadcast_displays(state: AppState) -> None:
    for display in list(state.displays.values()):
        await send_display_update(state, display)


# ---------------------------------------------------------------------------
# Drucker-Display (`/drucker-display`)
# ---------------------------------------------------------------------------


async def send_printer_display_update(state: AppState, display) -> None:
    """Aktuellen Zustand an ein Drucker-Display schicken. Nicht authorisiert →
    Registrierungs-Code (große Nummer), damit der Host das Display zuordnen
    kann. Authorisiert → gefilterte Queue-Sicht (zugewiesene Drucker + relevante
    Warteschlange, s. ``AppState.printer_display_view``). ``send_websocket``
    liefert False statt zu werfen — Cleanup dediziert am Rückgabewert."""
    if display.ws is None:
        return
    if not display.authorized:
        msg = {
            "type": "registration",
            "code": display.registration_code,
            "display_id": display.display_id,
            "label": display.label,
        }
    else:
        msg = {
            "type": "queue",
            "label": display.label,
            **state.printer_display_view(display),
        }
    # Theme nur mitgeben, wenn der Host es explizit gesetzt hat (sonst None =
    # Display folgt der System-Einstellung). Bei nicht autorisierten Displays
    # ist theme ohnehin noch None.
    if display.theme is not None:
        msg["theme"] = display.theme
    if not await _sessions.get_hub().send_websocket(display.ws, msg):
        display.ws = None


async def broadcast_printer_displays(state: AppState) -> None:
    """Jedem verbundenen Drucker-Display seine aktuelle Queue-Sicht pushen
    (s. ``send_printer_display_update``). Aufgerufen bei Druck-Übergängen
    (``print_queue._notify_all``) und Pool-Mutationen (``_after_pool_change``)."""
    for display in list(state.printer_displays.values()):
        await send_printer_display_update(state, display)


async def send_printer_scanner_update(state: AppState, scanner) -> None:
    """Gerätezustand an einen Drucker-Scanner schicken: vor der Freischaltung
    nur den Registrierungs-Code, danach Name/Theme/Eingabeart. Der Scanner
    selbst zeigt kein Scan-Ergebnis — das läuft ausschließlich über die
    Drucker-Display(s), denen er zugewiesen ist (s. ``broadcast_scanner_result``)."""
    if scanner.ws is None:
        return
    if not scanner.authorized:
        msg = {
            "type": "registration",
            "code": scanner.registration_code,
            "scanner_id": scanner.scanner_id,
            "theme": scanner.theme,
            "input_mode": scanner.input_mode,
        }
    else:
        msg = {
            "type": "ready",
            "label": scanner.label,
            "theme": scanner.theme,
            "input_mode": scanner.input_mode,
        }
    if not await _sessions.get_hub().send_websocket(scanner.ws, msg):
        scanner.ws = None


async def broadcast_printer_scanners(state: AppState) -> None:
    """Gerätezustand an alle Drucker-Scanner pushen (Theme/Name/Eingabeart/
    Freischaltung)."""
    for scanner in list(state.printer_scanners.values()):
        await send_printer_scanner_update(state, scanner)


async def broadcast_scanner_result(state: AppState, scanner) -> None:
    """Nach jeder Scan-Auswertung (s. ``routes/ws.py::ws_drucker_scan``) an
    ALLE Drucker-Displays pushen, denen dieser Scanner zugewiesen ist — das
    Ergebnis lebt auf der Scanner-Session, gerendert wird es aber auf der/den
    Display-Seite(n) (s. ``AppState._scanners_for_display``)."""
    for display in state.printer_displays.values():
        ids = display.assigned_scanner_ids
        if ids is None or scanner.scanner_id in ids:
            await send_printer_display_update(state, display)


# ---------------------------------------------------------------------------
# Lehrkraft-Statusansicht (`/teacher`)
# ---------------------------------------------------------------------------


async def send_teacher_update(state: AppState, session: TeacherSession) -> None:
    """Aktuellen Zustand an eine Lehrer-Session schicken. Nicht autorisiert →
    nur der Registrierungscode (keine Klassen-/Schülerdaten). Autorisiert →
    der minimierte, klassenscharfe `teacher_snapshot` (NIE `state_snapshot()`).
    `send_websocket` liefert False statt zu werfen — Cleanup dediziert am
    Rückgabewert (wie bei den Drucker-Displays)."""
    if session.ws is None:
        return
    if not session.authorized:
        msg = {"type": "registration", "code": session.registration_code}
    else:
        msg = {"type": "teacher_state", **state.teacher_snapshot(session.context_id)}
    if not await _sessions.get_hub().send_websocket(session.ws, msg):
        session.ws = None


async def broadcast_teacher_sessions(state: AppState) -> None:
    """Jeder verbundenen Lehrer-Session ihren aktuellen (klassenscharfen)
    Zustand pushen. Aufgerufen von `Hub.broadcast_host` bei JEDER
    Zustandsänderung — analog `Hub.broadcast_queue_size` für Helfer."""
    for session in list(state.teacher_sessions.values()):
        await send_teacher_update(state, session)


async def revoke_teacher_session(state: AppState, session: TeacherSession, *, reason: str) -> None:
    """Eine Lehrer-Session hart entwerten: aus `state.teacher_sessions`
    entfernen und ihre WebSocket schließen. Der Token ist lang & zufällig und
    wird nie wiederverwendet — anders als bei den Drucker-Displays braucht es
    keine Bannliste, ein einfaches „Token unbekannt" beim nächsten Verbindungs-
    versuch (s. routes/ws.py::ws_teacher) reicht, um einen Reconnect zuverlässig
    abzuweisen (PLAN: „Ein Reload kann den Zugang nicht wiederherstellen")."""
    state.teacher_sessions.pop(session.token, None)
    ws = session.ws
    session.ws = None
    if ws is not None:
        try:
            await _sessions.get_hub().send_websocket(ws, {"type": "forbidden"})
            await ws.close(code=4009, reason=reason)
        except Exception:  # noqa: BLE001 — Schließen darf den Aufrufer nicht crashen
            pass


async def revoke_teacher_sessions_for_context(
    state: AppState, context_id: str, *, reason: str
) -> None:
    """Alle Lehrer-Sessions einer Klasse entwerten (Klasse schließen/Tab ×).
    In der Praxis höchstens eine (s. `AppState.teacher_session_for_context`),
    aber defensiv über eine Liste statt einer Annahme."""
    for session in [s for s in state.teacher_sessions.values() if s.context_id == context_id]:
        await revoke_teacher_session(state, session, reason=reason)


async def revoke_all_teacher_sessions(state: AppState, *, reason: str) -> None:
    """Alle Lehrer-Sessions entwerten (Schuljahreswechsel — alle Kontexte
    fallen weg, s. routes/classes.py::select_schoolyear)."""
    for session in list(state.teacher_sessions.values()):
        await revoke_teacher_session(state, session, reason=reason)
