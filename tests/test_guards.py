from datetime import datetime, timedelta, timezone

import pytest

from xagent.guards import (
    PolicyViolation,
    PostTrigger,
    RateLimitConfig,
    RateLimiter,
    can_transition,
    ensure_post_authorized,
    ensure_postable,
)
from xagent.models import DraftStatus


def _now():
    return datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)


def test_approval_gate_blocks_unapproved():
    with pytest.raises(PolicyViolation):
        ensure_postable(DraftStatus.DRAFT)
    # 承認済み/キューはOK(例外が出ない)
    ensure_postable(DraftStatus.APPROVED)
    ensure_postable(DraftStatus.QUEUED)


def test_transitions():
    assert can_transition(DraftStatus.DRAFT, DraftStatus.APPROVED)
    assert can_transition(DraftStatus.APPROVED, DraftStatus.QUEUED)
    assert can_transition(DraftStatus.QUEUED, DraftStatus.POSTED)
    # 未承認からいきなり投稿済みへは不可
    assert not can_transition(DraftStatus.DRAFT, DraftStatus.POSTED)
    # 投稿済みからの遷移は無し
    assert not can_transition(DraftStatus.POSTED, DraftStatus.APPROVED)


def test_rate_limiter_allows_when_clear():
    rl = RateLimiter(RateLimitConfig(max_per_day=10, min_interval_seconds=300))
    now = _now()
    times = [now - timedelta(hours=2), now - timedelta(hours=5)]
    assert rl.check(times, now).allowed is True


def test_rate_limiter_min_interval():
    rl = RateLimiter(RateLimitConfig(min_interval_seconds=300))
    now = _now()
    times = [now - timedelta(seconds=100)]
    d = rl.check(times, now)
    assert d.allowed is False
    assert d.retry_after_seconds == pytest.approx(200, abs=2)


def test_rate_limiter_daily_max():
    rl = RateLimiter(RateLimitConfig(max_per_day=3, min_interval_seconds=0))
    now = _now()
    times = [now - timedelta(hours=h) for h in (1, 3, 6)]  # 直近24hに3件
    assert rl.check(times, now).allowed is False


def test_rate_limiter_ignores_old_posts():
    rl = RateLimiter(RateLimitConfig(max_per_day=2, min_interval_seconds=0))
    now = _now()
    # 25時間前は当日カウントに含めない
    times = [now - timedelta(hours=25), now - timedelta(hours=26)]
    assert rl.check(times, now).allowed is True


# --- 投稿の厳重化(認証 or 予約のみ) ----------------------------------------

_NOW = datetime(2026, 5, 30, 12, 0, 0)  # naive UTC(本番と同じ扱い)


def test_manual_trigger_requires_approval():
    # 人の明示操作(MANUAL): 未承認は拒否、承認済み/キューはOK
    with pytest.raises(PolicyViolation):
        ensure_post_authorized(DraftStatus.DRAFT, None, PostTrigger.MANUAL, _NOW)
    ensure_post_authorized(DraftStatus.APPROVED, None, PostTrigger.MANUAL, _NOW)
    ensure_post_authorized(DraftStatus.QUEUED, _NOW, PostTrigger.MANUAL, _NOW)


def test_scheduled_trigger_rejects_without_reservation():
    # 予約時刻のないキューは自動投稿しない(誤爆防止の要)
    with pytest.raises(PolicyViolation):
        ensure_post_authorized(DraftStatus.QUEUED, None, PostTrigger.SCHEDULED, _NOW)


def test_scheduled_trigger_rejects_future_reservation():
    future = _NOW + timedelta(hours=3)
    with pytest.raises(PolicyViolation):
        ensure_post_authorized(DraftStatus.QUEUED, future, PostTrigger.SCHEDULED, _NOW)


def test_scheduled_trigger_rejects_non_queued():
    # 承認済み(未キュー)を自動投稿しようとしても拒否
    with pytest.raises(PolicyViolation):
        ensure_post_authorized(DraftStatus.APPROVED, _NOW, PostTrigger.SCHEDULED, _NOW)


def test_scheduled_trigger_allows_due_reservation():
    past = _NOW - timedelta(minutes=1)
    # 例外が出なければOK
    ensure_post_authorized(DraftStatus.QUEUED, past, PostTrigger.SCHEDULED, _NOW)
