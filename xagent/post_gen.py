"""オリジナル投稿の叩き台生成(A機能)。

フォロー転換の受け皿=オリジナル発信(現状ほぼ皆無)を補うため、設定したテーマ角度から
バズの型に沿った投稿の「叩き台」を生成して下書きにする。中身の核(実体験・数字)は
ユーザーが埋める前提で、AIは型・構成・フックを作り、事実部分は〔 〕プレースホルダにする。

生成は下書きまで(投稿は人間承認必須)。生成系のため自動は既定OFF・乱造ガード
(承認待ちPOSTが post_backlog_max 件以上ならスキップ)。手動「今すぐ生成」はガードを通らない。
ニュース速報(news.py)のバッチ生成と同じ構造に倣う。
"""

from __future__ import annotations

import json
from collections.abc import Callable

from sqlmodel import Session, select

from . import maintenance
from . import templates as templates_mod
from .cost import bill_formatter_usage
from .formatter import Formatter
from .models import Draft, DraftKind, DraftStatus, PostSettings, TemplateKind, _utcnow
from .style import active_style_guide


def get_post_settings(session: Session) -> PostSettings:
    """オリジナル投稿生成の設定(単一行)。無ければ既定(自動OFF)で作成する。"""
    row = session.exec(select(PostSettings)).first()
    if row is None:
        row = PostSettings()
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def set_post_settings(session: Session, **values) -> PostSettings:
    """オリジナル投稿生成の設定を更新する(指定キーのみ)。"""
    row = get_post_settings(session)
    for k, v in values.items():
        if not hasattr(row, k) or v is None:
            continue
        if k in ("max_posts_per_run", "post_backlog_max") and isinstance(v, int) and not isinstance(v, bool):
            setattr(row, k, max(0, v))
        elif k == "themes_json" and isinstance(v, str):
            setattr(row, k, v)
        elif isinstance(v, bool):
            setattr(row, k, v)
    row.updated_at = _utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def post_backlog_count(session: Session) -> int:
    """承認待ち(未承認=DRAFT)のオリジナル投稿下書きの件数。自動tickの乱造ガードに使う。"""
    return len(
        session.exec(
            select(Draft.id).where(
                Draft.status == DraftStatus.DRAFT,
                Draft.kind == DraftKind.POST,
            )
        ).all()
    )


def _create_post_draft(session: Session, post: dict) -> Draft:
    """生成済みのオリジナル投稿叩き台を Draft 化する(本文確定済み・LLMは呼ばない)。"""
    src_lines = [f"[オリジナル叩き台] 型: {post.get('type_label') or '(自動選択)'}"]
    if post.get("theme"):
        src_lines.append(f"[テーマ] {post['theme']}")
    if post.get("fill_hint"):
        src_lines.append(f"[埋める一次情報] {post['fill_hint']}")
    if post.get("reason"):
        src_lines.append(f"[AIメモ] {post['reason']}")
    draft = Draft(
        kind=DraftKind.POST,
        status=DraftStatus.DRAFT,
        source_text="\n".join(src_lines),
        segments_json=json.dumps([post["text"]], ensure_ascii=False),
    )
    session.add(draft)
    session.commit()
    session.refresh(draft)
    maintenance.enforce_db_capacity(session)
    return draft


def run_post_gen_once(
    session: Session,
    formatter: Formatter,
    max_posts: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """オリジナル投稿の叩き台生成を1回実行する。下書きを作るだけで投稿しない。

    手動「今すぐ生成」と自動(post_gen_tick)の両方から呼ばれる。
    """
    note = progress or (lambda m: None)
    settings = get_post_settings(session)
    themes = json.loads(settings.themes_json or "[]")
    limit = max_posts if max_posts is not None else settings.max_posts_per_run
    result: dict = {"themes": len(themes), "created": 0, "draft_ids": []}
    if not themes or limit <= 0:
        return result
    note(
        f"AIが叩き台を生成中: {len(themes)}個のテーマ角度から最大{limit}件"
        "(数分かかることがあります)"
    )
    playbook = templates_mod.active_body(session, TemplateKind.POST)
    posts = formatter.generate_post_drafts(
        themes, limit, style_guide=active_style_guide(session), playbook=playbook
    )
    for i, p in enumerate(posts):
        note(f"下書き作成中 {i + 1}/{len(posts)}件")
        d = _create_post_draft(session, p)
        result["draft_ids"].append(d.id)
        result["created"] += 1
    bill_formatter_usage(session, formatter, note="post-gen-batch")
    session.commit()
    return result
