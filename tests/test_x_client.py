"""XClient.get_tweet の正規化(tweepyレスポンス→dict)のテスト。外部API不要。"""

from types import SimpleNamespace

import tweepy

from xagent.x_client import XClient


class _FakeTweepy:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def get_tweet(self, **kwargs):
        self.calls.append(kwargs)
        return self._resp


def test_get_tweet_normalizes_response():
    from datetime import datetime, timezone

    created = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    resp = SimpleNamespace(
        data=SimpleNamespace(id=123, text="本文", author_id=999, created_at=created),
        includes={"users": [SimpleNamespace(username="famous")]},
    )
    fake = _FakeTweepy(resp)
    out = XClient(fake).get_tweet("123")
    assert out == {
        "id": "123",
        "text": "本文",
        "author_id": "999",
        "author_handle": "famous",
        "created_at": created,
    }
    # ハンドル取得のため expansions/user_fields を渡している
    assert fake.calls[0]["expansions"] == ["author_id"]
    # 投稿時刻を取るため tweet_fields に created_at を要求している
    assert "created_at" in fake.calls[0]["tweet_fields"]


def test_get_tweet_returns_none_when_no_data():
    fake = _FakeTweepy(SimpleNamespace(data=None, includes={}))
    assert XClient(fake).get_tweet("404") is None


def test_get_tweet_handles_missing_includes():
    resp = SimpleNamespace(
        data=SimpleNamespace(id=1, text="t", author_id=2),
        includes=None,
    )
    out = XClient(_FakeTweepy(resp)).get_tweet("1")
    assert out["author_handle"] is None
    assert out["author_id"] == "2"


# --- 読み取りバックエンド(twitterapi.io)優先 / 失敗時のみ公式フォールバック ----------

from xagent.twitterapi_client import TwitterApiIoError  # noqa: E402


class _FakeOfficial:
    """公式tweepyの最小フェイク。読み取り(get_tweet/mentions)と書込(retweet)、get_me。"""

    def __init__(self):
        self.retweeted = []
        self.mention_calls = 0
        self.list_writes = []  # リスト書込/公式リスト読取の記録

    def get_tweet(self, **kwargs):
        return SimpleNamespace(
            data=SimpleNamespace(id=1, text="公式本文", author_id=2),
            includes={"users": [SimpleNamespace(username="official_user")]},
        )

    def get_me(self, **kwargs):
        return SimpleNamespace(data=SimpleNamespace(id=1, username="me"))

    def get_users_mentions(self, **kwargs):
        self.mention_calls += 1
        return SimpleNamespace(data=[SimpleNamespace(id=11, text="公式メンション", author_id=3)])

    def retweet(self, tweet_id, **kwargs):
        self.retweeted.append(tweet_id)
        return None

    # --- リスト(書込 + 公式読取) ---
    def create_list(self, name, description=None, private=None):
        self.list_writes.append(("create_list", name, description, private))
        return SimpleNamespace(data={"id": "5001", "name": name})

    def update_list(self, id, name=None, description=None, private=None):
        self.list_writes.append(("update_list", id, name, description, private))

    def delete_list(self, id):
        self.list_writes.append(("delete_list", id))

    def add_list_member(self, id, user_id):
        self.list_writes.append(("add_list_member", id, user_id))

    def remove_list_member(self, id, user_id):
        self.list_writes.append(("remove_list_member", id, user_id))

    def get_owned_lists(self, id, **params):
        self.list_writes.append(("get_owned_lists", id))
        data = [SimpleNamespace(id=900, name="公式リスト", description="d", private=True, member_count=2)]
        return tweepy.Response(data=data, includes={}, errors=[], meta={})

    def get_list_members(self, id, **params):
        self.list_writes.append(("get_list_members", id))
        data = [
            SimpleNamespace(
                id=11, username="alice", name="Alice", description="bio",
                profile_image_url="http://img", public_metrics={"followers_count": 42},
            )
        ]
        return tweepy.Response(data=data, includes={}, errors=[], meta={})


class _FakeBackend:
    """twitterapi.io バックエンドのフェイク。fail=True で TwitterApiIoError を投げる。"""

    def __init__(self, fail=False, empty_members=False):
        self.fail = fail
        self.empty_members = empty_members  # 非公開リスト等で空が返る状況の再現
        self.calls = []

    def get_tweet(self, tweet_id):
        self.calls.append(("get_tweet", tweet_id))
        if self.fail:
            raise TwitterApiIoError("backend down")
        return {"id": str(tweet_id), "text": "backend本文", "author_id": "9", "author_handle": "bk"}

    def get_mentions(self, handle, since_id=None):
        self.calls.append(("get_mentions", handle, since_id))
        if self.fail:
            raise TwitterApiIoError("backend down")
        return [{"id": "21", "text": "backendメンション", "author_id": "3", "author_handle": "x"}]

    def get_following(self, user_name, max_total=100):
        self.calls.append(("get_following", user_name, max_total))
        if self.fail:
            raise TwitterApiIoError("backend down")
        return [{"id": "1", "username": "aplusk"}]

    def get_list_members(self, list_id, max_total=100):
        self.calls.append(("get_list_members", list_id, max_total))
        if self.fail:
            raise TwitterApiIoError("backend down")
        if self.empty_members:
            return []
        return [{
            "id": "99", "username": "bk_user", "name": "BK",
            "description": "", "profile_image_url": None, "followers_count": 7,
        }]

    def get_user_timeline(self, user_id, since_id=None):
        self.calls.append(("get_user_timeline", user_id, since_id))
        if self.fail:
            raise TwitterApiIoError("backend down")
        return [{"id": "31", "text": "backendTL", "author_id": str(user_id)}]


