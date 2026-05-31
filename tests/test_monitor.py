from xagent import monitor
from xagent.models import Draft, DraftKind, EngageTarget, MonitorCursor, TargetKind
from sqlmodel import select

from tests.conftest import FakeXClient


def test_poll_mentions_creates_reply_drafts(session, fake_formatter):
    fx = FakeXClient(mentions=[{"id": "101", "text": "メンション本文", "author_id": "u1"}])
    n = monitor.poll_mentions(session, fx, fake_formatter, me_user_id="me")
    assert n == 1
    drafts = session.exec(select(Draft).where(Draft.kind == DraftKind.REPLY)).all()
    assert len(drafts) == 1
    assert drafts[0].target_tweet_id == "101"
    # カーソルが更新される
    cur = session.exec(select(MonitorCursor).where(MonitorCursor.stream == "mentions")).first()
    assert cur.last_seen_id == "101"


def test_poll_targets_creates_quote_drafts(session, fake_formatter):
    session.add(EngageTarget(kind=TargetKind.MANUAL, handle="famous", user_id="u9", active=True))
    session.commit()
    fx = FakeXClient(timelines={"u9": [{"id": "202", "text": "有名人の新規投稿", "author_id": "u9"}]})
    n = monitor.poll_targets(session, fx, fake_formatter)
    assert n == 1
    drafts = session.exec(select(Draft).where(Draft.kind == DraftKind.QUOTE)).all()
    assert len(drafts) == 1
    assert drafts[0].target_tweet_id == "202"


def test_run_once_summary(session, fake_formatter):
    session.add(EngageTarget(kind=TargetKind.MANUAL, handle="famous", user_id="u9", active=True))
    session.commit()
    fx = FakeXClient(
        mentions=[{"id": "101", "text": "m", "author_id": "u1"}],
        timelines={"u9": [{"id": "202", "text": "t", "author_id": "u9"}]},
    )
    res = monitor.run_once(session, fx, fake_formatter, me_user_id="me")
    assert res == {"reply_suggestions": 1, "quote_suggestions": 1}


def test_keyword_source_off_by_default_then_on(session, fake_formatter):
    session.add(EngageTarget(kind=TargetKind.GENRE, keyword="AI副業", active=True))
    session.commit()
    fx = FakeXClient(searches={"AI副業": [{"id": "303", "text": "話題の投稿", "author_id": "u3"}]})
    # 既定では keyword_search_enabled=False → 絡み案ゼロ
    res = monitor.run_once(session, fx, fake_formatter, me_user_id="me")
    assert res["quote_suggestions"] == 0
    # オンにすると検索ソースが動く
    monitor.set_monitor_settings(session, keyword_search_enabled=True)
    res2 = monitor.run_once(session, fx, fake_formatter, me_user_id="me")
    assert res2["quote_suggestions"] == 1


def test_following_source_gated(session, fake_formatter):
    fx = FakeXClient(
        following=[{"id": "u9", "username": "famous"}],
        timelines={"u9": [{"id": "404", "text": "follow投稿", "author_id": "u9"}]},
    )
    # 既定 following_enabled=False → 動かない
    assert monitor.run_once(session, fx, fake_formatter, me_user_id="me")["quote_suggestions"] == 0
    monitor.set_monitor_settings(session, following_enabled=True)
    assert monitor.run_once(session, fx, fake_formatter, me_user_id="me")["quote_suggestions"] == 1


def test_mentions_can_be_disabled(session, fake_formatter):
    monitor.set_monitor_settings(session, mentions_enabled=False)
    fx = FakeXClient(mentions=[{"id": "101", "text": "m", "author_id": "u1"}])
    assert monitor.run_once(session, fx, fake_formatter, me_user_id="me")["reply_suggestions"] == 0
