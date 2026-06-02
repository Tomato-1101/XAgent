"""twitterapi.io 読み取りクライアントのテスト。

httpx.MockTransport で実際の HTTP 経路(ヘッダ/パラメータ/raise_for_status/JSON)を通す。
外部ネットワークには出ない。
"""

from datetime import datetime

import httpx
import pytest

from xagent import twitterapi_client as tac
from xagent.twitterapi_client import TwitterApiIoClient, TwitterApiIoError


def _client_with(monkeypatch, handler) -> TwitterApiIoClient:
    """_get が内部生成する httpx.Client に MockTransport を差し込む。"""
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(tac.httpx, "Client", fake_client)
    return TwitterApiIoClient("test-key")


def _tweet(i, text="本文", uid="9", uname="someone", **extra):
    return {"id": str(i), "text": text, "author": {"id": uid, "userName": uname}, **extra}


def test_get_tweet_normalizes_and_sends_key(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("X-API-Key")
        seen["path"] = request.url.path
        seen["tweet_ids"] = request.url.params.get("tweet_ids")
        return httpx.Response(200, json={"tweets": [_tweet(123, "やあ")], "status": "success"})

    c = _client_with(monkeypatch, handler)
    out = c.get_tweet("123")
    assert out == {"id": "123", "text": "やあ", "author_id": "9", "author_handle": "someone"}
    assert seen["key"] == "test-key"
    assert seen["path"] == "/twitter/tweets"
    assert seen["tweet_ids"] == "123"


def test_get_tweet_none_when_empty(monkeypatch):
    c = _client_with(monkeypatch, lambda r: httpx.Response(200, json={"tweets": []}))
    assert c.get_tweet("999") is None


def test_timeline_pagination_and_since_id(monkeypatch):
    pages = {
        "": {"tweets": [_tweet(105), _tweet(104), _tweet(103)],
             "has_next_page": True, "next_cursor": "c1"},
        "c1": {"tweets": [_tweet(102), _tweet(101), _tweet(100)],
               "has_next_page": True, "next_cursor": "c2"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        cur = request.url.params.get("cursor", "")
        return httpx.Response(200, json=pages[cur])

    c = _client_with(monkeypatch, handler)
    # since_id=102 → 102以下に当たった時点で打ち切り(105,104,103 のみ)
    out = c.get_user_timeline("777", since_id="102")
    assert [t["id"] for t in out] == ["105", "104", "103"]


def test_timeline_keeps_newer_when_page_unordered(monkeypatch):
    # 同一ページ内が完全な降順でなくても、since_id超の新しい要素は取りこぼさない
    page = {"tweets": [_tweet(105), _tweet(103), _tweet(106), _tweet(102)],
            "has_next_page": False}
    c = _client_with(monkeypatch, lambda r: httpx.Response(200, json=page))
    out = c.get_user_timeline("777", since_id="103")
    assert sorted(t["id"] for t in out) == ["105", "106"]  # 103以下(103,102)は除外


def test_last_tweets_nested_data_extraction(monkeypatch):
    # last_tweets は tweets を data.tweets にネストする(他エンドポイントはトップ直下)
    page = {"status": "success", "data": {"tweets": [_tweet(50)]}, "has_next_page": False}
    c = _client_with(monkeypatch, lambda r: httpx.Response(200, json=page))
    out = c.get_user_timeline("777")
    assert [t["id"] for t in out] == ["50"]


def test_get_following_uses_username_param(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["userName"] = request.url.params.get("userName")
        return httpx.Response(200, json={
            "followings": [{"id": "1", "userName": "aplusk"}], "has_next_page": False,
        })

    c = _client_with(monkeypatch, handler)
    out = c.get_following("@elonmusk", max_total=3)
    assert seen["userName"] == "elonmusk"   # userId ではなく userName(+@除去)
    assert out == [{"id": "1", "username": "aplusk"}]


def test_search_normalizes(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("queryType") == "Latest"
        return httpx.Response(200, json={"tweets": [_tweet(1, "検索結果", uname="famous")]})

    c = _client_with(monkeypatch, handler)
    out = c.search_recent("AI")
    assert out[0]["author_handle"] == "famous"


def test_user_tweets_full_parses_created_at(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "tweets": [_tweet(
                1, createdAt="Wed Oct 10 20:19:24 +0000 2018", likeCount=5, retweetCount=2
            )],
            "has_next_page": False,
        })

    c = _client_with(monkeypatch, handler)
    out = c.get_user_tweets_full("777", max_total=50)
    assert isinstance(out[0]["created_at"], datetime)
    assert out[0]["created_at"].year == 2018
    assert out[0]["like_count"] == 5
    assert out[0]["retweet_count"] == 2


def test_http_error_raises(monkeypatch):
    c = _client_with(monkeypatch, lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(TwitterApiIoError):
        c.get_tweet("1")


def test_api_status_error_raises(monkeypatch):
    c = _client_with(
        monkeypatch,
        lambda r: httpx.Response(200, json={"status": "error", "message": "資格情報不正"}),
    )
    with pytest.raises(TwitterApiIoError):
        c.get_tweet("1")


def test_get_user_by_username_normalizes(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"id": 42, "userName": "famous"}})

    c = _client_with(monkeypatch, handler)
    assert c.get_user_by_username("@famous") == {"id": "42", "username": "famous"}


def test_list_members_normalize_and_paginate(monkeypatch):
    pages = {
        "": {
            "members": [{
                "id": "11", "userName": "alice", "name": "Alice",
                "description": "bio", "profilePicture": "http://img", "followers": 42,
            }],
            "has_next_page": True, "next_cursor": "c1",
        },
        "c1": {
            "members": [{"id": "12", "userName": "bob", "name": "Bob", "followers": 5}],
            "has_next_page": False,
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("listId") == "999"
        return httpx.Response(200, json=pages[request.url.params.get("cursor", "")])

    c = _client_with(monkeypatch, handler)
    out = c.get_list_members("999")
    assert out[0] == {
        "id": "11", "username": "alice", "name": "Alice", "description": "bio",
        "profile_image_url": "http://img", "followers_count": 42,
    }
    assert [m["username"] for m in out] == ["alice", "bob"]
    assert out[1]["description"] == ""          # description欠落→""
    assert out[1]["profile_image_url"] is None  # profilePicture欠落→None
