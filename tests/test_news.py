"""ニュース速報の下書き生成(XNewsBot連携)のテスト。

XNewsBotのDB(genredigest/newsitem)を一時SQLiteで再現し、
新着抽出(ジャンル絞り・処理済みカーソル)・下書き作成(post/quote/URL2ツイート目)・
設定API・run-onceジョブを検証する。実DB・実LLMには触れない。
"""

from __future__ import annotations

import json
import sqlite3

from xagent import news as news_mod
from xagent.models import DraftKind, NewsSettings
from tests.conftest import FakeFormatter
from tests.test_api import client, wait_job  # noqa: F401 (fixture)


def _make_xnews_db(path, digests):
    """XNewsBotのスキーマを最小再現して投入する。

    digests: [(id, genre, items)] / items: [(title, summary, source_tweets, source_urls)]
    """
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE genredigest (id INTEGER PRIMARY KEY, digest_date DATE NOT NULL,"
        " slot VARCHAR NOT NULL, genre VARCHAR NOT NULL, created_at DATETIME NOT NULL)"
    )
    con.execute(
        "CREATE TABLE newsitem (id INTEGER PRIMARY KEY, genre_digest_id INTEGER NOT NULL,"
        " genre VARCHAR NOT NULL, importance VARCHAR NOT NULL, rank INTEGER NOT NULL,"
        " title VARCHAR NOT NULL, summary VARCHAR NOT NULL, source_urls JSON,"
        " source_tweets JSON, top_view_count INTEGER NOT NULL, detail VARCHAR DEFAULT '',"
        " genres JSON DEFAULT '[]')"
    )
    for did, genre, items in digests:
        con.execute(
            "INSERT INTO genredigest VALUES (?, '2026-06-12', 'morning', ?, '2026-06-12 00:00:00')",
            (did, genre),
        )
        for rank, (title, summary, tweets, urls) in enumerate(items):
            con.execute(
                "INSERT INTO newsitem (genre_digest_id, genre, importance, rank, title,"
                " summary, source_urls, source_tweets, top_view_count) VALUES (?,?,?,?,?,?,?,?,?)",
                (did, genre, "small", rank, title, summary,
                 json.dumps(urls), json.dumps(tweets), 1000),
            )
    con.commit()
    con.close()
    return str(path)


def _db_with_ai_news(tmp_path, *, with_tweet=False):
    tweets = (
        [{"text": "公式発表のポスト", "author": "openai",
          "url": "https://x.com/openai/status/123456789", "views": 50000}]
        if with_tweet
        else []
    )
    return _make_xnews_db(
        tmp_path / "xnewsbot.db",
        [
            (1, "AI", [("GPT-6発表", "OpenAIが新モデルを発表", tweets,
                        ["https://example.com/gpt6"])]),
            (2, "政治", [("対象外ニュース", "ジャンル対象外", [], [])]),
        ],
    )


def test_fetch_new_items_filters_genre_and_advances_cursor(session, tmp_path):
    db = _db_with_ai_news(tmp_path)
    settings = NewsSettings()  # 既定ジャンル: AI/テクノロジー
    items, max_id = news_mod.fetch_new_items(settings, db_path=db)
    assert [i["title"] for i in items] == ["GPT-6発表"]  # 政治は対象外
    assert max_id == 2  # 対象外ジャンルも含む全体のMAX(再処理しないため)
    # カーソル以降に新着が無ければ空
    settings.last_digest_id = 2
    items2, _ = news_mod.fetch_new_items(settings, db_path=db)
    assert items2 == []


def test_run_news_once_creates_post_draft_and_marks_processed(session, tmp_path):
    db = _db_with_ai_news(tmp_path)
    f = FakeFormatter()
    res = news_mod.run_news_once(session, f, progress=None, db_path=db)
    assert res["news_items"] == 1
    assert res["created"] == 1
    assert f.news_calls[0]["max_n"] == 3  # 既定の max_posts_per_run
    from xagent.service import get_draft

    d = get_draft(session, res["draft_ids"][0])
    assert d.kind == DraftKind.POST
    assert "[ニュース速報]" in d.source_text
    assert json.loads(d.segments_json) == ["【速報】GPT-6発表"]
    # 処理済みカーソルが進み、2回目は何も作らない(同じニュースを二度生成しない)
    assert news_mod.get_news_settings(session).last_digest_id == 2
    res2 = news_mod.run_news_once(session, FakeFormatter(), db_path=db)
    assert res2 == {"news_items": 0, "created": 0, "draft_ids": []}


