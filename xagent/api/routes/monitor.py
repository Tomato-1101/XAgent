"""Monitor: 受信監視を手動トリガする(返信案・絡み案を生成)。

常駐デーモンでも回すが、UIから任意に1サイクル実行できるようにする。
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends
from sqlmodel import Session

from ... import monitor as monitor_mod
from ...formatter import Formatter
from ...models import MonitorSettings
from ...x_client import XClient
from ..deps import db_session, get_formatter, get_session_factory, get_x_client, require_api_token
from ..jobs import start_job
from ..schemas import MonitorSettingsRequest

router = APIRouter(prefix="/monitor", tags=["monitor"], dependencies=[Depends(require_api_token)])


@router.post("/run-once")
def run_once(
    limit: int | None = None,
    x_client: XClient = Depends(get_x_client),
    formatter: Formatter = Depends(get_formatter),
    session_factory: Callable[[], Session] = Depends(get_session_factory),
) -> dict:
    """監視を1サイクル実行。limit を渡すとその回だけ生成数をその件数までに絞る(乱造防止)。

    候補収集+バッチAI選定で数分〜15分かかるため job_id を即返し、フロントが
    /jobs/{id} をポーリングする(同期だとブラウザfetchの約300秒上限で必ず切れる)。
    """

    def _run(set_progress: Callable[[str], None]) -> dict:
        set_progress("自分のアカウント情報を確認中")
        me = x_client.get_me()
        with session_factory() as session:
            return monitor_mod.run_once(
                session, x_client, formatter, me["id"], max_drafts=limit, progress=set_progress
            )

    return {"job_id": start_job(_run, label="monitor-run-once")}


@router.post("/celeb-run-once")
def celeb_run_once(
    limit: int | None = None,
    x_client: XClient = Depends(get_x_client),
    formatter: Formatter = Depends(get_formatter),
    session_factory: Callable[[], Session] = Depends(get_session_factory),
) -> dict:
    """有名人ウォッチを1回実行(リストの有名人のAI言及投稿に絡み案を生成)。

    検索1〜2クエリ+ヒット時のみAI生成。トグル(celeb_watch_enabled)がOFFでも手動で試せる。
    """

    def _run(set_progress: Callable[[str], None]) -> dict:
        with session_factory() as session:
            return monitor_mod.run_celeb_once(
                session, x_client, formatter, max_drafts=limit, progress=set_progress
            )

    return {"job_id": start_job(_run, label="celeb-run-once")}


@router.post("/buzz-run-once")
def buzz_run_once(
    limit: int | None = None,
    x_client: XClient = Depends(get_x_client),
    formatter: Formatter = Depends(get_formatter),
    session_factory: Callable[[], Session] = Depends(get_session_factory),
) -> dict:
    """バズウォッチを1回実行(min_faves検索で「既にバズった投稿」に絡み案を生成)。

    検索1クエリ+ヒット時のみAI生成。トグル(buzz_watch_enabled)がOFFでも手動で試せる。
    手動実行は自動tickの乱造ガード(承認待ち件数)を通らない。
    """

    def _run(set_progress: Callable[[str], None]) -> dict:
        with session_factory() as session:
            return monitor_mod.run_buzz_once(
                session, x_client, formatter, max_drafts=limit, progress=set_progress
            )

    return {"job_id": start_job(_run, label="buzz-run-once")}


@router.get("/settings", response_model=MonitorSettings)
def get_settings(session: Session = Depends(db_session)) -> MonitorSettings:
    return monitor_mod.get_monitor_settings(session)


@router.put("/settings", response_model=MonitorSettings)
def put_settings(
    req: MonitorSettingsRequest, session: Session = Depends(db_session)
) -> MonitorSettings:
    flags = {k: v for k, v in req.model_dump().items() if v is not None}
    return monitor_mod.set_monitor_settings(session, **flags)
