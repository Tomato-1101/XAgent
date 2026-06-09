from datetime import datetime, timedelta, timezone

from xagent import monitor
from xagent.models import Draft, DraftKind, EngageTarget, MonitorCursor, TargetKind
from sqlmodel import select

from tests.conftest import FakeXClient


def test_poll_mentions_creates_reply_drafts(session, fake_formatter):
    fx = FakeXClient(mentions=[{"id": "101", "text": "メンション本文", "author_id": "u1"}])
    r, q = monitor.poll_mentions(session, fx, fake_formatter, me_user_id="me")
    assert (r, q) == (1, 0)  # 自分宛メンションは返信のみ(引用RTしない)
    drafts = session.exec(select(Draft).where(Draft.kind == DraftKind.REPLY)).all()
    assert len(drafts) == 1
    assert drafts[0].target_tweet_id == "101"
    # カーソルが更新される
    cur = session.exec(select(MonitorCursor).where(MonitorCursor.stream == "mentions")).first()
    assert cur.last_seen_id == "101"


def test_poll_targets_creates_one_ai_judged_draft(session, fake_formatter):
    """対象アカウント(MANUAL)の新規投稿には、AIが選んだ型(既定reply)で1案だけ作る。"""
    session.add(EngageTarget(kind=TargetKind.MANUAL, handle="famous", user_id="u9", active=True))
    session.commit()
    fx = FakeXClient(timelines={"u9": [{"id": "202", "text": "有名人の新規投稿", "author_id": "u9"}]})
    r, q = monitor.poll_targets(session, fx, fake_formatter)
    assert (r, q) == (1, 0)  # 1投稿=1案(AI判断・既定は返信)
    replies = session.exec(select(Draft).where(Draft.kind == DraftKind.REPLY)).all()
    quotes = session.exec(select(Draft).where(Draft.kind == DraftKind.QUOTE)).all()
    assert [d.target_tweet_id for d in replies] == ["202"]
    assert quotes == []


def test_poll_targets_quote_when_ai_picks_quote(session, fake_formatter):
    """AIが quote と判断した投稿は引用案のみ1件作る(返信は作らない)。"""
    fake_formatter.engage_type_return = "quote"
    session.add(EngageTarget(kind=TargetKind.MANUAL, handle="famous", user_id="u9", active=True))
    session.commit()
    fx = FakeXClient(timelines={"u9": [{"id": "202", "text": "拡散したいニュース", "author_id": "u9"}]})
    r, q = monitor.poll_targets(session, fx, fake_formatter)
    assert (r, q) == (0, 1)
    quotes = session.exec(select(Draft).where(Draft.kind == DraftKind.QUOTE)).all()
    assert [d.target_tweet_id for d in quotes] == ["202"]
    assert session.exec(select(Draft).where(Draft.kind == DraftKind.REPLY)).all() == []


def test_auto_run_flags_default_off_and_toggle(session):
    """絡み案の自動生成は既定OFF(手動スキャン方針)。set_monitor_settings でON/OFFできる。"""
    cfg = monitor.get_monitor_settings(session)
    assert cfg.auto_monitor_enabled is False
    cfg = monitor.set_monitor_settings(session, auto_monitor_enabled=True)
    assert cfg.auto_monitor_enabled is True


def test_poll_lists_expands_members_one_draft_each(session, fake_formatter):
    """kind=LIST はリストの現メンバーへ展開し、各メンバーの新規投稿にAI判断で1案ずつ作る。"""
    session.add(EngageTarget(kind=TargetKind.LIST, handle="絡み候補A", list_id="L1", active=True))
    session.commit()
    fx = FakeXClient(
        list_members={"L1": [{"id": "u9", "username": "famous"}, {"id": "u8", "username": "other"}]},
        timelines={
            "u9": [{"id": "202", "text": "メンバー1の投稿", "author_id": "u9"}],
            "u8": [{"id": "303", "text": "メンバー2の投稿", "author_id": "u8"}],
        },
    )
    r, q = monitor.poll_lists(session, fx, fake_formatter)
    assert (r, q) == (2, 0)  # 2メンバー × 1案(既定reply)
    replies = session.exec(select(Draft).where(Draft.kind == DraftKind.REPLY)).all()
    assert {d.target_tweet_id for d in replies} == {"202", "303"}
    assert {d.target_handle for d in replies} == {"famous", "other"}


