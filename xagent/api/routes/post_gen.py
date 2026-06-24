"""Post-gen: オリジナル投稿の叩き台の生成(A機能)。

フォロー転換の受け皿=オリジナル発信を補うため、設定したテーマ角度から型に沿った
投稿の叩き台を生成して下書きにする。生成は下書きまで(投稿は人間承認必須)。
自動生成は auto_post_gen_enabled トグル(既定OFF・乱造ガード)。手動「今すぐ生成」はここ。
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends
from sqlmodel import Session

from ... import post_gen as post_gen_mod
from ...formatter import Formatter
from ...models import PostSettings
from ..deps import db_session, get_formatter, get_session_factory, require_api_token
from ..jobs import start_job
from ..schemas import PostSettingsRequest

router = APIRouter(prefix="/post-gen", tags=["post-gen"], dependencies=[Depends(require_api_token)])


@router.post("/run-once")
def run_once(
    limit: int | None = None,
    formatter: Formatter = Depends(get_formatter),
    session_factory: Callable[[], Session] = Depends(get_session_factory),
) -> dict:
    """オリジナル投稿の叩き台生成を1回実行(下書きのみ・投稿しない)。

    AIのバッチ生成に数分かかるため job_id を即返し、フロントが /jobs/{id} をポーリングする
    (ニュース/絡みの run-once と同じ方式)。手動実行は乱造ガードを通らない。
    """

    def _run(set_progress: Callable[[str], None]) -> dict:
        with session_factory() as session:
            return post_gen_mod.run_post_gen_once(
                session, formatter, max_posts=limit, progress=set_progress
            )

    return {"job_id": start_job(_run, label="post-gen-run-once")}


@router.get("/settings", response_model=PostSettings)
def get_settings(session: Session = Depends(db_session)) -> PostSettings:
    return post_gen_mod.get_post_settings(session)


@router.put("/settings", response_model=PostSettings)
def put_settings(
    req: PostSettingsRequest, session: Session = Depends(db_session)
) -> PostSettings:
    values = {k: v for k, v in req.model_dump().items() if v is not None}
    return post_gen_mod.set_post_settings(session, **values)