def test_run_news_once_quote_format_creates_quote_draft(session, tmp_path):
    db = _db_with_ai_news(tmp_path, with_tweet=True)
    f = FakeFormatter()
    items, _ = news_mod.fetch_new_items(NewsSettings(), db_path=db)
    f.news_return = [{
        "news_index": 0, "format": "quote", "text": "【速報】一次情報を引用",
        "source_url": None, "reason": "公式発表は引用RT(N2)", "item": items[0],
    }]
    res = news_mod.run_news_once(session, f, db_path=db)
    from xagent.service import get_draft

    d = get_draft(session, res["draft_ids"][0])
    assert d.kind == DraftKind.QUOTE
    assert d.target_tweet_id == "123456789"  # 元ポストURLからID抽出
    assert d.target_handle == "openai"
    assert d.target_text == "公式発表のポスト"


def test_run_news_once_source_url_goes_to_second_segment(session, tmp_path):
    """ソースURLは本文でなく2ツイート目(リプ欄)に置く(外部リンクのリーチ減点対策)。"""
    db = _db_with_ai_news(tmp_path)
    f = FakeFormatter()
    items, _ = news_mod.fetch_new_items(NewsSettings(), db_path=db)
    f.news_return = [{
        "news_index": 0, "format": "post", "text": "【速報】本文にURLなし",
        "source_url": "https://example.com/gpt6", "reason": "N1", "item": items[0],
    }]
    res = news_mod.run_news_once(session, f, db_path=db)
    from xagent.service import get_draft

    d = get_draft(session, res["draft_ids"][0])
    assert json.loads(d.segments_json) == ["【速報】本文にURLなし", "https://example.com/gpt6"]


def test_fetch_new_items_first_run_limits_to_latest_date(session, tmp_path):
    """初回(カーソル0)は全履歴でなく最新の収集日だけを対象にする(過去分の大量生成防止)。"""
    db = _make_xnews_db(
        tmp_path / "xnewsbot.db",
        [
            (1, "AI", [("古いニュース", "数日前の分", [], [])]),
            (2, "AI", [("今日のニュース", "最新の分", [], [])]),
        ],
    )
    con = sqlite3.connect(db)
    con.execute("UPDATE genredigest SET digest_date='2026-06-10' WHERE id=1")
    con.commit()
    con.close()
    items, max_id = news_mod.fetch_new_items(NewsSettings(), db_path=db)
    assert [i["title"] for i in items] == ["今日のニュース"]
    assert max_id == 2


def test_fetch_new_items_missing_db_raises(session, tmp_path):
    try:
        news_mod.fetch_new_items(NewsSettings(), db_path=str(tmp_path / "no_such.db"))
    except news_mod.NewsSourceUnavailable as e:
        assert "見つかりません" in str(e)
    else:
        raise AssertionError("NewsSourceUnavailable が送出されるべき")


def test_news_settings_api_defaults_and_update(client):
    s = client.get("/news/settings").json()
    assert s["auto_news_enabled"] is False  # 既定OFF(乱造防止)
    assert s["max_posts_per_run"] == 3
    s2 = client.put(
        "/news/settings", json={"auto_news_enabled": True, "max_posts_per_run": 5}
    ).json()
    assert s2["auto_news_enabled"] is True
    assert s2["max_posts_per_run"] == 5


def test_news_run_once_api_job(client, tmp_path, monkeypatch):
    """run-once はジョブ化され、結果(生成件数)はジョブ経由で返る。"""
    from xagent.config import Settings

    db = _db_with_ai_news(tmp_path)
    monkeypatch.setattr(
        "xagent.news.get_settings",
        lambda: Settings(_env_file=None, xnewsbot_db_path=db),
    )
    j = wait_job(client, client.post("/news/run-once"))
    assert j["status"] == "done"
    assert j["result"]["created"] == 1


def test_news_recent_api_unavailable_returns_friendly(client, monkeypatch):
    """XNewsBotのDBが読めなくても /news/recent は500にせず案内を返す。"""
    from xagent.config import Settings

    monkeypatch.setattr(
        "xagent.news.get_settings",
        lambda: Settings(_env_file=None, xnewsbot_db_path="/no/such/path.db"),
    )
    r = client.get("/news/recent").json()
    assert r["available"] is False
    assert "見つかりません" in r["error"]
