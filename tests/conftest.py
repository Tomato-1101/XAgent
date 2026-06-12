"""テスト共通フィクスチャ: インメモリSQLite と フェイクの formatter / x_client。"""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from xagent.config import Settings
from xagent.formatter import FormatResult
from xagent.models import BlackoutSettings


@pytest.fixture(autouse=True)
def _no_api_token(monkeypatch):
    """ローカル .env の API_TOKEN がテストへ漏れて全APIが401になるのを防ぐ。

    認証そのもののテスト(test_spa_auth)は個別に get_settings を上書きする。
    """
    monkeypatch.setattr("xagent.api.deps.get_settings", lambda: Settings(_env_file=None))


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
    """LLMを呼ばず、入力をそのまま/定型で返す整形器。

    playbooks に各整形呼び出しで渡された型(playbook)を記録し、テストで注入を検証できる。
    complete_return をセットすると complete() の戻り値を差し替えられる(型のAI選択テスト用)。
    """

    def __init__(self):
        self.playbooks = []          # 整形に渡された playbook を順に記録
        self.complete_return = None  # set すると complete() がこれを返す
        self.engage_kind_return = "reply"  # select_engagements の既定の型(テストで切替)
        self.select_calls = []       # select_engagements の呼び出し内容を記録(検証用)
        self.select_return = None    # set すると select_engagements がこれを返す
        self.news_calls = []         # generate_news_posts の呼び出し内容を記録(検証用)
        self.news_return = None      # set すると generate_news_posts がこれを返す

    def format_post(
        self, source_text, style_guide="", allow_long=False,
        emulate_profile_text="", emulate_examples=None, playbook="",
    ):
        self.playbooks.append(playbook)
        prefix = "[真似]" if emulate_profile_text else ""
        return FormatResult([prefix + source_text.strip()], folded=False, weighted_total=0)

    def format_variations(
        self, source_text, n=3, style_guide="", allow_long=False,
        emulate_profile_text="", emulate_examples=None, playbook="",
    ):
        self.playbooks.append(playbook)
        return [
            FormatResult([f"{source_text.strip()} (案{i + 1})"], folded=False)
            for i in range(max(1, min(n, 5)))
        ]

    def generate_reply(self, target_text, target_handle="", style_guide="", examples=None,
                       playbook=""):
        self.playbooks.append(playbook)
        return FormatResult([f"返信案: {target_text[:10]}"], folded=False)

    def generate_quote(self, target_text, target_handle="", style_guide="", examples=None,
                       playbook=""):
        self.playbooks.append(playbook)
        return FormatResult([f"引用案: {target_text[:10]}"], folded=False)

    def select_engagements(self, candidates, max_n, style_guide="", examples=None,
                           reply_playbook="", quote_playbook=""):
        self.select_calls.append({
            "candidates": list(candidates), "max_n": max_n,
            "reply_playbook": reply_playbook, "quote_playbook": quote_playbook,
        })
        if self.select_return is not None:
            return self.select_return
        return [
            {
                "tweet_id": c["tweet_id"], "kind": self.engage_kind_return,
                "text": f"絡み案: {str(c.get('text', ''))[:10]}",
                "reason": "テスト選定", "candidate": c,
            }
            for c in candidates[:max_n]
        ]

    def generate_news_posts(self, items, max_n, style_guide="", examples=None, playbook=""):
        self.news_calls.append({"items": list(items), "max_n": max_n, "playbook": playbook})
        if self.news_return is not None:
            return self.news_return
        return [
            {
                "news_index": it["index"], "format": "post",
                "text": f"【速報】{it['title']}",
                "source_url": None, "reason": "テスト選定(N2)", "item": it,
            }
            for it in items[:max_n]
        ]

    def complete(self, system, user):
        if self.complete_return is not None:
            return self.complete_return
        # プロファイル抽出のフェイク: 最低限のJSONを返す
        return '{"tone":"短い断定","themes":["X運用"],"summary":"短く言い切る。"}'


class FakeXClient:
    """投稿を記録し、連番IDを返す。読み取りは事前セットしたツイートを返す。"""

    def __init__(self, mentions=None, timelines=None, users=None, full_tweets=None,
                 searches=None, following=None, tweets=None, lists=None, list_members=None):
        self.posted = []
        self.retweeted = []
        self._mentions = mentions or []
        self._timelines = timelines or {}      # user_id -> list[dict]
        self._users = users or {}              # handle -> {id, username}
        self._full_tweets = full_tweets or {}  # user_id -> list[dict](created_at/metrics付)
        self._searches = searches or {}        # query -> list[dict]
        self._following = following or []      # list[{id, username}]
        self._tweets = tweets or {}            # tweet_id -> {id, text, author_id, author_handle}
        self._lists = list(lists or [])        # 所有リスト list[dict]
        self._list_members = list_members or {}  # list_id -> list[member dict]
        self.list_calls = []                   # リスト書込の記録(検証用)
        self._list_counter = 5000
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

    def get_user_timeline(self, user_id, since_id=None, official_fallback=True):
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

    # --- リスト ---
    def create_list(self, name, description=None, private=True):
        self._list_counter += 1
        lid = str(self._list_counter)
        self.list_calls.append(("create_list", name, description, private))
        self._lists.append(
            {"id": lid, "name": name, "description": description or "",
             "private": private, "member_count": 0}
        )
        self._list_members.setdefault(lid, [])
        return lid

    def update_list(self, list_id, name=None, description=None, private=None):
        self.list_calls.append(("update_list", str(list_id), name, description, private))

    def delete_list(self, list_id):
        self.list_calls.append(("delete_list", str(list_id)))

    def add_list_member(self, list_id, user_id):
        self.list_calls.append(("add_list_member", str(list_id), str(user_id)))

    def remove_list_member(self, list_id, user_id):
        self.list_calls.append(("remove_list_member", str(list_id), str(user_id)))

    def get_owned_lists(self, max_total=100):
        return list(self._lists)[:max_total]

    def get_list_members(self, list_id, max_total=100):
        return list(self._list_members.get(str(list_id), []))[:max_total]


@pytest.fixture
def fake_formatter():
    return FakeFormatter()


@pytest.fixture
def fake_x():
    return FakeXClient()
