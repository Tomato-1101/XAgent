import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from xagent.api.deps import db_session, get_formatter, get_x_client
from xagent.api.main import app
from xagent.models import BlackoutSettings
from tests.conftest import FakeFormatter, FakeXClient


@pytest.fixture
def client():
    # StaticPool: in-memory SQLiteを単一コネクションで共有(別スレッドのハンドラからも見える)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        # 制限帯は既定で無効化(既存の投稿系テストを実時刻に依存させない)
        s.add(BlackoutSettings(enabled=False))
        s.commit()
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


def test_status_endpoint(client):
    """/status は予約スケジューラの稼働状況を返す。TestClientはlifespanを起動しない=未稼働。"""
    body = client.get("/status").json()
    for k in [
        "scheduler_enabled", "scheduler_running", "healthy",
        "queue_interval_seconds", "posting_enabled", "auto_monitor_enabled",
    ]:
        assert k in body
    assert body["scheduler_running"] is False  # lifespan未起動なのでスケジューラは無し
    assert body["healthy"] is False
    assert body["posting_enabled"] is True
    assert body["auto_monitor_enabled"] is False  # 既定OFF


def test_recast_endpoint_switches_kind(client):
    """Inboxの型切替: 返信案を引用RT案へ作り直す(kindが切り替わる)。"""
    did = client.post(
        "/compose/command", json={"action": "reply", "text": "返信文", "target_tweet_id": "555"}
    ).json()["id"]
    assert client.get(f"/drafts/{did}").json()["kind"] == "reply"
    r = client.post(f"/drafts/{did}/recast", json={"to": "quote"})
    assert r.status_code == 200
    assert r.json()["kind"] == "quote"


def test_recast_endpoint_409_on_approved(client):
    """承認済み(DRAFT以外)は型を切り替えられず409。"""
    did = client.post(
        "/compose/command", json={"action": "reply", "text": "返信文", "target_tweet_id": "555"}
    ).json()["id"]
    client.post(f"/drafts/{did}/approve")
    assert client.post(f"/drafts/{did}/recast", json={"to": "quote"}).status_code == 409


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


def test_post_now_returns_502_on_x_rejection(client):
    """X側が引用不可・権限不足等で拒否したら、500ではなく502+理由を返し、下書きは未投稿で残す。"""
    from xagent.api.deps import get_x_client
    from xagent.api.main import app
    from xagent.x_client import XClientError

    did = client.post("/compose", json={"text": "引用できない投稿"}).json()["id"]
    client.post(f"/drafts/{did}/approve")

    class _ForbidX(FakeXClient):
        def post(self, *a, **k):
            raise XClientError(
                "投稿に失敗しました: 403 Forbidden Quoting this post is not allowed"
            )

    app.dependency_overrides[get_x_client] = lambda: _ForbidX()
    resp = client.post(f"/drafts/{did}/post")
    assert resp.status_code == 502
    assert "Quoting this post is not allowed" in resp.json()["detail"]
    # 拒否されても下書きは投稿済みにならず承認済みのまま残る
    assert client.get(f"/drafts/{did}").json()["status"] == "approved"


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


def test_recommended_times_endpoint(client):
    r = client.get("/schedule/recommended").json()
    assert len(r["slots"]) >= 4
    assert all("hour" in s and "label" in s for s in r["slots"])
    assert len(r["next_slots"]) == 3
    assert r["sources"]


def test_compose_rejects_bad_media(client):
    # 画像と動画の混在は 400 で弾く
    r = client.post("/compose", json={"text": "x", "media_paths": ["a.jpg", "b.mp4"]})
    assert r.status_code == 400


