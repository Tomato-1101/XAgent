"""制限時間帯(ブラックアウト)と通常リポストのテスト。

- is_blackout は外部API不要の純関数(曜日/時間帯/境界/カスタム/無効)。
- guards.ensure_not_blackout の override 挙動。
- 通常リポスト下書き → x_client.retweet 呼び出し。
- post_draft が制限帯でブロック/override で突破。
- スケジューラの予約発火は override 済みだけ制限帯でも投稿。
"""

from datetime import datetime

import pytest

from xagent import service
from xagent.blackout import is_blackout, set_blackout_settings
from xagent.config import Settings
from xagent.guards import BlackoutViolation, ensure_not_blackout
from xagent.models import BlackoutSettings, DraftKind, DraftStatus
from xagent.scheduler import process_due_queue


def _settings(**kw):
    base = dict(
        max_posts_per_day=100,
        hard_cap_posts_per_day=1000,
        min_post_interval_seconds=0,
        timezone="Asia/Tokyo",
        posting_enabled=True,
    )
    base.update(kw)
    return Settings(**base)


# --- is_blackout(純関数) ----------------------------------------------------

def test_is_blackout_weekday_morning_window():
    # 月曜 JST 11:00 = UTC 02:00 → 09-12帯でブロック
    blocked, reason = is_blackout(datetime(2026, 6, 1, 2, 0, 0), BlackoutSettings())
    assert blocked is True
    assert "制限時間帯" in reason


def test_is_blackout_weekday_afternoon_window():
    # 月曜 JST 14:00 = UTC 05:00 → 13-19帯でブロック
    blocked, _ = is_blackout(datetime(2026, 6, 1, 5, 0, 0), BlackoutSettings())
    assert blocked is True


def test_is_blackout_noon_boundary_exclusive():
    # 月曜 JST 12:00 = UTC 03:00 → 09-12 の end は排他 → ブロックしない
    blocked, _ = is_blackout(datetime(2026, 6, 1, 3, 0, 0), BlackoutSettings())
    assert blocked is False


def test_is_blackout_outside_window():
    # 月曜 JST 20:00 = UTC 11:00 → 帯外
    blocked, _ = is_blackout(datetime(2026, 6, 1, 11, 0, 0), BlackoutSettings())
    assert blocked is False


def test_is_blackout_weekend_free():
    # 土曜 JST 11:00 → 既定の対象曜日(月〜金)外
    blocked, _ = is_blackout(datetime(2026, 6, 6, 2, 0, 0), BlackoutSettings())
    assert blocked is False


def test_is_blackout_disabled():
    blocked, _ = is_blackout(datetime(2026, 6, 1, 2, 0, 0), BlackoutSettings(enabled=False))
    assert blocked is False


def test_set_blackout_custom_window(session):
    # カスタム: 月曜の夜20-22時だけ制限
    row = set_blackout_settings(session, enabled=True, weekdays=[0], windows=[["20:00", "22:00"]])
    # 月曜 JST 21:00 = UTC 12:00 → ブロック
    assert is_blackout(datetime(2026, 6, 1, 12, 0, 0), row)[0] is True
    # 月曜 JST 11:00 → カスタム帯外
    assert is_blackout(datetime(2026, 6, 1, 2, 0, 0), row)[0] is False


def test_set_blackout_filters_invalid_window(session):
    # 不正な "HH:MM" は弾かれる
    row = set_blackout_settings(session, enabled=True, windows=[["09:00", "bad"], ["10:00", "11:00"]])
    import json

    assert json.loads(row.windows_json) == [["10:00", "11:00"]]


# --- guards.ensure_not_blackout --------------------------------------------

def test_ensure_not_blackout_blocks_without_override():
    with pytest.raises(BlackoutViolation):
        ensure_not_blackout(True, False, "理由")


def test_ensure_not_blackout_passes_with_override():
    ensure_not_blackout(True, True, "理由")  # 例外なし


def test_ensure_not_blackout_passes_when_free():
    ensure_not_blackout(False, False)  # 例外なし


# --- 通常リポスト ------------------------------------------------------------

def test_create_repost_draft(session):
    d = service.create_repost_draft(session, "555", target_text="元の投稿")
    assert d.kind == DraftKind.REPOST
    assert d.status == DraftStatus.DRAFT
    assert d.target_tweet_id == "555"
    assert d.target_text == "元の投稿"