def test_poll_lists_reflects_membership_live(session, fake_formatter):
    """リストのメンバーは巡回ごとに取り直すため、メンバー増減が自動で反映される(連携)。"""
    session.add(EngageTarget(kind=TargetKind.LIST, handle="A", list_id="L1", active=True))
    session.commit()
    fx = FakeXClient(
        list_members={"L1": [{"id": "u9", "username": "famous"}]},
        timelines={
            "u9": [{"id": "202", "text": "p", "author_id": "u9"}],
            "u8": [{"id": "303", "text": "q", "author_id": "u8"}],
        },
    )
    assert monitor.poll_lists(session, fx, fake_formatter) == (1, 0)  # 当初メンバーは1人(1案・既定reply)
    # リスト側にメンバーを追加 → 次の巡回で取り直すため新メンバーも自動で対象になる
    fx._list_members["L1"].append({"id": "u8", "username": "other"})
    monitor.poll_lists(session, fx, fake_formatter)
    drafts = session.exec(select(Draft).where(Draft.kind == DraftKind.REPLY)).all()
    # 追加した other(u8) の投稿が新たに対象化されている(=リスト更新が連携)
    assert "other" in {d.target_handle for d in drafts}


def test_poll_targets_ignores_list_kind(session, fake_formatter):
    """poll_targets は MANUAL 限定。LIST 対象は poll_targets では処理しない(二重生成防止)。"""
    session.add(EngageTarget(kind=TargetKind.LIST, handle="A", list_id="L1", active=True))
    session.commit()
    fx = FakeXClient(
        list_members={"L1": [{"id": "u9", "username": "famous"}]},
        timelines={"u9": [{"id": "202", "text": "p", "author_id": "u9"}]},
    )
    assert monitor.poll_targets(session, fx, fake_formatter) == (0, 0)


def test_run_once_summary(session, fake_formatter):
    monitor.set_monitor_settings(session, mentions_enabled=True)  # 既定OFFのメンションを明示的にON
    session.add(EngageTarget(kind=TargetKind.MANUAL, handle="famous", user_id="u9", active=True))
    session.commit()
    fx = FakeXClient(
        mentions=[{"id": "101", "text": "m", "author_id": "u1"}],
        timelines={"u9": [{"id": "202", "text": "t", "author_id": "u9"}]},
    )
    res = monitor.run_once(session, fx, fake_formatter, me_user_id="me")
    # メンション1(返信のみ) + 対象アカウント1(AI判断・既定reply) = 返信2・引用0
    assert res == {"reply_suggestions": 2, "quote_suggestions": 0}


def test_keyword_source_off_by_default_then_on(session, fake_formatter):
    session.add(EngageTarget(kind=TargetKind.GENRE, keyword="AI副業", active=True))
    session.commit()
    fx = FakeXClient(searches={"AI副業": [{"id": "303", "text": "話題の投稿", "author_id": "u3"}]})
    # 既定では keyword_search_enabled=False → 生成ゼロ
    res = monitor.run_once(session, fx, fake_formatter, me_user_id="me")
    assert res["reply_suggestions"] == 0
    # オンにすると検索ソースが動く(AI判断で1案・既定reply)
    monitor.set_monitor_settings(session, keyword_search_enabled=True)
    res2 = monitor.run_once(session, fx, fake_formatter, me_user_id="me")
    assert res2["reply_suggestions"] == 1
    assert res2["quote_suggestions"] == 0


def test_following_source_gated(session, fake_formatter):
    fx = FakeXClient(
        following=[{"id": "u9", "username": "famous"}],
        timelines={"u9": [{"id": "404", "text": "follow投稿", "author_id": "u9"}]},
    )
    # 既定 following_enabled=False → 動かない
    assert monitor.run_once(session, fx, fake_formatter, me_user_id="me")["reply_suggestions"] == 0
    monitor.set_monitor_settings(session, following_enabled=True)
    assert monitor.run_once(session, fx, fake_formatter, me_user_id="me")["reply_suggestions"] == 1


def test_mentions_can_be_disabled(session, fake_formatter):
    monitor.set_monitor_settings(session, mentions_enabled=False)
    fx = FakeXClient(mentions=[{"id": "101", "text": "m", "author_id": "u1"}])
    assert monitor.run_once(session, fx, fake_formatter, me_user_id="me")["reply_suggestions"] == 0


# --- 生成数バジェット(1サイクルの総生成数上限) ----------------------------

def test_poll_mentions_stores_target_text(session, fake_formatter):
    """メンション返信案にも元ポスト本文が target_text として残る。"""
    fx = FakeXClient(mentions=[{"id": "101", "text": "メンション本文", "author_id": "u1"}])
    monitor.poll_mentions(session, fx, fake_formatter, me_user_id="me")
    d = session.exec(select(Draft).where(Draft.kind == DraftKind.REPLY)).first()
    assert d.target_text == "メンション本文"


