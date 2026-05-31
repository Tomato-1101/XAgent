"""Monitor: 受信監視を手動トリガする(返信案・絡み案を生成)。

常駐デーモンでも回すが、UIから任意に1サイクル実行できるようにする。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from ... import monitor as monitor_mod
from ...formatter import Formatter
from ...models import MonitorSettings
from ...x_client import XClient
from ..deps import db_session, get_formatter, get_x_client, require_api_token
from ..schemas import MonitorSettingsRequest

router = APIRouter(prefix="/monitor", tags=["monitor"], dependencies=[Depends(require_api_token)])


@router.post("/run-once")
def run_once(
    session: Session = Depends(db_session),
    x_client: XClient = Depends(get_x_client),
    formatter: Formatter = Depends(get_formatter),
) -> dict:
    me = x_client.get_me()
    return monitor_mod.run_once(session, x_client, formatter, me["id"])


@router.get("/settings", response_model=MonitorSettings)
def get_settings(session: Session = Depends(db_session)) -> MonitorSettings:
    return monitor_mod.get_monitor_settings(session)


@router.put("/settings", response_model=MonitorSettings)
def put_settings(
    req: MonitorSettingsRequest, session: Session = Depends(db_session)
) -> MonitorSettings:
    flags = {k: v for k, v in req.model_dump().items() if v is not None}
    return monitor_mod.set_monitor_settings(session, **flags)
