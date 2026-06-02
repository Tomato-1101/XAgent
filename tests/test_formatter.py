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
