"""Posts: 自分の直近投稿の取得/表示と、その通常リポスト(コメント無し)。

- GET /posts/recent: DBにキャッシュ済みの自分の直近投稿を返す。
- POST /posts/refresh: X API から自分の投稿を取得し PastPost(is_own) に upsert する。
- POST /posts/{tweet_id}/repost: 通常リポストの下書きを作り、即時 or 指定時刻に発火する。

リポストは「自分の過去投稿の再拡散」用途。他人への引用RT(コメント付き)は compose 側の別経路。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ... import scheduler, service
from ...cost import log_cost
from ...guards import BlackoutViolation, PolicyViolation
from ...models import CostKind, PastPost
from ...x_client import XClient, XClientError
from ..deps import db_session, get_x_client, require_api_token
from ..schemas import DraftRead, RecentPost, RepostRequest, draft_to_read

router = APIRouter(prefix="/posts", tags=["posts"], dependencies=[Depends(require_api_token)])


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _tweet_url(handle: str | None, tweet_id: str) -> str:
    return (
        f"https://x.com/{handle}/status/{tweet_id}"
        if handle
        else f"https://x.com/i/web/status/{tweet_id}"
    )


def _recent_from_db(session: Session, days: int) -> list[RecentPost]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    rows = session.exec(
        select(PastPost)
        .where(PastPost.is_own == True)  # noqa: E712
        .where(PastPost.created_at != None)  # noqa: E711
        .order_by(PastPost.created_at.desc())
    ).all()
    out: list[RecentPost] = []
    for r in rows:
        if r.created_at is None or r.created_at < cutoff:
            continue
        out.append(
            RecentPost(
                tweet_id=r.tweet_id,
                text=r.text,
                created_at=r.created_at,
                like_count=r.like_count,
                retweet_count=r.retweet_count,
                url=_tweet_url(r.author_handle, r.tweet_id),
            )
        )
    return out


@router.get("/recent", response_model=list[RecentPost])
def recent(
    days: int = Query(7, ge=1, le=30),
    session: Session = Depends(db_session),
) -> list[RecentPost]:
    """DBにキャッシュ済みの自分の直近投稿(既定7日)を返す。取得は /posts/refresh。"""
    return _recent_from_db(session, days)


@router.post("/refresh", response_model=list[RecentPost])
def refresh(
    days: int = Query(7, ge=1, le=30),
    max_total: int = Query(100, ge=1, le=400),
    session: Session = Depends(db_session),
    x_client: XClient = Depends(get_x_client),
) -> list[RecentPost]:
    """X API から自分の投稿を取得し PastPost(is_own) に upsert してから直近を返す。"""
    try:
        me = x_client.get_me()
        tweets = x_client.get_user_tweets_full(me["id"], max_total=max_total)
    except XClientError as e:
        raise HTTPException(503, f"X API に接続できません: {e}")

    log_cost(session, CostKind.READ, units=len(tweets), note="posts:refresh")
    username = me.get("username")
    for t in tweets:
        tid = t["id"]
        row = session.exec(select(PastPost).where(PastPost.tweet_id == tid)).first()
        created = _to_naive_utc(t.get("created_at"))
        like = int(t.get("like_count", 0))
        rt = int(t.get("retweet_count", 0))
        if row is None:
            session.add(
                PastPost(
                    tweet_id=tid,
                    text=t.get("text", ""),
                    created_at=created,
                    like_count=like,
                    retweet_count=rt,
                    author_user_id=me["id"],
                    author_handle=username,
                    is_own=True,
                )
            )
        else:
            # メトリクスは時間で増えるので更新。所有情報も補完する。
            row.like_count = like
            row.retweet_count = rt
            row.is_own = True
            row.author_user_id = me["id"]
            if username:
                row.author_handle = username
            if created and row.created_at is None:
                row.created_at = created
            session.add(row)
    session.commit()
    return _recent_from_db(session, days)


@router.post("/{tweet_id}/repost", response_model=DraftRead)
def repost(
    tweet_id: str,
    req: RepostRequest,
    session: Session = Depends(db_session),
    x_client: XClient = Depends(get_x_client),
) -> DraftRead:
    """自分の投稿を通常リポストする。mode=now は即時、time は scheduled_at に予約。

    制限時間帯は override=True(二段階確認済み)が必要。制限帯ブロックは 423 を返す。
    """
    d = service.create_repost_draft(session, tweet_id, target_text=req.text)
    service.approve_draft(session, d)
    try:
        if req.mode == "time" and req.scheduled_at is not None:
            service.queue_draft(
                session, d, scheduled_at=req.scheduled_at, blackout_override=req.override
            )
        else:
            service.post_draft(session, x_client, d, override=req.override)
    except BlackoutViolation as e:
        raise HTTPException(423, str(e))
    except PolicyViolation as e:
        raise HTTPException(409, str(e))
    return draft_to_read(d)
