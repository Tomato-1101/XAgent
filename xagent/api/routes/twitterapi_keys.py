"""twitterapi.io 読み取りキーのCRUD・並べ替え・疎通テスト。

複数キーを優先度順に登録し、読み取り時に上から試してフォールバックする(チェーン本体は
x_client._build_read_backend)。書き込みも読み取りもローカルDBのみ(疎通テストだけ twitterapi.io を叩く)。
キーは秘密情報のため一覧では末尾4文字にマスクして返す。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session

from ... import twitterapi_keys as keys_mod
from ..deps import db_session, require_api_token
from ..schemas import (
    TwitterApiKeyCreate,
    TwitterApiKeyRead,
    TwitterApiKeyReorder,
    TwitterApiKeyUpdate,
    twitterapi_key_to_read,
)

router = APIRouter(
    prefix="/twitterapi-keys",
    tags=["twitterapi-keys"],
    dependencies=[Depends(require_api_token)],
)


@router.get("", response_model=list[TwitterApiKeyRead])
def list_keys(session: Session = Depends(db_session)) -> list[TwitterApiKeyRead]:
    return [twitterapi_key_to_read(r) for r in keys_mod.list_keys(session)]


@router.post("", response_model=TwitterApiKeyRead)
def create_key(
    req: TwitterApiKeyCreate, session: Session = Depends(db_session)
) -> TwitterApiKeyRead:
    if not req.api_key.strip():
        raise HTTPException(400, "APIキーを入力してください。")
    row = keys_mod.create_key(
        session, req.api_key, label=req.label, priority=req.priority, enabled=req.enabled
    )
    return twitterapi_key_to_read(row)


@router.patch("/{key_id}", response_model=TwitterApiKeyRead)
def update_key(
    key_id: int, req: TwitterApiKeyUpdate, session: Session = Depends(db_session)
) -> TwitterApiKeyRead:
    row = keys_mod.update_key(
        session, key_id,
        label=req.label, api_key=req.api_key, priority=req.priority, enabled=req.enabled,
    )
    if row is None:
        raise HTTPException(404, "キーが見つかりません。")
    return twitterapi_key_to_read(row)


@router.delete("/{key_id}", status_code=204)
def delete_key(key_id: int, session: Session = Depends(db_session)) -> Response:
    if not keys_mod.delete_key(session, key_id):
        raise HTTPException(404, "キーが見つかりません。")
    return Response(status_code=204)


@router.post("/reorder", response_model=list[TwitterApiKeyRead])
def reorder_keys(
    req: TwitterApiKeyReorder, session: Session = Depends(db_session)
) -> list[TwitterApiKeyRead]:
    return [twitterapi_key_to_read(r) for r in keys_mod.reorder(session, req.ids)]


@router.post("/{key_id}/test", response_model=TwitterApiKeyRead)
def test_key(key_id: int, session: Session = Depends(db_session)) -> TwitterApiKeyRead:
    """キー1本で twitterapi.io に1回読み取りを投げ、成否を記録して返す(UIの「テスト」)。"""
    row = keys_mod.probe_key(session, key_id)
    if row is None:
        raise HTTPException(404, "キーが見つかりません。")
    return twitterapi_key_to_read(row)
