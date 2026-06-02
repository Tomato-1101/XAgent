"""Schedule: おすすめ投稿時間(JST)の提示と、制限時間帯(ブラックアウト)の設定/判定。

おすすめ時間の根拠はネット調査による一般的な高エンゲージ時間帯(日本)。
制限時間帯は平日の指定帯に自分の書き込みを禁止する安全弁(UIで編集可能)。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from ... import blackout as blackout_mod, scheduler
from ...config import get_settings
from ..deps import db_session, require_api_token
from ..schemas import BlackoutRead, BlackoutStatus, BlackoutUpdateRequest

router = APIRouter(prefix="/schedule", tags=["schedule"], dependencies=[Depends(require_api_token)])


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.get("/recommended")
def recommended() -> dict:
    return scheduler.recommended_times(_now_utc())


def _blackout_to_read(row) -> BlackoutRead:
    return BlackoutRead(
        enabled=row.enabled,
        weekdays=json.loads(row.weekdays_json or "[]"),
        windows=json.loads(row.windows_json or "[]"),
        updated_at=row.updated_at,
    )


@router.get("/blackout", response_model=BlackoutRead)
def get_blackout(session: Session = Depends(db_session)) -> BlackoutRead:
    return _blackout_to_read(blackout_mod.get_blackout_settings(session))


@router.put("/blackout", response_model=BlackoutRead)
def put_blackout(
    req: BlackoutUpdateRequest, session: Session = Depends(db_session)
) -> BlackoutRead:
    row = blackout_mod.set_blackout_settings(
        session, enabled=req.enabled, weekdays=req.weekdays, windows=req.windows
    )
    return _blackout_to_read(row)


@router.get("/blackout/status", response_model=BlackoutStatus)
def blackout_status(
    at: datetime | None = Query(None, description="判定したい時刻(ISO)。未指定は現在"),
    session: Session = Depends(db_session),
) -> BlackoutStatus:
    """指定時刻(既定=現在)が制限時間帯かを返す。UIが書き込み前/予約時刻の判定に使う。"""
    settings = get_settings()
    if at is None:
        now = _now_utc()
    elif at.tzinfo is not None:
        now = at.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        now = at
    row = blackout_mod.get_blackout_settings(session)
    flag, reason = blackout_mod.is_blackout(now, row, settings.timezone)
    return BlackoutStatus(blackout=flag, reason=reason, at=now)
