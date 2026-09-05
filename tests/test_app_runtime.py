"""Application-local runtime isolation without starting external services."""

from __future__ import annotations

from starlette.testclient import TestClient

import server.config as config_module
from server.app import create_app
from server.config import Config
from server.routes import _deps
from server.state import QueueStudent


def test_create_app_instances_own_isolated_state_and_hub():
    first = create_app()
    second = create_app()

    first_runtime = first.state.runtime
    second_runtime = second.state.runtime
    assert first_runtime is not second_runtime
    assert first_runtime.state is not second_runtime.state
    assert first_runtime.hub is not second_runtime.hub

    first_runtime.state.open_context("10a").queue.append(
        QueueStudent(student_id=1, lastname="A", firstname="B", form="10a")
    )
    first_runtime.state.add_host_session("first")

    assert second_runtime.state.contexts == {}
    assert "first" not in second_runtime.state.host_sessions


def test_http_request_is_bound_to_its_own_app_runtime(monkeypatch):
    app = create_app()
    app.state.runtime.state.add_host_session("local-sid")
    cfg = Config(
        iserv_domain="example.org",
        iserv_username="u",
        iserv_password="p",
        host_password="secret",
    )
    monkeypatch.setattr(_deps, "get_config", lambda: cfg)
    # state.state_snapshot() importiert get_config pro Aufruf lokal aus
    # server.config — der _deps-Patch allein würde ohne .env in load_config
    # enden (SystemExit in CI).
    monkeypatch.setattr(config_module, "get_config", lambda: cfg)

    client = TestClient(app)
    client.cookies.set("session_id", "local-sid")
    response = client.get("/api/state")

    assert response.status_code == 200
    assert response.json()["type"] == "state"
