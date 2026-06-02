"""自由文の「指令」パーサ。

入力欄に書かれた自由文(例:「このURLをリポストして、以下の文で投稿して: …」)を
解析し、X操作の意図(引用RT / 通常投稿 / そのまま投稿)と本文を構造化する。

方針:
- 「リポスト」は引用リツイート(quote)として扱う(素のRTは対象外。ユーザー方針)。
- ツイートURLの抽出は Python 側で確定し、LLM の誤りに依存しない。
- LLM は意図判定と本文(指示語を除いた投稿文)の抽出にだけ使う。
- 解析のみを行い下書きは作らない(UI 側で確認してから作成する)。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .formatter import CompleteFn

# x.com / twitter.com のツイートURL → (handle, tweet_id)
# 形式: x.com/<handle>/status/<id> または x.com/i/web/status/<id>
_TWEET_URL_RE = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/"
    r"(?:([A-Za-z0-9_]{1,15})/status(?:es)?|i/web/status)/(\d+)",
    re.IGNORECASE,
)

# 「整形せずそのまま投稿」を表す指示語
_RAW_HINT_RE = re.compile(
    r"(そのまま|加工しないで|加工せず|整形しないで|整形せず|原文(?:のまま|ママ)?|このまま投稿)"
)


@dataclass
class CommandParse:
    action: str  # "quote" | "post"
    target_url: str | None
    target_tweet_id: str | None
    target_handle: str | None
    body: str
    raw: bool
    note: str


def extract_tweet_ref(text: str) -> tuple[str, str | None, str] | None:
    """テキスト中の最初のツイートURLを探し (tweet_id, handle, url) を返す。"""
    m = _TWEET_URL_RE.search(text)
    if not m:
        return None
    handle: str | None = m.group(1)  # i/web/status 形式では None
    if handle and handle.lower() in {"i", "www", "mobile"}:
        handle = None
    return m.group(2), handle, m.group(0)


def has_command_markers(text: str) -> bool:
    """LLM 解析に回すべき入力か(ツイートURL or そのまま指示)を安価に判定する。"""
    return extract_tweet_ref(text) is not None or _RAW_HINT_RE.search(text) is not None


_SYSTEM = """あなたはX運用ツールの指示パーサ。ユーザーが入力欄に書いた自由文を解析し、JSONだけを返す。

判定ルール:
- 入力にツイートURL(x.com/.../status/数字 等)が含まれ、かつ「リポスト/引用/RT/これに絡めて」等そのツイートに絡める意図があれば action="quote"。
- それ以外(URLが無い、または単に本文を投稿したいだけ)は action="post"。
- 「そのまま」「整形しないで」「加工なし」「原文ママ」「このまま投稿」等、整形を拒否する指示があれば raw=true。無ければ false。
- body = 実際に投稿する本文。ツイートURLや「リポストして」「以下の文で投稿して」等の指示語・命令文は取り除き、投稿したい文だけにする。指示が無く本文だけならそのまま使う。

出力(JSONオブジェクトのみ。前置き・説明・コードブロックは禁止):
{"action":"quote|post","target_url":"URL文字列|null","body":"投稿本文","raw":true|false,"note":"検出内容を日本語で一言"}"""


def _extract_json(s: str) -> dict:
    """LLM 出力から最初の JSON オブジェクトを取り出す(コードフェンス混入にも耐える)。"""
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"JSONを取り出せませんでした: {s[:200]}")
    return json.loads(s[start : end + 1])


def parse_command(complete: CompleteFn, text: str) -> CommandParse:
    """自由文を解析して CommandParse を返す(LLM 1回 + Python 検証)。

    LLM の意図判定/本文抽出は使うが、ツイートURL の有無は Python 側で確定する。
    解析に失敗しても安全側(全文を通常投稿の本文)へフォールバックする。
    """
    has_raw_hint = bool(_RAW_HINT_RE.search(text))
    try:
        data = _extract_json(complete(_SYSTEM, text))
    except (ValueError, json.JSONDecodeError):
        data = {
            "action": "post",
            "body": text,
            "raw": has_raw_hint,
            "note": "解析に失敗したため通常投稿として扱います。",
        }

    action = "quote" if str(data.get("action", "")).lower() == "quote" else "post"
    body = (data.get("body") or "").strip() or text.strip()
    raw = bool(data.get("raw")) or has_raw_hint
    note = (data.get("note") or "").strip()

    ref = extract_tweet_ref(text)
    if ref is None:
        if action == "quote":
            note = (note + " 引用にはツイートURLが必要なため通常投稿にしました。").strip()
            action = "post"
        return CommandParse("post", None, None, None, body, raw, note or "通常投稿として整形します。")

    tweet_id, handle, url = ref
    if action != "quote":
        # URL はあるが引用意図ではない → URL を含む通常投稿として扱う(本文は編集可)
        return CommandParse(
            "post", url, tweet_id, handle, body, raw,
            note or "ツイートURLを含む通常投稿として扱います。",
        )

    # 引用本文に対象URLが残っていたら除去(URLは引用先として別添するため)
    body = _TWEET_URL_RE.sub("", body).strip()
    return CommandParse(
        "quote", url, tweet_id, handle, body, raw,
        note or "引用リポストとして整形します。",
    )
