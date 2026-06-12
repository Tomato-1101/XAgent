"""「型」(PromptTemplate)の管理サービス。

投稿/リプ生成に使う型を名前付き・カテゴリ別(post/reply/quote)に複数保持し、
- Compose で手動選択 or 「AIに任せる」でAIが入力内容から最適な型を自動選択(`choose_template`)、
- monitor の自動リプ/引用では各kindの active な型を既定として使う、
ために CRUD・active 管理・AI選択・初期シードを提供する。

CLI / MCP / API(routes/templates.py) で共用する(ロジック一元化)。`style.py` の作法に倣う。
"""

from __future__ import annotations

import re

from sqlmodel import Session, select

from .models import PromptTemplate, TemplateKind
from .prompts import NEWS_PLAYBOOK, POST_PLAYBOOK, QUOTE_PLAYBOOK, REPLY_PLAYBOOK

# シード(buzz-playbook由来)の型。name で既存判定し冪等に投入する。
_BUILTIN = [
    ("バズの型 (buzz-playbook)", TemplateKind.POST, POST_PLAYBOOK),
    ("絡みリプの型 (R1〜R6)", TemplateKind.REPLY, REPLY_PLAYBOOK),
    ("引用RTの型", TemplateKind.QUOTE, QUOTE_PLAYBOOK),
    ("ニュース速報の型 (N1〜N5)", TemplateKind.NEWS, NEWS_PLAYBOOK),
]


def _utcnow():
    from .models import _utcnow as now  # 単一実装を使い回す(naive UTC)

    return now()


def list_templates(session: Session, kind: TemplateKind | None = None) -> list[PromptTemplate]:
    stmt = select(PromptTemplate)
    if kind is not None:
        stmt = stmt.where(PromptTemplate.kind == kind)
    stmt = stmt.order_by(PromptTemplate.kind, PromptTemplate.created_at)
    return list(session.exec(stmt).all())


def get_template(session: Session, template_id: int) -> PromptTemplate | None:
    return session.get(PromptTemplate, template_id)


def create_template(
    session: Session,
    name: str,
    kind: TemplateKind,
    body: str,
    active: bool = False,
    builtin: bool = False,
) -> PromptTemplate:
    row = PromptTemplate(name=name, kind=kind, body=body, builtin=builtin)
    session.add(row)
    session.commit()
    session.refresh(row)
    if active:
        set_active(session, row.id)
        session.refresh(row)
    return row


def update_template(
    session: Session,
    template_id: int,
    name: str | None = None,
    body: str | None = None,
    kind: TemplateKind | None = None,
    active: bool | None = None,
) -> PromptTemplate | None:
    row = session.get(PromptTemplate, template_id)
    if row is None:
        return None
    if name is not None:
        row.name = name
    if body is not None:
        row.body = body
    if kind is not None:
        row.kind = kind
    row.updated_at = _utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    if active:  # True のときだけ既定化(False=何もしない。明示解除はUIの別操作にしない)
        set_active(session, row.id)
        session.refresh(row)
    return row


def delete_template(session: Session, template_id: int) -> bool:
    row = session.get(PromptTemplate, template_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def set_active(session: Session, template_id: int) -> PromptTemplate | None:
    """指定の型を既定(active)にし、同kindの他をすべて非activeにする。"""
    row = session.get(PromptTemplate, template_id)
    if row is None:
        return None
    others = session.exec(
        select(PromptTemplate).where(PromptTemplate.kind == row.kind)
    ).all()
    for o in others:
        o.active = o.id == template_id
        session.add(o)
    session.commit()
    session.refresh(row)
    return row


def active_body(session: Session, kind: TemplateKind) -> str:
    """指定kindの既定(active)な型の本文。無ければ ""。monitorの自動生成が使う。"""
    row = session.exec(
        select(PromptTemplate)
        .where(PromptTemplate.kind == kind)
        .where(PromptTemplate.active == True)  # noqa: E712
    ).first()
    return row.body if row else ""


def resolve_body(session: Session, template_id: int | None) -> str:
    """選択された型IDの本文。None/不在なら ""。"""
    if template_id is None:
        return ""
    row = session.get(PromptTemplate, template_id)
    return row.body if row else ""


def _gist(body: str, limit: int = 160) -> str:
    """AI選択プロンプト用に型本文の要旨(先頭の意味のある数行)を切り出す。"""
    text = " ".join(line.strip() for line in body.splitlines() if line.strip())
    return text[:limit]


def choose_template(complete_fn, candidates: list[PromptTemplate], source_text: str) -> int | None:
    """候補の型からAIに最適な1つを選ばせ、その id を返す(=「AIに任せる」)。

    complete_fn(system, user)->str(=Formatter.complete)。候補が0/1件なら呼ばず即返す。
    LLMには id を返させ、候補集合に含まれる整数だけ採用する(不正値・解析失敗は None)。
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0].id
    valid = {c.id for c in candidates}
    listing = "\n".join(f"- id={c.id}: {c.name} — {_gist(c.body)}" for c in candidates)
    system = (
        "あなたはX投稿アシスタント。ユーザーのメモに最も効果的な「型」を候補から1つ選ぶ。"
        "選んだ型のidの数字だけを出力する(説明・記号・前置きは一切付けない)。"
    )
    user = f"# 候補の型\n{listing}\n\n# ユーザーのメモ\n{source_text}\n\n最適な型のidを1つ:"
    raw = complete_fn(system, user)
    m = re.search(r"\d+", raw or "")
    if not m:
        return None
    chosen = int(m.group())
    return chosen if chosen in valid else None


def seed_builtin_templates(session: Session) -> int:
    """buzz-playbook由来の型を初期投入する(冪等)。新規投入した件数を返す。

    新規は投入。既存でも builtin(prompts.py由来)なら本文を最新へ同期する
    (prompts.pyの型テキストを更新したら再シードで反映されるように)。
    ユーザーが編集/作成した型(builtin=False)は上書きしない。
    各kindに既定(active)が無ければ、投入/既存のbuiltinを既定にする。
    """
    created = 0
    for name, kind, body in _BUILTIN:
        exists = session.exec(
            select(PromptTemplate).where(PromptTemplate.name == name)
        ).first()
        if exists is None:
            create_template(session, name, kind, body, builtin=True)
            created += 1
        elif exists.builtin and exists.body != body:
            exists.body = body
            session.add(exists)
            session.commit()
    # 各kindに既定が無ければ、その kind の最初の型を既定にする
    for kind in (TemplateKind.POST, TemplateKind.REPLY, TemplateKind.QUOTE, TemplateKind.NEWS):
        has_active = session.exec(
            select(PromptTemplate)
            .where(PromptTemplate.kind == kind)
            .where(PromptTemplate.active == True)  # noqa: E712
        ).first()
        if has_active is None:
            first = session.exec(
                select(PromptTemplate)
                .where(PromptTemplate.kind == kind)
                .order_by(PromptTemplate.created_at)
            ).first()
            if first is not None:
                set_active(session, first.id)
    return created
