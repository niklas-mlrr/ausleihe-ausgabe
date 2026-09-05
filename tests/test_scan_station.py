"""Tests für die Scan-Station (`/scan-station`).

Drei Ebenen:

1. **Zettel** — Code-39-Kodierung und das gebaute A4-PDF (Barcode-Maße/-Position,
   Klasse/Name, Buchlisten). Reines Rechnen, kein Server.
2. **Code-Vergabe** — Stabilität pro Schüler, Eindeutigkeit, kein Recycling.
3. **Endpunkte + Session-Logik** — Freischaltung, Theme, Verbieten, Druck über
   die Druckerwarteschlange und die Annahmeregeln für Zettel-Codes
   (`resolve_station_code`), über echtes HTTP gegen `create_app()` ohne
   Lifespan. IServ/Worker/echter Drucker bleiben außen vor.

Aufbau der Fixtures gespiegelt von `tests/test_drucker_display.py`.
"""

from __future__ import annotations

import asyncio

import pytest

import server.hub as hub
import server.routes.auth as auth_routes
import server.routes.classes as classes_routes
import server.routes.drucker_display as drucker_display_routes
import server.routes.modus_b as modus_b_routes
import server.routes.queue as queue_routes
import server.routes.scan_station as scan_station_routes
import server.routes.settings as settings_routes
import server.routes.slips as slips_routes
import server.sessions as sessions
from server.config import Config
from server.print_queue import PrintJob
from server.routes import _deps as deps_routes
from server.scan_station import build_sheet_pdf, encode_code39
from server.state import AppState, QueueStudent, ScanStationSession, StudentSessionB

_CM = 72.0 / 2.54


# ---------------------------------------------------------------------------
# 1. Zettel (Barcode + PDF)
# ---------------------------------------------------------------------------


def test_encode_code39_module_count():
    """4 Ziffern + 2× Start/Stopp = 6 Zeichen à 12 Module (Breitverhältnis 2)
    plus 5 schmale Zwischenräume = 77 Module."""
    elements = encode_code39("4821")
    assert sum(width for _bar, width in elements) == 77.0
    # 6 Zeichen à 9 Elemente + 5 Zwischenräume.
    assert len(elements) == 6 * 9 + 5


def test_encode_code39_starts_and_ends_with_bar():
    elements = encode_code39("0000")
    assert elements[0][0] is True
    assert elements[-1][0] is True


def test_encode_code39_matches_known_pattern():
    """Bekanntes Muster für „0": nnnwwnwnn (Standard-Code-39-Tabelle)."""
    elements = encode_code39("0")
    # Muster des Nutzdatenzeichens liegt hinter Start (9) + Zwischenraum (1).
    payload = elements[10:19]
    widths = [w for _bar, w in payload]
    assert widths == [1.0, 1.0, 1.0, 2.0, 2.0, 1.0, 2.0, 1.0, 1.0]


@pytest.mark.parametrize("bad", ["", "*", "12A4", "12-4", "   "])
def test_encode_code39_rejects_non_digits(bad):
    with pytest.raises(ValueError):
        encode_code39(bad)


def _sheet(**over) -> bytes:
    kwargs = dict(
        form="Klasse 10a",
        lastname="Muster",
        firstname="Max",
        code="4821",
        lent_books=[{"subject": "Mathematik", "title": "Lambacher 10"}],
        pending_books=[{"subject": "Englisch", "title": "Green Line 6"}],
    )
    kwargs.update(over)
    return build_sheet_pdf(**kwargs)


def test_sheet_barcode_size_and_position():
    """Barcode exakt 6,5 × 1,2 cm und oben rechts (rechte Kante am Seitenrand)."""
    fitz = pytest.importorskip("fitz")
    doc = fitz.open(stream=_sheet(), filetype="pdf")
    page = doc[0]
    # Die Balken sind die einzigen schmalen, hohen Rechtecke im Kopfbereich.
    bars = [
        d["rect"] for d in page.get_drawings()
        if d["rect"].y0 < 100 and d["rect"].width < 10 and d["rect"].height > 30
    ]
    assert bars, "kein Barcode gezeichnet"
    left = min(r.x0 for r in bars)
    right = max(r.x1 for r in bars)
    top = min(r.y0 for r in bars)
    bottom = max(r.y1 for r in bars)
    assert (right - left) == pytest.approx(6.5 * _CM, abs=0.05)
    assert (bottom - top) == pytest.approx(1.2 * _CM, abs=0.05)
    # Rechte Kante am 42-pt-Rand der A4-Seite (595,276 pt breit).
    assert right == pytest.approx(page.rect.width - 42.0, abs=0.5)
    # Obere Kante ebenfalls am Seitenrand.
    assert top == pytest.approx(42.0, abs=0.5)


def test_sheet_contains_class_name_code_and_books():
    fitz = pytest.importorskip("fitz")
    text = fitz.open(stream=_sheet(), filetype="pdf")[0].get_text()
    assert "Klasse 10a" in text
    assert "Muster, Max" in text
    assert "4821" in text
    assert "Noch vorgemerkt" in text
    assert "Green Line 6" in text
    assert "Bereits ausgeliehen" in text
    assert "Lambacher 10" in text


