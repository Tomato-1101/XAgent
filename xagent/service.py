"""サービス層。下書きの生成→承認→キュー→投稿を、ガードとコスト記録込みで束ねる。

DBセッションは引数で受け取り(テスト容易性のため)、外部依存(formatter / x_client)も注入する。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlmodel import Session, select

from .config import Settings, get_settings
from .cost import log_cost
from .formatter import Formatter
from .guards import (
    PolicyViolation,
    PostTrigger,
    RateLimitConfig,
    RateLimiter,
    can_transition,
    ensure_post_authorized,
)
from .models import CostKind, Draft, DraftKind, DraftStatus
from .profiles import example_posts_for_account, get_profile_by_handle
from .style import active_style_guide
from .x_client import XClient


def _emulate_inputs(session: Session, emulate_handle: str | None) -> tuple[str, list[str] | None]:
    """「真似る相手」が指定されていれば、その特徴テキストと代表投稿を返す(選択時のみ)。"""
    if not emulate_handle:
        return "", None
    prof = get_profile_by_handle(session, emulate_handle)
    if prof is None:
        return "", None
    return prof.profile_text or "", example_posts_for_account(session, emulate_handle)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(dt: datetime | None) -> datetime | None:
    """予約時刻を内部表現(naive UTC)へ正規化する。

    API/CLI から aware datetime(+09:00 や Z 付き)が来ても、内部の naive UTC 比較
    (scheduler.due_drafts)とフロントの表示(+\"Z\")が壊れないように揃える。
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _segments(draft: Draft) -> list[str]:
    return json.loads(draft.segments_json or "[]")


def _media(draft: Draft) -> list[str]:
    return json.loads(draft.media_paths_json or "[]")


# --- 下書き生成 -------------------------------------------------------------

def create_post_draft(
    session: Session,
    formatter: Formatter,
    source_text: str,
    style_guide: str | None = None,
    allow_long: bool = False,
    media_paths: list[str] | None = None,
    emulate_handle: str | None = None,
) -> Draft:
    """テキストを整形して未承認の下書きを作る。

    常時適用は手入力のスタイルガイド(active_style_guide)のみ。学習データは自動注入しない。
    emulate_handle を指定したときだけ、そのアカウントの特徴・代表投稿を整形に乗せる。
    """
    sg = style_guide if style_guide is not None else active_style_guide(session)
    emulate_text, emulate_examples = _emulate_inputs(session, emulate_handle)
    res = formatter.format_post(
        source_text, sg, allow_long=allow_long,
        emulate_profile_text=emulate_text, emulate_examples=emulate_examples,
    )
    draft = Draft(
        kind=DraftKind.POST,
        status=DraftStatus.DRAFT,
        source_text=source_text,
        segments_json=json.dumps(res.segments, ensure_ascii=False),
        media_paths_json=json.dumps(media_paths or [], ensure_ascii=False),
    )
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft


def create_post_variations(
    session: Session,
    formatter: Formatter,
    source_text: str,
    n: int = 3,
    style_guide: str | None = None,
    allow_long: bool = False,
    emulate_handle: str | None = None,
) -> list[Draft]:
    """1つのメモから言い回し違いのn案を未承認下書きとして作る。"""
    sg = style_guide if style_guide is not None else active_style_guide(session)
    emulate_text, emulate_examples = _emulate_inputs(session, emulate_handle)
    results = formatter.format_variations(
        source_text, n=n, style_guide=sg, allow_long=allow_long,
        emulate_profile_text=emulate_text, emulate_examples=emulate_examples,
    )
    drafts: list[Draft] = []
    for res in results:
        d = Draft(
            kind=DraftKind.POST,
            status=DraftStatus.DRAFT,
            source_text=source_text,
            segments_json=json.dumps(res.segments, ensure_ascii=False),
        )
        session.add(d)
        drafts.append(d)
    session.commit()
    for d in drafts:
        session.refresh(d)
    return drafts


def create_reply_draft(
    session: Session,
    formatter: Formatter,
    target_tweet_id: str,
    target_text: str,
    target_handle: str | None = None,
) -> Draft:
    res = formatter.generate_reply(
        target_text, target_handle or "", active_style_guide(session)
    )
    draft = Draft(
        kind=DraftKind.REPLY,
        status=DraftStatus.DRAFT,
        source_text=target_text,
        segments_json=json.dumps(res.segments, ensure_ascii=False),
        target_tweet_id=target_tweet_id,
        target_handle=target_handle,
    )
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft


def create_quote_draft(
    session: Session,
    formatter: Formatter,
    target_tweet_id: str,
    target_text: str,
    target_handle: str | None = None,
) -> Draft:
    res = formatter.generate_quote(
        target_text, target_handle or "", active_style_guide(session)
    )
    draft = Draft(
        kind=DraftKind.QUOTE,
        status=DraftStatus.DRAFT,
        source_text=target_text,
        segments_json=json.dumps(res.segments, ensure_ascii=False),
        target_tweet_id=target_tweet_id,
        target_handle=target_handle,
    )
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft


# --- 取得・状態遷移 ---------------------------------------------------------

def get_draft(session: Session, draft_id: int) -> Draft | None:
    return session.get(Draft, draft_id)


def list_drafts(
    session: Session,
    status: DraftStatus | None = None,
    kind: DraftKind | None = None,
) -> list[Draft]:
    stmt = select(Draft)
    if status is not None:
        stmt = stmt.where(Draft.status == status)
    if kind is not None:
        stmt = stmt.where(Draft.kind == kind)
    stmt = stmt.order_by(Draft.created_at.desc())
    return list(session.exec(stmt).all())


def _set_status(session: Session, draft: Draft, new: DraftStatus) -> Draft:
    if not can_transition(draft.status, new):
        raise PolicyViolation(
            f"不正な状態遷移: {draft.status.value} -> {new.value}"
        )
    draft.status = new
    draft.updated_at = _utcnow()
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft


def update_segments(session: Session, draft: Draft, segments: list[str]) -> Draft:
    """承認前に本文を手修正する。"""
    draft.segments_json = json.dumps(segments, ensure_ascii=False)
    draft.updated_at = _utcnow()
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft


def approve_draft(session: Session, draft: Draft) -> Draft:
    return _set_status(session, draft, DraftStatus.APPROVED)


def reject_draft(session: Session, draft: Draft) -> Draft:
    return _set_status(session, draft, DraftStatus.REJECTED)


def queue_draft(
    session: Session, draft: Draft, scheduled_at: datetime | None = None
) -> Draft:
    if scheduled_at is not None:
        draft.scheduled_at = to_naive_utc(scheduled_at)
    return _set_status(session, draft, DraftStatus.QUEUED)


# --- 投稿 -------------------------------------------------------------------

def recent_posted_times(session: Session) -> list[datetime]:
    stmt = select(Draft.posted_at).where(Draft.posted_at != None)  # noqa: E711
    return [t for t in session.exec(stmt).all() if t is not None]


def _rate_limiter(settings: Settings) -> RateLimiter:
    return RateLimiter(
        RateLimitConfig(
            max_per_day=settings.max_posts_per_day,
            hard_cap_per_day=settings.hard_cap_posts_per_day,
            min_interval_seconds=settings.min_post_interval_seconds,
        )
    )


def post_draft(
    session: Session,
    x_client: XClient,
    draft: Draft,
    settings: Settings | None = None,
    now: datetime | None = None,
    trigger: PostTrigger = PostTrigger.MANUAL,
) -> list[str]:
    """下書きを実際にXへ投稿する。ガード(緊急停止/認証・予約/頻度)を必ず通す。

    trigger=MANUAL(既定): 人の明示操作。MANUAL は承認済み/キュー済みのみ可。
    trigger=SCHEDULED: スケジューラ発火。予約時刻のある到来済みキューのみ可。
    """
    settings = settings or get_settings()
    now = now or _utcnow()

    if not settings.posting_enabled:
        raise PolicyViolation("投稿は停止中です(posting_enabled=False)。")

    # 認証(人の明示操作) か 予約(到来済みscheduled_at) でなければ投稿しない
    ensure_post_authorized(draft.status, draft.scheduled_at, trigger, now)

    decision = _rate_limiter(settings).check(recent_posted_times(session), now)
    if not decision.allowed:
        raise PolicyViolation(decision.reason)

    segments = _segments(draft)
    if not segments:
        raise PolicyViolation("本文が空の下書きは投稿できません。")

    media_ids: list[str] | None = None
    media_paths = _media(draft)
    if media_paths:
        media_ids = [x_client.upload_media(p) for p in media_paths]

    if draft.kind == DraftKind.REPLY:
        ids = [
            x_client.post(
                segments[0], in_reply_to_tweet_id=draft.target_tweet_id, media_ids=media_ids
            )
        ]
    elif draft.kind == DraftKind.QUOTE:
        ids = [
            x_client.post(
                segments[0], quote_tweet_id=draft.target_tweet_id, media_ids=media_ids
            )
        ]
    else:  # POST(スレッド対応)
        ids = x_client.post_thread(segments, media_ids_first=media_ids)

    log_cost(session, CostKind.WRITE, units=len(ids), note=f"draft#{draft.id}")

    draft.posted_at = now
    draft.posted_tweet_id = ids[0]
    draft.status = DraftStatus.POSTED
    draft.updated_at = now
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return ids
