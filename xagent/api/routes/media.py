"""Media: 画像/動画のアップロード受け口。

ブラウザから multipart で受け取り、ローカルに保存して保持パスを返す。
返ったパスを compose の media_paths に渡すと、投稿時に X へアップロードされる。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ...media import MAX_BYTES, classify, is_allowed_filename, save_bytes
from ..deps import require_api_token

router = APIRouter(prefix="/media", tags=["media"], dependencies=[Depends(require_api_token)])


@router.post("/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    if not is_allowed_filename(file.filename):
        raise HTTPException(400, "対応していない形式です(jpg/png/webp/gif/mp4/mov)。")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"ファイルが大きすぎます(上限{MAX_BYTES // (1024 * 1024)}MB)。")
    path = save_bytes(file.filename, data)
    return {"path": path, "kind": classify(path), "filename": file.filename}
