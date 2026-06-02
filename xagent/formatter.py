"""整形エンジン(Claude)。雑なテキストを「本人のノウハウ口調」のX投稿へ。

設計:
- LLM呼び出しは `complete(system, user) -> str` という関数に抽象化し、依存注入できる。
  既定は Anthropic SDK。テストはフェイクを注入してロジックを検証する。
- 折りたたみ閾値(加重280)に収めるのを既定とし、長文(allow_long)は明示時のみ。
- スレッド分割は text.split_into_thread に委譲。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .config import Settings, get_settings
from .text import FOLD_THRESHOLD_WEIGHTED, exceeds_fold, split_into_thread, weighted_length

CompleteFn = Callable[[str, str], str]

_MAX_TOKENS = 1500


@dataclass
class FormatResult:
    segments: list[str]
    folded: bool
    weighted_total: int = 0
    note: str = ""


def _style_block(style_guide: str, examples: list[str] | None) -> str:
    parts = []
    if style_guide.strip():
        parts.append(f"## 口調・スタイルガイド\n{style_guide.strip()}")
    if examples:
        joined = "\n".join(f"- {e}" for e in examples[:10])
        parts.append(f"## 本人の過去投稿(口調の参考。内容は流用しない)\n{joined}")
    return "\n\n".join(parts)


def _guide_with_playbook(playbook: str, style_guide: str, examples: list[str] | None) -> str:
    """型(playbook)＋口調ガイド を1ブロックにまとめる(リプ/引用の整形用)。"""
    parts = []
    if playbook.strip():
        parts.append(f"## 使う型(この型・狙いに沿って書く)\n{playbook.strip()}")
    sb = _style_block(style_guide, examples)
    if sb:
        parts.append(sb)
    return "\n\n".join(parts)


class Formatter:
    def __init__(
        self, settings: Settings | None = None, complete: CompleteFn | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self._complete = complete or self._anthropic_complete
        # この整形器インスタンスで消費した Claude トークン(コスト記録用に蓄積)
        self.usage_input = 0
        self.usage_output = 0

    def complete(self, system: str, user: str) -> str:
        """整形以外(プロファイル抽出等)でもLLMを使えるよう公開する。"""
        return self._complete(system, user)

    def _anthropic_complete(self, system: str, user: str) -> str:
        import anthropic

        if not self.settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY が未設定です。.env を設定してください。"
            )
        client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
        msg = client.messages.create(
            model=self.settings.claude_model,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        usage = getattr(msg, "usage", None)
        if usage is not None:
            self.usage_input += int(getattr(usage, "input_tokens", 0) or 0)
            self.usage_output += int(getattr(usage, "output_tokens", 0) or 0)
        return "".join(
            getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text"
        ).strip()

    def _post_system(
        self,
        style_guide: str,
        allow_long: bool,
        emulate_profile_text: str,
        emulate_examples: list[str] | None,
        playbook: str = "",
    ) -> str:
        length_rule = (
            "1ツイートは必ず140字(日本語の字数)以内に収める。改行・記号・スペースも字数に数える。"
            "140字を1字でも超えてはいけない。収まらなければ内容を削って言い切る。スレッドにしない。"
            if not allow_long
            else "フックとして長文も可。冒頭で続きを読みたくなる書き出しにする。"
        )
        blocks = []
        if playbook.strip():
            blocks.append(f"## 使う型(このフォーマット・狙いに沿って書く)\n{playbook.strip()}")
        if style_guide.strip():
            blocks.append(f"## 口調・スタイルガイド(常時適用)\n{style_guide.strip()}")
        if emulate_profile_text.strip():
            blocks.append(
                f"## 真似る相手の特徴(この口調・型に寄せる)\n{emulate_profile_text.strip()}"
            )
        if emulate_examples:
            joined = "\n".join(f"- {e}" for e in emulate_examples[:10])
            blocks.append(f"## 真似る相手の投稿例(口調の参考。内容は流用しない)\n{joined}")
        guide = "\n\n".join(blocks)
        return f"""あなたは日本語Xアカウントの運用アシスタント。ユーザーが投げた雑なメモを、指定の口調を保った価値ある投稿に整形する。

