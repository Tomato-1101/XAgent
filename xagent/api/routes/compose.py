"""Compose: 構造プレビュー(LLM不使用) と 整形して下書き作成(LLM使用)。"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ... import service
from ...commands import extract_tweet_ref, parse_command
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
from ..deps import (
    db_session,
    get_formatter,
    get_session_factory,
    get_x_client_optional,
    require_api_token,
)
from ..jobs import start_job
from ..schemas import (
    CommandRequest,
    ComposeRequest,
    DraftRead,
    InterpretRequest,
    InterpretResponse,
    QuoteFromUrlRequest,
    draft_to_read,
)


def _fetch_target(x_client: XClient | None, tweet_id: str | None):
    """対象ツイートの本文・ハンドル・投稿時刻を best-effort で取得。取れなければ ("", None, None)。"""
    if not (x_client and tweet_id):
        return "", None, None
    try:
        t = x_client.get_tweet(tweet_id)
    except Exception:
        return "", None, None
    if not t:
        return "", None, None
    return t.get("text", ""), t.get("author_handle"), t.get("created_at")

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
            template_id=req.template_id,
            auto_template=req.auto_template,
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
                    template_id=req.template_id,
                    auto_template=req.auto_template,
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
                template_id=req.template_id,
                auto_template=req.auto_template,
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
            target_text, target_handle, target_created_at = _fetch_target(
                x_client, req.target_tweet_id
            )
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
                target_created_at=target_created_at,
                raw=req.raw,
                style_guide=req.style_guide,
                allow_long=req.allow_long,
                emulate_handle=req.emulate_handle,
                media_paths=req.media_paths,
                template_id=req.template_id,
                auto_template=req.auto_template,
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
                template_id=req.template_id,
                auto_template=req.auto_template,
            )
    except PolicyViolation as e:
        raise HTTPException(400, str(e))
    return draft_to_read(draft)


@router.post("/quote-from-url")
def quote_from_url(
    req: QuoteFromUrlRequest,
    formatter: Formatter = Depends(get_formatter),
    x_client: XClient | None = Depends(get_x_client_optional),
    session_factory: Callable[[], Session] = Depends(get_session_factory),
) -> dict:
    """ツイートURLから引用案(引用RT)をAIに生成させる(Inboxの手動ボタン)。

    監視の自動生成と同じ create_quote_draft を使い、相手投稿本文からコメントをAI生成する
    (自分のコメントを添える引用は Compose の指令フローを使う)。
    AI生成は数十秒〜数分かかるため同期では返さず、job_id を即返してフロントが
    /jobs/{id} をポーリングする(ブラウザfetchの約300秒上限とCLIタイムアウト対策)。
    """
    ref = extract_tweet_ref(req.url)
    if ref is None:
        raise HTTPException(400, "ツイートURL(x.com/.../status/<id>)を入力してください。")
    tweet_id, handle, _url = ref

    def _generate() -> dict:
        target_text, target_handle, target_created_at = _fetch_target(x_client, tweet_id)
        with session_factory() as session:
            draft = service.create_quote_draft(
                session,
                formatter,
                tweet_id,
                target_text,
                target_handle=handle or target_handle,
                target_created_at=target_created_at,
            )
            return draft_to_read(draft).model_dump(mode="json")

    return {"job_id": start_job(_generate)}