def test_sheet_survives_empty_book_lists():
    """Ohne Bücher darf der Zettel nicht scheitern — der Barcode ist der Zweck."""
    fitz = pytest.importorskip("fitz")
    text = fitz.open(
        stream=_sheet(lent_books=[], pending_books=[]), filetype="pdf"
    )[0].get_text()
    assert "4821" in text
    assert "Noch vorgemerkt" in text


# ---------------------------------------------------------------------------
# 2. Code-Vergabe
# ---------------------------------------------------------------------------


def test_station_code_is_stable_per_student():
    """Nachdruck trägt denselben Barcode — der erste Zettel bleibt gültig."""
    state = AppState()
    code = state.allocate_station_code(42)
    assert state.allocate_station_code(42) == code
    assert len(code) == 4 and code.isdigit()
    assert state.student_id_for_station_code(code) == 42


def test_station_codes_are_unique_across_students():
    state = AppState()
    codes = {state.allocate_station_code(i) for i in range(200)}
    assert len(codes) == 200


def test_station_codes_are_never_recycled():
    """Auch für einen längst fertigen Schüler bleibt der Code belegt — ein alter
    Zettel darf nie plötzlich jemand anderen laden."""
    state = AppState()
    code = state.allocate_station_code(1)
    # Fertigwerden/Entfernen des Schülers ändert die Zuordnung nicht.
    assert state.student_id_for_station_code(code) == 1
    other = state.allocate_station_code(2)
    assert other != code


def test_unknown_station_code_resolves_to_none():
    assert AppState().student_id_for_station_code("9999") is None


# ---------------------------------------------------------------------------
# 3. Endpunkte + Session-Logik
# ---------------------------------------------------------------------------


class _FakeWS:
    def __init__(self) -> None:
        self.sent = []
        self.closed = False

    async def send_json(self, msg) -> None:
        self.sent.append(msg)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


class _FakeHub:
    def __init__(self) -> None:
        self.broadcasts = []

    async def broadcast_host(self, snapshot) -> None:
        self.broadcasts.append(snapshot)

    async def broadcast_settings(self, *a, **kw) -> None:
        pass

    async def send_scanner(self, *a, **kw) -> None:
        pass

    async def send_websocket(self, ws, msg) -> bool:
        try:
            await ws.send_json(msg)
        except Exception:  # noqa: BLE001 — wie echtes Hub: False statt werfen
            return False
        return True


_ROUTE_MODULES = [
    deps_routes,
    auth_routes,
    classes_routes,
    queue_routes,
    slips_routes,
    modus_b_routes,
    settings_routes,
    drucker_display_routes,
    scan_station_routes,
]


def _make_config(**over) -> Config:
    base = dict(
        iserv_domain="example.org",
        iserv_username="u",
        iserv_password="p",
        host_password="secret",
        allow_booking=False,
    )
    base.update(over)
    return Config(**base)


@pytest.fixture
def ctx(monkeypatch):
    """Frischer State + Config + Fake-Hub; gültige Host-Session 'sid'."""
    state = AppState()
    state.add_host_session("sid")
    cfg = _make_config()
    hub_inst = _FakeHub()
    for mod in _ROUTE_MODULES:
        if hasattr(mod, "get_state"):
            monkeypatch.setattr(mod, "get_state", lambda: state)
        if hasattr(mod, "get_config"):
            monkeypatch.setattr(mod, "get_config", lambda: cfg)
        if hasattr(mod, "get_hub"):
            monkeypatch.setattr(mod, "get_hub", lambda: hub_inst)
    monkeypatch.setattr(sessions, "get_hub", lambda: hub_inst)
    monkeypatch.setattr(sessions, "get_state", lambda: state)
    monkeypatch.setattr(hub, "get_hub", lambda: hub_inst)
    # Persistenz-Isolation (data/scan_stations.json) macht die autouse
    # `_isolate_device_persistence`-Fixture (conftest.py).
    return state, cfg, hub_inst


_TOKEN = "abc123def456"


def _station(state, *, authorized=False, token=_TOKEN, code="ABCD") -> ScanStationSession:
    station = ScanStationSession(
        station_id=token, registration_code=code, authorized=authorized
    )
    station.ws = _FakeWS()
    state.scan_stations[token] = station
    return station


def _queue_student(state, *, student_id=1, status="pending") -> QueueStudent:
    """Einen Schüler in einen offenen Klassen-Kontext legen."""
    class_ctx = state.active_context or state.open_context("Klasse 10a")
    student = QueueStudent(
        student_id=student_id, lastname="Muster", firstname="Max", form="Klasse 10a"
    )
    student.status = status
    class_ctx.queue.append(student)
    return student


# ---- Seite + Auth-Guards ---------------------------------------------------


def test_page_without_token_redirects_to_fresh_token(client, ctx):
    r = client.get("/scan-station", follow_redirects=False)
    assert r.status_code == 307
    assert "/scan-station?token=" in r.headers["location"]


def test_qr_requires_host(client, ctx):
    assert client.get("/api/scan-station/qr").status_code == 403


def test_enable_requires_host(client, ctx):
    r = client.post("/api/scan-station/enable", json={"station_id": _TOKEN, "label": "A"})
    assert r.status_code == 403


def test_print_sheet_requires_host(client, ctx):
    r = client.post("/api/scan-station/print-sheet", json={"student_id": 1})
    assert r.status_code == 403


