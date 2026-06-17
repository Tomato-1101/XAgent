"""Claude Code CLI のヘッドレス使い捨てセッションで LLM 補完を行う。

Anthropic API(従量課金)を使わず、サブスクリプション(OAuth)で動く `claude -p` に置き換える。

設計上の注意:
- `--bare` は使わない。--bare は認証が ANTHROPIC_API_KEY 限定になり OAuth が読めない。
  代わりに通常モードで起動し、env から API キーを除去して subscription 認証を強制する。
- cwd は中立ディレクトリ(~/.xagent/claude-cwd)。プロジェクトの CLAUDE.md 自動読込を防ぐ。
- `--tools ""` で全ツール無効、`--no-session-persistence` で履歴を残さない。
- user プロンプトは stdin で渡す(長文・引用符・引数長制限の対策)。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Settings

_FALLBACK_CLI = "~/.local/bin/claude"
_NEUTRAL_CWD = "~/.xagent/claude-cwd"


@dataclass
class ClaudeCliResult:
    text: str
    structured: dict | None
    input_tokens: int
    output_tokens: int
    cost_usd: float


def _resolve_cli(settings: Settings) -> str:
    if settings.claude_cli_path:
        return os.path.expanduser(settings.claude_cli_path)
    found = shutil.which("claude")
    if found:
        return found
    return os.path.expanduser(_FALLBACK_CLI)


def run_claude(
    system: str,
    user: str,
    settings: Settings,
    json_schema: dict | None = None,
    timeout: int | None = None,
    allowed_tools: list[str] | None = None,
) -> ClaudeCliResult:
    """使い捨ての claude セッションを1回実行し、結果テキスト/構造化出力/usage を返す。

    allowed_tools を渡すと、その組み込みツール(例: WebSearch/WebFetch)だけを有効化し、
    -p では権限プロンプトを出せないため bypassPermissions で自律実行させる(返信案のリサーチ用)。
    有効化対象を読み取り系に限るので Bash/Edit 等は使えないまま。既定(None)は全ツール無効。
    """
    cmd = [
        _resolve_cli(settings),
        "-p",
        "--model", settings.claude_model,
        "--system-prompt", system,
    ]
    if allowed_tools:
        cmd += ["--tools", ",".join(allowed_tools), "--permission-mode", "bypassPermissions"]
    else:
        cmd += ["--tools", ""]
    cmd += [
        "--output-format", "json",
        "--no-session-persistence",
        "--setting-sources", "",
    ]
    if json_schema is not None:
        cmd += ["--json-schema", json.dumps(json_schema, ensure_ascii=False)]

    env = {
        k: v for k, v in os.environ.items()
        if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    }
    cwd = Path(os.path.expanduser(_NEUTRAL_CWD))
    cwd.mkdir(parents=True, exist_ok=True)

    timeout_s = timeout or settings.claude_cli_timeout_seconds
    try:
        proc = subprocess.run(
            cmd,
            input=user,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(cwd),
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"AI生成が{timeout_s}秒でタイムアウトしました。時間をおいて再試行してください。"
        ) from None
    if proc.returncode != 0:
        # SIGTERM/SIGKILL(143/-15/137/-9)はサーバ再起動(--reload/kickstart)に巻き込まれた合図
        if proc.returncode in (143, -15, 137, -9):
            raise RuntimeError("サーバ再起動により生成が中断されました。もう一度実行してください。")
        err = (proc.stderr or proc.stdout or "").strip()[:500]
        raise RuntimeError(f"claude CLI が失敗しました (exit={proc.returncode}): {err}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"claude CLI の出力をJSONとして解釈できません: {proc.stdout[:300]}"
        ) from e
    if payload.get("is_error"):
        raise RuntimeError(f"claude CLI がエラーを返しました: {payload.get('result', '')[:500]}")

    usage = payload.get("usage") or {}
    # 名目コスト記録用。キャッシュ書込/読出も入力トークンとして合算する。
    input_tokens = sum(
        int(usage.get(k, 0) or 0)
        for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    structured = payload.get("structured_output")
    return ClaudeCliResult(
        text=str(payload.get("result") or "").strip(),
        structured=structured if isinstance(structured, dict) else None,
        input_tokens=input_tokens,
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cost_usd=float(payload.get("total_cost_usd", 0.0) or 0.0),
    )
