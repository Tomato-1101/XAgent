"""Compose: 構造プレビュー(LLM不使用) と 整形して下書き作成(LLM使用)。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ... import service
from ...commands import parse_command
from ...cost import bill_formatter_usage
from ...formatter import Formatter
from ...guards import PolicyViolation
from ...text import (
    POST_LIMIT_CHARS,
    char_length,
    exceeds_fold,
    split_into_thread,
    weighted_length,
)
from ...x_client import XClient
from ..deps import db_session, get_formatter, get_x_client_optional, require_api_token
from ..schemas import (
    CommandRequest,
    ComposeRequest,
    DraftRead,
    InterpretRequest,
    InterpretResponse,
    draft_to_read,
)


def _fetch_target(x_client: XClient | None, tweet_id: str | None) -> tuple[str, str | None]:
    """対象ツイートの本文とハンドルを best-effort で取得する。取れなければ ("", None)。"""
    if not (x_client and tweet_id):
        return "", None
    try:
        t = x_client.get_tweet(tweet_id)
    except Exception:
        return "", None
    if not t:
        return "", None
    return t.get("text", ""), t.get("author_handle")

router = APIRouter(prefix="/compose", tags=["compose"], dependencies=[Depends(require_api_token)])


class PreviewRequest(BaseModel):
    text: str
    allow_long: bool = False


class PreviewSegment(BaseModel):
    text: str
    weighted_length: int
    char_length: int


class PreviewResponse(BaseModel):
    weighted_length: int
    char_length: int
    folded: bool
    over_limit: bool
    segments: list[PreviewSegment]


@router.post("/preview", response_model=PreviewResponse)
def preview(req: PreviewRequest) -> PreviewResponse:
    """字数(140字)基準で折りたたみ/分割の見通しを返す。長文許可時は分割しない。"""
    cl = char_length(req.text)
    seg_texts = (
        [req.text.strip()] if req.allow_long and req.text.strip() else split_into_thread(req.text)
    )
    return PreviewResponse(
        weighted_length=weighted_length(req.text),
        char_length=cl,
        folded=exceeds_fold(req.text),
        over_limit=cl > POST_LIMIT_CHARS,
        segments=[
            PreviewSegment(text=s, weighted_length=weighted_length(s), char_length=char_length(s))
            for s in seg_texts
        ],
    )


@router.post("", response_model=DraftRead)
def compose(
    req: ComposeRequest,
    session: Session = Depends(db_session),
    formatter: Formatter = Depends(get_formatter),
) -> DraftRead:
    """テキストをClaudeで整形し、未承認の下書きを作成する。"""
    try:
        draft = service.create_post_draft(
            session,
            formatter,
            req.text,
            style_guide=req.style_guide,
            allow_long=req.allow_long,
            media_paths=req.media_paths,
            emulate_handle=req.emulate_handle,
            raw=req.raw,
        )
    except PolicyViolation as e:
        raise HTTPException(400, str(e))
    return draft_to_read(draft)


@router.post("/variations", response_model=list[DraftRead])
def compose_variations(
    req: ComposeRequest,
    session: Session = Depends(db_session),
    formatter: Formatter = Depends(get_formatter),
) -> list[DraftRead]:
    """1つのメモから言い回し違いのN案を未承認下書きとして作る。

    raw(そのまま投稿)は言い回し違いを作れないため、1件のみ作成する。
    """
    try:
        if req.raw:
            drafts = [
                service.create_post_draft(
                    session,
                    formatter,
                    req.text,
                    style_guide=req.style_guide,
                    allow_long=req.allow_long,
                    media_paths=req.media_paths,
                    emulate_handle=req.emulate_handle,
                    raw=True,
                )
            ]
        else:
            drafts = service.create_post_variations(
                session,
                formatter,
                req.text,
                n=max(1, req.n_variations),
                style_guide=req.style_guide,
                allow_long=req.allow_long,
                emulate_handle=req.emulate_handle,
                media_paths=req.media_paths,
            )
    except PolicyViolation as e:
        raise HTTPException(400, str(e))
    return [draft_to_read(d) for d in drafts]


@router.post("/interpret", response_model=InterpretResponse)
def interpret(
    req: InterpretRequest,
    session: Session = Depends(db_session),
    formatter: Formatter = Depends(get_formatter),
) -> InterpretResponse:
    """自由文の指令(引用RT/通常投稿/そのまま投稿)を解析する。下書きは作らない。"""
    parsed = parse_command(formatter.complete, req.text)
    # 解析で使った Claude トークンをコスト記録(下書きは作らないがLLMは消費している)
    if bill_formatter_usage(session, formatter, note="interpret") is not None:
        session.commit()
    return InterpretResponse(
        action=parsed.action,
        target_url=parsed.target_url,
        target_tweet_id=parsed.target_tweet_id,
        target_handle=parsed.target_handle,
        body=parsed.body,
        raw=parsed.raw,
        note=parsed.note,
    )


@router.post("/command", response_model=DraftRead)
def command(
    req: CommandRequest,
    session: Session = Depends(db_session),
    formatter: Formatter = Depends(get_formatter),
    x_client: XClient | None = Depends(get_x_client_optional),
) -> DraftRead:
    """確認済みの指令から下書きを作る(引用RT / 返信 / 通常投稿、整形 or そのまま)。

    引用・返信では対象ツイート本文を best-effort で取得し、表示用に target_text へ保存する。
    """
    try:
        if req.action in ("quote", "reply"):
            if not req.target_tweet_id:
                raise HTTPException(400, "引用/返信には対象ツイート(URL/ID)が必要です。")
            target_text, target_handle = _fetch_target(x_client, req.target_tweet_id)
            maker = (
                service.create_quote_command_draft
                if req.action == "quote"
                else service.create_reply_command_draft
            )
            draft = maker(
                session,
                formatter,
                req.target_tweet_id,
                req.text,
                target_handle=req.target_handle or target_handle,
                target_text=target_text,
                raw=req.raw,
                style_guide=req.style_guide,
                allow_long=req.allow_long,
                emulate_handle=req.emulate_handle,
                media_paths=req.media_paths,
            )
        else:
            draft = service.create_post_draft(
                session,
                formatter,
                req.text,
                style_guide=req.style_guide,
                allow_long=req.allow_long,
                media_paths=req.media_paths,
                emulate_handle=req.emulate_handle,
                raw=req.raw,
            )
    except PolicyViolation as e:
        raise HTTPException(400, str(e))
    return draft_to_read(draft)
