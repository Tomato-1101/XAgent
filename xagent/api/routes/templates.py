"""Templates: 投稿/リプ生成に使う「型」のCRUDと既定(active)切替。

書き込みも読み取りもローカルDBのみ(X APIは触らない)。Composeで選択 or「AIに任せる」で使う。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlmodel import Session

from ... import templates as templates_mod
from ...models import TemplateKind
from ..deps import db_session, require_api_token
from ..schemas import TemplateCreate, TemplateRead, TemplateUpdate

router = APIRouter(prefix="/templates", tags=["templates"], dependencies=[Depends(require_api_token)])


@router.get("", response_model=list[TemplateRead])
def list_templates(
    kind: TemplateKind | None = Query(None),
    session: Session = Depends(db_session),
) -> list[TemplateRead]:
    return [TemplateRead.model_validate(t, from_attributes=True)
            for t in templates_mod.list_templates(session, kind)]


@router.post("", response_model=TemplateRead)
def create_template(req: TemplateCreate, session: Session = Depends(db_session)) -> TemplateRead:
    row = templates_mod.create_template(
        session, req.name, req.kind, req.body, active=req.active
    )
    return TemplateRead.model_validate(row, from_attributes=True)


@router.patch("/{template_id}", response_model=TemplateRead)
def update_template(
    template_id: int, req: TemplateUpdate, session: Session = Depends(db_session)
) -> TemplateRead:
    row = templates_mod.update_template(
        session, template_id, name=req.name, body=req.body, kind=req.kind, active=req.active
    )
    if row is None:
        raise HTTPException(404, "型が見つかりません。")
    return TemplateRead.model_validate(row, from_attributes=True)


@router.post("/{template_id}/activate", response_model=TemplateRead)
def activate_template(template_id: int, session: Session = Depends(db_session)) -> TemplateRead:
    row = templates_mod.set_active(session, template_id)
    if row is None:
        raise HTTPException(404, "型が見つかりません。")
    return TemplateRead.model_validate(row, from_attributes=True)


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: int, session: Session = Depends(db_session)) -> Response:
    if not templates_mod.delete_template(session, template_id):
        raise HTTPException(404, "型が見つかりません。")
    return Response(status_code=204)
