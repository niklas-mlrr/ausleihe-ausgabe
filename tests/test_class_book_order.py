"""Klassenweite Bücher-Reihenfolge für den Scanner.

Deckt ab: Katalog-Aufbau aus der Jahrgangs-Bücherliste (borrowable-Filter, Dedupe,
Mehrjahresband-Filter, Default-Sortierung), die Reihenfolge-Normalisierung des
POST-Endpoints und den State-Reset. Rein logisch — kein echter IServ/HTTP.
"""

from __future__ import annotations

import asyncio

from server.book_order import get_hidden_isbns_for_form
from server.iserv_client import IsServClient
from server.routes.api import normalize_book_order
from server.sessions import apply_empty_stock_flag, apply_empty_stock_visibility, apply_hidden_books
from server.state import AppState

# ---------------------------------------------------------------------------
# get_class_book_catalog — mit gefälschter Jahrgangs-Bücherliste
# ---------------------------------------------------------------------------


def _item(isbn, title, subject, borrowable=True, multi=False, grades=None):
    """Ein Booklist-Item mit `series_data` (wie der /booklists/:id-Endpunkt liefert)."""
    return {
        "borrowable": borrowable,
        "series": isbn,
        "series_data": {
            "isbn": isbn,
            "title": title,
            "subjectsFlat": [subject],
            "isMultiYear": multi,
            "gradesFlat": grades or [],
        },
    }


def _booklist(items):
    return {"sections": [{"options": [{"items": items}]}]}


class _FakeSchoolyears:
    def __init__(self, booklists, full):
        self._booklists = booklists
        self._full = full  # booklist_id -> full booklist

    def get_booklists(self, sy):
        return self._booklists

    def get_booklist(self, sy, bid):
        return self._full[bid]


class _FakeClient:
    def __init__(self, forms, booklists, full):
        self._forms = forms
        self.schoolyears = _FakeSchoolyears(booklists, full)

    def get(self, path, **kw):
        return self._forms  # nur der /forms-Endpunkt wird direkt genutzt


def _catalog_with_grade(forms, booklists, full, form="9a", sy="2025/2026"):
    c = IsServClient("d", "u", "p")
    c._client = _FakeClient(forms, booklists, full)  # _get_client()
    c._series_map = {}  # series_data reicht → kein Fetch
    return asyncio.run(c.get_class_book_catalog(form, sy))  # (grade, catalog)


def _catalog(forms, booklists, full, form="9a", sy="2025/2026"):
    # get_class_book_catalog liefert (grade, catalog) — hier nur der Katalog.
    return _catalog_with_grade(forms, booklists, full, form, sy)[1]


def test_catalog_from_booklist_filters_borrowable_dedupes_sorts():
    forms = [{"name": "9a", "grade": 9}]
    items = [
        _item("A", "Mathe", "Mathematik"),
        _item("B", "Deutsch Arbeitsheft", "Deutsch", borrowable=False),  # raus
        _item("C", "Bio", "Biologie"),
        _item("A", "Mathe", "Mathematik"),  # Dublette
    ]
    cat = _catalog(forms, [{"grade": 9, "id": 100}], {100: _booklist(items)})
    # borrowable + dedupe, sortiert nach (subject, title): Biologie(C), Mathematik(A)
    assert [b["isbn"] for b in cat] == ["C", "A"]
    assert cat[0] == {"isbn": "C", "title": "Bio", "subject": "Biologie"}


def test_catalog_includes_multiyear_in_every_grade():
    # Mehrjahresband M (Jg. 7-8): die komplette ausleihbare Jahrgangsliste wird
    # gezeigt — der Band erscheint in BEIDEN Jahrgängen (kein min-grade-Filter).
    m = _item("M", "Bioskop 7/8", "Biologie", multi=True, grades=[8, 7])

    cat7 = _catalog(
        [{"name": "7a", "grade": 7}],
        [{"grade": 7, "id": 7}],
        {7: _booklist([_item("N", "Normal", "Deutsch"), m])},
        form="7a",
    )
    assert {b["isbn"] for b in cat7} == {"N", "M"}

    cat8 = _catalog(
        [{"name": "8a", "grade": 8}],
        [{"grade": 8, "id": 8}],
        {8: _booklist([_item("P", "Physik", "Physik"), m])},
        form="8a",
    )
    assert {b["isbn"] for b in cat8} == {"P", "M"}  # oberer Jg. → Band bleibt drin


def test_catalog_empty_when_no_booklist_for_grade():
    forms = [{"name": "9a", "grade": 9}]
    cat = _catalog(forms, [{"grade": 5, "id": 5}], {5: _booklist([_item("A", "X", "Y")])})
    assert cat == []


