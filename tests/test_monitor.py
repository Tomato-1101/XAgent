from datetime import datetime, timedelta, timezone

from xagent import monitor
from xagent.models import Draft, DraftKind, DraftStatus, EngageTarget, MonitorCursor, TargetKind
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


def test_auto_run_flags_default_off_and_toggle(session):
    """絡み案の自動生成は既定OFF(手動スキャン方針)。set_monitor_settings でON/OFFできる。"""
    cfg = monitor.get_monitor_settings(session)
    assert cfg.auto_monitor_enabled is False
    cfg = monitor.set_monitor_settings(session, auto_monitor_enabled=True)
    assert cfg.auto_monitor_enabled is True


# --- 候補収集(collect_engage_candidates) -----------------------------------

def test_collect_candidates_from_manual_targets(session, fake_formatter):
    """対象アカウント(MANUAL)の直近投稿が候補になる(handle は対象の登録値)。"""
    session.add(EngageTarget(kind=TargetKind.MANUAL, handle="famous", user_id="u9", active=True))
    session.commit()
    fx = FakeXClient(timelines={"u9": [{"id": "202", "text": "有名人の新規投稿", "author_id": "u9"}]})
    cands = monitor.collect_engage_candidates(session, fx, me_user_id="me")
    assert [(c["tweet_id"], c["handle"]) for c in cands] == [("202", "famous")]


def test_collect_expands_list_members(session, fake_formatter):
    """kind=LIST はリストの現メンバーへ展開し、各メンバーの投稿を候補に入れる。"""
    session.add(EngageTarget(kind=TargetKind.LIST, handle="絡み候補A", list_id="L1", active=True))
    session.commit()
    fx = FakeXClient(
        list_members={"L1": [{"id": "u9", "username": "famous"}, {"id": "u8", "username": "other"}]},
        timelines={
            "u9": [{"id": "202", "text": "メンバー1の投稿", "author_id": "u9"}],
            "u8": [{"id": "303", "text": "メンバー2の投稿", "author_id": "u8"}],
        },
    )
    cands = monitor.collect_engage_candidates(session, fx, me_user_id="me")
    assert {(c["tweet_id"], c["handle"]) for c in cands} == {("202", "famous"), ("303", "other")}


def test_collect_reflects_list_membership_live(session, fake_formatter):
    """リストのメンバーは収集ごとに取り直すため、メンバー増減が自動で反映される(連携)。"""
    session.add(EngageTarget(kind=TargetKind.LIST, handle="A", list_id="L1", active=True))
    session.commit()
    fx = FakeXClient(
        list_members={"L1": [{"id": "u9", "username": "famous"}]},
        timelines={
            "u9": [{"id": "202", "text": "p", "author_id": "u9"}],
            "u8": [{"id": "303", "text": "q", "author_id": "u8"}],
        },
    )
    assert len(monitor.collect_engage_candidates(session, fx, me_user_id="me")) == 1
    fx._list_members["L1"].append({"id": "u8", "username": "other"})
    cands = monitor.collect_engage_candidates(session, fx, me_user_id="me")
    assert "other" in {c["handle"] for c in cands}


def test_collect_excludes_tweets_with_existing_drafts(session, fake_formatter):
    """既に同じ tweet を対象にした Draft があれば(却下済みでも)候補から外す(乱造防止)。"""
    session.add(EngageTarget(kind=TargetKind.MANUAL, handle="famous", user_id="u9", active=True))
    session.add(Draft(kind=DraftKind.REPLY, status=DraftStatus.REJECTED,
                      segments_json='["旧案"]', target_tweet_id="202"))
    session.commit()
    fx = FakeXClient(timelines={"u9": [
        {"id": "202", "text": "却下済みの投稿", "author_id": "u9"},
        {"id": "203", "text": "新しい投稿", "author_id": "u9"},
    ]})
    cands = monitor.collect_engage_candidates(session, fx, me_user_id="me")
    assert [c["tweet_id"] for c in cands] == ["203"]


def test_collect_dedupes_across_sources(session, fake_formatter):
    """MANUAL対象とリストメンバーが同一人物でも、同じ tweet は候補に1回しか入らない。"""
    session.add(EngageTarget(kind=TargetKind.MANUAL, handle="famous", user_id="u9", active=True))
    session.add(EngageTarget(kind=TargetKind.LIST, handle="A", list_id="L1", active=True))
    session.commit()
    fx = FakeXClient(
        list_members={"L1": [{"id": "u9", "username": "famous"}]},
        timelines={"u9": [{"id": "202", "text": "p", "author_id": "u9"}]},
    )
    cands = monitor.collect_engage_candidates(session, fx, me_user_id="me")
    assert [c["tweet_id"] for c in cands] == ["202"]


