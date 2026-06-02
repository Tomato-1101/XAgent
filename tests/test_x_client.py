"""XClient.get_tweet の正規化(tweepyレスポンス→dict)のテスト。外部API不要。"""

from types import SimpleNamespace

from xagent.x_client import XClient


class _FakeTweepy:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def get_tweet(self, **kwargs):
        self.calls.append(kwargs)
        return self._resp


def test_get_tweet_normalizes_response():
    resp = SimpleNamespace(
        data=SimpleNamespace(id=123, text="本文", author_id=999),
        includes={"users": [SimpleNamespace(username="famous")]},
    )
    fake = _FakeTweepy(resp)
    out = XClient(fake).get_tweet("123")
    assert out == {
        "id": "123",
        "text": "本文",
        "author_id": "999",
        "author_handle": "famous",
    }
    # ハンドル取得のため expansions/user_fields を渡している
    assert fake.calls[0]["expansions"] == ["author_id"]


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


class _FakeBackend:
    """twitterapi.io バックエンドのフェイク。fail=True で TwitterApiIoError を投げる。"""

    def __init__(self, fail=False):
        self.fail = fail
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
