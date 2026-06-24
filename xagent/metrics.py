"""自分の投稿メトリクスの取得・保存・集計(B機能)。

公式API v2(OAuth1.0a user context)で自分の直近投稿(手動リプ/引用含む)を
non_public_metrics 付きで取得し、PostMetric に tweet_id で upsert する。
impression_count は自分のツイートのみ取得可。読み取り専用で投稿はしない。

改善を数字で追えるようにするための可視化用: 種別(post/reply/quote)別に
平均インプレッション・いいね・RT・リプ・ブックマークを集計して返す。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from .cost import log_cost
from .models import CostKind, MetricsSettings, PostMetric, _utcnow


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    """aware/naive datetime を内部統一の naive UTC に揃える(混在比較ズレ防止)。"""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def get_metrics_settings(session: Session) -> MetricsSettings:
    """メトリクス取得設定(単一行)。無ければ既定(自動ON・30日)で作成する。"""
    row = session.exec(select(MetricsSettings)).first()
    if row is None:
        row = MetricsSettings()
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def set_metrics_settings(session: Session, **values) -> MetricsSettings:
    """メトリクス取得設定を更新する(指定キーのみ)。"""
    row = get_metrics_settings(session)
    for k, v in values.items():
        if not hasattr(row, k) or v is None:
            continue
        if k == "lookback_days" and isinstance(v, int) and not isinstance(v, bool):
            setattr(row, k, max(1, min(v, 90)))
        elif isinstance(v, bool):
            setattr(row, k, v)
    row.updated_at = _utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def collect_metrics_once(
    session: Session,
    x_client,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """直近 lookback_days 分の自分の投稿を取得し PostMetric に upsert する(読み取りのみ)。

    手動「今すぐ取得」と自動(metrics_tick)の両方から呼ばれる。投稿は一切しない。
    """
    note = progress or (lambda m: None)
    settings = get_metrics_settings(session)
    start_time = datetime.now(timezone.utc) - timedelta(days=settings.lookback_days)
    note(f"直近{settings.lookback_days}日の自分の投稿メトリクスを取得中")
    tweets = x_client.get_my_recent_tweets_with_metrics(start_time=start_time)
    log_cost(session, CostKind.READ, units=len(tweets), note="metrics-collect")

    inserted = 0
    updated = 0
    for t in tweets:
        tid = str(t.get("id") or "")
        if not tid:
            continue
        row = session.exec(
            select(PostMetric).where(PostMetric.tweet_id == tid)
        ).first()
        if row is None:
            row = PostMetric(tweet_id=tid, created_at=_to_naive_utc(t.get("created_at")))
            inserted += 1
        else:
            updated += 1
        row.kind = t.get("kind") or row.kind or "post"
        row.text = t.get("text", "")
        if t.get("created_at") is not None:
            row.created_at = _to_naive_utc(t.get("created_at"))
        row.captured_at = _utcnow()
        row.impression_count = t.get("impression_count")
        row.like_count = int(t.get("like_count", 0) or 0)
        row.retweet_count = int(t.get("retweet_count", 0) or 0)
        row.reply_count = int(t.get("reply_count", 0) or 0)
        row.quote_count = int(t.get("quote_count", 0) or 0)
        row.bookmark_count = int(t.get("bookmark_count", 0) or 0)
        session.add(row)
    session.commit()
    return {"fetched": len(tweets), "inserted": inserted, "updated": updated}


def metrics_summary(session: Session) -> dict:
    """保存済み PostMetric を種別別に集計して返す(Analytics画面の表示用)。

    各種別と全体について、件数・平均/中央値インプレッション・平均いいね/RT/リプ/引用/ブックマークを返す。
    """
    rows = session.exec(select(PostMetric)).all()

    def _agg(items: list[PostMetric]) -> dict:
        n = len(items)
        if n == 0:
            return {
                "count": 0, "avg_impressions": None, "median_impressions": None,
                "avg_likes": 0.0, "avg_retweets": 0.0, "avg_replies": 0.0,
                "avg_quotes": 0.0, "avg_bookmarks": 0.0, "sum_bookmarks": 0,
            }
        imps = sorted(r.impression_count for r in items if r.impression_count is not None)
        avg_imp = round(sum(imps) / len(imps), 1) if imps else None
        med_imp = imps[len(imps) // 2] if imps else None
        return {
            "count": n,
            "avg_impressions": avg_imp,
            "median_impressions": med_imp,
            "avg_likes": round(sum(r.like_count for r in items) / n, 2),
            "avg_retweets": round(sum(r.retweet_count for r in items) / n, 2),
            "avg_replies": round(sum(r.reply_count for r in items) / n, 2),
            "avg_quotes": round(sum(r.quote_count for r in items) / n, 2),
            "avg_bookmarks": round(sum(r.bookmark_count for r in items) / n, 2),
            "sum_bookmarks": sum(r.bookmark_count for r in items),
        }

    by_kind = {}
    for kind in ("post", "reply", "quote", "repost"):
        by_kind[kind] = _agg([r for r in rows if r.kind == kind])
    last = max((r.captured_at for r in rows), default=None)
    return {
        "total": _agg(list(rows)),
        "by_kind": by_kind,
        "captured_at": last.isoformat() if last is not None else None,
    }