def test_catalog_empty_when_form_unknown():
    cat = _catalog(
        [{"name": "8a", "grade": 8}],
        [{"grade": 8, "id": 8}],
        {8: _booklist([_item("A", "X", "Y")])},
        form="9z",
    )
    assert cat == []


def test_catalog_returns_grade_alongside_books():
    # (grade, catalog): der Jahrgang der Klasse wird zum Seeden der jahrgangs-
    # weiten Reihenfolge mitgeliefert.
    grade, cat = _catalog_with_grade(
        [{"name": "9a", "grade": 9}],
        [{"grade": 9, "id": 100}],
        {100: _booklist([_item("A", "Mathe", "Mathematik")])},
    )
    assert grade == 9 and [b["isbn"] for b in cat] == ["A"]


def test_catalog_grade_none_when_form_unknown():
    grade, cat = _catalog_with_grade(
        [{"name": "8a", "grade": 8}],
        [{"grade": 8, "id": 8}],
        {8: _booklist([_item("A", "X", "Y")])},
        form="9z",
    )
    assert grade is None and cat == []


# ---------------------------------------------------------------------------
# normalize_book_order — Beschränkung auf Katalog + Anhängen fehlender
# ---------------------------------------------------------------------------


def test_normalize_keeps_requested_order_within_catalog():
    catalog = ["A", "B", "C"]
    assert normalize_book_order(catalog, ["C", "A", "B"]) == ["C", "A", "B"]


def test_normalize_drops_unknown_and_dupes_and_appends_missing():
    catalog = ["A", "B", "C"]
    # X unbekannt (raus), A doppelt (einmal), B fehlt in requested → hinten angehängt
    assert normalize_book_order(catalog, ["C", "X", "A", "A"]) == ["C", "A", "B"]


def test_normalize_empty_request_yields_catalog_order():
    assert normalize_book_order(["A", "B"], []) == ["A", "B"]


# ---------------------------------------------------------------------------
# State-Reset
# ---------------------------------------------------------------------------


def test_reset_clears_order_and_catalog():
    st = AppState()
    ctx = st.open_context("9a")
    ctx.book_order = ["A", "B"]
    ctx.class_catalog = [{"isbn": "A"}]
    ctx.class_catalog_form = "9a"
    ctx.class_catalog_grade = 9
    st.reset_class_book_order()
    assert ctx.book_order == [] and ctx.class_catalog == [] and ctx.class_catalog_form is None
    assert ctx.class_catalog_grade is None


def test_reset_class_order_keeps_booklist_orders():
    # Klassen-/Schülerwechsel setzt die aktive Reihenfolge zurück, aber die
    # jahrgangsweiten (schuljahrweit gültigen) Reihenfolgen bleiben bestehen.
    st = AppState()
    st.caches.book_orders_by_grade = {9: ["A", "B"]}
    st.reset_class_book_order()
    assert st.caches.book_orders_by_grade == {9: ["A", "B"]}


def test_reset_booklist_orders_clears_all_grades():
    st = AppState()
    st.caches.book_orders_by_grade = {9: ["A"], 10: ["B"]}
    st.reset_booklist_orders()
    assert st.caches.book_orders_by_grade == {}


def test_snapshot_includes_book_order(monkeypatch):
    # state_snapshot() importiert get_config pro Aufruf lokal aus server.config
    # — ohne Patch bricht load_config() ohne .env mit SystemExit (CI).
    import server.config as config_module
    from server.config import Config

    cfg = Config(
        iserv_domain="example.org",
        iserv_username="u",
        iserv_password="p",
        host_password="secret",
    )
    monkeypatch.setattr(config_module, "get_config", lambda: cfg)
    st = AppState()
    ctx = st.open_context("9a")
    ctx.book_order = ["A", "B"]
    assert st.state_snapshot()["book_order"] == ["A", "B"]


def test_reset_booklist_orders_clears_hidden_too():
    st = AppState()
    st.caches.book_orders_by_grade = {9: ["A"]}
    st.caches.hidden_isbns_by_grade = {9: {"A"}}
    st.reset_booklist_orders()
    assert st.caches.book_orders_by_grade == {} and st.caches.hidden_isbns_by_grade == {}


# ---------------------------------------------------------------------------
# Ausgeblendete Buchreihen (Einstellungen-Dialog, „Ausblenden"-Button je Buch)
# ---------------------------------------------------------------------------


