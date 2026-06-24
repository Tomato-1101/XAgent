"""Metrics: 自分の投稿メトリクスの取得・集計(B機能)。

公式APIで自分の直近投稿を non_public_metrics(インプレッション)付きで取得し PostMetric に
保存する。読み取りのみ(投稿しない)。自動取得は metrics_enabled(既定ON・6時間毎)。
改善を数字で追えるよう、種別(post/reply/quote)別の平均インプ/いいね/RT/ブックマークを集計して返す。
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends
from sqlmodel import Session

from ... import metrics as metrics_mod
from ...models import MetricsSettings
from ..deps import db_session, get_session_factory, require_api_token
from ..jobs import start_job
from ..schemas import MetricsSettingsRequest

router = APIRouter(prefix="/metrics", tags=["metrics"], dependencies=[Depends(require_api_token)])


@router.post("/run-once")
def run_once(
    session_factory: Callable[[], Session] = Depends(get_session_factory),
) -> dict:
    """自分の投稿メトリクスの取得を1回実行(読み取りのみ・投稿しない)。

    取得に十数秒〜かかるため job_id を即返し、フロントが /jobs/{id} をポーリングする。
    """

    def _run(set_progress: Callable[[str], None]) -> dict:
        from ...x_client import XClient

        with session_factory() as session:
            x = XClient.from_settings()
            return metrics_mod.collect_metrics_once(session, x, progress=set_progress)

    return {"job_id": start_job(_run, label="metrics-run-once")}


@router.get("/summary")
def summary(session: Session = Depends(db_session)) -> dict:
    """保存済みメトリクスを種別別に集計して返す(Analytics画面の表示用)。"""
    return metrics_mod.metrics_summary(session)


@router.get("/settings", response_model=MetricsSettings)
def get_settings(session: Session = Depends(db_session)) -> MetricsSettings:
    return metrics_mod.get_metrics_settings(session)


@router.put("/settings", response_model=MetricsSettings)
def put_settings(
    req: MetricsSettingsRequest, session: Session = Depends(db_session)
) -> MetricsSettings:
    values = {k: v for k, v in req.model_dump().items() if v is not None}
    return metrics_mod.set_metrics_settings(session, **values)