def test_collect_24h_window(session, fake_formatter):
    """「今から24時間前まで」の投稿だけ候補にする(古い投稿は取らない)。"""
    session.add(EngageTarget(kind=TargetKind.MANUAL, handle="famous", user_id="u9", active=True))
    session.commit()
    now = datetime.now(timezone.utc)
    fx = FakeXClient(timelines={"u9": [
        {"id": "1", "text": "30時間前", "author_id": "u9", "created_at": now - timedelta(hours=30)},
        {"id": "2", "text": "2時間前", "author_id": "u9", "created_at": now - timedelta(hours=2)},
    ]})
    cands = monitor.collect_engage_candidates(session, fx, me_user_id="me")
    assert [c["tweet_id"] for c in cands] == ["2"]


def test_collect_drops_reposts(session, fake_formatter):
    """リポスト(単純RT・引用RT)は候補にせず、本人のオリジナル投稿だけ集める。"""
    session.add(EngageTarget(kind=TargetKind.MANUAL, handle="famous", user_id="u9", active=True))
    session.commit()
    now = datetime.now(timezone.utc)
    fx = FakeXClient(timelines={"u9": [
        {"id": "1", "text": "RT @other: 他人の投稿", "author_id": "u9", "created_at": now},  # 単純RT(本文判定)
        {"id": "2", "text": "これ良いね！", "author_id": "u9", "created_at": now, "is_repost": True},  # 引用RT
        {"id": "3", "text": "本人のオリジナル投稿", "author_id": "u9", "created_at": now},
    ]})
    cands = monitor.collect_engage_candidates(session, fx, me_user_id="me")
    assert [c["tweet_id"] for c in cands] == ["3"]


def test_collect_sources_gated_by_toggles(session, fake_formatter):
    """keyword/following ソースは既定OFF。ONにすると候補に入る。"""
    session.add(EngageTarget(kind=TargetKind.GENRE, keyword="AI副業", active=True))
    session.commit()
    fx = FakeXClient(
        searches={"AI副業": [{"id": "303", "text": "話題の投稿", "author_id": "u3"}]},
        following=[{"id": "u9", "username": "famous"}],
        timelines={"u9": [{"id": "404", "text": "follow投稿", "author_id": "u9"}]},
    )
    assert monitor.collect_engage_candidates(session, fx, me_user_id="me") == []
    monitor.set_monitor_settings(session, keyword_search_enabled=True, following_enabled=True)
    cands = monitor.collect_engage_candidates(session, fx, me_user_id="me")
    assert {c["tweet_id"] for c in cands} == {"303", "404"}


def test_collect_caps_candidates_newest_first(session, fake_formatter):
    """候補は新しい順(id降順)に SELECT_MAX_CANDIDATES 件で打ち切る(LLM入力の安全弁)。"""
    session.add(EngageTarget(kind=TargetKind.MANUAL, handle="famous", user_id="u9", active=True))
    session.commit()
    n = monitor.SELECT_MAX_CANDIDATES + 5
    fx = FakeXClient(timelines={"u9": [
        {"id": str(1000 + i), "text": f"p{i}", "author_id": "u9"} for i in range(n)
    ]})
    cands = monitor.collect_engage_candidates(session, fx, me_user_id="me")
    assert len(cands) == monitor.SELECT_MAX_CANDIDATES
    assert cands[0]["tweet_id"] == str(1000 + n - 1)  # 最新が先頭


# --- run_once(バッチ選定→下書き作成) ---------------------------------------

def test_run_once_batch_select_creates_drafts(session, fake_formatter):
    """run_once は候補をまとめて1回のAI選定に渡し、その結果から下書きを作る。"""
    monitor.set_monitor_settings(session, mentions_enabled=True)
    session.add(EngageTarget(kind=TargetKind.MANUAL, handle="famous", user_id="u9", active=True))
    session.commit()
    fx = FakeXClient(
        mentions=[{"id": "101", "text": "m", "author_id": "u1"}],
        timelines={"u9": [{"id": "202", "text": "t", "author_id": "u9"}]},
    )
    res = monitor.run_once(session, fx, fake_formatter, me_user_id="me")
    # メンション1(返信のみ) + バッチ選定1(既定reply) = 返信2・引用0
    assert res == {"reply_suggestions": 2, "quote_suggestions": 0}
    assert len(fake_formatter.select_calls) == 1  # AI判断は1回のバッチに集約


