"""FastAPI バックエンド。Web UIダッシュボードのAPI層。

コアライブラリ(xagent)を各ルータから呼ぶ。フロント(Vite, :5173)からのCORSを許可。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import get_settings
from ..db import init_db
from ..media import media_dir
from ..x_client import XClient, XClientError
from .deps import get_x_client
from .routes import (
    analytics,
    compose,
    drafts,
    media,
    monitor,
    posts,
    profiles,
    schedule,
    style,
    targets,
)

log = logging.getLogger("xagent.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings = get_settings()
    sched = None
    if settings.scheduler_enabled:
        # 予約投稿/リポストを自動発火させる常駐スケジューラ(別デーモン不要)。
        # queue_tick が posting_enabled/認証・予約/制限帯/頻度ガードを通すので誤爆しない。
        from apscheduler.schedulers.background import BackgroundScheduler

        from ..daemon import queue_tick

        sched = BackgroundScheduler(timezone="UTC")
        sched.add_job(
            queue_tick, "interval",
            seconds=settings.scheduler_interval_seconds, id="queue",
        )
        sched.start()
        log.info("予約キューの常駐スケジューラを起動 (interval=%ss)", settings.scheduler_interval_seconds)
    try:
        yield
    finally:
        if sched is not None:
            sched.shutdown(wait=False)


app = FastAPI(title="XAgent", version=__version__, lifespan=lifespan)

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


@app.get("/me")
def me(x_client: XClient = Depends(get_x_client)) -> dict:
    """現在の投稿先アカウント。UIの確認ダイアログ表示・投稿URL生成に使う。"""
    try:
        return x_client.get_me()
    except XClientError:
        return {"id": None, "username": None}


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

# 添付画像/動画のプレビュー配信(ローカル)。アップロードAPIは /media/upload、配信は /media/files。
app.mount("/media/files", StaticFiles(directory=media_dir()), name="media-files")
