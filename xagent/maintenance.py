"""DB のメンテナンス: 容量上限を超えたら古い端末状態の下書きから物理削除する。

取消(CANCELED)・却下(REJECTED)・投稿済み(POSTED)はゴミ箱/履歴として残すが、
DBファイルが上限(settings.max_db_bytes)を超えたら古い順に削除し、VACUUM で実ファイルを縮める。
未投稿で生きている下書き(DRAFT/APPROVED/QUEUED)は削除しない。
"""

from __future__ import annotations

import os

from sqlmodel import Session, select

from .config import Settings, get_settings
from .db import get_engine
from .models import Draft, DraftStatus

# 物理削除してよい端末状態(古い順に消す)
_TERMINAL = (DraftStatus.POSTED, DraftStatus.REJECTED, DraftStatus.CANCELED)

_PURGE_BATCH = 200


def db_file_path(settings: Settings | None = None) -> str | None:
    """設定からDBファイルの実パスを返す。インメモリ等(ファイルなし)は None。"""
    settings = settings or get_settings()
    url = settings.sqlite_url
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    path = url[len(prefix):]
    if not path or path == ":memory:":
        return None
    return path


def db_size_bytes(settings: Settings | None = None) -> int:
    """DBファイルのサイズ(バイト)。ファイルが無ければ0。"""
    path = db_file_path(settings)
    if not path or not os.path.exists(path):
        return 0
    return os.path.getsize(path)


def _vacuum(engine) -> None:
    """VACUUM で削除済み領域を実ファイルから回収する(トランザクション外で実行)。"""
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("VACUUM")
    except Exception:
        # VACUUM 不可の環境でも致命にしない(行削除自体は済んでいる)
        pass


def enforce_db_capacity(
    session: Session, settings: Settings | None = None, engine=None
) -> int:
    """DBが容量上限を超えていれば、古い端末状態の下書きから削除する。削除件数を返す。

    通常は上限(2GB)に達しないため何もしない。達した場合のみ古い順に削る安全弁。
    """
    settings = settings or get_settings()
    path = db_file_path(settings)
    if not path or not os.path.exists(path):
        return 0  # インメモリ/未作成: 何もしない(テスト等)
    if os.path.getsize(path) <= settings.max_db_bytes:
        return 0

    engine = engine or get_engine()
    purged = 0
    while os.path.getsize(path) > settings.max_db_bytes:
        batch = session.exec(
            select(Draft)
            .where(Draft.status.in_(_TERMINAL))  # type: ignore[attr-defined]
            .order_by(Draft.created_at)
            .limit(_PURGE_BATCH)
        ).all()
        if not batch:
            break  # 消せる端末状態がもう無い(生きた下書きは消さない)
        for d in batch:
            session.delete(d)
            purged += 1
        session.commit()
        _vacuum(engine)
    return purged
