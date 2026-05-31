"""投稿スケジューリング。最適時間への分散と、予約キューの消化。

- 最適時間スロット(JSTの一般的なエンゲージ時間帯)に分散して予約する。
- process_due_queue でキューの期限到来分を投稿する(頻度ガードで抑止された分は据え置き)。
- next_optimal_slot は純粋関数で、テスト可能。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from .config import Settings, get_settings
from .guards import PolicyViolation, PostTrigger
from .models import Draft, DraftStatus
from .service import post_draft
from .x_client import XClient

# 日本時間で一般にエンゲージが高いとされる時間帯(時)。実データがあれば後で差し替え。
DEFAULT_SLOTS_HOUR: tuple[int, ...] = (7, 8, 12, 18, 21, 22)


def _to_utc_naive(dt_aware: datetime) -> datetime:
    return dt_aware.astimezone(timezone.utc).replace(tzinfo=None)


def next_optimal_slot(
    now_utc: datetime,
    taken_utc: list[datetime],
    slots_hour: tuple[int, ...] = DEFAULT_SLOTS_HOUR,
    tz_name: str = "Asia/Tokyo",
    min_gap_minutes: int = 90,
    horizon_days: int = 14,
) -> datetime:
    """now(naive UTC)以降で、まだ埋まっていない最適スロット(naive UTC)を返す。"""
    tz = ZoneInfo(tz_name)
    now_local = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    slots = sorted(set(slots_hour))
    gap = timedelta(minutes=min_gap_minutes)

    for day in range(horizon_days):
        date_local = (now_local + timedelta(days=day)).date()
        for hour in slots:
            cand_local = datetime.combine(date_local, time(hour=hour), tzinfo=tz)
            cand_utc = _to_utc_naive(cand_local)
            if cand_utc <= now_utc:
                continue
            if any(abs((cand_utc - t).total_seconds()) < gap.total_seconds() for t in taken_utc):
                continue
            return cand_utc
    # フォールバック: ホライズンを超えたら最終候補の翌日同時刻
    return now_utc + timedelta(days=horizon_days)


def queued_scheduled_times(session: Session) -> list[datetime]:
    stmt = select(Draft.scheduled_at).where(
        Draft.status == DraftStatus.QUEUED, Draft.scheduled_at != None  # noqa: E711
    )
    return [t for t in session.exec(stmt).all() if t is not None]


def schedule_optimal(
    session: Session, draft: Draft, now: datetime | None = None,
    settings: Settings | None = None,
) -> Draft:
    """下書きを最適時間スロットに割り当ててキュー投入する(承認済みが前提)。"""
    settings = settings or get_settings()
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    slot = next_optimal_slot(now, queued_scheduled_times(session), tz_name=settings.timezone)
    from .service import queue_draft

    return queue_draft(session, draft, scheduled_at=slot)


def due_drafts(session: Session, now: datetime) -> list[Draft]:
    """自動投稿対象の到来済みキューを返す。

    安全のため **予約時刻(scheduled_at)が設定済みで、かつ到来済み** のものだけ。
    予約時刻のないキューは自動投稿しない(人が明示投稿するか、予約を設定する)。
    """
    stmt = select(Draft).where(
        Draft.status == DraftStatus.QUEUED,
        Draft.scheduled_at != None,  # noqa: E711
    )
    return [d for d in session.exec(stmt).all() if d.scheduled_at <= now]


def process_due_queue(
    session: Session,
    x_client: XClient,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> dict:
    """期限到来分を投稿。頻度ガードで弾かれた分は据え置き、結果サマリを返す。"""
    settings = settings or get_settings()
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    posted, skipped, errors = [], [], []
    for draft in due_drafts(session, now):
        try:
            ids = post_draft(
                session, x_client, draft, settings=settings, now=now,
                trigger=PostTrigger.SCHEDULED,
            )
            posted.append({"draft_id": draft.id, "tweet_ids": ids})
        except PolicyViolation as e:
            skipped.append({"draft_id": draft.id, "reason": str(e)})
        except Exception as e:  # 投稿失敗(ネットワーク等)は据え置き
            errors.append({"draft_id": draft.id, "error": str(e)})
    return {"posted": posted, "skipped": skipped, "errors": errors}
