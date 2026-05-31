"""Drafts: 下書き/予約の一覧・編集・承認・却下・キュー・投稿。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ... import scheduler, service
from ...guards import PolicyViolation
from ...models import DraftKind, DraftStatus
from ...x_client import XClient
from ..deps import db_session, get_x_client, require_api_token
from ..schemas import DraftRead, QueueRequest, UpdateDraftRequest, draft_to_read

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


@router.post("/{draft_id}/queue", response_model=DraftRead)
def queue(
    draft_id: int, req: QueueRequest, session: Session = Depends(db_session)
) -> DraftRead:
    """承認済み下書きをキューへ。mode=optimalで最適時間自動割当、timeで指定時刻。"""
    d = _load(session, draft_id)
    try:
        if req.mode == "time" and req.scheduled_at is not None:
            service.queue_draft(session, d, scheduled_at=req.scheduled_at)
        else:
            scheduler.schedule_optimal(session, d)
    except PolicyViolation as e:
        raise HTTPException(409, str(e))
    return draft_to_read(d)


@router.post("/{draft_id}/post", response_model=DraftRead)
def post_now(
    draft_id: int,
    session: Session = Depends(db_session),
    x_client: XClient = Depends(get_x_client),
) -> DraftRead:
    """即時投稿(承認/頻度ガードを通過した場合のみ)。"""
    d = _load(session, draft_id)
    try:
        service.post_draft(session, x_client, d)
    except PolicyViolation as e:
        raise HTTPException(409, str(e))
    return draft_to_read(d)
