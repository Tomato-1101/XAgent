"""DBエンジンとセッション。SQLiteに作成する。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from .config import get_settings

# import しておくことで create_all がテーブルを認識する
from . import models  # noqa: F401

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        # check_same_thread=False: FastAPI/デーモンの別スレッドからも使うため
        _engine = create_engine(
            settings.sqlite_url, connect_args={"check_same_thread": False}
        )
    return _engine


# 既存DBに後から増えた列(SQLModel.create_all は ALTER しない)を冪等に追加する。
# {テーブル名: [(列名, SQLite型, デフォルト句), ...]}
_ADDED_COLUMNS = {
    "pastpost": [
        ("author_user_id", "VARCHAR", ""),
        ("author_handle", "VARCHAR", ""),
        ("is_own", "BOOLEAN", "DEFAULT 1"),
    ],
    "draft": [
        ("blackout_override", "BOOLEAN", "DEFAULT 0"),
        ("target_text", "VARCHAR", "DEFAULT ''"),
        ("target_created_at", "TIMESTAMP", ""),
        ("schedule_missed", "BOOLEAN", "DEFAULT 0"),
    ],
    "monitorsettings": [
        ("max_drafts_per_run", "INTEGER", "DEFAULT 10"),
        ("auto_monitor_enabled", "BOOLEAN", "DEFAULT 1"),
        ("auto_post_enabled", "BOOLEAN", "DEFAULT 1"),
        ("min_impressions", "INTEGER", "DEFAULT 10000"),
        ("celeb_watch_enabled", "BOOLEAN", "DEFAULT 0"),
        ("celeb_list_id", "VARCHAR", ""),
    ],
    "engagetarget": [
        ("list_id", "VARCHAR", ""),
    ],
}


def _migrate(engine) -> None:
    """既存テーブルへの列追加マイグレーション(冪等)。新規テーブルは create_all が作る。"""
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            if not rows:
                continue  # テーブルが無い(新規DB)→ create_all 済みなので追加不要
            existing = {r[1] for r in rows}  # r[1] = column name
            for col, sqltype, default in columns:
                if col not in existing:
                    clause = f" {default}" if default else ""
                    conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {col} {sqltype}{clause}")
                    )


def init_db() -> None:
    """テーブルを作成(なければ)し、列追加マイグレーションと初期シードを適用する。"""
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    _migrate(engine)
    # buzz-playbook由来の「型」を初期投入(冪等)。新規DB/既存DBどちらでも安全。
    from .templates import seed_builtin_templates

    with Session(engine) as session:
        seed_builtin_templates(session)


@contextmanager
def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
