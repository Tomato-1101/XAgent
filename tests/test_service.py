from datetime import datetime, timedelta

import pytest

from xagent.config import Settings
from xagent.guards import PolicyViolation
from xagent.models import DraftKind, DraftStatus
from xagent import service


def _settings(**kw):
    base = dict(max_posts_per_day=10, hard_cap_posts_per_day=100, min_post_interval_seconds=0)
    base.update(kw)
    return Settings(**base)


def test_create_approve_post_happy_path(session, fake_formatter, fake_x):
    d = service.create_post_draft(session, fake_formatter, "テスト投稿の内容")
    assert d.status == DraftStatus.DRAFT
    service.approve_draft(session, d)
    ids = service.post_draft(session, fake_x, d, settings=_settings())
    assert len(ids) == 1
    assert d.status == DraftStatus.POSTED
    assert d.posted_tweet_id == ids[0]
    assert fake_x.posted[0]["text"] == "テスト投稿の内容"


def test_post_unapproved_blocked(session, fake_formatter, fake_x):
    d = service.create_post_draft(session, fake_formatter, "未承認")
    with pytest.raises(PolicyViolation):
        service.post_draft(session, fake_x, d, settings=_settings())


def test_reply_uses_in_reply_to(session, fake_formatter, fake_x):
    d = service.create_reply_draft(session, fake_formatter, "555", "元の投稿", "someone")
    assert d.kind == DraftKind.REPLY
    service.approve_draft(session, d)
    service.post_draft(session, fake_x, d, settings=_settings())
    assert fake_x.posted[0]["reply_to"] == "555"


def test_quote_uses_quote_id(session, fake_formatter, fake_x):
    d = service.create_quote_draft(session, fake_formatter, "777", "有名人の投稿", "famous")
    service.approve_draft(session, d)
    service.post_draft(session, fake_x, d, settings=_settings())
    assert fake_x.posted[0]["quote"] == "777"


def test_target_created_at_stored_naive_utc(session, fake_formatter):
    """元投稿の投稿時刻(aware)を渡すと naive UTC で保持され、表示・鮮度判断に使える。"""
    from datetime import timezone

    aware = datetime(2026, 6, 1, 12, 0, tzinfo=timezone(timedelta(hours=9)))  # JST 12:00
    d = service.create_quote_draft(
        session, fake_formatter, "777", "元投稿", "famous", target_created_at=aware
    )
    assert d.target_created_at == datetime(2026, 6, 1, 3, 0)  # UTC・tzなし
    assert d.target_created_at.tzinfo is None
    # 渡さなければ None(取得できなかった旧データ等)
    d2 = service.create_quote_draft(session, fake_formatter, "778", "元投稿2", "famous")
    assert d2.target_created_at is None


def test_rate_limit_min_interval_blocks(session, fake_formatter, fake_x):
    now = datetime(2026, 5, 30, 12, 0, 0)
    # 1件目を投稿
    d1 = service.create_post_draft(session, fake_formatter, "一件目")
    service.approve_draft(session, d1)
    service.post_draft(session, fake_x, d1, settings=_settings(min_post_interval_seconds=300), now=now)
    # 直後の2件目は連投間隔で弾かれる
    d2 = service.create_post_draft(session, fake_formatter, "二件目")
    service.approve_draft(session, d2)
    with pytest.raises(PolicyViolation):
        service.post_draft(
            session, fake_x, d2,
            settings=_settings(min_post_interval_seconds=300),
            now=now + timedelta(seconds=100),
        )


def test_thread_posting_multiple_segments(session, fake_formatter, fake_x):
    d = service.create_post_draft(session, fake_formatter, "x")
    # 手動で複数セグメントに差し替え(スレッド)
    service.update_segments(session, d, ["seg1", "seg2", "seg3"])
    service.approve_draft(session, d)
    ids = service.post_draft(session, fake_x, d, settings=_settings())
    assert len(ids) == 3
    # 2件目以降は前の投稿へのリプライ(スレッド)
    assert fake_x.posted[1]["reply_to"] == ids[0]
    assert fake_x.posted[2]["reply_to"] == ids[1]


def test_raw_post_skips_formatter(session, fake_x):
    """raw=True なら整形器を呼ばず、入力をそのまま本文にする。"""

    class BoomFormatter:
        def format_post(self, *a, **k):
            raise AssertionError("raw のとき format_post を呼んではいけない")

    d = service.create_post_draft(session, BoomFormatter(), "そのまま出す本文", raw=True)
    assert d.kind == DraftKind.POST
    assert d.segments_json and "そのまま出す本文" in d.segments_json
    service.approve_draft(session, d)
    service.post_draft(session, fake_x, d, settings=_settings())
    assert fake_x.posted[0]["text"] == "そのまま出す本文"