def test_media_upload(client, monkeypatch, tmp_path):
    import xagent.media as media_mod

    monkeypatch.setattr(media_mod, "media_dir", lambda: str(tmp_path))
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    r = client.post("/media/upload", files={"file": ("pic.png", png, "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "image"
    assert body["path"].endswith(".png")
    # 不正拡張子は 400
    bad = client.post("/media/upload", files={"file": ("x.txt", b"hi", "text/plain")})
    assert bad.status_code == 400


def test_compose_variations_endpoint(client):
    rows = client.post("/compose/variations", json={"text": "ネタ", "n_variations": 3}).json()
    assert len(rows) == 3
    assert all(r["status"] == "draft" for r in rows)


def test_monitor_settings_toggle(client):
    # 既定値(mentions は手動返信方針のため既定OFF)
    s = client.get("/monitor/settings").json()
    assert s["mentions_enabled"] is False
    assert s["manual_targets_enabled"] is True
    assert s["following_enabled"] is False
    # フォロー監視をオンにする
    s2 = client.put("/monitor/settings", json={"following_enabled": True}).json()
    assert s2["following_enabled"] is True
    # 他のトグルは維持(指定しなかった manual_targets は True のまま)
    assert s2["manual_targets_enabled"] is True


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


# --- 指令(interpret / command) ---------------------------------------------

def test_interpret_extracts_tweet_url(client):
    r = client.post(
        "/compose/interpret",
        json={"text": "https://x.com/BitoFCE/status/123 をリポストして: コメント"},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["target_tweet_id"] == "123"
    assert d["target_handle"] == "BitoFCE"


def test_interpret_raw_hint(client):
    r = client.post("/compose/interpret", json={"text": "これそのまま投稿して: 本文"})
    assert r.status_code == 200
    assert r.json()["raw"] is True


def test_command_quote_creates_quote_draft(client):
    r = client.post(
        "/compose/command",
        json={
            "action": "quote",
            "text": "自分のコメント",
            "target_tweet_id": "999",
            "target_handle": "famous",
        },
    )
    assert r.status_code == 200
    d = r.json()
    assert d["kind"] == "quote"
    assert d["target_tweet_id"] == "999"
    assert "自分のコメント" in d["segments"][0]


def test_command_quote_requires_target(client):
    r = client.post("/compose/command", json={"action": "quote", "text": "コメントだけ"})
    assert r.status_code == 400


def test_command_reply_creates_reply_draft(client):
    r = client.post(
        "/compose/command",
        json={
            "action": "reply",
            "text": "自分の返信文",
            "target_tweet_id": "888",
            "target_handle": "someone",
        },
    )
    assert r.status_code == 200
    d = r.json()
    assert d["kind"] == "reply"
    assert d["target_tweet_id"] == "888"
    assert "自分の返信文" in d["segments"][0]


def test_command_reply_requires_target(client):
    r = client.post("/compose/command", json={"action": "reply", "text": "返信文だけ"})
    assert r.status_code == 400


def test_command_reply_fetches_target_text(client):
    """資格情報があれば get_tweet で元ポスト本文を取得し target_text に保存する。"""
    from xagent.api.deps import get_x_client_optional

    fx = FakeXClient(
        tweets={"888": {"id": "888", "text": "相手の元ポスト本文", "author_handle": "someone"}}
    )
    app.dependency_overrides[get_x_client_optional] = lambda: fx
    r = client.post(
        "/compose/command",
        json={"action": "reply", "text": "返信", "target_tweet_id": "888"},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["target_text"] == "相手の元ポスト本文"
    assert d["target_handle"] == "someone"


def test_quote_from_url_ai_generates_quote(client):
    """InboxのURL手動入力 → 相手投稿本文からAIが引用コメントを生成し引用案を作る。"""
    from xagent.api.deps import get_x_client_optional

    fx = FakeXClient(
        tweets={"777": {"id": "777", "text": "引用元の本文", "author_handle": "famous"}}
    )
    app.dependency_overrides[get_x_client_optional] = lambda: fx
    try:
        r = client.post(
            "/compose/quote-from-url", json={"url": "https://x.com/famous/status/777"}
        )
        assert r.status_code == 200
        d = r.json()
        assert d["kind"] == "quote"
        assert d["target_tweet_id"] == "777"
        assert d["target_text"] == "引用元の本文"
        assert d["target_handle"] == "famous"
    finally:
        app.dependency_overrides.pop(get_x_client_optional, None)


def test_quote_from_url_rejects_non_url(client):
    r = client.post("/compose/quote-from-url", json={"url": "ただの文字列"})
    assert r.status_code == 400


def test_command_raw_post_skips_formatting(client):
    r = client.post("/compose/command", json={"action": "post", "text": "原文ママ", "raw": True})
    d = r.json()
    assert d["kind"] == "post"
    assert d["segments"][0] == "原文ママ"


def test_compose_raw_flag(client):
    r = client.post("/compose", json={"text": "そのままの本文", "raw": True})
    assert r.json()["segments"][0] == "そのままの本文"


# --- 取消(ゴミ箱)・復元 ----------------------------------------------------

def test_cancel_and_restore_draft(client):
    did = client.post("/compose", json={"text": "取消する"}).json()["id"]
    canceled = client.post(f"/drafts/{did}/cancel")
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceled"
    # ゴミ箱タブ(status=canceled)で見える
    rows = client.get("/drafts", params={"status": "canceled"}).json()
    assert any(r["id"] == did for r in rows)
    # 復元 → 下書きへ戻る
    restored = client.post(f"/drafts/{did}/restore")
    assert restored.status_code == 200
    assert restored.json()["status"] == "draft"


def test_cancel_queued_clears_schedule(client):
    did = client.post("/compose", json={"text": "予約して取消"}).json()["id"]
    client.post(f"/drafts/{did}/approve")
    q = client.post(f"/drafts/{did}/queue", json={"mode": "optimal"}).json()
    assert q["scheduled_at"] is not None
    canceled = client.post(f"/drafts/{did}/cancel").json()
    assert canceled["status"] == "canceled"
    assert canceled["scheduled_at"] is None  # 予約解除(自動投稿されない)


def test_cancel_posted_returns_409(client):
    did = client.post("/compose", json={"text": "投稿済みは取消不可"}).json()["id"]
    client.post(f"/drafts/{did}/approve")
    client.post(f"/drafts/{did}/post")
    assert client.post(f"/drafts/{did}/cancel").status_code == 409


def test_analytics_cost_split(client):
    """投稿すると X API(write)コストが付き、x_api 側に集計される。"""
    did = client.post("/compose", json={"text": "コスト計上の投稿"}).json()["id"]
    client.post(f"/drafts/{did}/approve")
    client.post(f"/drafts/{did}/post")
    c = client.get("/analytics/cost").json()
    assert "x_api" in c and "claude_api" in c and "total_usd" in c
    assert c["x_api"]["cost_usd"] > 0          # 投稿(write)コスト
    assert c["x_api"]["by_kind"].get("write", {}).get("units") == 1
    # FakeFormatter はトークン使用量を持たないため Claude コストは0
    assert c["claude_api"]["cost_usd"] == 0.0
    assert c["total_usd"] == c["x_api"]["cost_usd"]


# --- 制限時間帯(ブラックアウト) --------------------------------------------

def test_blackout_get_default(client):
    b = client.get("/schedule/blackout").json()
    assert set(["enabled", "weekdays", "windows"]).issubset(b.keys())
    assert isinstance(b["weekdays"], list)


def test_blackout_put_and_status(client):
    r = client.put(
        "/schedule/blackout",
        json={
            "enabled": True,
            "weekdays": [0, 1, 2, 3, 4],
            "windows": [["09:00", "12:00"], ["13:00", "19:00"]],
        },
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is True
    # 月曜 JST 11:00 = UTC 02:00 → 制限帯
    s = client.get("/schedule/blackout/status", params={"at": "2026-06-01T02:00:00"}).json()
    assert s["blackout"] is True
    assert s["reason"]
    # 土曜 → 制限なし
    s2 = client.get("/schedule/blackout/status", params={"at": "2026-06-06T02:00:00"}).json()
    assert s2["blackout"] is False


# --- 自分の直近投稿・通常リポスト -------------------------------------------

def test_posts_recent_empty(client):
    assert client.get("/posts/recent").json() == []


def test_posts_refresh_and_recent(client):
    from datetime import datetime, timedelta, timezone

    created = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    client.fake_x._full_tweets = {
        "1": [
            {"id": "111", "text": "自分の投稿A", "created_at": created,
             "like_count": 5, "retweet_count": 2},
        ]
    }
    r = client.post("/posts/refresh").json()
    assert len(r) == 1
    assert r[0]["tweet_id"] == "111"
    assert r[0]["url"].endswith("/status/111")
    # 取得後は recent でも返る
    assert len(client.get("/posts/recent").json()) == 1


def test_posts_repost_now(client):
    r = client.post("/posts/999/repost", json={"mode": "now", "text": "再拡散"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "posted"
    assert body["kind"] == "repost"
    assert client.fake_x.retweeted == ["999"]


def test_posts_repost_time_queues(client):
    r = client.post(
        "/posts/999/repost",
        json={"mode": "time", "scheduled_at": "2030-01-01T00:00:00+09:00", "text": "予約RT"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["scheduled_at"] is not None
    assert client.fake_x.retweeted == []  # まだ発火しない


def test_posts_repost_blocked_423_then_override(client):
    # 全曜日・ほぼ終日を制限帯にして、現在時刻に依存せずブロックを再現する
    client.put(
        "/schedule/blackout",
        json={"enabled": True, "weekdays": [0, 1, 2, 3, 4, 5, 6], "windows": [["00:00", "23:59"]]},
    )
    r = client.post("/posts/999/repost", json={"mode": "now", "text": "突破前"})
    assert r.status_code == 423
    assert client.fake_x.retweeted == []
    # 二段階確認(override=True)で突破 → 200
    r2 = client.post("/posts/1000/repost", json={"mode": "now", "text": "突破", "override": True})
    assert r2.status_code == 200
    assert "1000" in client.fake_x.retweeted


def test_add_target_list_stores_list_id(client):
    """リストを対象に追加: kind=list + list_id で登録され、user_id解決は走らない。"""
    r = client.post("/targets", json={"kind": "list", "handle": "絡み候補A", "list_id": "L1"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "list"
    assert body["list_id"] == "L1"
    assert body["handle"] == "絡み候補A"
    assert body["user_id"] is None
    # 一覧に出る
    assert any(t["list_id"] == "L1" for t in client.get("/targets").json())


def test_add_target_list_requires_list_id(client):
    r = client.post("/targets", json={"kind": "list", "handle": "絡み候補A"})
    assert r.status_code == 400
