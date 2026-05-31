import json
from datetime import datetime, timezone

from xagent import profiles, service
from xagent.models import AccountProfile, PastPost
from tests.conftest import FakeFormatter, FakeXClient


def _tweet(tid, text, hour_utc, likes=0, rts=0):
    return {
        "id": str(tid),
        "text": text,
        "author_id": "42",
        "created_at": datetime(2026, 5, 1, hour_utc, 0, 0, tzinfo=timezone.utc),
        "like_count": likes,
        "retweet_count": rts,
    }


def _fake_x():
    return FakeXClient(
        users={"someone": {"id": "42", "username": "someone"}},
        full_tweets={
            "42": [
                _tweet(1, "短く言い切る投稿A", 0, likes=50),   # UTC0時 = JST9時
                _tweet(2, "短く言い切る投稿B", 3, likes=10),   # UTC3時 = JST12時
                _tweet(3, "短く言い切る投稿C", 0, likes=200),  # JST9時
            ]
        },
    )


def _complete(system, user):
    return '{"tone":"短い断定","themes":["X運用"],"summary":"短く言い切る。具体で。"}'


def test_compute_posting_times_histogram():
    tweets = [_tweet(1, "a", 0), _tweet(2, "b", 0), _tweet(3, "c", 3)]
    hist = profiles.compute_posting_times(tweets, tz_name="Asia/Tokyo")
    by_hour = dict(hist)
    assert len(hist) == 24
    assert by_hour[9] == 2   # UTC0時 → JST9時が2件
    assert by_hour[12] == 1  # UTC3時 → JST12時が1件


def test_learn_account_creates_profile_and_posts(session):
    prof = profiles.learn_account(session, _fake_x(), _complete, "someone", max_total=100)
    assert isinstance(prof, AccountProfile)
    assert prof.handle == "someone"
    assert prof.user_id == "42"
    assert prof.is_self is False
    assert prof.posts_fetched == 3
    assert "短く言い切る" in prof.profile_text
    hist = json.loads(prof.active_hours_json)
    assert len(hist) == 24
    # 生投稿が他人(is_own=False)として保存されている
    rows = session.exec(__import__("sqlmodel").select(PastPost)).all()
    assert len(rows) == 3
    assert all(r.is_own is False and r.author_handle == "someone" for r in rows)


def test_example_posts_for_account_orders_by_likes(session):
    profiles.learn_account(session, _fake_x(), _complete, "someone", max_total=100)
    ex = profiles.example_posts_for_account(session, "someone", n=2)
    assert ex[0] == "短く言い切る投稿C"  # like200が先頭


def test_emulate_injected_only_when_selected(session, fake_formatter):
    profiles.learn_account(session, _fake_x(), _complete, "someone", max_total=100)
    # emulate 指定あり → FakeFormatter が "[真似]" を付ける
    d1 = service.create_post_draft(session, fake_formatter, "メモ", emulate_handle="someone")
    assert json.loads(d1.segments_json)[0].startswith("[真似]")
    # 指定なし → 付かない(学習データは自動注入されない)
    d2 = service.create_post_draft(session, fake_formatter, "メモ")
    assert not json.loads(d2.segments_json)[0].startswith("[真似]")


def test_create_post_variations(session, fake_formatter):
    drafts = service.create_post_variations(session, fake_formatter, "メモ", n=3)
    assert len(drafts) == 3
    assert all(d.kind.value == "post" for d in drafts)