def test_post_repost_calls_retweet(session, fake_x):
    d = service.create_repost_draft(session, "555", target_text="元の投稿")
    service.approve_draft(session, d)
    ids = service.post_draft(session, fake_x, d, settings=_settings())
    assert fake_x.retweeted == ["555"]
    assert fake_x.posted == []  # 通常リポストは新規postを作らない
    assert ids == ["555"]
    assert d.posted_tweet_id == "555"
    assert d.status == DraftStatus.POSTED


# --- post_draft の制限帯ガード ----------------------------------------------

def test_post_draft_blocked_in_blackout(session, fake_formatter, fake_x):
    set_blackout_settings(session, enabled=True)
    d = service.create_post_draft(session, fake_formatter, "制限帯の投稿")
    service.approve_draft(session, d)
    now = datetime(2026, 6, 1, 2, 0, 0)  # 月 JST 11:00
    with pytest.raises(BlackoutViolation):
        service.post_draft(session, fake_x, d, settings=_settings(), now=now)
    assert fake_x.posted == []
    assert d.status == DraftStatus.APPROVED  # 未投稿のまま


def test_post_draft_override_in_blackout(session, fake_formatter, fake_x):
    set_blackout_settings(session, enabled=True)
    d = service.create_post_draft(session, fake_formatter, "二段階確認で突破")
    service.approve_draft(session, d)
    now = datetime(2026, 6, 1, 2, 0, 0)
    ids = service.post_draft(session, fake_x, d, settings=_settings(), now=now, override=True)
    assert len(ids) == 1
    assert d.status == DraftStatus.POSTED


def test_post_draft_weekend_not_blocked(session, fake_formatter, fake_x):
    set_blackout_settings(session, enabled=True)
    d = service.create_post_draft(session, fake_formatter, "土曜は自由")
    service.approve_draft(session, d)
    now = datetime(2026, 6, 6, 2, 0, 0)  # 土 JST 11:00
    ids = service.post_draft(session, fake_x, d, settings=_settings(), now=now)
    assert len(ids) == 1
    assert d.status == DraftStatus.POSTED


# --- スケジューラの予約発火 × 制限帯 -----------------------------------------

def test_scheduled_fires_with_override_in_blackout(session, fake_x):
    set_blackout_settings(session, enabled=True)
    d = service.create_repost_draft(session, "777")
    service.approve_draft(session, d)
    service.queue_draft(
        session, d, scheduled_at=datetime(2026, 6, 1, 1, 0, 0), blackout_override=True
    )
    now = datetime(2026, 6, 1, 2, 0, 0)  # 月 JST 11:00(制限帯)
    res = process_due_queue(session, fake_x, now=now, settings=_settings())
    assert len(res["posted"]) == 1
    assert fake_x.retweeted == ["777"]


def test_scheduled_blocked_in_blackout_without_override(session, fake_x):
    set_blackout_settings(session, enabled=True)
    d = service.create_repost_draft(session, "888")
    service.approve_draft(session, d)
    service.queue_draft(
        session, d, scheduled_at=datetime(2026, 6, 1, 1, 0, 0), blackout_override=False
    )
    now = datetime(2026, 6, 1, 2, 0, 0)
    res = process_due_queue(session, fake_x, now=now, settings=_settings())
    assert res["posted"] == []
    assert len(res["skipped"]) == 1
    assert fake_x.retweeted == []
    # 据え置き: 制限帯が明ければ次回発火できるよう QUEUED のまま
    assert d.status == DraftStatus.QUEUED


def test_scheduled_fires_after_blackout_window(session, fake_x):
    set_blackout_settings(session, enabled=True)
    d = service.create_repost_draft(session, "999")
    service.approve_draft(session, d)
    service.queue_draft(
        session, d, scheduled_at=datetime(2026, 6, 1, 1, 0, 0), blackout_override=False
    )
    now = datetime(2026, 6, 1, 11, 0, 0)  # 月 JST 20:00(帯外)→ override無しでも発火
    res = process_due_queue(session, fake_x, now=now, settings=_settings())
    assert len(res["posted"]) == 1
    assert fake_x.retweeted == ["999"]
