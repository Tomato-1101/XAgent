"""Targets: 絡む対象(有名人等)の管理。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ...models import EngageTarget
from ...x_client import XClient, XClientError
from ..deps import db_session, get_x_client, require_api_token
from ..schemas import TargetRequest

router = APIRouter(prefix="/targets", tags=["targets"], dependencies=[Depends(require_api_token)])


@router.get("", response_model=list[EngageTarget])
def list_targets(session: Session = Depends(db_session)) -> list[EngageTarget]:
    return list(session.exec(select(EngageTarget)).all())


@router.post("", response_model=EngageTarget)
def add_target(req: TargetRequest, session: Session = Depends(db_session)) -> EngageTarget:
    handle = req.handle.lstrip("@")
    user_id = None
    if req.resolve_user_id:
        # user_id解決は任意。資格情報が無い/見つからない場合はhandleだけで登録する(後で解決可)
        try:
            x = get_x_client()
            info = x.get_user_by_username(handle)
            user_id = info.get("id") if info else None
        except (XClientError, HTTPException):
            user_id = None
    target = EngageTarget(
        kind=req.kind, handle=handle, user_id=user_id, keyword=req.keyword, notes=req.notes
    )
    session.add(target)
    session.commit()
    session.refresh(target)
    return target


@router.delete("/{target_id}")
def delete_target(target_id: int, session: Session = Depends(db_session)) -> dict:
    t = session.get(EngageTarget, target_id)
    if t is None:
        raise HTTPException(404, "対象が見つかりません。")
    session.delete(t)
    session.commit()
    return {"deleted": target_id}
