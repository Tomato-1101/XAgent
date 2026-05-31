"""スタイル(口調)管理とトーン学習。

- スタイルガイド: 文章で指定(StyleProfile)。
- 過去投稿: X APIで自動取得して PastPost に保存し、整形のfew-shot例として使う。
"""

from __future__ import annotations

from sqlmodel import Session, select

from .cost import log_cost
from .models import CostKind, PastPost, StyleProfile


def active_style_guide(session: Session) -> str:
    row = session.exec(
        select(StyleProfile).where(StyleProfile.active == True)  # noqa: E712
    ).first()
    return row.guide_text if row else ""


def set_style_guide(session: Session, text: str, name: str = "default") -> StyleProfile:
    """アクティブなスタイルガイドを更新(なければ作成)。"""
    row = session.exec(
        select(StyleProfile).where(StyleProfile.name == name)
    ).first()
    if row is None:
        row = StyleProfile(name=name, guide_text=text, active=True)
        session.add(row)
    else:
        row.guide_text = text
        row.active = True
    session.commit()
    session.refresh(row)
    return row


def example_texts(session: Session, n: int = 10) -> list[str]:
    """自分の過去投稿(エンゲージ上位)を返す。表示・参考用(整形へは自動注入しない)。"""
    rows = session.exec(
        select(PastPost)
        .where(PastPost.is_own == True)  # noqa: E712
        .order_by(PastPost.like_count.desc())
        .limit(n)
    ).all()
    return [r.text for r in rows]


def learn_past_posts(session: Session, x_client, me_user_id: str, max_posts: int = 100) -> int:
    """自分の過去投稿を取得して保存(重複はtweet_idでスキップ)。保存件数を返す。"""
    tweets = x_client.get_user_timeline(me_user_id)
    log_cost(session, CostKind.READ, units=len(tweets), note="learn_past_posts")
    saved = 0
    for t in tweets[:max_posts]:
        exists = session.exec(
            select(PastPost).where(PastPost.tweet_id == t["id"])
        ).first()
        if exists:
            continue
        session.add(PastPost(tweet_id=t["id"], text=t.get("text", "")))
        saved += 1
    session.commit()
    return saved
