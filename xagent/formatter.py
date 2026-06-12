"""整形エンジン(Claude)。雑なテキストを「本人のノウハウ口調」のX投稿へ。

設計:
- LLM呼び出しは `complete(system, user) -> str` という関数に抽象化し、依存注入できる。
  既定は Claude Code CLI のヘッドレス使い捨てセッション(claude_cli.run_claude、
  サブスクリプション課金)。テストはフェイクを注入してロジックを検証する。
- 折りたたみ閾値(加重280)に収めるのを既定とし、長文(allow_long)は明示時のみ。
- スレッド分割は text.split_into_thread に委譲。
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from typing import Callable

from .claude_cli import run_claude
from .config import Settings, get_settings
from .text import FOLD_THRESHOLD_WEIGHTED, exceeds_fold, split_into_thread, weighted_length

# 返信が毎回同じ長さに寄らないよう、生成ごとに目安の長さ帯をランダムに選ぶ。
# (lo, hi, 表現) — 内容を優先しつつ、この幅を狙う。上限の140字は別途厳守。
_REPLY_LENGTH_BANDS = [
    (15, 40, "ごく短い一言で言い切る"),
    (40, 80, "中くらいの長さで一言添える"),
    (80, 130, "しっかり具体を添えて書く"),
]


def _reply_length_hint() -> str:
    lo, hi, how = random.choice(_REPLY_LENGTH_BANDS)
    return (
        f"今回は{how}（目安 {lo}〜{hi} 字）。毎回同じ長さにせず幅を持たせる。"
        "ただし内容を最優先し、字数合わせのために不自然に伸ばしたり削ったりしない。"
    )


# モデルが稀に本文末尾へ付ける自己解説トレーラー(例: "\n---\n**96字**／型：…で返しています。")を落とす。
# プロンプトで「本文のみ」と指示しても Opus が間欠的に付けるため、生成後の保険として除去する。
# markdown 水平線(--- 等)の後ろが「字数・型・構成」の自己言及のときだけ切り、通常の本文は壊さない。
_HR_RE = re.compile(r"\n[ \t]*[-—–_*]{3,}[ \t]*(?:\n|$)")
_META_TRAILER_RE = re.compile(r"\d+\s*字|型[：:]|構成|狙い")


def _strip_reasoning_trailer(text: str) -> str:
    m = _HR_RE.search(text)
    if not m:
        return text
    head, tail = text[: m.start()], text[m.end() :]
    if head.strip() and _META_TRAILER_RE.search(tail):
        return head.rstrip()
    return text


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
        self._complete = complete or self._claude_cli_complete
        # この整形器インスタンスで消費した Claude トークン(コスト記録用に蓄積)
        self.usage_input = 0
        self.usage_output = 0

    def complete(self, system: str, user: str) -> str:
        """整形以外(プロファイル抽出等)でもLLMを使えるよう公開する。"""
        return self._complete(system, user)

    def _claude_cli_complete(self, system: str, user: str) -> str:
        res = run_claude(system, user, self.settings)
        self.usage_input += res.input_tokens
        self.usage_output += res.output_tokens
        return res.text

    def _run_structured(
        self, system: str, user: str, schema: dict, timeout: int | None = None
    ) -> dict | None:
        """JSONスキーマ強制で1回実行し、構造化出力(dict)を返す。テストで差し替え可能。"""
        res = run_claude(system, user, self.settings, json_schema=schema, timeout=timeout)
        self.usage_input += res.input_tokens
        self.usage_output += res.output_tokens
        if res.structured is not None:
            return res.structured
        try:
            return json.loads(res.text)
        except (json.JSONDecodeError, TypeError):
            return None

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
        system = f"""あなたは日本語Xアカウントの運用アシスタント。相手の投稿への自然なリプライ案を1つ作る。

