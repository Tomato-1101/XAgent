"""twitterapi.io 読み取りキーのCRUDと疎通テスト。

複数キーを優先度順に保持し、読み取り時に上から試してフォールバックする
(チェーンの組み立ては x_client._build_read_backend)。キーは秘密情報のためローカルDBにのみ
保存し、一覧では mask_key() でマスクして返す。X APIは触らない(疎通テストのみ twitterapi.io を叩く)。
"""

from __future__ import annotations

from sqlmodel import Session, select

from .models import TwitterApiKey, _utcnow
from .twitterapi_client import TwitterApiIoClient, TwitterApiIoError

# 疎通テストで引く安定したハンドル(存在し続けるアカウント)。1リクエスト=1ユニットで安い。
_PROBE_HANDLE = "X"


def mask_key(api_key: str) -> str:
    """秘密キーを末尾4文字だけ残してマスク表示する(UI・ログ用)。"""
    if not api_key:
        return ""
    if len(api_key) <= 4:
        return "•" * len(api_key)
    return "•" * 6 + api_key[-4:]


def list_keys(session: Session) -> list[TwitterApiKey]:
    return list(
        session.exec(select(TwitterApiKey).order_by(TwitterApiKey.priority, TwitterApiKey.id)).all()
    )


def get_key(session: Session, key_id: int) -> TwitterApiKey | None:
    return session.get(TwitterApiKey, key_id)


def create_key(
    session: Session,
    api_key: str,
    label: str = "",
    priority: int | None = None,
    enabled: bool = True,
) -> TwitterApiKey:
    if priority is None:
        # 末尾に追加(既存の最大優先度+1)。空なら 0。
        rows = list_keys(session)
        priority = (max((r.priority for r in rows), default=-1) + 1)
    row = TwitterApiKey(api_key=api_key.strip(), label=label, priority=priority, enabled=enabled)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_key(
    session: Session,
    key_id: int,
    *,
    label: str | None = None,
    api_key: str | None = None,
    priority: int | None = None,
    enabled: bool | None = None,
) -> TwitterApiKey | None:
    row = session.get(TwitterApiKey, key_id)
    if row is None:
        return None
    if label is not None:
        row.label = label
    if api_key is not None and api_key.strip():
        # 新しいキーに差し替えたら過去の疎通結果は無効化する(古い成功表示を残さない)。
        row.api_key = api_key.strip()
        row.last_ok_at = None
        row.last_error = None
    if priority is not None:
        row.priority = priority
    if enabled is not None:
        row.enabled = enabled
    row.updated_at = _utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def delete_key(session: Session, key_id: int) -> bool:
    row = session.get(TwitterApiKey, key_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def reorder(session: Session, ids: list[int]) -> list[TwitterApiKey]:
    """渡された id 順に priority を 0,1,2... で振り直す(UIの並べ替え用)。"""
    for i, key_id in enumerate(ids):
        row = session.get(TwitterApiKey, key_id)
        if row is not None:
            row.priority = i
            row.updated_at = _utcnow()
            session.add(row)
    session.commit()
    return list_keys(session)


def probe_key(session: Session, key_id: int) -> TwitterApiKey | None:
    """キー1本で twitterapi.io に1回読み取りを投げ、成否を last_ok_at/last_error に記録する。

    例外なく応答すれば成功(ユーザー不在で None が返っても、認証・課金は通っている=OK)。
    TwitterApiIoError(残高切れ402・タイムアウト等)は失敗として記録する。
    """
    row = session.get(TwitterApiKey, key_id)
    if row is None:
        return None
    try:
        TwitterApiIoClient(row.api_key).get_user_by_username(_PROBE_HANDLE)
        row.last_ok_at = _utcnow()
        row.last_error = None
    except TwitterApiIoError as e:
        row.last_error = str(e)
    except Exception as e:  # ネットワーク以外の予期せぬ失敗もUIに見せる(握り潰さない)
        row.last_error = f"想定外エラー: {e}"
    row.updated_at = _utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
