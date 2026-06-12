"""ニュース速報の下書き生成(XNewsBot連携)。

XNewsBot(別プロジェクト)が朝夕に収集したニュースDB(xnewsbot.db)を**読み取り専用**で参照し、
対象ジャンル(既定: AI/テクノロジー)の新着ダイジェストから速報風の投稿下書きを生成する。
書き込みは一切しない(XNewsBot側のコード・DBを変更しない)。

生成は1回のバッチAI判断(formatter.generate_news_posts)で「どの記事か」「通常投稿か引用RTか」
「型(N1〜N5)と本文」まで決める。下書きを作るだけで投稿はしない(人間承認必須)。
処理済みダイジェストは NewsSettings.last_digest_id で記録し再生成しない。
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

from sqlmodel import Session, select

from . import maintenance
from . import templates as templates_mod
from .config import get_settings
from .cost import bill_formatter_usage
from .formatter import Formatter
from .models import Draft, DraftKind, DraftStatus, NewsSettings, TemplateKind, _utcnow
from .style import active_style_guide

_STATUS_URL_RE = re.compile(r"/status(?:es)?/(\d+)")


class NewsSourceUnavailable(Exception):
    """XNewsBotのDBが見つからない/読めない。"""


def get_news_settings(session: Session) -> NewsSettings:
    """ニュース速報設定(単一行)。無ければ既定(自動OFF)で作成する。"""
    row = session.exec(select(NewsSettings)).first()
    if row is None:
        row = NewsSettings()
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def set_news_settings(session: Session, **values) -> NewsSettings:
    """ニュース速報設定を更新する(指定キーのみ)。"""
    row = get_news_settings(session)
    for k, v in values.items():
        if not hasattr(row, k) or v is None:
            continue
        if k == "max_posts_per_run" and isinstance(v, int) and not isinstance(v, bool):
            setattr(row, k, max(0, v))
        elif k == "genres_json" and isinstance(v, str):
            setattr(row, k, v)
        elif isinstance(v, bool):
            setattr(row, k, v)
    row.updated_at = _utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _connect_ro(db_path: str | None) -> sqlite3.Connection:
    path = db_path or get_settings().xnewsbot_db_path
    if not path or not Path(path).exists():
        raise NewsSourceUnavailable(
            f"XNewsBotのDBが見つかりません: {path or '(未設定)'}。"
            " .env の XNEWSBOT_DB_PATH を確認してください。"
        )
    # mode=ro で読み取り専用に固定する(XNewsBot側のDBを誤って変更しないための保証)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _row_to_item(row: sqlite3.Row, index: int) -> dict:
    tweets = json.loads(row["source_tweets"] or "[]")
    urls = json.loads(row["source_urls"] or "[]")
    st = tweets[0] if tweets else None
    return {
        "index": index,
        "digest_id": row["digest_id"],
        "digest_date": str(row["digest_date"]),
        "slot": row["slot"],
        "genre": row["genre"],
        "importance": row["importance"],
        "title": row["title"],
        "summary": row["summary"],
        "detail": row["detail"] or "",
        "source_url": urls[0] if urls else None,
        "source_tweet": (
            {
                "url": st.get("url", ""),
                "author": st.get("author", ""),
                "text": (st.get("text") or "")[:280],
                "views": st.get("views", 0),
            }
            if st
            else None
        ),
        "top_view_count": row["top_view_count"],
    }


_ITEM_QUERY = """
SELECT ni.*, gd.id AS digest_id, gd.digest_date, gd.slot
FROM genredigest gd JOIN newsitem ni ON ni.genre_digest_id = gd.id
WHERE {where} AND gd.genre IN ({ph})
ORDER BY gd.id DESC, ni.rank
"""


def fetch_new_items(
    news_settings: NewsSettings, db_path: str | None = None
) -> tuple[list[dict], int]:
    """last_digest_id より新しい対象ジャンルのニュースと、現在の最大digest IDを返す。

    最大IDは対象ジャンル以外も含む全体の MAX(id)。処理後にそこまで進めることで、
    対象外ジャンルのダイジェストを毎回読み直さない。
    """
    genres = json.loads(news_settings.genres_json or "[]")
    if not genres:
        return [], news_settings.last_digest_id
    con = _connect_ro(db_path)
    try:
        ph = ",".join("?" * len(genres))
        if news_settings.last_digest_id <= 0:
            # 初回はカーソルが無く全履歴が「新着」になってしまうため、最新の収集日だけに絞る
            # (過去数週間分をまとめてLLMに流し込み大量生成するのを防ぐ)。
            q = _ITEM_QUERY.format(
                where="gd.digest_date = (SELECT MAX(digest_date) FROM genredigest)", ph=ph
            )
            rows = con.execute(q, list(genres)).fetchall()
        else:
            q = _ITEM_QUERY.format(where="gd.id > ?", ph=ph)
            rows = con.execute(q, [news_settings.last_digest_id, *genres]).fetchall()
        max_id = con.execute("SELECT COALESCE(MAX(id), 0) FROM genredigest").fetchone()[0]
    finally:
        con.close()
    items = [_row_to_item(r, i) for i, r in enumerate(rows)]
    return items, int(max_id)


def recent_items(
    news_settings: NewsSettings, db_path: str | None = None, days: int = 2
) -> list[dict]:
    """直近days日の対象ジャンルのニュース(UI表示用)。新着判定には使わない。"""
    genres = json.loads(news_settings.genres_json or "[]")
    if not genres:
        return []
    con = _connect_ro(db_path)
    try:
        ph = ",".join("?" * len(genres))
        q = _ITEM_QUERY.format(where=f"gd.digest_date >= date('now', '-{int(days)} day')", ph=ph)
        rows = con.execute(q, list(genres)).fetchall()
    finally:
        con.close()
    return [_row_to_item(r, i) for i, r in enumerate(rows)]


def _create_news_draft(session: Session, post: dict) -> Draft:
    """バッチ生成済みのニュース速報案をDraft化する(本文確定済み・LLMは呼ばない)。

    - format=post: 通常投稿。source_url があれば2ツイート目(リプ欄)に置く(リンク減点対策)。
    - format=quote: 一次情報の元ポストへの引用RT(画像も引用元から見える)。
    """
    item = post["item"]
    src_lines = [f"[ニュース速報] {item['genre']} / {item['title']}"]
    if post.get("reason"):
        src_lines.append(f"[AI選定理由] {post['reason']}")
    source_text = "\n".join(src_lines)

    tweet_id = None
    st = item.get("source_tweet") or {}
    if post["format"] == "quote" and st.get("url"):
        m = _STATUS_URL_RE.search(st["url"])
        tweet_id = m.group(1) if m else None
    if tweet_id:
        draft = Draft(
            kind=DraftKind.QUOTE,
            status=DraftStatus.DRAFT,
            source_text=source_text,
            segments_json=json.dumps([post["text"]], ensure_ascii=False),
            target_tweet_id=tweet_id,
            target_handle=st.get("author") or None,
            target_text=st.get("text", ""),
        )
    else:
        segments = [post["text"]]
        if post.get("source_url"):
            segments.append(post["source_url"])
        draft = Draft(
            kind=DraftKind.POST,
            status=DraftStatus.DRAFT,
            source_text=source_text,
            segments_json=json.dumps(segments, ensure_ascii=False),
        )
    session.add(draft)
    session.commit()
    session.refresh(draft)
    maintenance.enforce_db_capacity(session)
    return draft


def run_news_once(
    session: Session,
    formatter: Formatter,
    max_posts: int | None = None,
    progress: Callable[[str], None] | None = None,
    db_path: str | None = None,
) -> dict:
    """ニュース速報の生成を1回実行する。新着が無ければ何も作らない。

    手動「今すぐ生成」と自動(news_tick)の両方から呼ばれる。下書きを作るだけで投稿しない。
    """
    note = progress or (lambda m: None)
    settings = get_news_settings(session)
    note("XNewsBotの新着ニュースを確認中")
    items, max_digest_id = fetch_new_items(settings, db_path=db_path)
    limit = max_posts if max_posts is not None else settings.max_posts_per_run
    result: dict = {"news_items": len(items), "created": 0, "draft_ids": []}
    if items and limit > 0:
        note(
            f"AIが記事選定と本文生成中: 新着{len(items)}件から最大{limit}件"
            "(数分かかることがあります)"
        )
        playbook = templates_mod.active_body(session, TemplateKind.NEWS)
        posts = formatter.generate_news_posts(
            items, limit, style_guide=active_style_guide(session), playbook=playbook
        )
        for i, p in enumerate(posts):
            note(f"下書き作成中 {i + 1}/{len(posts)}件")
            d = _create_news_draft(session, p)
            result["draft_ids"].append(d.id)
            result["created"] += 1
        bill_formatter_usage(session, formatter, note="news-batch")
    if max_digest_id > settings.last_digest_id:
        settings.last_digest_id = max_digest_id
        settings.updated_at = _utcnow()
        session.add(settings)
    session.commit()
    return result
