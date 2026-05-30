"""Smoke tests for the dashboard API (no network: read endpoints hit the ledger)."""
import pytest

from liquidsky.config import load_config

flask = pytest.importorskip("flask")
from liquidsky.web import create_app  # noqa: E402


@pytest.fixture
def client():
    app = create_app(load_config(env="paper"))
    app.config.update(TESTING=True)
    return app.test_client()


def test_index_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"LiquidSky" in resp.data


def test_status_endpoint_shape(client):
    data = client.get("/api/status").get_json()
    for key in ("env", "cash", "equity", "open_count", "positions", "runner"):
        assert key in data
    assert data["env"] == "paper"
    assert data["runner"]["looping"] is False


def test_report_and_equity_endpoints(client):
    report = client.get("/api/report").get_json()
    for key in ("trades_opened", "trades_closed", "win_rate", "realized_pnl",
                "peak_equity", "max_drawdown_pct"):
        assert key in report
    equity = client.get("/api/equity").get_json()
    assert "points" in equity and isinstance(equity["points"], list)


def test_logs_endpoint_filters_by_after(client):
    data = client.get("/api/logs?after=0").get_json()
    assert "lines" in data and isinstance(data["lines"], list)
