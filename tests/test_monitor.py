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


def test_auto_run_flags_default_on_and_toggle(session):
    """デーモンの自動運用スイッチは既定ON。UI(set_monitor_settings)からON/OFFできる。"""
    cfg = monitor.get_monitor_settings(session)
    assert cfg.auto_monitor_enabled is True
    assert cfg.auto_post_enabled is True
    cfg = monitor.set_monitor_settings(session, auto_monitor_enabled=False, auto_post_enabled=False)
    assert cfg.auto_monitor_enabled is False
    assert cfg.auto_post_enabled is False


def test_poll_lists_expands_members_to_quotes(session, fake_formatter):
    """kind=LIST はリストの現メンバーへ展開し、各メンバーの新規投稿に引用RT案を作る。"""
    session.add(EngageTarget(kind=TargetKind.LIST, handle="絡み候補A", list_id="L1", active=True))
    session.commit()
    fx = FakeXClient(
        list_members={"L1": [{"id": "u9", "username": "famous"}, {"id": "u8", "username": "other"}]},
        timelines={
            "u9": [{"id": "202", "text": "メンバー1の投稿", "author_id": "u9"}],
            "u8": [{"id": "303", "text": "メンバー2の投稿", "author_id": "u8"}],
        },
    )
    n = monitor.poll_lists(session, fx, fake_formatter)
    assert n == 2
    drafts = session.exec(select(Draft).where(Draft.kind == DraftKind.QUOTE)).all()
    assert {d.target_tweet_id for d in drafts} == {"202", "303"}
    assert {d.target_handle for d in drafts} == {"famous", "other"}


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
    assert monitor.poll_lists(session, fx, fake_formatter) == 1  # 当初メンバーは1人(famous)
    # リスト側にメンバーを追加 → 次の巡回で取り直すため新メンバーも自動で対象になる
    fx._list_members["L1"].append({"id": "u8", "username": "other"})
    monitor.poll_lists(session, fx, fake_formatter)
    drafts = session.exec(select(Draft).where(Draft.kind == DraftKind.QUOTE)).all()
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
    assert monitor.poll_targets(session, fx, fake_formatter) == 0


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


# --- 生成数バジェット(1サイクルの総生成数上限) ----------------------------

def test_poll_mentions_stores_target_text(session, fake_formatter):
    """メンション返信案にも元ポスト本文が target_text として残る。"""
    fx = FakeXClient(mentions=[{"id": "101", "text": "メンション本文", "author_id": "u1"}])
    monitor.poll_mentions(session, fx, fake_formatter, me_user_id="me")
    d = session.exec(select(Draft).where(Draft.kind == DraftKind.REPLY)).first()
    assert d.target_text == "メンション本文"


def test_max_drafts_per_run_caps_total(session, fake_formatter):
    """max_drafts_per_run が監視1サイクルの総生成数を上限で打ち切る。"""
    monitor.set_monitor_settings(session, max_drafts_per_run=3)
    fx = FakeXClient(
        mentions=[{"id": str(100 + i), "text": f"m{i}", "author_id": "u1"} for i in range(10)],
    )
    res = monitor.run_once(session, fx, fake_formatter, me_user_id="me")
    assert res["reply_suggestions"] + res["quote_suggestions"] == 3
    assert len(session.exec(select(Draft)).all()) == 3


def test_budget_shared_across_sources(session, fake_formatter):
    """バジェットはメンションと引用候補で共有され、合計が上限を超えない。"""
    monitor.set_monitor_settings(session, max_drafts_per_run=2, keyword_search_enabled=True)
    session.add(EngageTarget(kind=TargetKind.GENRE, keyword="AI", active=True))
    session.commit()
    fx = FakeXClient(
        mentions=[{"id": "101", "text": "m", "author_id": "u1"}],
        searches={"AI": [{"id": str(200 + i), "text": f"s{i}", "author_id": "u3"} for i in range(5)]},
    )
    res = monitor.run_once(session, fx, fake_formatter, me_user_id="me")
    assert res["reply_suggestions"] == 1          # メンションで1消費
    assert res["quote_suggestions"] == 1          # 残バジェット1のみ(5件中1件)


def test_max_drafts_zero_creates_nothing(session, fake_formatter):
    monitor.set_monitor_settings(session, max_drafts_per_run=0)
    fx = FakeXClient(mentions=[{"id": "101", "text": "m", "author_id": "u1"}])
    res = monitor.run_once(session, fx, fake_formatter, me_user_id="me")
    assert res == {"reply_suggestions": 0, "quote_suggestions": 0}
