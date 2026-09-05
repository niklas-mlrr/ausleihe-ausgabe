"""HTTP-Tests für `POST /api/booklist-empty` (server/routes/booklists.py).

Läuft über einen echten HTTP-Client (`starlette.testclient.TestClient`, Fixture
`client` aus `conftest.py`) — gleiches Muster wie `tests/test_api_guards.py`.
Deckt ab: Katalog-Validierung, „scoped replace" (ein Jahrgang darf beim
Speichern keine Bestand-leer-Flags anderer Jahrgänge löschen — wichtig für
Mehrjahresbände, die in mehreren Jahrgangs-Katalogen auftauchen), Persistenz
und den Repush-Fanout an betroffene Helfer-/Schüler-Sessions.
"""

from __future__ import annotations

import server.config as config_module
import server.routes.booklists as booklists_routes
from server.config import Config
from server.routes import _deps as deps_routes
from server.state import AppState, HelperSession, QueueStudent, ScanStationSession


class _FakeHub:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.ws_sent: list[tuple[object, dict]] = []
        self.host_broadcasts: list[dict] = []
        self.settings_broadcasts = 0

    async def broadcast_host(self, snapshot) -> None:
        self.host_broadcasts.append(snapshot)

    async def broadcast_settings(self, *a, **kw) -> None:
        self.settings_broadcasts += 1

    async def send_scanner(self, token, msg) -> None:
        self.sent.append((token, msg))

    async def send_websocket(self, websocket, msg) -> bool:
        self.ws_sent.append((websocket, msg))
        return True


class _FakeIserv:
    def __init__(self, catalog: list[dict]) -> None:
        self._catalog = catalog

    async def get_booklist_catalog_by_grade(self, grade, schoolyear=None):
        return self._catalog


def _catalog(*isbns: str) -> list[dict]:
    return [{"isbn": i, "title": i, "subject": "Mathematik"} for i in isbns]


def _ctx(monkeypatch, catalog):
    state = AppState()
    state.add_host_session("sid")
    state.iserv = _FakeIserv(catalog)
    hub = _FakeHub()
    # Zwei get_config-Ablösungen: require_host nutzt den _deps-eigenen
    # from-Import; `state_snapshot()` importiert get_config pro Aufruf lokal
    # aus server.config. Ohne beide Patches läuft der echte load_config() und
    # bricht ohne .env mit SystemExit (CI; lokal still von der echten .env
    # gedeckt).
    cfg = Config(
        iserv_domain="example.org",
        iserv_username="u",
        iserv_password="p",
        host_password="secret",
    )
    monkeypatch.setattr(booklists_routes, "get_state", lambda: state)
    monkeypatch.setattr(booklists_routes, "get_hub", lambda: hub)
    monkeypatch.setattr(deps_routes, "get_state", lambda: state)
    monkeypatch.setattr(deps_routes, "get_config", lambda: cfg)
    monkeypatch.setattr(config_module, "get_config", lambda: cfg)
    return state, hub


