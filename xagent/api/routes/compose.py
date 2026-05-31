"""Compose: 構造プレビュー(LLM不使用) と 整形して下書き作成(LLM使用)。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from ... import service
from ...formatter import Formatter
from ...text import POST_LIMIT_WEIGHTED, exceeds_fold, split_into_thread, weighted_length
from ..deps import db_session, get_formatter, require_api_token
from ..schemas import ComposeRequest, DraftRead, draft_to_read

router = APIRouter(prefix="/compose", tags=["compose"], dependencies=[Depends(require_api_token)])


class PreviewRequest(BaseModel):
    text: str
    allow_long: bool = False


class PreviewSegment(BaseModel):
    text: str
    weighted_length: int


class PreviewResponse(BaseModel):
    weighted_length: int
    folded: bool
    over_limit: bool
    segments: list[PreviewSegment]


@router.post("/preview", response_model=PreviewResponse)
def preview(req: PreviewRequest) -> PreviewResponse:
    wl = weighted_length(req.text)
    seg_texts = (
        [req.text.strip()] if req.allow_long and req.text.strip() else split_into_thread(req.text)
    )
    return PreviewResponse(
        weighted_length=wl,
        folded=exceeds_fold(req.text),
        over_limit=wl > POST_LIMIT_WEIGHTED,
        segments=[PreviewSegment(text=s, weighted_length=weighted_length(s)) for s in seg_texts],
    )


@router.post("", response_model=DraftRead)
def compose(
    req: ComposeRequest,
    session: Session = Depends(db_session),
    formatter: Formatter = Depends(get_formatter),
) -> DraftRead:
    """テキストをClaudeで整形し、未承認の下書きを作成する。"""
    draft = service.create_post_draft(
        session,
        formatter,
        req.text,
        style_guide=req.style_guide,
        allow_long=req.allow_long,
        media_paths=req.media_paths,
        emulate_handle=req.emulate_handle,
    )
    return draft_to_read(draft)


@router.post("/variations", response_model=list[DraftRead])
def compose_variations(
    req: ComposeRequest,
    session: Session = Depends(db_session),
    formatter: Formatter = Depends(get_formatter),
) -> list[DraftRead]:
    """1つのメモから言い回し違いのN案を未承認下書きとして作る。"""
    drafts = service.create_post_variations(
        session,
        formatter,
        req.text,
        n=max(1, req.n_variations),
        style_guide=req.style_guide,
        allow_long=req.allow_long,
        emulate_handle=req.emulate_handle,
    )
    return [draft_to_read(d) for d in drafts]
