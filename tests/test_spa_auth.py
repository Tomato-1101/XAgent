"""リモートアクセス対応のテスト: API_TOKEN 認証と SPA(frontend/dist)配信。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import xagent.api.main as main
from xagent.api.deps import db_session, get_formatter, get_x_client
from xagent.api.main import app
from xagent.config import Settings
from xagent.models import BlackoutSettings
from tests.conftest import FakeFormatter, FakeXClient


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(BlackoutSettings(enabled=False))
        s.commit()

    def _session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[db_session] = _session
    app.dependency_overrides[get_formatter] = lambda: FakeFormatter()
    app.dependency_overrides[get_x_client] = lambda: FakeXClient()
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def with_token(monkeypatch):
    """API_TOKEN を設定した状態をシミュレートする(.env には触れない)。"""
    settings = Settings(_env_file=None, api_token="sekrit")
    monkeypatch.setattr("xagent.api.deps.get_settings", lambda: settings)


# ---- 認証: API_TOKEN 設定時 ----

@pytest.mark.parametrize("path", ["/drafts", "/status", "/me"])
def test_token_required_when_configured(client, with_token, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-API-Token": "wrong"}).status_code == 401
    assert client.get(path, headers={"X-API-Token": "sekrit"}).status_code == 200


def test_health_stays_open_with_token(client, with_token):
    # launchd死活確認・ログイン前の接続確認に使うため /health は常に開放。
    assert client.get("/health").status_code == 200


# ---- 認証: 未設定時は素通し(ローカル運用の挙動不変) ----

@pytest.mark.parametrize("path", ["/drafts", "/status", "/me", "/health"])
def test_open_when_token_not_configured(client, path):
    assert client.get(path).status_code == 200


# ---- SPA 配信 ----

def test_spa_index_served(client, monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>XAgent</title>")
    monkeypatch.setattr(main, "FRONTEND_DIST", tmp_path)
    res = client.get("/")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert res.headers["cache-control"] == "no-cache"


def test_spa_index_missing_returns_503(client, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "FRONTEND_DIST", tmp_path)
    res = client.get("/")
    assert res.status_code == 503
    assert "npm run build" in res.json()["detail"]


def test_health_not_shadowed_by_spa_route(client):
    # "/" 追加後も既存エンドポイントが優先されること。
    assert client.get("/health").json()["status"] == "ok"