def test_read_uses_backend_when_present():
    backend = _FakeBackend()
    x = XClient(_FakeOfficial(), read_backend=backend)
    out = x.get_tweet("123")
    assert out["text"] == "backend本文"          # 公式ではなくバックエンド
    assert backend.calls == [("get_tweet", "123")]


def test_read_falls_back_to_official_on_backend_error():
    backend = _FakeBackend(fail=True)
    x = XClient(_FakeOfficial(), read_backend=backend)
    out = x.get_tweet("123")
    assert out["text"] == "公式本文"             # 失敗→公式で読み直し
    assert out["author_handle"] == "official_user"


def test_no_backend_uses_official():
    x = XClient(_FakeOfficial())                 # read_backend=None
    assert x.get_tweet("1")["text"] == "公式本文"


def test_user_timeline_no_official_fallback_skips_on_error():
    """official_fallback=False ではバックエンド失敗時に公式へ落とさず空を返す。

    監視の候補収集用: 公式APIは wait_on_rate_limit でレート制限時に最大15分ブロック
    するため、バルク読み取りでは失敗メンバーをスキップして先に進む。
    """
    backend = _FakeBackend(fail=True)
    x = XClient(_FakeOfficial(), read_backend=backend)
    assert x.get_user_timeline("u1", official_fallback=False) == []
    # 成功時は通常どおりバックエンドの結果を返す
    ok = XClient(_FakeOfficial(), read_backend=_FakeBackend())
    assert ok.get_user_timeline("u1", official_fallback=False)[0]["text"] == "backendTL"


def test_writes_always_use_official_even_with_backend():
    backend = _FakeBackend()
    official = _FakeOfficial()
    x = XClient(official, read_backend=backend)
    x.retweet("555")
    assert official.retweeted == ["555"]         # 書込は公式のみ
    assert backend.calls == []                   # バックエンドは触らない


def test_mentions_resolve_handle_via_official_get_me():
    backend = _FakeBackend()
    x = XClient(_FakeOfficial(), read_backend=backend)
    out = x.get_mentions("user-id-1")
    # 公式get_meで解決した自分のハンドル("me")でバックエンドに問い合わせる
    assert backend.calls == [("get_mentions", "me", None)]
    assert out[0]["text"] == "backendメンション"


def test_mentions_fallback_uses_official():
    backend = _FakeBackend(fail=True)
    official = _FakeOfficial()
    x = XClient(official, read_backend=backend)
    out = x.get_mentions("user-id-1")
    assert official.mention_calls == 1
    assert out[0]["text"] == "公式メンション"


def test_following_resolves_self_handle_for_backend():
    # twitterapi.io の followings は userName 必須。XClientは自ハンドルで引く。
    backend = _FakeBackend()
    x = XClient(_FakeOfficial(), read_backend=backend)
    out = x.get_following("user-id-1", max_total=3)
    assert backend.calls == [("get_following", "me", 3)]   # idではなく自ハンドル"me"
    assert out[0]["username"] == "aplusk"


# --- リスト: 書込は公式のみ / メンバー読取はバックエンド優先+公式フォールバック / 所有一覧は公式のみ ---

def test_list_writes_always_use_official():
    backend = _FakeBackend()
    official = _FakeOfficial()
    x = XClient(official, read_backend=backend)
    lid = x.create_list("L", description="d", private=True)
    assert lid == "5001"
    x.update_list(lid, name="L2")
    x.delete_list(lid)
    x.add_list_member(lid, "11")
    x.remove_list_member(lid, "11")
    kinds = [c[0] for c in official.list_writes]
    assert kinds == ["create_list", "update_list", "delete_list",
                     "add_list_member", "remove_list_member"]
    assert backend.calls == []   # 書込はバックエンドを一切触らない


def test_get_list_members_uses_backend_when_present():
    backend = _FakeBackend()
    x = XClient(_FakeOfficial(), read_backend=backend)
    out = x.get_list_members("900")
    assert out[0]["username"] == "bk_user"             # バックエンド経由
    assert backend.calls == [("get_list_members", "900", 100)]


def test_get_list_members_falls_back_to_official():
    backend = _FakeBackend(fail=True)
    official = _FakeOfficial()
    x = XClient(official, read_backend=backend)
    out = x.get_list_members("900")
    assert out[0]["username"] == "alice"               # 失敗→公式で読み直し
    assert out[0]["followers_count"] == 42             # public_metrics から
    assert ("get_list_members", "900") in official.list_writes


def test_get_list_members_empty_backend_falls_back_to_official():
    # 非公開リストは twitterapi.io が空を返す → 公式(ユーザー認証)で読み直す
    backend = _FakeBackend(empty_members=True)
    official = _FakeOfficial()
    x = XClient(official, read_backend=backend)
    out = x.get_list_members("900")
    assert out[0]["username"] == "alice"               # 空→公式で正しいメンバーを取得
    assert backend.calls == [("get_list_members", "900", 100)]  # backendは試した
    assert ("get_list_members", "900") in official.list_writes


def test_get_owned_lists_is_official_only_even_with_backend():
    backend = _FakeBackend()
    official = _FakeOfficial()
    x = XClient(official, read_backend=backend)
    out = x.get_owned_lists()
    assert out[0]["name"] == "公式リスト"
    assert out[0]["private"] is True
    assert out[0]["member_count"] == 2
    assert backend.calls == []                         # 所有一覧はbackend非対応=公式のみ
    assert ("get_owned_lists", "1") in official.list_writes  # get_me id="1" で引く
