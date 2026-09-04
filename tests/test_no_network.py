"""Proof that the local (non-integration) path performs no network I/O.

Any attempt to open a socket during the offline pipeline or home render fails the
test loudly.
"""
import socket

from kitchen_prep import config
from kitchen_prep.gemini.client import OfflineClient
from kitchen_prep.orchestrator import run_daily_prep
from kitchen_prep.render.html import render_home


def test_offline_pipeline_and_render_use_no_network(monkeypatch, tmp_store):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted in a local test")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)

    plan = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    assert plan["date"] == config.DEMO_DATE
    assert plan["forecast"]["forecast_source"] == "deterministic_fallback"

    html = render_home(plan)
    assert "<html" in html.lower()
