"""「型」(PromptTemplate)サービスのテスト: CRUD・既定切替・シード冪等・AI選択。"""

from __future__ import annotations

from xagent import templates as templates_mod
from xagent.models import TemplateKind


def test_crud_and_list(session):
    a = templates_mod.create_template(session, "型A", TemplateKind.POST, "本文A")
    b = templates_mod.create_template(session, "型B", TemplateKind.REPLY, "本文B")
    assert {t.name for t in templates_mod.list_templates(session)} == {"型A", "型B"}
    assert [t.name for t in templates_mod.list_templates(session, TemplateKind.POST)] == ["型A"]

    templates_mod.update_template(session, a.id, name="型A2", body="本文A2")
    assert templates_mod.get_template(session, a.id).name == "型A2"
    assert templates_mod.resolve_body(session, a.id) == "本文A2"

    assert templates_mod.delete_template(session, b.id) is True
    assert templates_mod.get_template(session, b.id) is None
    assert templates_mod.delete_template(session, 9999) is False


def test_set_active_is_one_per_kind(session):
    a = templates_mod.create_template(session, "投稿1", TemplateKind.POST, "p1")
    b = templates_mod.create_template(session, "投稿2", TemplateKind.POST, "p2")
    r = templates_mod.create_template(session, "リプ1", TemplateKind.REPLY, "r1", active=True)

    templates_mod.set_active(session, a.id)
    assert templates_mod.active_body(session, TemplateKind.POST) == "p1"
    templates_mod.set_active(session, b.id)               # 切替で a は非activeに
    assert templates_mod.active_body(session, TemplateKind.POST) == "p2"
    session.refresh(a)
    assert a.active is False
    # 別kind(reply)は影響を受けない
    assert templates_mod.active_body(session, TemplateKind.REPLY) == "r1"
    assert r.active is True


def test_resolve_body_none_and_missing(session):
    assert templates_mod.resolve_body(session, None) == ""
    assert templates_mod.resolve_body(session, 12345) == ""
    assert templates_mod.active_body(session, TemplateKind.QUOTE) == ""  # active無し


def test_seed_builtin_is_idempotent(session):
    n1 = templates_mod.seed_builtin_templates(session)
    assert n1 == 3                                        # post/reply/quote の3件
    n2 = templates_mod.seed_builtin_templates(session)    # 再実行で重複投入しない
    assert n2 == 0
    assert len(templates_mod.list_templates(session)) == 3
    # 各kindに既定(active)が立つ
    assert templates_mod.active_body(session, TemplateKind.POST) != ""
    assert templates_mod.active_body(session, TemplateKind.REPLY) != ""


def test_seed_builtin_syncs_body_for_builtin(session):
    """builtin(prompts.py由来)の型は再シードで本文が最新へ同期される(prompts.py更新の反映)。"""
    from xagent.prompts import REPLY_PLAYBOOK

    templates_mod.seed_builtin_templates(session)
    reply = templates_mod.list_templates(session, TemplateKind.REPLY)[0]
    assert reply.builtin is True
    # 本文を古い値に差し替え → 再シードで prompts.py の最新値へ戻る
    reply.body = "古い本文"
    session.add(reply)
    session.commit()
    templates_mod.seed_builtin_templates(session)
    session.refresh(reply)
    assert reply.body == REPLY_PLAYBOOK
    assert reply.body != "古い本文"


def test_choose_template_picks_valid_id(session):
    a = templates_mod.create_template(session, "型A", TemplateKind.POST, "本文A")
    b = templates_mod.create_template(session, "型B", TemplateKind.POST, "本文B")
    cands = templates_mod.list_templates(session, TemplateKind.POST)

    # LLMが b.id を返す → b が選ばれる
    chosen = templates_mod.choose_template(lambda s, u: f"選ぶなら {b.id} です", cands, "メモ")
    assert chosen == b.id
    # 候補外の数字 → None(安全側)
    assert templates_mod.choose_template(lambda s, u: "9999", cands, "メモ") is None
    # 数字なし → None
    assert templates_mod.choose_template(lambda s, u: "わかりません", cands, "メモ") is None


def test_choose_template_shortcuts(session):
    # 候補0件 → None、1件 → completeを呼ばずそれを返す
    assert templates_mod.choose_template(lambda s, u: "1", [], "メモ") is None
    a = templates_mod.create_template(session, "唯一", TemplateKind.POST, "x")
    called = []
    only = templates_mod.list_templates(session, TemplateKind.POST)
    chosen = templates_mod.choose_template(lambda s, u: called.append(1) or "999", only, "メモ")
    assert chosen == a.id and called == []                # 1件なら呼ばない