def test_run_once_quote_kind_creates_quote_draft(session, fake_formatter):
    """AIが quote と判断した選定は引用案として下書きされる(選定理由も保持)。"""
    fake_formatter.engage_kind_return = "quote"
    session.add(EngageTarget(kind=TargetKind.MANUAL, handle="famous", user_id="u9", active=True))
    session.commit()
    fx = FakeXClient(timelines={"u9": [{"id": "202", "text": "拡散したいニュース", "author_id": "u9"}]})
    res = monitor.run_once(session, fx, fake_formatter, me_user_id="me")
    assert res == {"reply_suggestions": 0, "quote_suggestions": 1}
    d = session.exec(select(Draft).where(Draft.kind == DraftKind.QUOTE)).one()
    assert d.target_tweet_id == "202"
    assert d.target_handle == "famous"
    assert d.target_text == "拡散したいニュース"
    assert "[AI選定理由]" in d.source_text


def test_run_once_spreads_across_accounts(session, fake_formatter):
    """複数アカウントの投稿が同じバッチに乗り、選定が分散される(従来の先頭偏りの解消)。"""
    session.add(EngageTarget(kind=TargetKind.MANUAL, handle="a1", user_id="u1", active=True))
    session.add(EngageTarget(kind=TargetKind.MANUAL, handle="a2", user_id="u2", active=True))
    session.commit()
    fx = FakeXClient(timelines={
        "u1": [{"id": str(200 + i), "text": f"a1の投稿{i}", "author_id": "u1"} for i in range(10)],
        "u2": [{"id": "300", "text": "a2の投稿", "author_id": "u2"}],
    })
    monitor.run_once(session, fx, fake_formatter, me_user_id="me")
    # 後段アカウントの投稿もAI選定の候補に含まれている(直列バジェット先食いが起きない)
    handles = {c["handle"] for c in fake_formatter.select_calls[0]["candidates"]}
    assert handles == {"a1", "a2"}


def test_mentions_can_be_disabled(session, fake_formatter):
    monitor.set_monitor_settings(session, mentions_enabled=False)
    fx = FakeXClient(mentions=[{"id": "101", "text": "m", "author_id": "u1"}])
    assert monitor.run_once(session, fx, fake_formatter, me_user_id="me")["reply_suggestions"] == 0


def test_poll_mentions_stores_target_text(session, fake_formatter):
    """メンション返信案にも元ポスト本文が target_text として残る。"""
    fx = FakeXClient(mentions=[{"id": "101", "text": "メンション本文", "author_id": "u1"}])
    monitor.poll_mentions(session, fx, fake_formatter, me_user_id="me")
    d = session.exec(select(Draft).where(Draft.kind == DraftKind.REPLY)).first()
    assert d.target_text == "メンション本文"


# --- 生成数バジェット(1サイクルの総生成数上限) ----------------------------

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
    """バジェットはメンションとバッチ選定で共有され、合計が上限を超えない。"""
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
    # メンション1 + 残バジェット1(選定 max_n=1) = 計2、すべて返信案に集約
    assert res["reply_suggestions"] == 2
    assert res["quote_suggestions"] == 0
    assert fake_formatter.select_calls[0]["max_n"] == 1


def test_max_drafts_zero_creates_nothing(session, fake_formatter):
    monitor.set_monitor_settings(session, max_drafts_per_run=0)
    fx = FakeXClient(mentions=[{"id": "101", "text": "m", "author_id": "u1"}])
    res = monitor.run_once(session, fx, fake_formatter, me_user_id="me")
    assert res == {"reply_suggestions": 0, "quote_suggestions": 0}


def test_within_age_skips_old_posts(session, fake_formatter):
    """24時間より古い投稿にはリプ案を作らない(新しいものだけ対象・乱造/無意味リプ防止)。"""
    now = datetime.now(timezone.utc)
    fx = FakeXClient(mentions=[
        {"id": "1", "text": "古い", "author_id": "u1", "created_at": now - timedelta(hours=30)},
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
