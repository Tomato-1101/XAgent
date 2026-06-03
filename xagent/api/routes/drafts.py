"""Drafts: 下書き/予約の一覧・編集・承認・却下・キュー・投稿。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ... import scheduler, service
from ...guards import BlackoutViolation, PolicyViolation
from ...models import DraftKind, DraftStatus
from ...x_client import XClient
from ..deps import db_session, get_x_client, require_api_token
from ..schemas import (
    DraftRead,
    PostNowRequest,
    QueueRequest,
    UpdateDraftRequest,
    draft_to_read,
)

router = APIRouter(prefix="/drafts", tags=["drafts"], dependencies=[Depends(require_api_token)])


def _load(session: Session, draft_id: int):
    d = service.get_draft(session, draft_id)
    if d is None:
        raise HTTPException(404, "下書きが見つかりません。")
    return d


@router.get("", response_model=list[DraftRead])
def list_drafts(
    status: DraftStatus | None = Query(None),
    kind: DraftKind | None = Query(None),
    session: Session = Depends(db_session),
) -> list[DraftRead]:
    return [draft_to_read(d) for d in service.list_drafts(session, status=status, kind=kind)]


@router.post("/reconcile-schedules")
def reconcile_schedules(session: Session = Depends(db_session)) -> dict:
    """発火できなかった予約(PCオフ等)を失効させ承認済みへ戻す。失効したdraft idを返す。"""
    return {"missed": service.reconcile_missed_schedules(session)}


@router.get("/{draft_id}", response_model=DraftRead)
def get_draft(draft_id: int, session: Session = Depends(db_session)) -> DraftRead:
    return draft_to_read(_load(session, draft_id))


@router.patch("/{draft_id}", response_model=DraftRead)
def update_draft(
    draft_id: int, req: UpdateDraftRequest, session: Session = Depends(db_session)
) -> DraftRead:
    d = _load(session, draft_id)
    if req.segments is not None:
        service.update_segments(session, d, req.segments)
    if req.scheduled_at is not None:
        d.scheduled_at = service.to_naive_utc(req.scheduled_at)
        session.add(d)
        session.commit()
        session.refresh(d)
    return draft_to_read(d)


@router.post("/{draft_id}/approve", response_model=DraftRead)
def approve(draft_id: int, session: Session = Depends(db_session)) -> DraftRead:
    d = _load(session, draft_id)
    try:
        service.approve_draft(session, d)
    except PolicyViolation as e:
        raise HTTPException(409, str(e))
    return draft_to_read(d)


@router.post("/{draft_id}/reject", response_model=DraftRead)
def reject(draft_id: int, session: Session = Depends(db_session)) -> DraftRead:
    d = _load(session, draft_id)
    try:
        service.reject_draft(session, d)
    except PolicyViolation as e:
        raise HTTPException(409, str(e))
    return draft_to_read(d)


@router.post("/{draft_id}/cancel", response_model=DraftRead)
def cancel(draft_id: int, session: Session = Depends(db_session)) -> DraftRead:
    """取消(ゴミ箱へ)。予約済みは自動投稿もキャンセルされる。投稿済みは不可。"""
    d = _load(session, draft_id)
    try:
        service.cancel_draft(session, d)
    except PolicyViolation as e:
        raise HTTPException(409, str(e))
    return draft_to_read(d)


@router.post("/{draft_id}/restore", response_model=DraftRead)
def restore(draft_id: int, session: Session = Depends(db_session)) -> DraftRead:
    """取消(ゴミ箱)から下書きへ復元する。"""
    d = _load(session, draft_id)
    try:
        service.restore_draft(session, d)
    except PolicyViolation as e:
        raise HTTPException(409, str(e))
    return draft_to_read(d)


@router.post("/{draft_id}/queue", response_model=DraftRead)
def queue(
    draft_id: int, req: QueueRequest, session: Session = Depends(db_session)
) -> DraftRead:
    """承認済み下書きをキューへ。mode=optimalで最適時間自動割当、timeで指定時刻。

    制限時間帯への予約は override=True(二段階確認済み)のときだけ発火時に許可される。
    """
    d = _load(session, draft_id)
    try:
        if req.mode == "time" and req.scheduled_at is not None:
            service.queue_draft(
                session, d, scheduled_at=req.scheduled_at, blackout_override=req.override
            )
        else:
            scheduler.schedule_optimal(session, d)
    except PolicyViolation as e:
        raise HTTPException(409, str(e))
    return draft_to_read(d)


@router.post("/{draft_id}/post", response_model=DraftRead)
def post_now(
    draft_id: int,
    req: PostNowRequest = PostNowRequest(),
    session: Session = Depends(db_session),
    x_client: XClient = Depends(get_x_client),
) -> DraftRead:
    """即時投稿(承認/制限帯/頻度ガードを通過した場合のみ)。

    制限時間帯のときは override=True(二段階確認済み)が必要。制限帯ブロックは 423 を返す。
    """
    d = _load(session, draft_id)
    try:
        service.post_draft(session, x_client, d, override=req.override)
    except BlackoutViolation as e:
        raise HTTPException(423, str(e))  # Locked: 制限時間帯。UIは二段階確認へ誘導する。
    except PolicyViolation as e:
        raise HTTPException(409, str(e))
    return draft_to_read(d)