ルール:
- {length_rule}
- 説明的・冗長を避け、一文目で引きを作る。ノウハウ発信として具体で言い切る。
- ハッシュタグは多用しない(必要なら末尾に1個まで)。絵文字も控えめ。
- 与えられたメモの範囲を脚色しすぎない。
- 出力は投稿本文のみ。前置き・説明・引用符・コードブロックは付けない。

{guide}""".strip()

    # --- 投稿(自分の発信) ---
    def format_post(
        self,
        source_text: str,
        style_guide: str = "",
        allow_long: bool = False,
        emulate_profile_text: str = "",
        emulate_examples: list[str] | None = None,
        playbook: str = "",
    ) -> FormatResult:
        system = self._post_system(
            style_guide, allow_long, emulate_profile_text, emulate_examples, playbook
        )
        text = self._complete(system, source_text).strip()
        segments = [text] if allow_long else split_into_thread(text)
        return FormatResult(
            segments=segments,
            folded=exceeds_fold(text),
            weighted_total=weighted_length(text),
        )

    def format_variations(
        self,
        source_text: str,
        n: int = 3,
        style_guide: str = "",
        allow_long: bool = False,
        emulate_profile_text: str = "",
        emulate_examples: list[str] | None = None,
        playbook: str = "",
    ) -> list[FormatResult]:
        """1つのメモから言い回し違いをn案生成する。下書きを多く出すため。"""
        n = max(1, min(n, 5))
        base = self._post_system(
            style_guide, allow_long, emulate_profile_text, emulate_examples, playbook
        )
        system = (
            base
            + f"\n\n## 追加指示\n上記メモから、切り口・書き出しの異なる案を{n}個作る。"
            + "各案は「---」だけの行で区切る。番号や見出しは付けない。"
        )
        raw = self._complete(system, source_text).strip()
        parts = [p.strip() for p in raw.split("\n---") if p.strip()]
        # フォールバック: 区切れなければ全体を1案扱い
        texts = [p.lstrip("-").strip() for p in parts] or [raw]
        results = []
        for text in texts[:n]:
            segments = [text] if allow_long else split_into_thread(text)
            results.append(
                FormatResult(segments, exceeds_fold(text), weighted_length(text))
            )
        return results

    # --- リプライ案 ---
    def generate_reply(
        self,
        target_text: str,
        target_handle: str = "",
        style_guide: str = "",
        examples: list[str] | None = None,
        playbook: str = "",
    ) -> FormatResult:
        system = f"""あなたは日本語Xアカウントの運用アシスタント。相手の投稿に対する自然で価値あるリプライ案を1つ作る。

ルール:
- 必ず140字(日本語の字数)以内。1字も超えない。短く、相手に響く具体を添える。
- 媚びすぎ・定型の褒めだけは避け、会話が続く一言にする。本人の口調を保つ。
- 出力はリプライ本文のみ。

{_guide_with_playbook(playbook, style_guide, examples)}""".strip()
        user = f"相手(@{target_handle})の投稿:\n{target_text}"
        text = self._complete(system, user).strip()
        return FormatResult([text], exceeds_fold(text), weighted_length(text))

    # --- 引用RT案 ---
    def generate_quote(
        self,
        target_text: str,
        target_handle: str = "",
        style_guide: str = "",
        examples: list[str] | None = None,
        playbook: str = "",
    ) -> FormatResult:
        system = f"""あなたは日本語Xアカウントの運用アシスタント。相手の投稿を引用RTする際の本文案を1つ作る。

ルール:
- 必ず140字(日本語の字数)以内。1字も超えない。自分の視点・ノウハウを足して価値を上乗せする。
- 単なる感想で終わらせず、フォロワーに学びがある切り口にする。本人の口調を保つ。
- 出力は引用本文のみ。

{_guide_with_playbook(playbook, style_guide, examples)}""".strip()
        user = f"引用する相手(@{target_handle})の投稿:\n{target_text}"
        text = self._complete(system, user).strip()
        return FormatResult([text], exceeds_fold(text), weighted_length(text))