def test_apply_hidden_books_removes_hidden_isbns_from_info():
    info = {
        "books": [
            {"isbn": "A", "status": "vorgemerkt"},
            {"isbn": "B", "status": "vorgemerkt"},
            {"isbn": "C", "status": "ausgeliehen"},
        ]
    }
    apply_hidden_books(info, {"B"})
    assert [b["isbn"] for b in info["books"]] == ["A", "C"]


def test_apply_hidden_books_noop_when_nothing_hidden():
    info = {"books": [{"isbn": "A", "status": "vorgemerkt"}]}
    apply_hidden_books(info, set())
    assert [b["isbn"] for b in info["books"]] == ["A"]


# ---------------------------------------------------------------------------
# „Bestand leer" (apply_empty_stock_flag) — nur für den Helfer-Client sichtbar,
# entfernt NIE die Zeile (anders als apply_hidden_books, bleibt buchbar).
# ---------------------------------------------------------------------------


def test_apply_empty_stock_flag_sets_flag_when_visible():
    info = {"books": [{"isbn": "A", "status": "vorgemerkt"}, {"isbn": "B", "status": "vorgemerkt"}]}
    apply_empty_stock_flag(info, {"A"}, visible=True)
    assert info["books"][0].get("bestand_leer") is True
    assert "bestand_leer" not in info["books"][1]
    # Zeile bleibt erhalten — anders als apply_hidden_books.
    assert [b["isbn"] for b in info["books"]] == ["A", "B"]


def test_apply_empty_stock_flag_noop_when_not_visible():
    info = {"books": [{"isbn": "A", "status": "vorgemerkt"}]}
    apply_empty_stock_flag(info, {"A"}, visible=False)
    assert "bestand_leer" not in info["books"][0]


def test_apply_empty_stock_flag_noop_when_nothing_empty():
    info = {"books": [{"isbn": "A", "status": "vorgemerkt"}]}
    apply_empty_stock_flag(info, set(), visible=True)
    assert "bestand_leer" not in info["books"][0]


# ---------------------------------------------------------------------------
# „Bestand leer" — Sichtbarkeitsfilter für Schüler-Client/Scan-Station/Zettel
# (apply_empty_stock_visibility): nicht ausgeliehene Bestand-leer-Reihen
# verschwinden aus der Anzeige, ausgeliehene bleiben stehen.
# ---------------------------------------------------------------------------


def test_apply_empty_stock_visibility_removes_unloaned_empty_stock_rows():
    info = {
        "books": [
            {"isbn": "A", "status": "vorgemerkt"},
            {"isbn": "B", "status": "vorgemerkt"},
            {"isbn": "C", "status": "ausgeliehen"},
        ]
    }
    apply_empty_stock_visibility(info, {"A"})
    assert [b["isbn"] for b in info["books"]] == ["B", "C"]


def test_apply_empty_stock_visibility_keeps_already_loaned_empty_stock_row():
    info = {"books": [{"isbn": "A", "status": "ausgeliehen"}]}
    apply_empty_stock_visibility(info, {"A"})
    assert [b["isbn"] for b in info["books"]] == ["A"]


def test_apply_empty_stock_visibility_noop_when_nothing_empty():
    info = {"books": [{"isbn": "A", "status": "vorgemerkt"}]}
    apply_empty_stock_visibility(info, set())
    assert [b["isbn"] for b in info["books"]] == ["A"]


def test_get_hidden_isbns_for_form_resolves_grade_via_class_catalog():
    forms = [{"name": "9a", "grade": 9}]
    items = [_item("A", "Mathe", "Mathematik"), _item("B", "Bio", "Biologie")]
    c = IsServClient("d", "u", "p")
    c._client = _FakeClient(forms, [{"grade": 9, "id": 100}], {100: _booklist(items)})
    c._series_map = {}
    st = AppState()
    st.iserv = c
    st.selected_schoolyear = "2025/2026"
    st.caches.hidden_isbns_by_grade = {9: {"A"}}
    hidden = asyncio.run(get_hidden_isbns_for_form(st, "9a"))
    assert hidden == {"A"}


def test_get_hidden_isbns_for_form_empty_when_grade_unresolvable():
    c = IsServClient("d", "u", "p")
    c._client = _FakeClient(
        [{"name": "8a", "grade": 8}],
        [{"grade": 8, "id": 8}],
        {8: _booklist([_item("A", "X", "Y")])},
    )
    c._series_map = {}
    st = AppState()
    st.iserv = c
    st.selected_schoolyear = "2025/2026"
    st.caches.hidden_isbns_by_grade = {8: {"A"}}
    hidden = asyncio.run(get_hidden_isbns_for_form(st, "9z"))
    assert hidden == set()
