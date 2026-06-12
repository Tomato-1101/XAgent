"""News: ニュース速報の下書き生成(XNewsBot連携)。

XNewsBotが朝夕に収集したニュースの新着から、速報風の投稿下書きを生成する。
生成は下書きまで(投稿は人間承認必須)。自動生成は auto_news_enabled トグル(既定OFF)。
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends
from sqlmodel import Session

from ... import news as news_mod
from ...formatter import Formatter
from ...models import NewsSettings
from ..deps import db_session, get_formatter, get_session_factory, require_api_token
from ..jobs import start_job
from ..schemas import NewsSettingsRequest

router = APIRouter(prefix="/news", tags=["news"], dependencies=[Depends(require_api_token)])


@router.post("/run-once")
def run_once(
    limit: int | None = None,
    formatter: Formatter = Depends(get_formatter),
    session_factory: Callable[[], Session] = Depends(get_session_factory),
) -> dict:
    """ニュース速報の生成を1回実行(新着が無ければ何も作らない)。

    AIのバッチ選定・本文生成に数分かかるため job_id を即返し、フロントが
    /jobs/{id} をポーリングする(絡みの run-once と同じ方式)。
    """

    def _run(set_progress: Callable[[str], None]) -> dict:
        with session_factory() as session:
            return news_mod.run_news_once(
                session, formatter, max_posts=limit, progress=set_progress
            )

    return {"job_id": start_job(_run, label="news-run-once")}


@router.get("/settings", response_model=NewsSettings)
def get_settings(session: Session = Depends(db_session)) -> NewsSettings:
    return news_mod.get_news_settings(session)


@router.put("/settings", response_model=NewsSettings)
def put_settings(
    req: NewsSettingsRequest, session: Session = Depends(db_session)
) -> NewsSettings:
    values = {k: v for k, v in req.model_dump().items() if v is not None}
    return news_mod.set_news_settings(session, **values)


@router.get("/recent")
def recent(session: Session = Depends(db_session)) -> dict:
    """直近2日の対象ジャンルのニュース一覧(UI表示用)。

    XNewsBotのDBが読めない場合も500にせず available=false で返す(UIが案内を出す)。
    """
    settings = news_mod.get_news_settings(session)
    try:
        items = news_mod.recent_items(settings)
    except news_mod.NewsSourceUnavailable as e:
        return {"available": False, "error": str(e), "items": [], "last_digest_id": settings.last_digest_id}
    return {
        "available": True,
        "error": None,
        "items": items,
        "last_digest_id": settings.last_digest_id,
    }