def test_quote_command_formats_user_comment(session, fake_formatter, fake_x):
    """指令フローの引用: ユーザーのコメントを整形して引用先IDを付けて投稿する。"""
    d = service.create_quote_command_draft(
        session, fake_formatter, "12345", "自分のコメント", target_handle="BitoFCE"
    )
    assert d.kind == DraftKind.QUOTE
    assert d.target_tweet_id == "12345"
    service.approve_draft(session, d)
    service.post_draft(session, fake_x, d, settings=_settings())
    assert fake_x.posted[0]["quote"] == "12345"
    assert "自分のコメント" in fake_x.posted[0]["text"]


def test_quote_command_raw_skips_formatter(session, fake_x):
    class BoomFormatter:
        def format_post(self, *a, **k):
            raise AssertionError("raw のとき format_post を呼んではいけない")

    d = service.create_quote_command_draft(
        session, BoomFormatter(), "999", "そのままの引用コメント", raw=True
    )
    service.approve_draft(session, d)
    service.post_draft(session, fake_x, d, settings=_settings())
    assert fake_x.posted[0]["quote"] == "999"
    assert fake_x.posted[0]["text"] == "そのままの引用コメント"


# --- target_text(元ポスト本文)の保持 --------------------------------------

def test_create_reply_draft_stores_target_text(session, fake_formatter):
    """AI生成リプライでも元ポスト本文を target_text に保持する(承認判断・表示用)。"""
    d = service.create_reply_draft(session, fake_formatter, "555", "返信先の元ポスト", "someone")
    assert d.kind == DraftKind.REPLY
    assert d.target_tweet_id == "555"
    assert d.target_text == "返信先の元ポスト"


def test_create_quote_draft_stores_target_text(session, fake_formatter):
    d = service.create_quote_draft(session, fake_formatter, "777", "引用元の本文", "famous")
    assert d.kind == DraftKind.QUOTE
    assert d.target_text == "引用元の本文"


def test_quote_command_stores_target_text(session, fake_formatter):
    """指令フローでは source_text=自分のコメント / target_text=引用元本文 を分離保持する。"""
    d = service.create_quote_command_draft(
        session, fake_formatter, "12345", "自分のコメント",
        target_handle="BitoFCE", target_text="引用元の本文",
    )
    assert d.source_text == "自分のコメント"
    assert d.target_text == "引用元の本文"


# --- 指令フローのリプライ(URL指定で自分の文を返信) ------------------------

def test_reply_command_formats_user_comment(session, fake_formatter, fake_x):
    """指令フローのリプライ: 自分の文を整形し、元ポスト本文も保持して in_reply_to で投稿。"""
    d = service.create_reply_command_draft(
        session, fake_formatter, "888", "自分の返信文",
        target_handle="someone", target_text="相手の元ポスト",
    )
    assert d.kind == DraftKind.REPLY
    assert d.target_tweet_id == "888"
    assert d.source_text == "自分の返信文"
    assert d.target_text == "相手の元ポスト"
    service.approve_draft(session, d)
    service.post_draft(session, fake_x, d, settings=_settings())
    assert fake_x.posted[0]["reply_to"] == "888"
    assert "自分の返信文" in fake_x.posted[0]["text"]


def test_reply_command_raw_skips_formatter(session, fake_x):
    class BoomFormatter:
        def format_post(self, *a, **k):
            raise AssertionError("raw のとき format_post を呼んではいけない")

    d = service.create_reply_command_draft(
        session, BoomFormatter(), "999", "そのままの返信文", raw=True
    )
    assert d.kind == DraftKind.REPLY
    service.approve_draft(session, d)
    service.post_draft(session, fake_x, d, settings=_settings())
    assert fake_x.posted[0]["reply_to"] == "999"
    assert fake_x.posted[0]["text"] == "そのままの返信文"


# --- 取消(ゴミ箱)・復元 ----------------------------------------------------

def test_cancel_draft_moves_to_canceled(session, fake_formatter):
    d = service.create_post_draft(session, fake_formatter, "取消する下書き")
    service.cancel_draft(session, d)
    assert d.status == DraftStatus.CANCELED


