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
