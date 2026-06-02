"""FastAPI 依存性。DBセッション・整形器・Xクライアントを供給する。

これらは app.dependency_overrides でテスト時に差し替えられる。
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Header, HTTPException
from sqlmodel import Session

from ..config import get_settings
from ..db import get_engine
from ..formatter import Formatter
from ..x_client import XClient, XClientError


def require_api_token(x_api_token: str | None = Header(default=None)) -> None:
    """`config.api_token` を設定したときだけ X-API-Token を必須にする。

    未設定(既定)ならローカル開放のまま(挙動不変)。書き込み系ルータに適用する。
    """
    token = get_settings().api_token
    if token and x_api_token != token:
        raise HTTPException(status_code=401, detail="X-API-Token が不正です。")


def db_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


def get_formatter() -> Formatter:
    return Formatter(get_settings())


def get_x_client() -> XClient:
    try:
        return XClient.from_settings(get_settings())
    except XClientError as e:
        raise HTTPException(status_code=503, detail=str(e))


def get_x_client_optional() -> XClient | None:
    """資格情報が無くても 503 にせず None を返す Xクライアント依存。

    元ポスト本文の取得など「取れたら使う」best-effort 用途。投稿系には get_x_client を使う。
    """
    try:
        return XClient.from_settings(get_settings())
    except XClientError:
        return None