def test_cancel_approved_draft(session, fake_formatter):
    d = service.create_post_draft(session, fake_formatter, "承認後に取消")
    service.approve_draft(session, d)
    service.cancel_draft(session, d)
    assert d.status == DraftStatus.CANCELED


def test_cancel_queued_clears_schedule_and_stops_autopost(session, fake_formatter):
    """予約済みを取消したら予約解除され、スケジューラの投稿対象から外れる。"""
    from xagent import scheduler

    d = service.create_post_draft(session, fake_formatter, "予約してから取消")
    service.approve_draft(session, d)
    past = datetime(2026, 5, 1, 0, 0, 0)  # 到来済みの予約
    service.queue_draft(session, d, scheduled_at=past)
    assert d.status == DraftStatus.QUEUED
    # 取消前は到来済みキューとして投稿対象に挙がる
    assert any(x.id == d.id for x in scheduler.due_drafts(session, datetime(2026, 5, 2)))

    service.cancel_draft(session, d)
    assert d.status == DraftStatus.CANCELED
    assert d.scheduled_at is None
    # 取消後は自動投稿対象に挙がらない
    assert all(x.id != d.id for x in scheduler.due_drafts(session, datetime(2026, 5, 2)))


def test_canceled_draft_cannot_be_posted_by_scheduler(session, fake_formatter, fake_x):
    """取消済みは SCHEDULED トリガでも投稿されない(ガードで拒否)。"""
    from xagent.guards import PostTrigger

    d = service.create_post_draft(session, fake_formatter, "取消済みは投稿されない")
    service.approve_draft(session, d)
    service.queue_draft(session, d, scheduled_at=datetime(2026, 5, 1))
    service.cancel_draft(session, d)
    with pytest.raises(PolicyViolation):
        service.post_draft(
            session, fake_x, d, settings=_settings(),
            now=datetime(2026, 5, 2), trigger=PostTrigger.SCHEDULED,
        )


def test_restore_canceled_to_draft(session, fake_formatter):
    d = service.create_post_draft(session, fake_formatter, "復元する下書き")
    service.cancel_draft(session, d)
    service.restore_draft(session, d)
    assert d.status == DraftStatus.DRAFT


def test_cannot_cancel_posted(session, fake_formatter, fake_x):
    """投稿済みは取消できない(履歴の改ざん防止)。"""
    d = service.create_post_draft(session, fake_formatter, "投稿済みは取消不可")
    service.approve_draft(session, d)
    service.post_draft(session, fake_x, d, settings=_settings())
    assert d.status == DraftStatus.POSTED
    with pytest.raises(PolicyViolation):
        service.cancel_draft(session, d)


# --- 「型」(PromptTemplate)の適用 -----------------------------------------

from xagent import templates as templates_mod  # noqa: E402
from xagent.models import TemplateKind          # noqa: E402


def test_post_draft_injects_selected_template(session, fake_formatter):
    t = templates_mod.create_template(session, "型X", TemplateKind.POST, "型本文X")
    service.create_post_draft(session, fake_formatter, "メモ", template_id=t.id)
    assert fake_formatter.playbooks[-1] == "型本文X"   # 選んだ型がformatterに渡る


def test_post_draft_no_template_means_empty_playbook(session, fake_formatter):
    service.create_post_draft(session, fake_formatter, "メモ")
    assert fake_formatter.playbooks[-1] == ""


def test_post_draft_auto_template_lets_ai_choose(session, fake_formatter):
    templates_mod.create_template(session, "型A", TemplateKind.POST, "本文A")
    b = templates_mod.create_template(session, "型B", TemplateKind.POST, "本文B")
    fake_formatter.complete_return = str(b.id)         # AIが型Bを選ぶ
    service.create_post_draft(session, fake_formatter, "メモ", auto_template=True)
    assert fake_formatter.playbooks[-1] == "本文B"


def test_monitor_reply_uses_active_reply_template(session, fake_formatter):
    templates_mod.create_template(session, "リプ型", TemplateKind.REPLY, "リプ本文", active=True)
    service.create_reply_draft(session, fake_formatter, "555", "元の投稿", "someone")
    assert fake_formatter.playbooks[-1] == "リプ本文"   # active reply型が自動適用


def test_reply_command_injects_template(session, fake_formatter):
    t = templates_mod.create_template(session, "リプ型C", TemplateKind.REPLY, "コマンドリプ本文")
    service.create_reply_command_draft(
        session, fake_formatter, "555", "自分のコメント", template_id=t.id
    )
    assert fake_formatter.playbooks[-1] == "コマンドリプ本文"
