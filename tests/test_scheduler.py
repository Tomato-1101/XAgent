from datetime import datetime

from xagent import service
from xagent.config import Settings
from xagent.models import DraftStatus
from xagent.scheduler import (
    DEFAULT_SLOTS_HOUR,
    next_optimal_slot,
    process_due_queue,
    schedule_optimal,
)


def _settings(**kw):
    base = dict(min_post_interval_seconds=0, timezone="Asia/Tokyo")
    base.update(kw)
    return Settings(**base)


def test_next_optimal_slot_returns_future():
    now = datetime(2026, 5, 30, 0, 0, 0)  # naive UTC = JST 09:00
    slot = next_optimal_slot(now, [], tz_name="Asia/Tokyo")
    assert slot > now


def test_next_optimal_slot_avoids_taken():
    now = datetime(2026, 5, 30, 0, 0, 0)
    first = next_optimal_slot(now, [], tz_name="Asia/Tokyo")
    second = next_optimal_slot(now, [first], tz_name="Asia/Tokyo")
    # 既に埋まっているスロットは避ける(90分以上離れる)
    assert abs((second - first).total_seconds()) >= 90 * 60


def test_schedule_optimal_sets_queue_and_time(session, fake_formatter):
    d = service.create_post_draft(session, fake_formatter, "予約投稿")
    service.approve_draft(session, d)
    now = datetime(2026, 5, 30, 0, 0, 0)
    schedule_optimal(session, d, now=now, settings=_settings())
    assert d.status == DraftStatus.QUEUED
    assert d.scheduled_at is not None and d.scheduled_at > now


def test_process_due_queue_posts_due_only(session, fake_formatter, fake_x):
    now = datetime(2026, 5, 30, 12, 0, 0)
    # 期限到来(過去時刻)のキュー
    d_due = service.create_post_draft(session, fake_formatter, "出庫対象")
    service.approve_draft(session, d_due)
    service.queue_draft(session, d_due, scheduled_at=datetime(2026, 5, 30, 11, 0, 0))
    # 未来予約は対象外
    d_future = service.create_post_draft(session, fake_formatter, "未来予約")
    service.approve_draft(session, d_future)
    service.queue_draft(session, d_future, scheduled_at=datetime(2026, 5, 31, 12, 0, 0))

    result = process_due_queue(session, fake_x, now=now, settings=_settings())
    assert len(result["posted"]) == 1
    assert result["posted"][0]["draft_id"] == d_due.id
    session.refresh(d_due)
    session.refresh(d_future)
    assert d_due.status == DraftStatus.POSTED
    assert d_future.status == DraftStatus.QUEUED


def test_due_queue_never_posts_without_reservation(session, fake_formatter, fake_x):
    """予約時刻のないキューは、自動投稿(SCHEDULED)では絶対に出ない。"""
    now = datetime(2026, 5, 30, 12, 0, 0)
    d = service.create_post_draft(session, fake_formatter, "予約なしキュー")
    service.approve_draft(session, d)
    service.queue_draft(session, d)  # scheduled_at を渡さない → None のままキュー
    assert d.scheduled_at is None

    result = process_due_queue(session, fake_x, now=now, settings=_settings())
    assert result["posted"] == []
    session.refresh(d)
    assert d.status == DraftStatus.QUEUED  # 投稿されず据え置き
    assert fake_x.posted == []  # Xへも一切送っていない


def test_posting_disabled_blocks_scheduled(session, fake_formatter, fake_x):
    """posting_enabled=False なら到来済み予約でも投稿しない(緊急停止)。"""
    now = datetime(2026, 5, 30, 12, 0, 0)
    d = service.create_post_draft(session, fake_formatter, "停止中")
    service.approve_draft(session, d)
    service.queue_draft(session, d, scheduled_at=datetime(2026, 5, 30, 11, 0, 0))

    result = process_due_queue(
        session, fake_x, now=now, settings=_settings(posting_enabled=False)
    )
    assert result["posted"] == []
    assert len(result["skipped"]) == 1
    assert fake_x.posted == []


def test_queue_normalizes_aware_scheduled_at(session, fake_formatter, fake_x):
    """aware datetime(+09:00)で予約しても内部はnaive UTCに正規化され、到来判定が壊れない。"""
    from datetime import timedelta, timezone

    now = datetime(2026, 5, 30, 12, 0, 0)  # naive UTC
    d = service.create_post_draft(session, fake_formatter, "aware予約")
    service.approve_draft(session, d)
    # JST 20:00 = 11:00 UTC(到来済み)
    aware = datetime(2026, 5, 30, 20, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    service.queue_draft(session, d, scheduled_at=aware)
    assert d.scheduled_at.tzinfo is None  # naive 化
    assert d.scheduled_at == datetime(2026, 5, 30, 11, 0, 0)

    # naive UTC の now と比較しても TypeError にならず、投稿対象になる
    result = process_due_queue(session, fake_x, now=now, settings=_settings())
    assert len(result["posted"]) == 1


def test_slots_are_within_day():
    assert all(0 <= h <= 23 for h in DEFAULT_SLOTS_HOUR)
