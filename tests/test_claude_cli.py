"""claude_cli.run_claude のテスト。subprocess をモックし、コマンド組み立てと
出力JSONのパース・エラー処理を検証する(実CLIは呼ばない)。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from xagent.claude_cli import run_claude
from xagent.config import Settings


def _settings(**kw):
    return Settings(_env_file=None, claude_cli_path="/fake/claude", **kw)


def _ok_payload(**over):
    payload = {
        "result": "ok",
        "is_error": False,
        "usage": {
            "input_tokens": 10,
            "cache_creation_input_tokens": 5,
            "cache_read_input_tokens": 1,
            "output_tokens": 3,
        },
        "total_cost_usd": 0.01,
    }
    payload.update(over)
    return payload


def _patch_run(monkeypatch, payload=None, returncode=0, stderr=""):
    calls = {}

    def fake_run(cmd, input=None, capture_output=None, text=None, env=None,
                 cwd=None, timeout=None):
        calls.update(cmd=cmd, input=input, env=env, cwd=cwd, timeout=timeout)
        return SimpleNamespace(
            returncode=returncode,
            stdout=json.dumps(payload if payload is not None else _ok_payload()),
            stderr=stderr,
        )

    monkeypatch.setattr("xagent.claude_cli.subprocess.run", fake_run)
    return calls


def test_builds_headless_command_without_bare(monkeypatch):
    """--bare は使わない(OAuth/subscription認証が読めなくなる)。user は stdin で渡す。"""
    calls = _patch_run(monkeypatch)
    res = run_claude("システム指示", "ユーザー入力", _settings())
    cmd = calls["cmd"]
    assert cmd[0] == "/fake/claude"
    assert "--bare" not in cmd
    assert "-p" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4-8"
    assert cmd[cmd.index("--system-prompt") + 1] == "システム指示"
    assert cmd[cmd.index("--tools") + 1] == ""  # 全ツール無効
    assert "--no-session-persistence" in cmd
    assert calls["input"] == "ユーザー入力"
    assert calls["timeout"] == 240  # 既定タイムアウト
    assert res.text == "ok"


def test_strips_api_key_from_env(monkeypatch):
    """env から APIキーを除去し subscription(OAuth)認証を強制する。HOME は維持。"""
    calls = _patch_run(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    run_claude("s", "u", _settings())
    assert "ANTHROPIC_API_KEY" not in calls["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in calls["env"]
    assert "HOME" in calls["env"]


def test_parses_usage_including_cache_tokens(monkeypatch):
    """input は cache 書込/読出も合算する(名目コスト記録用)。"""
    _patch_run(monkeypatch)
    res = run_claude("s", "u", _settings())
    assert res.input_tokens == 16  # 10 + 5 + 1
    assert res.output_tokens == 3
    assert res.cost_usd == 0.01


def test_json_schema_flag_and_structured_output(monkeypatch):
    calls = _patch_run(
        monkeypatch, payload=_ok_payload(structured_output={"selections": []})
    )
    schema = {"type": "object"}
    res = run_claude("s", "u", _settings(), json_schema=schema)
    cmd = calls["cmd"]
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == schema
    assert res.structured == {"selections": []}


def test_nonzero_exit_raises(monkeypatch):
    _patch_run(monkeypatch, returncode=1, stderr="login required")
    with pytest.raises(RuntimeError, match="login required"):
        run_claude("s", "u", _settings())


def test_is_error_payload_raises(monkeypatch):
    _patch_run(monkeypatch, payload=_ok_payload(is_error=True, result="rate limited"))
    with pytest.raises(RuntimeError, match="rate limited"):
        run_claude("s", "u", _settings())
