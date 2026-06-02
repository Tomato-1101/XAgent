"""指令パーサ(commands.parse_command)のテスト。LLMはフェイクで注入する。"""

from __future__ import annotations

import json

from xagent.commands import extract_tweet_ref, has_command_markers, parse_command


def _fake_complete(payload: dict):
    """渡した dict を JSON 文字列で返す complete(system, user) を作る。"""

    def complete(system: str, user: str) -> str:
        return json.dumps(payload, ensure_ascii=False)

    return complete


def test_extract_tweet_ref_x_com():
    ref = extract_tweet_ref("見て https://x.com/BitoFCE/status/1234567890 これ")
    assert ref is not None
    tweet_id, handle, url = ref
    assert tweet_id == "1234567890"
    assert handle == "BitoFCE"


def test_extract_tweet_ref_twitter_and_i_web():
    assert extract_tweet_ref("https://twitter.com/foo/status/55")[0] == "55"
    # /i/web/status/ は handle 不明として None
    _id, handle, _url = extract_tweet_ref("https://x.com/i/web/status/99")
    assert _id == "99"
    assert handle is None


def test_extract_tweet_ref_none():
    assert extract_tweet_ref("ただのテキスト https://example.com/a") is None


def test_has_command_markers():
    assert has_command_markers("https://x.com/a/status/1 をリポスト")
    assert has_command_markers("これそのまま投稿して")
    assert not has_command_markers("普通のメモです")


def test_parse_quote_with_url():
    text = "https://x.com/BitoFCE/status/123 をリポストして、以下の文で投稿: 最高の知見"
    p = parse_command(
        _fake_complete({"action": "quote", "body": "最高の知見", "raw": False, "note": "引用"}),
        text,
    )
    assert p.action == "quote"
    assert p.target_tweet_id == "123"
    assert p.target_handle == "BitoFCE"
    assert "最高の知見" in p.body
    assert p.raw is False


def test_parse_quote_strips_url_from_body():
    text = "https://x.com/a/status/9 引用して"
    # LLMが誤って body に URL を残しても除去する
    p = parse_command(
        _fake_complete({"action": "quote", "body": "https://x.com/a/status/9 コメント", "raw": False}),
        text,
    )
    assert "status/9" not in p.body
    assert "コメント" in p.body


def test_parse_quote_without_url_downgrades_to_post():
    # URL が無いのに quote 判定 → 通常投稿へ降格
    p = parse_command(
        _fake_complete({"action": "quote", "body": "本文", "raw": False}),
        "リポストしたい本文",
    )
    assert p.action == "post"
    assert p.target_tweet_id is None


def test_parse_raw_from_hint_even_if_llm_false():
    # 入力に「そのまま」があれば LLM が raw=false でも raw=true に補正
    p = parse_command(
        _fake_complete({"action": "post", "body": "原文ママの本文", "raw": False}),
        "これをそのまま投稿して: 原文ママの本文",
    )
    assert p.raw is True


def test_parse_plain_post():
    p = parse_command(
        _fake_complete({"action": "post", "body": "整形対象のメモ", "raw": False}),
        "整形対象のメモ",
    )
    assert p.action == "post"
    assert p.target_tweet_id is None
    assert p.raw is False


def test_parse_invalid_json_falls_back_to_post():
    def bad_complete(system: str, user: str) -> str:
        return "これはJSONではありません"

    p = parse_command(bad_complete, "適当なメモ")
    assert p.action == "post"
    assert p.body == "適当なメモ"