ルール:
- 必ず140字(日本語の字数)以内。1字も超えない。
- 具体や知識は無理に足さない。素の共感・面白がり・一言ツッコミでよい(「あ、そうですよね」「これ面白い」「わかる」級)。短く本音っぽいほど伸びる。
- {_reply_length_hint()}
- 媚びすぎ・定型の褒めは避ける。が、賢く見せようと説明的・解説的になるのはもっと避ける。本人の口調を保つ。
- 出力はリプライ本文のみ。字数・型・狙いなどの自己解説や「---」区切りの注記を一切付けない。

{_guide_with_playbook(playbook, style_guide, examples)}""".strip()
        user = f"相手(@{target_handle})の投稿:\n{target_text}"
        text = _strip_reasoning_trailer(self._complete(system, user).strip())
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
- 必ず140字(日本語の字数)以内。1字も超えない。
- 学びは必須でない。素直な反応＋自分の一言でよい(「これ面白い」「わかる」級)。賢く解説しようとして説明的になるより本音の短さが伸びる。
- {_reply_length_hint()}
- 本人の口調を保つ。出力は引用本文のみ。字数・型・狙いなどの自己解説や「---」区切りの注記を一切付けない。

{_guide_with_playbook(playbook, style_guide, examples)}""".strip()
        user = f"引用する相手(@{target_handle})の投稿:\n{target_text}"
        text = _strip_reasoning_trailer(self._complete(system, user).strip())
        return FormatResult([text], exceeds_fold(text), weighted_length(text))

    # --- 絡み候補のバッチ選定(分散・reply/quote判断・本文生成まで1回で行う) ---
    _SELECT_SCHEMA = {
        "type": "object",
        "properties": {
            "selections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tweet_id": {"type": "string"},
                        "kind": {"type": "string", "enum": ["reply", "quote"]},
                        "text": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["tweet_id", "kind", "text", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["selections"],
        "additionalProperties": False,
    }

    # 同一アカウントからの選定上限(コード側の保証。プロンプトでも「原則1件」を指示する)
    _SELECT_MAX_PER_AUTHOR = 2

    def select_engagements(
        self,
        candidates: list[dict],
        max_n: int,
        style_guide: str = "",
        examples: list[str] | None = None,
        reply_playbook: str = "",
        quote_playbook: str = "",
    ) -> list[dict]:
        """直近24hの候補投稿一覧から、絡む価値が高いものを最大 max_n 件まとめて選定する。

        1回のバッチ判断で「どの投稿に絡むか(分散)」「reply/quote どちらか」「本文」まで
        生成する。従来の直列スキャン＋投稿ごと判断は、先頭アカウントがバジェットを使い切り
        同一アカウント偏りを生んだため、全候補を俯瞰するこの方式に置き換えた。
        返り値は {tweet_id, kind, text, reason, candidate} のリスト(candidate は元候補dict)。
        """
        if not candidates or max_n <= 0:
            return []
        blocks = []
        if reply_playbook.strip():
            blocks.append(f"## リプライの型(reply の本文はこれに沿う)\n{reply_playbook.strip()}")
        if quote_playbook.strip():
            blocks.append(f"## 引用RTの型(quote の本文はこれに沿う)\n{quote_playbook.strip()}")
        sb = _style_block(style_guide, examples)
        if sb:
            blocks.append(sb)
        guide = "\n\n".join(blocks)
        system = f"""あなたは日本語Xアカウントの運用アシスタント。直近24時間の候補投稿一覧から「絡む価値が高い投稿」を最大{max_n}件選び、それぞれにリプライ(reply)か引用RT(quote)かを判断し、本文まで作る。

選定ルール:
- 自分が本当に返信したいと思う投稿だけ選ぶ。無理に{max_n}件埋めない(良い候補が少なければ少なく返す)。
- 特定のアカウントに偏らせない。同一アカウントからは原則1件、多くても2件まで。
- 会話が生まれそうな投稿、伸び始めの投稿、いいね/RT/閲覧数(view_count)が付き始めている投稿を優先する。
- note が付いた候補はその背景・狙いを優先的に考慮する。

reply / quote の判断基準:
- reply: 相手のスレッドに短い共感・反応で割り込んで露出を取る方が自然な投稿(会話的・あるある・伸び始めのバズへの相乗り)。これが主力・デフォルト。迷ったら reply。
- quote: 自分のフォロワーに広める価値があり、自分の視点や一次情報を一段添えたい投稿(ニュース/知見/主張の紹介＋自分の意見)。

本文ルール:
- 必ず140字(日本語の字数)以内。1字も超えない。
- 具体や知識は無理に足さない。素の共感・面白がり・一言ツッコミでよい(「あ、そうですよね」「これ面白い」「わかる」級)。短く本音っぽいほど伸びる。
- 媚びすぎ・定型の褒めは避ける。賢く見せようと説明的・解説的になるのはもっと避ける。本人の口調を保つ。
- 案ごとに長さをばらす(ごく短い一言〜しっかり具体まで)。全案を同じ長さにしない。
- reason には選んだ理由と型の判断根拠を日本語で簡潔に書く。

{guide}""".strip()
        lines = []
        for c in candidates:
            item = {
                "tweet_id": str(c.get("tweet_id", "")),
                "handle": c.get("handle") or "",
                "text": c.get("text", ""),
                "created_at": str(c.get("created_at") or ""),
                "like_count": c.get("like_count", 0),
                "retweet_count": c.get("retweet_count", 0),
                "view_count": c.get("view_count", 0),
            }
            if c.get("note"):
                item["note"] = c["note"]
            lines.append(json.dumps(item, ensure_ascii=False))
        user = "候補投稿一覧(1行1件のJSON):\n" + "\n".join(lines)
        # バッチ選定は9万トークン超の入力で既定240秒を超えることがあるため専用の長いタイムアウト
        data = self._run_structured(
            system, user, self._SELECT_SCHEMA,
            timeout=self.settings.claude_cli_select_timeout_seconds,
        )
        by_id = {str(c.get("tweet_id", "")): c for c in candidates}
        out: list[dict] = []
        seen_ids: set[str] = set()
        author_count: dict[str, int] = {}
        for s in (data or {}).get("selections", []):
            tid = str(s.get("tweet_id", ""))
            cand = by_id.get(tid)
            if cand is None or tid in seen_ids:
                continue
            text = str(s.get("text", "")).strip()
            if not text or len(text) > 140:
                continue
            kind = str(s.get("kind", "")).strip().lower()
            if kind not in ("reply", "quote"):
                kind = "reply"
            author = str(cand.get("handle") or "")
            if author_count.get(author, 0) >= self._SELECT_MAX_PER_AUTHOR:
                continue
            author_count[author] = author_count.get(author, 0) + 1
            seen_ids.add(tid)
            out.append(
                {
                    "tweet_id": tid,
                    "kind": kind,
                    "text": text,
                    "reason": str(s.get("reason", "")).strip(),
                    "candidate": cand,
                }
            )
            if len(out) >= max_n:
                break
        return out

    # --- ニュース速報のバッチ生成(記事選定・型選択・本文生成まで1回で行う) ---
    _NEWS_SCHEMA = {
        "type": "object",
        "properties": {
            "posts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "news_index": {"type": "integer"},
                        "format": {"type": "string", "enum": ["post", "quote"]},
                        "text": {"type": "string"},
                        "source_url": {"type": ["string", "null"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["news_index", "format", "text", "source_url", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["posts"],
        "additionalProperties": False,
    }

    def generate_news_posts(
        self,
        items: list[dict],
        max_n: int,
        style_guide: str = "",
        examples: list[str] | None = None,
        playbook: str = "",
    ) -> list[dict]:
        """新着ニュース一覧から速報価値の高い記事を最大 max_n 件選び、速報投稿の本文まで作る。

        1回のバッチ判断で「どの記事を投稿するか」「通常投稿(post)か元ポストの引用RT(quote)か」
        「型(N1〜N5)の適用と本文」まで生成する(絡みの select_engagements と同じ構造)。
        items は news.py が渡す {index, genre, importance, title, summary, detail,
        source_url, source_tweet(url/text/views), top_view_count} のリスト。
        返り値は {news_index, format, text, source_url, reason, item} のリスト。
        """
        if not items or max_n <= 0:
            return []
        blocks = []
        if playbook.strip():
            blocks.append(f"## 速報の型(本文はこれに沿う)\n{playbook.strip()}")
        sb = _style_block(style_guide, examples)
        if sb:
            blocks.append(sb)
        guide = "\n\n".join(blocks)
        system = f"""あなたは日本語Xアカウントの運用アシスタント。新着ニュース一覧から「速報として投稿する価値が高い記事」を最大{max_n}件選び、それぞれ速報投稿の本文を作る。

選定ルール:
- 速報価値(新しさ・大きさ・読者への影響)が高い記事だけ選ぶ。無理に{max_n}件埋めない。
- 同じ話題の重複記事は1つにまとめる(最も情報量の多い記事を採用)。
- top_view_count(元ポストの閲覧数)が大きい記事は関心が実証されている=優先。

format の判断基準:
- post: 通常投稿。これが主力・デフォルト。
- quote: source_tweet(一次情報の元ポスト)があり、それを引用RTで見せる方が伝わる記事
  (公式発表のポスト・画像/動画付きの一次情報など)。引用なら元ポストの画像も表示される。

本文ルール:
- 速報の型(N1〜N5)から記事の性質に合うものを1つ選び、その構成・トーンで書く。
- 事実は与えられた記事の範囲のみ。数字・固有名詞・発言を創作しない。
- ソースURLは本文に入れない(リーチ減点)。source_url フィールドにURLを返せば
  2ツイート目(リプ欄)として投稿される。出典を添える価値がある記事だけ返す(不要なら null)。
- quote のときは引用元が見えるため、本文は元ポストの要約でなく自分の速報文にする。
- reason には選んだ理由と使った型(N1〜N5)を日本語で簡潔に書く。

{guide}""".strip()
        lines = []
        for it in items:
            row = {
                "news_index": it["index"],
                "genre": it.get("genre", ""),
                "importance": it.get("importance", ""),
                "title": it.get("title", ""),
                "summary": it.get("summary", ""),
                "detail": (it.get("detail") or "")[:600],
                "source_url": it.get("source_url"),
                "source_tweet": it.get("source_tweet"),
                "top_view_count": it.get("top_view_count", 0),
            }
            lines.append(json.dumps(row, ensure_ascii=False))
        user = "新着ニュース一覧(1行1件のJSON):\n" + "\n".join(lines)
        data = self._run_structured(
            system, user, self._NEWS_SCHEMA,
            timeout=self.settings.claude_cli_select_timeout_seconds,
        )
        by_index = {it["index"]: it for it in items}
        out: list[dict] = []
        seen: set[int] = set()
        for p in (data or {}).get("posts", []):
            idx = p.get("news_index")
            item = by_index.get(idx)
            if item is None or idx in seen:
                continue
            text = str(p.get("text", "")).strip()
            if not text:
                continue
            fmt = str(p.get("format", "")).strip().lower()
            if fmt not in ("post", "quote"):
                fmt = "post"
            if fmt == "quote" and not (item.get("source_tweet") or {}).get("url"):
                fmt = "post"  # 引用先が無いのに quote を選んだ場合は通常投稿に落とす
            url = p.get("source_url")
            seen.add(idx)
            out.append(
                {
                    "news_index": idx,
                    "format": fmt,
                    "text": text,
                    "source_url": str(url).strip() if url else None,
                    "reason": str(p.get("reason", "")).strip(),
                    "item": item,
                }
            )
            if len(out) >= max_n:
                break
        return out
