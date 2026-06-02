"""Xネイティブ「リスト」のCRUDとメンバー閲覧/編集。

- 所有リスト一覧 = 読み取り(twitterapi.io 非対応のため公式のみ)。
- メンバー閲覧 = 読み取り(プロフィール付。twitterapi.io 優先・失敗時のみ公式)。
- 作成/更新/削除/メンバー追加削除 = 書き込み = 公式X APIのみ(自分のアカウント操作=BAN回避)。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ... import lists as lists_service
from ...x_client import XClient, XClientError
from ..deps import get_x_client, require_api_token
from ..schemas import (
    ListCreateRequest,
    ListCreateResult,
    ListMember,
    ListMemberAddRequest,
    ListRead,
    ListUpdateRequest,
)

router = APIRouter(prefix="/lists", tags=["lists"], dependencies=[Depends(require_api_token)])


@router.get("", response_model=list[ListRead])
def owned_lists(
    max_total: int = Query(100, ge=1, le=1000),
    x: XClient = Depends(get_x_client),
) -> list[ListRead]:
    """自分が作成した(所有)リスト一覧。"""
    try:
        return [ListRead(**item) for item in x.get_owned_lists(max_total=max_total)]
    except XClientError as e:
        raise HTTPException(503, f"X API に接続できません: {e}")


@router.get("/{list_id}/members", response_model=list[ListMember])
def list_members(
    list_id: str,
    max_total: int = Query(100, ge=1, le=1000),
    x: XClient = Depends(get_x_client),
) -> list[ListMember]:
    """リストのメンバーをプロフィール(表示名/bio/フォロワー数等)付きで返す。"""
    try:
        return [ListMember(**m) for m in x.get_list_members(list_id, max_total=max_total)]
    except XClientError as e:
        raise HTTPException(503, f"X API に接続できません: {e}")


@router.post("", response_model=ListCreateResult)
def create_list(req: ListCreateRequest, x: XClient = Depends(get_x_client)) -> ListCreateResult:
    """ハンドル一覧から新規リストを作成し、解決できたアカウントを一括追加する。"""
    try:
        result = lists_service.create_list_from_handles(
            x, req.name, req.accounts, description=req.description, private=req.private
        )
    except XClientError as e:
        raise HTTPException(503, f"リスト作成に失敗しました: {e}")
    return ListCreateResult(**result)


@router.patch("/{list_id}", status_code=204)
def update_list(
    list_id: str, req: ListUpdateRequest, x: XClient = Depends(get_x_client)
) -> Response:
    """名前/説明/公開設定を更新する(None の項目は変更しない)。"""
    try:
        x.update_list(list_id, name=req.name, description=req.description, private=req.private)
    except XClientError as e:
        raise HTTPException(503, f"リスト更新に失敗しました: {e}")
    return Response(status_code=204)


@router.delete("/{list_id}", status_code=204)
def delete_list(list_id: str, x: XClient = Depends(get_x_client)) -> Response:
    try:
        x.delete_list(list_id)
    except XClientError as e:
        raise HTTPException(503, f"リスト削除に失敗しました: {e}")
    return Response(status_code=204)


@router.post("/{list_id}/members", status_code=204)
def add_member(
    list_id: str, req: ListMemberAddRequest, x: XClient = Depends(get_x_client)
) -> Response:
    """メンバー追加。handle が来たら user_id を解決(安価読取)してから追加する。"""
    user_id = req.user_id
    if not user_id:
        if not req.handle:
            raise HTTPException(422, "handle か user_id のいずれかが必要です。")
        try:
            info = x.get_user_by_username(req.handle)
        except XClientError as e:
            raise HTTPException(503, f"ユーザー解決に失敗しました: {e}")
        if not info or not info.get("id"):
            raise HTTPException(404, f"@{req.handle.lstrip('@')} を解決できません。")
        user_id = str(info["id"])
    try:
        x.add_list_member(list_id, user_id)
    except XClientError as e:
        raise HTTPException(503, f"メンバー追加に失敗しました: {e}")
    return Response(status_code=204)


@router.delete("/{list_id}/members/{user_id}", status_code=204)
def remove_member(list_id: str, user_id: str, x: XClient = Depends(get_x_client)) -> Response:
    try:
        x.remove_list_member(list_id, user_id)
    except XClientError as e:
        raise HTTPException(503, f"メンバー削除に失敗しました: {e}")
    return Response(status_code=204)
