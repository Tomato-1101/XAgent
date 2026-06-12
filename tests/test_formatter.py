"""formatter の「型」(playbook)注入のテスト。LLMは呼ばず system プロンプトを捕捉する。"""

from __future__ import annotations

from xagent.formatter import Formatter


def _capturing_formatter():
    captured = {}

    def fake(system, user):
        captured["system"] = system
        captured["user"] = user
        return "整形済み出力"

    return Formatter(complete=fake), captured


def test_playbook_injected_into_post_system():
    f, cap = _capturing_formatter()
    f.format_post("雑なメモ", style_guide="一人称は俺", playbook="型本文ABC")
    assert "型本文ABC" in cap["system"]
    assert "使う型" in cap["system"]
    assert "一人称は俺" in cap["system"]          # スタイルガイドも併存


def test_no_playbook_means_no_type_block():
    f, cap = _capturing_formatter()
    f.format_post("雑なメモ", style_guide="一人称は俺")
    assert "使う型" not in cap["system"]


def test_playbook_injected_into_reply_and_quote():
    f, cap = _capturing_formatter()
    f.generate_reply("相手の投稿", "someone", style_guide="", playbook="リプ型R")
    assert "リプ型R" in cap["system"]
    assert "使う型" in cap["system"]

    f2, cap2 = _capturing_formatter()
    f2.generate_quote("引用元", "someone", style_guide="", playbook="引用型Q")
    assert "引用型Q" in cap2["system"]


def test_variations_inject_playbook():
    f, cap = _capturing_formatter()
    # 区切りの無い出力でも1案にフォールバックする。systemに型が乗ることだけ見る
    f.format_variations("メモ", n=2, playbook="型本文XYZ")
    assert "型本文XYZ" in cap["system"]


def test_reply_includes_length_variation_hint():
    """返信は毎回どれかの長さ帯ヒントが乗り、文字数が一定に寄らないようにする。"""
    f, cap = _capturing_formatter()
    f.generate_reply("相手の投稿", "someone")
    assert "目安" in cap["system"]
    assert "幅を持たせる" in cap["system"]


# --- select_engagements(バッチ選定)の検証ロジック ---------------------------

def _selecting_formatter(data):
    """_run_structured(LLM呼び出し)を差し替え、固定の選定結果を返す Formatter。"""
    f = Formatter(complete=lambda s, u: "")
    captured = {}

    def fake(system, user, schema, timeout=None):
        captured["system"] = system
        captured["user"] = user
        captured["schema"] = schema
        captured["timeout"] = timeout
        return data

    f._run_structured = fake
    return f, captured


_CANDS = [
    {"tweet_id": "1", "handle": "a", "text": "投稿1"},
    {"tweet_id": "2", "handle": "a", "text": "投稿2"},
    {"tweet_id": "3", "handle": "a", "text": "投稿3"},
    {"tweet_id": "4", "handle": "b", "text": "投稿4"},
]


def test_select_prompt_carries_candidates_and_dispersion_rule():
    f, cap = _selecting_formatter({"selections": []})
    f.select_engagements(_CANDS, 5, reply_playbook="リプ型R", quote_playbook="引用型Q")
    assert "同一アカウントからは原則1件" in cap["system"]
    assert "リプ型R" in cap["system"] and "引用型Q" in cap["system"]
    assert "投稿4" in cap["user"]  # 候補一覧がuserプロンプトに乗る
    assert cap["schema"] is Formatter._SELECT_SCHEMA


def test_select_validates_results():
    """不正ID破棄・140字超破棄・kind正規化・tweet_id重複は先勝ち。"""
    f, _ = _selecting_formatter({"selections": [
        {"tweet_id": "99", "kind": "reply", "text": "候補に無いID", "reason": "r"},
        {"tweet_id": "1", "kind": "変な型", "text": "本文A", "reason": "r"},
        {"tweet_id": "1", "kind": "quote", "text": "重複は捨てる", "reason": "r"},
        {"tweet_id": "2", "kind": "reply", "text": "あ" * 141, "reason": "r"},
        {"tweet_id": "4", "kind": "quote", "text": "本文B", "reason": "r"},
    ]})
    out = f.select_engagements(_CANDS, 10)
    assert [(s["tweet_id"], s["kind"], s["text"]) for s in out] == [
        ("1", "reply", "本文A"),  # kind不正はreplyに正規化
        ("4", "quote", "本文B"),
    ]
    assert out[0]["candidate"] == _CANDS[0]  # 元候補dictを保持(Draft作成材料)


def test_select_caps_same_author_at_two():
    """同一アカウントは最大2件までコード側でも保証する(分散の再発防止)。"""
    f, _ = _selecting_formatter({"selections": [
        {"tweet_id": "1", "kind": "reply", "text": "A1", "reason": "r"},
        {"tweet_id": "2", "kind": "reply", "text": "A2", "reason": "r"},
        {"tweet_id": "3", "kind": "reply", "text": "A3は超過", "reason": "r"},
        {"tweet_id": "4", "kind": "reply", "text": "B1", "reason": "r"},
    ]})
    out = f.select_engagements(_CANDS, 10)
    assert [s["tweet_id"] for s in out] == ["1", "2", "4"]


def test_select_uses_long_timeout():
    """バッチ選定(9万トークン超)は既定240秒を超えるため、専用の長いタイムアウトでCLIを呼ぶ。"""
    f, cap = _selecting_formatter({"selections": []})
    f.select_engagements(_CANDS, 2)
    assert cap["timeout"] == f.settings.claude_cli_select_timeout_seconds
    assert cap["timeout"] > f.settings.claude_cli_timeout_seconds


def test_select_respects_max_n():
    f, _ = _selecting_formatter({"selections": [
        {"tweet_id": "1", "kind": "reply", "text": "A1", "reason": "r"},
        {"tweet_id": "4", "kind": "reply", "text": "B1", "reason": "r"},
    ]})
    assert len(f.select_engagements(_CANDS, 1)) == 1
    assert f.select_engagements(_CANDS, 0) == []
    assert f.select_engagements([], 5) == []