def test_set_booklist_empty_stores_valid_subset(client, monkeypatch):
    state, _hub = _ctx(monkeypatch, _catalog("A", "B", "C"))
    r = client.post(
        "/api/booklist-empty",
        json={"grade": 9, "empty": ["A", "C", "UNKNOWN"]},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert r.json()["empty"] == ["A", "C"]  # UNKNOWN nicht im Katalog -> gedroppt
    assert state.caches.empty_isbns == {"A", "C"}


def test_set_booklist_empty_is_scoped_replace_not_global_wipe():
    """Ein Mehrjahresband-ISBN, das in Klasse 7's Katalog NICHT vorkommt, darf
    beim Speichern von Klasse 6 nicht verloren gehen."""
    import asyncio

    from server.routes._deps import BooklistEmptyRequest

    async def _run():
        state = AppState()
        state.iserv = _FakeIserv(_catalog("A", "B"))  # Katalog Klasse 6
        state.caches.empty_isbns = {"MJB"}  # aus Klasse 7, nicht in Klasse 6's Katalog

        class _Hub:
            async def broadcast_settings(self, *a, **kw):
                pass

        import server.routes.booklists as mod

        mod_state_backup = mod.get_state
        mod_hub_backup = mod.get_hub
        try:
            mod.get_state = lambda: state
            mod.get_hub = lambda: _Hub()
            await mod.set_booklist_empty(BooklistEmptyRequest(grade=6, empty=["A"]))
        finally:
            mod.get_state = mod_state_backup
            mod.get_hub = mod_hub_backup

        assert state.caches.empty_isbns == {"A", "MJB"}  # MJB bleibt erhalten

    asyncio.run(_run())


def test_set_booklist_empty_persists(client, monkeypatch, tmp_path):
    import server.booklist_store as booklist_store

    monkeypatch.setattr(booklist_store, "STORE_PATH", tmp_path / "booklist_settings.json")
    state, _hub = _ctx(monkeypatch, _catalog("A"))
    r = client.post(
        "/api/booklist-empty", json={"grade": 9, "empty": ["A"]}, cookies={"session_id": "sid"}
    )
    assert r.status_code == 200
    _orders, _hidden, persisted_empty = booklist_store.load()
    assert persisted_empty == {"A"}


def test_set_booklist_empty_repushes_affected_helper_session(client, monkeypatch):
    state, hub = _ctx(monkeypatch, _catalog("A", "B"))
    ctx = state.open_context("9a")
    ctx.queue.append(
        QueueStudent(student_id=5, lastname="N", firstname="V", form="9a", status="active")
    )
    helper = HelperSession(token="tok", name="H")
    helper.student_id = 5
    helper.ws = object()
    helper.expected_isbns = {"A"}
    state.helper_sessions["tok"] = helper

    async def _fake_get_student_info(student_id, schoolyear):
        return {"enrolled": True, "books": [{"isbn": "A", "status": "vorgemerkt"}]}

    state.iserv.get_student_info = _fake_get_student_info

    r = client.post(
        "/api/booklist-empty", json={"grade": 9, "empty": ["A"]}, cookies={"session_id": "sid"}
    )
    assert r.status_code == 200
    assert any(msg.get("type") == "booklist_update" for _tok, msg in hub.sent)


def test_set_booklist_empty_repushes_affected_scan_station_session(client, monkeypatch):
    """Wie `test_set_booklist_empty_repushes_affected_helper_session`, aber
    für eine an einer Scan-Station angemeldete Session — dieselbe
    `repush_for_changed_empty_isbns` muss auch `state.scan_stations`
    durchlaufen, sonst bleibt die Bestand-leer-Reihe an der Station bis zum
    nächsten Reload sichtbar."""
    state, hub = _ctx(monkeypatch, _catalog("A", "B"))
    ctx = state.open_context("9a")
    ctx.queue.append(
        QueueStudent(student_id=5, lastname="N", firstname="V", form="9a", status="active")
    )
    station = ScanStationSession(station_id="abc123abc123", registration_code="1234")
    station.student_id = 5
    station.ws = object()
    station.expected_isbns = {"A"}
    state.scan_stations["abc123abc123"] = station

    async def _fake_get_student_info(student_id, schoolyear):
        return {"enrolled": True, "books": [{"isbn": "A", "status": "vorgemerkt"}]}

    state.iserv.get_student_info = _fake_get_student_info

    r = client.post(
        "/api/booklist-empty", json={"grade": 9, "empty": ["A"]}, cookies={"session_id": "sid"}
    )
    assert r.status_code == 200
    assert any(msg.get("type") == "booklist_update" for _ws, msg in hub.ws_sent)


def test_set_booklist_hidden_repushes_affected_scan_station_session(client, monkeypatch):
    """`POST /api/booklist-hidden` (Ausblenden statt Bestand-leer) muss
    ebenfalls eine an einer Scan-Station angemeldete Session live
    nachziehen — Gegenstück zu
    `test_set_booklist_empty_repushes_affected_scan_station_session`."""
    state, hub = _ctx(monkeypatch, _catalog("A", "B"))
    ctx = state.open_context("9a")
    ctx.queue.append(
        QueueStudent(student_id=5, lastname="N", firstname="V", form="9a", status="active")
    )
    # `_student_in_grade` liest den Jahrgang aus dem beim Laden befüllten
    # `form_catalog_cache` — hier vorab gesetzt, um den Katalog-Roundtrip zu
    # sparen (wie in `tests/test_booklist_repush.py::_setup`).
    state.caches.form_catalog_cache["9a"] = (9, ["A", "B"])
    station = ScanStationSession(station_id="def456def456", registration_code="5678")
    station.student_id = 5
    station.ws = object()
    state.scan_stations["def456def456"] = station

    async def _fake_get_student_info(student_id, schoolyear):
        return {"enrolled": True, "books": [{"isbn": "A", "status": "vorgemerkt"}]}

    state.iserv.get_student_info = _fake_get_student_info

    r = client.post(
        "/api/booklist-hidden", json={"grade": 9, "hidden": ["A"]}, cookies={"session_id": "sid"}
    )
    assert r.status_code == 200
    assert any(msg.get("type") == "booklist_update" for _ws, msg in hub.ws_sent)


def test_get_booklist_order_includes_empty(client, monkeypatch):
    state, _hub = _ctx(monkeypatch, _catalog("A", "B"))
    state.caches.empty_isbns = {"A", "OUTSIDE"}
    r = client.get("/api/booklist-order", params={"grade": 9}, cookies={"session_id": "sid"})
    assert r.status_code == 200
    assert r.json()["empty"] == ["A"]  # nur der Schnitt mit dem Katalog
