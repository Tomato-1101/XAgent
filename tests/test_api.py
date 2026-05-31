import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from xagent.api.deps import db_session, get_formatter, get_x_client
from xagent.api.main import app
from tests.conftest import FakeFormatter, FakeXClient


@pytest.fixture
def client():
    # StaticPool: in-memory SQLiteを単一コネクションで共有(別スレッドのハンドラからも見える)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    fx = FakeXClient()

    def _session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[db_session] = _session
    app.dependency_overrides[get_formatter] = lambda: FakeFormatter()
    app.dependency_overrides[get_x_client] = lambda: fx
    c = TestClient(app)
    c.fake_x = fx
    yield c
    app.dependency_overrides.clear()


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_me_endpoint(client):
    assert client.get("/me").json()["username"] == "tester"


def test_preview_endpoint(client):
    r = client.post("/compose/preview", json={"text": "あ" * 200})
    d = r.json()
    assert d["folded"] is True
    assert len(d["segments"]) == 2


def test_compose_approve_post_flow(client):
    # 整形して下書き作成
    r = client.post("/compose", json={"text": "今日学んだXのコツ"})
    assert r.status_code == 200
    draft = r.json()
    assert draft["status"] == "draft"
    did = draft["id"]

    # 未承認では投稿できない(409)
    assert client.post(f"/drafts/{did}/post").status_code == 409

    # 承認 → 即時投稿
    assert client.post(f"/drafts/{did}/approve").json()["status"] == "approved"
    posted = client.post(f"/drafts/{did}/post").json()
    assert posted["status"] == "posted"
    assert posted["posted_tweet_id"] is not None
    assert client.fake_x.posted[0]["text"] == "今日学んだXのコツ"


def test_queue_optimal(client):
    did = client.post("/compose", json={"text": "予約したい投稿"}).json()["id"]
    client.post(f"/drafts/{did}/approve")
    r = client.post(f"/drafts/{did}/queue", json={"mode": "optimal"})
    body = r.json()
    assert body["status"] == "queued"
    assert body["scheduled_at"] is not None


def test_style_put_get(client):
    client.put("/style", json={"guide_text": "一人称は俺。断定口調。"})
    assert "俺" in client.get("/style").json()["guide_text"]


def test_list_drafts_filter(client):
    client.post("/compose", json={"text": "A"})
    client.post("/compose", json={"text": "B"})
    rows = client.get("/drafts", params={"status": "draft"}).json()
    assert len(rows) == 2


def test_analytics_summary(client):
    client.post("/compose", json={"text": "X"})
    s = client.get("/analytics/summary").json()
    assert s["draft_counts"]["draft"] == 1


def test_compose_variations_endpoint(client):
    rows = client.post("/compose/variations", json={"text": "ネタ", "n_variations": 3}).json()
    assert len(rows) == 3
    assert all(r["status"] == "draft" for r in rows)


def test_monitor_settings_toggle(client):
    # 既定値
    s = client.get("/monitor/settings").json()
    assert s["mentions_enabled"] is True
    assert s["following_enabled"] is False
    # フォロー監視をオンにする
    s2 = client.put("/monitor/settings", json={"following_enabled": True}).json()
    assert s2["following_enabled"] is True
    # 他のトグルは維持
    assert s2["mentions_enabled"] is True


def test_api_token_auth(client, monkeypatch):
    """api_token を設定したら書き込み系は X-API-Token 必須。/health は常に開放。"""
    from xagent.config import reload_settings

    monkeypatch.setenv("API_TOKEN", "secret")
    reload_settings()
    try:
        # トークン未提示 → 401
        assert client.post("/compose", json={"text": "x"}).status_code == 401
        # 正しいトークン → 通る
        ok = client.post("/compose", json={"text": "x"}, headers={"X-API-Token": "secret"})
        assert ok.status_code == 200
        # 開放エンドポイントは常に通る
        assert client.get("/health").status_code == 200
    finally:
        monkeypatch.delenv("API_TOKEN", raising=False)
        reload_settings()


def test_profiles_learn_and_list(client):
    from datetime import datetime, timezone

    from xagent.api.deps import get_x_client
    from xagent.api.main import app
    from tests.conftest import FakeXClient

    fx = FakeXClient(
        users={"creator": {"id": "77", "username": "creator"}},
        full_tweets={
            "77": [
                {"id": "1", "text": "学びになる投稿", "author_id": "77",
                 "created_at": datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc),
                 "like_count": 30, "retweet_count": 2},
            ]
        },
    )
    app.dependency_overrides[get_x_client] = lambda: fx
    prof = client.post("/profiles/learn", json={"handle": "creator"}).json()
    assert prof["handle"] == "creator"
    assert prof["posts_fetched"] == 1
    assert prof["is_self"] is False
    listed = client.get("/profiles").json()
    assert any(p["handle"] == "creator" for p in listed)
