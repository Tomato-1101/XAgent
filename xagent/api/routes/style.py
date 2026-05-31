"""Style: スタイルガイドの取得/更新 と 過去投稿の学習。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from ... import style as style_mod
from ...x_client import XClient
from ..deps import db_session, get_x_client, require_api_token
from ..schemas import StyleRequest

router = APIRouter(prefix="/style", tags=["style"], dependencies=[Depends(require_api_token)])


@router.get("")
def get_style(session: Session = Depends(db_session)) -> dict:
    return {
        "guide_text": style_mod.active_style_guide(session),
        "examples": style_mod.example_texts(session),
    }


@router.put("")
def put_style(req: StyleRequest, session: Session = Depends(db_session)) -> dict:
    row = style_mod.set_style_guide(session, req.guide_text)
    return {"guide_text": row.guide_text}


@router.post("/learn")
def learn(
    session: Session = Depends(db_session),
    x_client: XClient = Depends(get_x_client),
) -> dict:
    """自分の過去投稿をAPIで取得して学習データに保存する。"""
    me = x_client.get_me()
    saved = style_mod.learn_past_posts(session, x_client, me["id"])
    return {"saved": saved, "me": me}
