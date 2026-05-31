"""Profiles: アカウント・プロファイルの学習と一覧。

任意ユーザー(自分/他人)の投稿を取得し、AIで特徴を抽出して保存する。
整形時に「真似る相手」として選べる(常時適用はスタイルガイドのみ)。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ... import profiles as profiles_mod
from ...formatter import Formatter
from ...models import AccountProfile
from ...x_client import XClient
from ..deps import db_session, get_formatter, get_x_client, require_api_token
from ..schemas import ProfileLearnRequest

router = APIRouter(prefix="/profiles", tags=["profiles"], dependencies=[Depends(require_api_token)])


@router.get("", response_model=list[AccountProfile])
def list_profiles(session: Session = Depends(db_session)) -> list[AccountProfile]:
    return profiles_mod.list_profiles(session)


@router.post("/learn", response_model=AccountProfile)
def learn(
    req: ProfileLearnRequest,
    session: Session = Depends(db_session),
    x_client: XClient = Depends(get_x_client),
    formatter: Formatter = Depends(get_formatter),
) -> AccountProfile:
    try:
        return profiles_mod.learn_account(
            session,
            x_client,
            formatter.complete,
            req.handle,
            max_total=req.max_total,
            is_self=req.is_self,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
