"""テスト共通フィクスチャ: インメモリSQLite と フェイクの formatter / x_client。"""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from xagent.formatter import FormatResult
from xagent.models import BlackoutSettings


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        # 制限帯は既定で無効化(投稿系テストを実時刻に依存させない)。
        # 制限帯テストは set_blackout_settings で明示的に有効化する。
        s.add(BlackoutSettings(enabled=False))
        s.commit()
        yield s


class FakeFormatter:
    """LLMを呼ばず、入力をそのまま/定型で返す整形器。"""

    def format_post(
        self, source_text, style_guide="", allow_long=False,
        emulate_profile_text="", emulate_examples=None,
    ):
        prefix = "[真似]" if emulate_profile_text else ""
        return FormatResult([prefix + source_text.strip()], folded=False, weighted_total=0)

    def format_variations(
        self, source_text, n=3, style_guide="", allow_long=False,
        emulate_profile_text="", emulate_examples=None,
    ):
        return [
            FormatResult([f"{source_text.strip()} (案{i + 1})"], folded=False)
            for i in range(max(1, min(n, 5)))
        ]

    def generate_reply(self, target_text, target_handle="", style_guide="", examples=None):
        return FormatResult([f"返信案: {target_text[:10]}"], folded=False)

    def generate_quote(self, target_text, target_handle="", style_guide="", examples=None):
        return FormatResult([f"引用案: {target_text[:10]}"], folded=False)

    def complete(self, system, user):
        # プロファイル抽出のフェイク: 最低限のJSONを返す
        return '{"tone":"短い断定","themes":["X運用"],"summary":"短く言い切る。"}'


class FakeXClient:
    """投稿を記録し、連番IDを返す。読み取りは事前セットしたツイートを返す。"""

    def __init__(self, mentions=None, timelines=None, users=None, full_tweets=None,
                 searches=None, following=None, tweets=None):
        self.posted = []
        self.retweeted = []
        self._mentions = mentions or []
        self._timelines = timelines or {}      # user_id -> list[dict]
        self._users = users or {}              # handle -> {id, username}
        self._full_tweets = full_tweets or {}  # user_id -> list[dict](created_at/metrics付)
        self._searches = searches or {}        # query -> list[dict]
        self._following = following or []      # list[{id, username}]
        self._tweets = tweets or {}            # tweet_id -> {id, text, author_id, author_handle}
        self._counter = 1000

    def _next_id(self):
        self._counter += 1
        return str(self._counter)

    def post(self, text, in_reply_to_tweet_id=None, quote_tweet_id=None, media_ids=None):
        tid = self._next_id()
        self.posted.append(
            {
                "id": tid,
                "text": text,
                "reply_to": in_reply_to_tweet_id,
                "quote": quote_tweet_id,
                "media": media_ids,
            }
        )
        return tid

    def post_thread(self, segments, in_reply_to_tweet_id=None, media_ids_first=None):
        ids = []
        prev = in_reply_to_tweet_id
        for i, seg in enumerate(segments):
            ids.append(self.post(seg, in_reply_to_tweet_id=prev,
                                  media_ids=media_ids_first if i == 0 else None))
            prev = ids[-1]
        return ids

    def retweet(self, tweet_id):
        self.retweeted.append(tweet_id)
        return tweet_id

    def upload_media(self, path):
        return f"media-{path}"

    def get_me(self):
        return {"id": "1", "username": "tester"}

    def get_mentions(self, user_id, since_id=None):
        return list(self._mentions)

    def get_user_timeline(self, user_id, since_id=None):
        return list(self._timelines.get(user_id, []))

    def get_user_by_username(self, username):
        return self._users.get(username.lstrip("@"))

    def get_user_tweets_full(self, user_id, max_total=200):
        return list(self._full_tweets.get(user_id, []))[:max_total]

    def search_recent(self, query, since_id=None):
        return list(self._searches.get(query, []))

    def get_following(self, user_id, max_total=100):
        return list(self._following)[:max_total]

    def get_tweet(self, tweet_id):
        return self._tweets.get(str(tweet_id))


@pytest.fixture
def fake_formatter():
    return FakeFormatter()


@pytest.fixture
def fake_x():
    return FakeXClient()