def test_max_drafts_per_run_caps_total(session, fake_formatter):
    """max_drafts_per_run が監視1サイクルの総生成数を上限で打ち切る。"""
    monitor.set_monitor_settings(session, max_drafts_per_run=3, mentions_enabled=True)
    fx = FakeXClient(
        mentions=[{"id": str(100 + i), "text": f"m{i}", "author_id": "u1"} for i in range(10)],
    )
    res = monitor.run_once(session, fx, fake_formatter, me_user_id="me")
    assert res["reply_suggestions"] + res["quote_suggestions"] == 3
    assert len(session.exec(select(Draft)).all()) == 3


def test_run_once_max_drafts_override(session, fake_formatter):
    """手動1回実行の max_drafts はその回だけ生成数を絞り、設定値より優先される。"""
    monitor.set_monitor_settings(session, max_drafts_per_run=10, mentions_enabled=True)
    fx = FakeXClient(
        mentions=[{"id": str(100 + i), "text": f"m{i}", "author_id": "u1"} for i in range(10)],
    )
    res = monitor.run_once(session, fx, fake_formatter, me_user_id="me", max_drafts=2)
    assert res["reply_suggestions"] == 2
    assert len(session.exec(select(Draft)).all()) == 2


def test_budget_shared_across_sources(session, fake_formatter):
    """バジェットはメンションと対象アカウントで共有され、合計が上限を超えない。"""
    monitor.set_monitor_settings(
        session, max_drafts_per_run=2, keyword_search_enabled=True, mentions_enabled=True
    )
    session.add(EngageTarget(kind=TargetKind.GENRE, keyword="AI", active=True))
    session.commit()
    fx = FakeXClient(
        mentions=[{"id": "101", "text": "m", "author_id": "u1"}],
        searches={"AI": [{"id": str(200 + i), "text": f"s{i}", "author_id": "u3"} for i in range(5)]},
    )
    res = monitor.run_once(session, fx, fake_formatter, me_user_id="me")
    # メンション1 + 残バジェット1(ジャンル5件中1件) = 計2、すべて返信案に集約
    assert res["reply_suggestions"] == 2
    assert res["quote_suggestions"] == 0


def test_max_drafts_zero_creates_nothing(session, fake_formatter):
    monitor.set_monitor_settings(session, max_drafts_per_run=0)
    fx = FakeXClient(mentions=[{"id": "101", "text": "m", "author_id": "u1"}])
    res = monitor.run_once(session, fx, fake_formatter, me_user_id="me")
    assert res == {"reply_suggestions": 0, "quote_suggestions": 0}


def test_within_age_skips_old_posts(session, fake_formatter):
    """3日より古い投稿にはリプ案を作らない(新しいものだけ対象・乱造/無意味リプ防止)。"""
    now = datetime.now(timezone.utc)
    fx = FakeXClient(mentions=[
        {"id": "1", "text": "古い", "author_id": "u1", "created_at": now - timedelta(days=5)},
        {"id": "2", "text": "新しい", "author_id": "u1", "created_at": now - timedelta(hours=2)},
    ])
    r, q = monitor.poll_mentions(session, fx, fake_formatter, me_user_id="me")
    assert (r, q) == (1, 0)
    d = session.exec(select(Draft).where(Draft.kind == DraftKind.REPLY)).one()
    assert d.target_tweet_id == "2"  # 新しい方だけ採用


def test_within_age_passes_when_created_at_missing(session, fake_formatter):
    """created_at が無いツイートは判定不能として通す(従来挙動・テスト互換)。"""
    fx = FakeXClient(mentions=[{"id": "9", "text": "日時なし", "author_id": "u1"}])
    assert monitor.poll_mentions(session, fx, fake_formatter, me_user_id="me") == (1, 0)


def test_drops_reposts(session, fake_formatter):
    """リポスト(単純RT・引用RT)には反応せず、対象アカウントの本人オリジナル投稿だけに案を作る。"""
    session.add(EngageTarget(kind=TargetKind.MANUAL, handle="famous", user_id="u9", active=True))
    session.commit()
    now = datetime.now(timezone.utc)
    fx = FakeXClient(timelines={"u9": [
        {"id": "1", "text": "RT @other: 他人の投稿", "author_id": "u9", "created_at": now},  # 単純RT(本文判定)
        {"id": "2", "text": "これ良いね！", "author_id": "u9", "created_at": now, "is_repost": True},  # 引用RT
        {"id": "3", "text": "リポスト", "author_id": "u9", "created_at": now, "is_repost": True},  # RT(フラグ判定)
        {"id": "4", "text": "本人のオリジナル投稿", "author_id": "u9", "created_at": now},
    ]})
    r, q = monitor.poll_targets(session, fx, fake_formatter)
    assert (r, q) == (1, 0)  # オリジナル(id=4)のみ × 1案(既定reply)
    ids = {d.target_tweet_id for d in session.exec(select(Draft).where(Draft.kind == DraftKind.REPLY)).all()}
    assert ids == {"4"}
