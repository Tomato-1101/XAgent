"""FastAPI バックエンド。Web UIダッシュボードのAPI層。

コアライブラリ(xagent)を各ルータから呼ぶ。フロント(Vite, :5173)からのCORSを許可。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__
from ..db import init_db
from ..x_client import XClient, XClientError
from .deps import get_x_client
from .routes import analytics, compose, drafts, monitor, profiles, style, targets


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


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
app.include_router(targets.router)
app.include_router(style.router)
app.include_router(monitor.router)
app.include_router(profiles.router)
app.include_router(analytics.router)