def test_qr_with_station_id_includes_token(client, ctx):
    state, _, _ = ctx
    _station(state)
    r = client.get(f"/api/scan-station/qr?station_id={_TOKEN}", cookies={"session_id": "sid"})
    assert r.status_code == 200
    assert r.json()["url"].endswith(f"/scan-station?token={_TOKEN}")


# ---- Freischalten / Name / Theme / Verbieten -------------------------------


def test_enable_sets_label_and_pushes_ready(client, ctx):
    state, _, _ = ctx
    station = _station(state)
    r = client.post(
        "/api/scan-station/enable",
        json={"station_id": _TOKEN, "label": "Eingang"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert station.authorized is True
    assert station.label == "Eingang"
    # Die Station bekommt jetzt „bereit" statt des Registrierungs-Codes.
    assert station.ws.sent[-1]["type"] == "ready"
    assert station.ws.sent[-1]["label"] == "Eingang"


def test_theme_rejects_unknown_value(client, ctx):
    state, _, _ = ctx
    _station(state, authorized=True)
    r = client.post(
        "/api/scan-station/theme",
        json={"station_id": _TOKEN, "theme": "neon"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 400


def test_theme_is_pushed_to_station(client, ctx):
    state, _, _ = ctx
    station = _station(state, authorized=True)
    r = client.post(
        "/api/scan-station/theme",
        json={"station_id": _TOKEN, "theme": "light"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert station.theme == "light"
    assert station.ws.sent[-1]["theme"] == "light"


def test_input_mode_rejects_unknown_value(client, ctx):
    state, _, _ = ctx
    _station(state, authorized=True)
    r = client.post(
        "/api/scan-station/input-mode",
        json={"station_id": _TOKEN, "input_mode": "voice"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 400


def test_input_mode_is_pushed_to_station(client, ctx):
    """Eingabeart (Kamera/Manuell wie im Helferclient) wird wie das Theme vom
    Host gesetzt und sofort an die Station geschoben."""
    state, _, _ = ctx
    station = _station(state, authorized=True)
    r = client.post(
        "/api/scan-station/input-mode",
        json={"station_id": _TOKEN, "input_mode": "manual"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert station.input_mode == "manual"
    assert station.ws.sent[-1]["input_mode"] == "manual"
    assert state.scan_stations_snapshot()[0]["input_mode"] == "manual"


def test_input_mode_defaults_to_station_choice(ctx):
    """Ohne Host-Vorgabe bleibt `input_mode` None — die Station entscheidet
    dann selbst (gemerkter Wert, Default Kamera)."""
    state, _, _ = ctx
    _station(state, authorized=True)
    assert state.scan_stations_snapshot()[0]["input_mode"] is None


def test_input_mode_requires_host(client, ctx):
    r = client.post(
        "/api/scan-station/input-mode", json={"station_id": _TOKEN, "input_mode": "manual"}
    )
    assert r.status_code == 403


def test_label_on_unauthorized_station_404(client, ctx):
    state, _, _ = ctx
    _station(state, authorized=False)
    r = client.post(
        "/api/scan-station/label",
        json={"station_id": _TOKEN, "label": "X"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 404


def test_forget_bans_token_and_closes_ws(client, ctx):
    state, _, _ = ctx
    station = _station(state, authorized=True)
    r = client.post(
        "/api/scan-station/forget",
        json={"station_id": _TOKEN},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert _TOKEN not in state.scan_stations
    assert _TOKEN in state.banned_scan_station_tokens
    assert station.ws.closed is True
    assert station.ws.sent[-1]["type"] == "forbidden"


# ---- Blockierender Buch-Hinweis (Host-Freigabe wie am Handy) --------------


def test_find_station_by_student(ctx):
    state, _, _ = ctx
    station = _station(state, authorized=True)
    station.student_id = 42
    assert state.find_station_by_student(42) is station
    assert state.find_station_by_student(99) is None


def test_clear_book_alert_unblocks_station(client, ctx):
    from datetime import datetime, timedelta

    state, _, _ = ctx
    station = _station(state, authorized=True)
    station.student_id = 7
    station.book_alert_open = True
    # Der Leerlauf-Timer stand während der offenen Meldung still (s.
    # `expired_scan_stations`) — die Freigabe muss ihn frisch neu starten,
    # statt die stillstehende Zeit sofort nachzuholen.
    station.last_activity = datetime.now() - timedelta(
        seconds=sessions.STATION_IDLE_TTL_S + 1
    )
    r = client.post(
        "/api/clear-book-alert",
        json={"student_id": 7},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert station.book_alert_open is False
    assert station.ws.sent[-1]["type"] == "book_alert_clear"
    assert (datetime.now() - station.last_activity).total_seconds() < 1


def test_clear_book_alert_noop_when_not_open(client, ctx):
    """Kein offener Alert → kein `book_alert_clear`-Frame (idempotent, kein
    Rauschen am Client)."""
    state, _, _ = ctx
    station = _station(state, authorized=True)
    station.student_id = 7
    station.book_alert_open = False
    r = client.post(
        "/api/clear-book-alert",
        json={"student_id": 7},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert station.ws.sent == []


def test_clear_book_alert_targets_only_the_matching_station(client, ctx):
    state, _, _ = ctx
    other = _station(state, authorized=True, token="ffffffffffff", code="ZZZZ")
    other.student_id = 99
    other.book_alert_open = True
    mine = _station(state, authorized=True, token="eeeeeeeeeeee", code="YYYY")
    mine.student_id = 7
    mine.book_alert_open = True
    client.post(
        "/api/clear-book-alert",
        json={"student_id": 7},
        cookies={"session_id": "sid"},
    )
    assert mine.book_alert_open is False
    assert other.book_alert_open is True
    assert other.ws.sent == []


# ---- Snapshot --------------------------------------------------------------


def test_snapshot_hides_paper_code(ctx):
    """Der Zettel-Code ist der Zugangs-Credential und darf nicht im breit
    gestreuten Host-Snapshot landen."""
    state, _, _ = ctx
    _station(state, authorized=True)
    code = state.allocate_station_code(7)
    snap = state.scan_stations_snapshot()
    assert len(snap) == 1
    assert code not in repr(snap)
    assert snap[0]["registration_code"] == "ABCD"
    assert snap[0]["student_id"] is None


def test_snapshot_reports_bound_student(ctx):
    state, _, _ = ctx
    station = _station(state, authorized=True)
    station.student_id = 5
    station.student_lastname = "Muster"
    station.student_firstname = "Max"
    snap = state.scan_stations_snapshot()[0]
    assert snap["student_id"] == 5
    assert snap["student_name"] == "Muster, Max"


# ---- Zettel-Code-Annahme (`resolve_station_code`) --------------------------


def test_resolve_rejects_unknown_code(ctx):
    state, _, _ = ctx
    student, reason = sessions.resolve_station_code(state, "1234")
    assert student is None
    assert "unbekannt" in reason.lower()


def test_resolve_accepts_waiting_student(ctx):
    state, _, _ = ctx
    _queue_student(state, student_id=1)
    code = state.allocate_station_code(1)
    student, reason = sessions.resolve_station_code(state, code)
    assert student is not None and student.student_id == 1
    assert reason == ""


def test_resolve_rejects_finished_student(ctx):
    state, _, _ = ctx
    _queue_student(state, student_id=1, status="done")
    code = state.allocate_station_code(1)
    student, reason = sessions.resolve_station_code(state, code)
    assert student is None
    assert "abgeschlossen" in reason.lower()


def test_resolve_rejects_student_with_helper(ctx):
    state, _, _ = ctx
    student = _queue_student(state, student_id=1)
    student.assigned_helper = "helper-token"
    code = state.allocate_station_code(1)
    resolved, reason = sessions.resolve_station_code(state, code)
    assert resolved is None
    assert "helfer" in reason.lower()


def test_resolve_rejects_student_paired_with_phone(ctx):
    state, _, _ = ctx
    _queue_student(state, student_id=1)
    session = StudentSessionB(session_token="t", pairing_code="1234")
    session.student_id = 1
    session.state = "paired"
    state.student_sessions["t"] = session
    code = state.allocate_station_code(1)
    resolved, reason = sessions.resolve_station_code(state, code)
    assert resolved is None
    assert "handy" in reason.lower()


def test_resolve_rejects_student_on_another_station(ctx):
    state, _, _ = ctx
    _queue_student(state, student_id=1)
    other = _station(state, authorized=True, token="ffffffffffff", code="ZZZZ")
    other.student_id = 1
    code = state.allocate_station_code(1)
    resolved, reason = sessions.resolve_station_code(state, code)
    assert resolved is None
    assert "station" in reason.lower()


# ---- Leerlauf-TTL ----------------------------------------------------------


def test_idle_sweep_ignores_stations_waiting_for_worker(ctx):
    """Solange kein Worker steht (`worker_ready=False`), läuft die Uhr nicht —
    sonst würde ein wartender Schüler mitten im Laden hinausgeworfen."""
    from datetime import datetime, timedelta

    state, _, _ = ctx
    station = _station(state, authorized=True)
    station.student_id = 1
    station.worker_ready = False
    station.last_activity = datetime.now() - timedelta(seconds=120)
    assert sessions.expired_scan_stations(state, datetime.now()) == []


def test_idle_sweep_releases_after_ttl(ctx):
    from datetime import datetime, timedelta

    state, _, _ = ctx
    station = _station(state, authorized=True)
    station.student_id = 1
    station.worker_ready = True
    station.last_activity = datetime.now() - timedelta(
        seconds=sessions.STATION_IDLE_TTL_S + 1
    )
    assert sessions.expired_scan_stations(state, datetime.now()) == [station]


def test_idle_sweep_ignores_stations_with_open_book_alert(ctx):
    """Solange ein blockierendes Buch-Hinweis-Modal auf die Host-Freigabe
    wartet (`book_alert_open`), darf der Leerlauf-Timer die Station nicht
    unter dem Schüler wegräumen — der Schüler hat ja nichts falsch gemacht,
    er wartet auf den Betreuer."""
    from datetime import datetime, timedelta

    state, _, _ = ctx
    station = _station(state, authorized=True)
    station.student_id = 1
    station.worker_ready = True
    station.book_alert_open = True
    station.last_activity = datetime.now() - timedelta(
        seconds=sessions.STATION_IDLE_TTL_S + 1
    )
    assert sessions.expired_scan_stations(state, datetime.now()) == []


def test_release_clears_binding_and_notifies(ctx):
    state, _, _ = ctx
    station = _station(state, authorized=True)
    station.student_id = 1
    station.worker_ready = True
    station.expected_isbns = {"978"}
    released = asyncio.run(
        sessions.release_station_student(state, station, reason="timeout")
    )
    assert released is True
    assert station.student_id is None
    assert station.expected_isbns == set()
    # Station bleibt freigeschaltet und verbunden — nur die Bindung fällt weg.
    assert station.authorized is True
    types = [m["type"] for m in station.ws.sent]
    assert "released" in types and types[-1] == "ready"


def test_release_is_idempotent(ctx):
    state, _, _ = ctx
    station = _station(state, authorized=True)
    assert asyncio.run(
        sessions.release_station_student(state, station, reason="x")
    ) is False


def test_end_student_done_releases_station(ctx):
    """Wird ein Schüler vom Host abgeschlossen (`end_student` mit `done`),
    muss die Scan-Station den Schüler abmelden — wie beim Trennen, nur ohne
    den Zettel-Code zu entwerten (Re-Scan wird am `done`-Status ohnehin
    abgelehnt, s. `resolve_station_code`). Sonst bliebe der beendete Schüler
    stale auf der Station stehen."""
    state, _, _ = ctx
    _queue_student(state, student_id=1, status="active")
    state.allocate_station_code(1)
    station = _station(state, authorized=True)
    station.student_id = 1
    station.worker_ready = True
    station.owns_worker = True

    hub = _FakeHub()
    asyncio.run(
        sessions.end_student(state, hub, 1, queue_status="done", session_state="completed")
    )

    # Station ist abgemeldet …
    assert station.student_id is None
    # … und bekommt wie beim Trennen `released` gefolgt von `ready`.
    types = [m["type"] for m in station.ws.sent]
    assert "released" in types and types[-1] == "ready"
    # Zettel-Code bleibt (im Gegensatz zum Trennen) bestehen — Re-Scan wird
    # via `resolve_station_code` am `done`-Status abgelehnt, nicht am Code.
    assert state.student_id_for_station_code(state.slip_codes.active_code_for(1)) == 1
    assert state.find_student(1).status == "done"


# ---- Druck über die Druckerwarteschlange -----------------------------------


def test_print_sheet_enqueues_station_sheet_job(client, ctx, monkeypatch):
    state, _, _ = ctx
    _queue_student(state, student_id=1)
    enqueued = []

    async def _fake_enqueue(job):
        enqueued.append(job)
        return 0

    monkeypatch.setattr(state.print_queue, "enqueue", _fake_enqueue)
    r = client.post(
        "/api/scan-station/print-sheet",
        json={"student_id": 1},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert len(enqueued) == 1
    job = enqueued[0]
    # Rolle „host" → Drucker-Display zeigt das gewohnte Host-Symbol; `kind`
    # unterscheidet den Zettel trotzdem klar vom Leihschein.
    assert job.role == "host"
    assert job.kind == "station_sheet"
    assert job.pages is None
    assert job.name == "Muster, Max (10a)"


def test_print_sheet_without_printer_pool_is_rejected(client, ctx):
    state, _, _ = ctx
    _queue_student(state, student_id=1)
    state.settings.printers = []
    r = client.post(
        "/api/scan-station/print-sheet",
        json={"student_id": 1},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 400


def test_print_sheet_for_unknown_student_404(client, ctx):
    r = client.post(
        "/api/scan-station/print-sheet",
        json={"student_id": 999},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 404


def test_station_sheet_never_marks_slip_printed(ctx):
    """Kernabsicherung: Ein Zetteldruck darf weder den „Leihschein gedruckt"-
    Marker setzen noch eine Modus-B-Session abschließen."""
    state, _, _ = ctx
    student = _queue_student(state, student_id=1)
    marked = []

    async def _boom(*a, **kw):
        marked.append(a)

    import server.sessions as sess_mod

    original = sess_mod._mark_slip_printed
    sess_mod._mark_slip_printed = _boom
    try:
        job = PrintJob.create(
            role="host", kind="station_sheet", student_id=1, pages=None, name="x"
        )
        job.result = {"ok": True}
        asyncio.run(state.print_queue._mark_slip_printed_after_completion(job))
    finally:
        sess_mod._mark_slip_printed = original
    assert marked == []
    assert student.slip_printed is False


def test_leihschein_completion_enters_signing_mode_for_station_student(ctx):
    """Scan-Station-Schüler haben keine Modus-B-Session und damit keine
    „Leihschein erhalten"-Bestätigung — bei aktivem `done_signed` wechselt
    ihr Status deshalb direkt bei Druckende in den Unterschriften-Modus,
    statt (wie bisher) bis zum manuellen Host-Abschluss auf „gedruckt"
    stehen zu bleiben. Ruft `sessions._mark_slip_printed` direkt (statt über
    `PrintQueue._mark_slip_printed_after_completion`), weil Letztere intern
    den globalen `state.get_state()`-Singleton statt der lokalen `ctx`-State
    verwendet."""
    state, _, _ = ctx
    student = _queue_student(state, student_id=1)
    state.active_context.done_signed = True
    state.allocate_station_code(1)

    asyncio.run(sessions._mark_slip_printed(state, 1))

    assert student.slip_printed is True
    assert student.slip_signing is True
    assert student.status == "pending"  # noch nicht abgeschlossen — wartet aufs Unterschreiben


def test_leihschein_completion_auto_finishes_station_student_without_signing(ctx):
    """Ohne aktives `done_signed` schließt der Druck den Scan-Station-Schüler
    direkt ab (Mirror des Modus-B-Auto-Fertig-Zweigs in
    `confirm_slip_received`, nur ohne auf eine nie kommende Bestätigung zu
    warten)."""
    state, _, _ = ctx
    student = _queue_student(state, student_id=1)
    state.active_context.done_signed = False
    state.allocate_station_code(1)

    asyncio.run(sessions._mark_slip_printed(state, 1))

    assert student.slip_printed is True
    assert student.status == "done"


def test_leihschein_completion_leaves_non_station_student_untouched(ctx):
    """Ohne Zettel-Code (Modus A ohne Scan-Station, kein Phone-Session-Match)
    bleibt das bisherige Verhalten: der Host beendet manuell."""
    state, _, _ = ctx
    student = _queue_student(state, student_id=1)
    state.active_context.done_signed = False
    # Bewusst KEIN allocate_station_code — Schüler ist kein Scan-Station-Fall.

    asyncio.run(sessions._mark_slip_printed(state, 1))

    assert student.slip_printed is True
    assert student.slip_signing is False
    assert student.status == "pending"


# ---------------------------------------------------------------------------
# 4. Zettel-Druck macht den Schüler aktiv + „Bücher sammeln"
# ---------------------------------------------------------------------------


class _FakeIServForPrint:
    """Minimaler Read-only-Fake für `print_station_sheet_for` — eine
    vorgemerkte Reihe, damit `books_total`/`done_isbns` etwas zu zählen haben."""

    async def get_student_info(self, student_id, schoolyear):
        return {
            "student_id": student_id,
            "books": [
                {"isbn": "978-1", "title": "Buch A", "subject": "Deutsch",
                 "status": "vorgemerkt"},
            ],
            "current_books": [],
        }


def test_print_sheet_marks_student_active_and_collecting(ctx, tmp_path, monkeypatch):
    """Der Zettel-Druck ist das „Aufrufen" des Zettel-/Stations-Flusses:
    wartender Schüler wird aktiv, Fortschritt wird befüllt, die persistente
    Markierung `station_zettel_printed` gesetzt."""
    state, cfg, hub_inst = ctx
    # `print_station_sheet_for` druckt tatsächlich (Backend „file") — Ausgabe
    # in ein pytest-Temp-Verzeichnis lenken, nicht ins Repo (`sessions.py`
    # nutzt `get_config()` unabhängig von den Route-Modulen, s. `ctx`-Fixture).
    monkeypatch.setattr(
        sessions, "get_config", lambda: _make_config(print_output_dir=tmp_path)
    )
    student = _queue_student(state, student_id=1, status="pending")
    state.iserv = _FakeIServForPrint()
    asyncio.run(sessions.print_station_sheet_for(state, 1))
    assert student.status == "active"
    assert student.station_zettel_printed is True
    assert student.books_total == 1
    assert student.done_isbns == set()
    # Host wird sofort benachrichtigt (nicht erst beim nächsten Snapshot).
    assert hub_inst.broadcasts


def test_print_sheet_does_not_reactivate_a_done_student(ctx, tmp_path, monkeypatch):
    """Ein Nachdruck für einen bereits fertigen Schüler darf ihn nicht aus
    Versehen wieder aktivieren — nur `pending` → `active` ist ein „Aufrufen"."""
    state, _, _ = ctx
    monkeypatch.setattr(
        sessions, "get_config", lambda: _make_config(print_output_dir=tmp_path)
    )
    student = _queue_student(state, student_id=1, status="done")
    state.iserv = _FakeIServForPrint()
    asyncio.run(sessions.print_station_sheet_for(state, 1))
    assert student.status == "done"
    assert student.station_zettel_printed is True


def test_print_sheet_reprint_for_active_student_keeps_baseline(ctx, tmp_path, monkeypatch):
    """Ein Nachdruck über den Host-Knopf im „Aktuell in Ausgabe"-Kästchen
    (bereits aktiver Schüler) ist KEIN erneutes Aufrufen — die Baseline
    (`loaned_at_load`) für den „seit Aufrufen"-Fortschritt in der
    Host-Statuszeile darf sich dabei nicht ändern, sonst würde der Nachdruck
    den laufenden Fortschritt zurücksetzen."""
    state, _, _ = ctx
    monkeypatch.setattr(
        sessions, "get_config", lambda: _make_config(print_output_dir=tmp_path)
    )
    student = _queue_student(state, student_id=1, status="active")
    student.loaned_at_load = 3
    state.iserv = _FakeIServForPrint()
    asyncio.run(sessions.print_station_sheet_for(state, 1))
    assert student.status == "active"
    assert student.loaned_at_load == 3


def test_print_sheet_reprint_keeps_same_code(ctx, tmp_path, monkeypatch):
    """Ein Nachdruck (egal ob „Erstellen" oder der „Drucken"-Knopf im
    „Aktuell in Ausgabe"-Kästchen) druckt IMMER denselben Zettel-Code — nur
    die Bücherliste auf dem Zettel wird bei jedem Druck frisch geholt."""
    state, _, _ = ctx
    monkeypatch.setattr(
        sessions, "get_config", lambda: _make_config(print_output_dir=tmp_path)
    )
    _queue_student(state, student_id=1, status="pending")
    state.iserv = _FakeIServForPrint()
    first = asyncio.run(sessions.print_station_sheet_for(state, 1))
    second = asyncio.run(sessions.print_station_sheet_for(state, 1))
    assert first["code"] == second["code"]
    assert state.student_id_for_station_code(first["code"]) == 1


class _FakeIServChangingBooks:
    """IServ-Fake mit sich änderndem Bücherstand zwischen zwei Aufrufen —
    für die Absicherung, dass ein Nachdruck die Bücherliste frisch holt,
    statt einen alten Stand zu cachen."""

    def __init__(self):
        self.calls = 0

    async def get_student_info(self, student_id, schoolyear):
        self.calls += 1
        status = "ausgeliehen" if self.calls > 1 else "vorgemerkt"
        return {
            "student_id": student_id,
            "books": [
                {"isbn": "978-1", "title": "Buch A", "subject": "Deutsch",
                 "status": status},
            ],
            "current_books": [],
        }


def test_print_sheet_reprint_refreshes_book_list(ctx, tmp_path, monkeypatch):
    """Zwischen zwei Drucken wechselt das einzige Buch von „vorgemerkt" zu
    „ausgeliehen" — der Nachdruck muss diesen aktuellen Stand übernehmen
    (`done_isbns`), obwohl der Zettel-Code gleich bleibt."""
    state, _, _ = ctx
    monkeypatch.setattr(
        sessions, "get_config", lambda: _make_config(print_output_dir=tmp_path)
    )
    student = _queue_student(state, student_id=1, status="pending")
    fake_iserv = _FakeIServChangingBooks()
    state.iserv = fake_iserv
    first = asyncio.run(sessions.print_station_sheet_for(state, 1))
    assert student.done_isbns == set()
    second = asyncio.run(sessions.print_station_sheet_for(state, 1))
    assert student.done_isbns == {"978-1"}
    assert first["code"] == second["code"]
    assert fake_iserv.calls == 2


def test_activate_station_student_marks_active_without_printing(ctx):
    """`activate_station_student` (Host-Knopf „Erstellen", ohne „und
    Drucken") setzt Status/Code/Fortschritt genau wie `print_station_sheet_
    for`, baut aber kein PDF und druckt nicht — reiner Aktivierungspfad."""
    state, _, hub_inst = ctx
    student = _queue_student(state, student_id=1, status="pending")
    state.iserv = _FakeIServForPrint()
    result = asyncio.run(sessions.activate_station_student(state, 1))
    assert result["ok"] is True
    assert result["code"] == state.slip_codes.active_code_for(1)
    assert student.status == "active"
    assert student.station_zettel_printed is True
    assert student.books_total == 1
    assert hub_inst.broadcasts


def test_activate_station_student_reprint_keeps_baseline(ctx):
    """Wie beim Zettel-Nachdruck: Aktivieren für einen bereits aktiven
    Schüler darf die laufende Fortschritts-Baseline nicht zurücksetzen."""
    state, _, _ = ctx
    student = _queue_student(state, student_id=1, status="active")
    student.loaned_at_load = 3
    state.iserv = _FakeIServForPrint()
    asyncio.run(sessions.activate_station_student(state, 1))
    assert student.status == "active"
    assert student.loaned_at_load == 3


def test_activate_and_print_share_the_same_code(ctx, tmp_path, monkeypatch):
    """Egal ob über „Erstellen" (`activate_station_student`) oder den
    „Drucken"-Knopf im „Aktuell in Ausgabe"-Kästchen (`print_station_sheet_
    for`) — beide Wege liefern denselben Zettel-Code für denselben
    Schüler, auch quer über die beiden Funktionen hinweg."""
    state, _, _ = ctx
    monkeypatch.setattr(
        sessions, "get_config", lambda: _make_config(print_output_dir=tmp_path)
    )
    _queue_student(state, student_id=1, status="pending")
    state.iserv = _FakeIServForPrint()
    via_activate = asyncio.run(sessions.activate_station_student(state, 1))
    via_print = asyncio.run(sessions.print_station_sheet_for(state, 1))
    assert via_activate["code"] == via_print["code"]
    assert state.student_id_for_station_code(via_print["code"]) == 1


def test_scan_station_activate_endpoint(client, ctx, monkeypatch):
    """HTTP-Ebene: `/api/scan-station/activate` aktiviert den Schüler und
    fasst die Druckerwarteschlange nicht an (kein `PrintJob`)."""
    state, _, _ = ctx
    _queue_student(state, student_id=1, status="pending")
    state.iserv = _FakeIServForPrint()
    enqueued = []

    async def _fake_enqueue(job):
        enqueued.append(job)
        return 0

    monkeypatch.setattr(state.print_queue, "enqueue", _fake_enqueue)
    r = client.post(
        "/api/scan-station/activate",
        json={"student_id": 1},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert enqueued == []
    assert state.find_student(1).status == "active"


def test_scan_station_activate_for_unknown_student_404(client, ctx):
    r = client.post(
        "/api/scan-station/activate",
        json={"student_id": 999},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 404


def test_reset_progress_clears_station_zettel_printed():
    student = QueueStudent(student_id=1, lastname="Muster", firstname="Max", form="10a")
    student.station_zettel_printed = True
    student.reset_progress()
    assert student.station_zettel_printed is False


# ---------------------------------------------------------------------------
# 5. Stationsname im Queue-Snapshot (für den Host-Badge, s. web/host-render.js)
# ---------------------------------------------------------------------------


def test_queue_snapshot_carries_station_name_while_logged_in(ctx):
    state, _, _ = ctx
    student = _queue_student(state, student_id=1)
    station = _station(state, authorized=True)
    station.student_id = 1
    station.label = "Eingang"
    d = state._queue_student_as_dict(student)
    assert d["station_name"] == "Eingang"


def test_queue_snapshot_falls_back_to_short_id_without_label(ctx):
    state, _, _ = ctx
    student = _queue_student(state, student_id=1)
    station = _station(state, authorized=True)
    station.student_id = 1
    station.label = ""
    d = state._queue_student_as_dict(student)
    assert d["station_name"] == station.station_id[:6]


def test_queue_snapshot_station_name_is_none_when_not_logged_in(ctx):
    state, _, _ = ctx
    student = _queue_student(state, student_id=1)
    d = state._queue_student_as_dict(student)
    assert d["station_name"] is None


# ---------------------------------------------------------------------------
# 6. Zettel-Code im Host-Snapshot — NUR dort, nicht in den Helferclient-
#    Queue-Pfaden (Credential, s. PLAN §3.7)
# ---------------------------------------------------------------------------


def test_station_code_included_when_requested(ctx):
    state, _, _ = ctx
    student = _queue_student(state, student_id=1)
    code = state.allocate_station_code(1)
    d = state._queue_student_as_dict(student, include_station_code=True)
    assert d["station_code"] == code


def test_station_code_omitted_by_default():
    """Der Default (`include_station_code=False`) ist das, was die Helfer-
    client-Pfade (`pending_queue_as_list`/`real_contexts_summary`) nutzen —
    der Zettel-Code darf dort nicht auftauchen."""
    state = AppState()
    student = _queue_student(state, student_id=1)
    state.allocate_station_code(1)
    d = state._queue_student_as_dict(student)
    assert d["station_code"] is None


def test_station_code_absent_without_a_printed_zettel(ctx):
    state, _, _ = ctx
    student = _queue_student(state, student_id=1)
    d = state._queue_student_as_dict(student, include_station_code=True)
    assert d["station_code"] is None


def test_helper_facing_queue_lists_never_expose_station_code(ctx):
    """`pending_queue_as_list`/`real_contexts_summary` gehen an den
    Helferclient — der Zettel-Code darf dort nie mitkommen, selbst wenn
    einer vergeben ist."""
    state, _, _ = ctx
    _queue_student(state, student_id=1)
    state.allocate_station_code(1)
    for entry in state.pending_queue_as_list():
        assert entry["station_code"] is None
    for ctx_summary in state.real_contexts_summary():
        for entry in ctx_summary["queue_all"]:
            assert entry["station_code"] is None


def test_host_state_snapshot_carries_station_code(ctx):
    """Der eigentliche Host-Draht (`state_snapshot()`) — sowohl das
    Top-Level- als auch das Kontext-scoped `queue` — führt den Code."""
    state, _, _ = ctx
    _queue_student(state, student_id=1)
    code = state.allocate_station_code(1)
    snap = state.state_snapshot()
    assert snap["queue"][0]["station_code"] == code
    ctx_id = state.active_context_id
    assert snap["contexts"][ctx_id]["queue"][0]["station_code"] == code


# ---------------------------------------------------------------------------
# 7. „Von der Station abmelden"-Endpoint (Now-Serving-Kästchen im Host)
# ---------------------------------------------------------------------------


def test_release_student_endpoint_releases_bound_station(client, ctx):
    state, _, _ = ctx
    _queue_student(state, student_id=1)
    station = _station(state, authorized=True)
    station.student_id = 1
    station.worker_ready = True
    r = client.post(
        "/api/scan-station/release-student",
        json={"student_id": 1},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert r.json()["released"] is True
    assert station.student_id is None
    # "released" (Bindung gelöst) gefolgt von "ready" (Station bleibt
    # freigeschaltet, zeigt wieder "Zettel-Code scannen") — Spiegel von
    # test_release_clears_binding_and_notifies.
    types = [m["type"] for m in station.ws.sent]
    assert "released" in types and types[-1] == "ready"


def test_release_student_endpoint_is_idempotent_without_a_station(client, ctx):
    state, _, _ = ctx
    _queue_student(state, student_id=1)
    r = client.post(
        "/api/scan-station/release-student",
        json={"student_id": 1},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert r.json()["released"] is False


def test_release_student_endpoint_requires_host(client, ctx):
    r = client.post("/api/scan-station/release-student", json={"student_id": 1})
    assert r.status_code == 403


def test_release_student_endpoint_requires_student_id(client, ctx):
    r = client.post(
        "/api/scan-station/release-student",
        json={},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 400
