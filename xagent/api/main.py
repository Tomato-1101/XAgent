"""FastAPI バックエンド。Web UIダッシュボードのAPI層。

コアライブラリ(xagent)を各ルータから呼ぶ。フロント(Vite, :5173)からのCORSを許可。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session
from starlette.middleware.base import BaseHTTPMiddleware

from .. import __version__
from ..config import get_settings
from ..db import init_db
from ..media import media_dir
from ..x_client import XClient, XClientError
from .deps import db_session, get_x_client, require_api_token
from .jobs import router as jobs_router
from .routes import (
    analytics,
    compose,
    drafts,
    lists,
    media,
    monitor,
    news,
    posts,
    profiles,
    schedule,
    style,
    targets,
    templates,
)

log = logging.getLogger("xagent.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings = get_settings()
    sched = None
    if settings.scheduler_enabled:
        # 別デーモン不要の常駐スケジューラ(APIプロセスが launchd 常駐)。
        # 予約投稿の発火(queue)は常時。絡み案の自動生成(monitor)はジョブ登録するが、実処理の
        # 可否は MonitorSettings.auto_monitor_enabled トグルで内部制御する(既定OFF=即returnし
        # API消費なし)。ユーザーが UI でオンにした時だけ自動スキャンが走る。生成は下書きのみで
        # 自動投稿はしない。
        from apscheduler.schedulers.background import BackgroundScheduler

        from ..daemon import monitor_tick, news_tick, queue_tick

        sched = BackgroundScheduler(timezone="UTC")
        # 予約投稿の発火: 常時。posting_enabled/認証・予約/制限帯/頻度ガードを通すので誤爆しない。
        # 起動直後に1回発火させ、ハートビート(/status)を即座に立てる(再起動直後の誤「停止」表示を防ぐ)。
        sched.add_job(
            queue_tick, "interval",
            seconds=settings.scheduler_interval_seconds, id="queue",
            next_run_time=datetime.now(timezone.utc),
        )
        # 絡み案の自動生成: auto_monitor_enabled(既定OFF)で内部制御。OFFなら即returnしAPI消費なし。
        sched.add_job(
            monitor_tick, "interval",
            seconds=settings.monitor_interval_seconds, id="monitor",
            max_instances=1, coalesce=True,
        )
        # ニュース速報の自動生成(ダイジェスト連動): auto_news_enabled(既定OFF)で内部制御。
        # ONでも新着ダイジェストが無ければ即return(XNewsBotのDBを読むだけでAPI消費なし)。
        sched.add_job(
            news_tick, "interval",
            seconds=settings.news_interval_seconds, id="news",
            max_instances=1, coalesce=True,
        )
        sched.start()
        log.info(
            "常駐スケジューラを起動 (queue=%ss, monitor=%ss・auto_monitor_enabledで制御)",
            settings.scheduler_interval_seconds, settings.monitor_interval_seconds,
        )
    app.state.scheduler = sched
    try:
        yield
    finally:
        if sched is not None:
            sched.shutdown(wait=False)


app = FastAPI(title="XAgent", version=__version__, lifespan=lifespan)


async def _json_error_middleware(request: Request, call_next):
    """未捕捉例外を CORS の内側で JSON 500 に変換する。

    Starlette 既定では最外層の ServerErrorMiddleware がプレーン500を返し、CORSMiddleware を
    通らないためクロスオリジン(vite dev :5180)では一律「TypeError: Failed to fetch」になる。
    ここで先に JSON 化すると CORS ヘッダ付き 500 になり、フロントに日本語の detail が届く。
    """
    try:
        return await call_next(request)
    except Exception as e:
        log.exception("未捕捉例外: %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": str(e) or "サーバ内部エラー"})


# add_middleware は後に追加した方が外側になる。JSON化(内側)→ CORS(外側)の順に並べる。
app.add_middleware(BaseHTTPMiddleware, dispatch=_json_error_middleware)
app.add_middleware(
    CORSMiddleware,
    # ローカル開発用: localhost/127.0.0.1 の任意ポートを許可(ポート衝突でずれても動くように)
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/me", dependencies=[Depends(require_api_token)])
def me(x_client: XClient = Depends(get_x_client)) -> dict:
    """現在の投稿先アカウント。UIの確認ダイアログ表示・投稿URL生成に使う。"""
    try:
        return x_client.get_me()
    except XClientError:
        return {"id": None, "username": None}


@app.get("/status", dependencies=[Depends(require_api_token)])
def status(session: Session = Depends(db_session)) -> dict:
    """予約スケジューラの稼働状況。UIが「予約投稿の常駐が動いているか」を表示するための情報。

    healthy=Trueなら予約発火ループが生きている。判定はハートビート(直近のqueue_tick発火時刻)が
    間隔の3倍以内に更新されているか(=スレッド生存)。起動直後でまだ未発火のときは稼働扱い(猶予)。
    """
    from ..daemon import last_queue_tick_at
    from ..monitor import get_monitor_settings

    settings = get_settings()
    sched = getattr(app.state, "scheduler", None)
    running = bool(sched is not None and getattr(sched, "running", False))
    interval = settings.scheduler_interval_seconds

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    last = last_queue_tick_at()
    seconds_since = (now - last).total_seconds() if last is not None else None

    next_run = None
    if sched is not None:
        job = sched.get_job("queue")
        if job is not None and job.next_run_time is not None:
            next_run = job.next_run_time.astimezone(timezone.utc).replace(tzinfo=None)

    # 生存判定: 起動直後(未発火)は猶予で稼働扱い、発火後は間隔の3倍を超える未更新を「停止/ハング」とみなす。
    fresh = seconds_since is None or seconds_since <= interval * 3
    healthy = settings.scheduler_enabled and running and fresh

    # 監視(絡み案自動生成)トグルの現値も併せて返す(UIで状態を一目で把握できるように)。
    auto_monitor = get_monitor_settings(session).auto_monitor_enabled

    return {
        "scheduler_enabled": settings.scheduler_enabled,
        "scheduler_running": running,
        "healthy": healthy,
        "last_queue_tick_at": last.isoformat() if last is not None else None,
        "seconds_since_queue_tick": seconds_since,
        "queue_interval_seconds": interval,
        "next_queue_run_at": next_run.isoformat() if next_run is not None else None,
        "posting_enabled": settings.posting_enabled,
        "auto_monitor_enabled": auto_monitor,
    }


app.include_router(compose.router)
app.include_router(drafts.router)
app.include_router(posts.router)
app.include_router(targets.router)
app.include_router(style.router)
app.include_router(monitor.router)
app.include_router(profiles.router)
app.include_router(schedule.router)
app.include_router(media.router)
app.include_router(analytics.router)
app.include_router(lists.router)
app.include_router(templates.router)
app.include_router(news.router)
app.include_router(jobs_router)

# 添付画像/動画のプレビュー配信(ローカル)。アップロードAPIは /media/upload、配信は /media/files。
# 注: <img>/<video> タグはカスタムヘッダを送れないため、この静的配信だけは X-API-Token 保護の
# 対象外(tailnet内限定の露出として許容)。
app.mount("/media/files", StaticFiles(directory=media_dir()), name="media-files")

# フロント(React)のビルド成果物を同一オリジンで配信する(スマホ等は :8000 だけ見ればよい)。
# 開発時は従来どおり vite dev(:5180) を使うのでここは本番UI専用。
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

# check_dir=False: dist 未ビルドでも起動を止めない(その間 /assets は404、ビルド後は再起動不要)。
app.mount(
    "/assets",
    StaticFiles(directory=FRONTEND_DIST / "assets", check_dir=False),
    name="spa-assets",
)


@app.get("/", include_in_schema=False)
def spa_index():
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        return JSONResponse(
            status_code=503,
            content={"detail": "frontend未ビルド: cd frontend && npm run build を実行してください。"},
        )
    # index.html はキャッシュさせない(古いUIがスマホに残るのを防ぐ。ハッシュ付き assets は既定のまま)。
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
