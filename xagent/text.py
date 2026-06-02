"""X(Twitter)のテキスト計量とスレッド分割。

なぜ独自実装か:
- Xは文字を「加重(weighted)」で数える。Latin等は1、CJK(日本語)や絵文字は2。
  標準ツイートの上限は加重280 = 日本語なら約140文字。
- タイムラインで「さらに表示(Show more)」に折りたたまれない長さに収めたい、という
  要件があるため「折りたたみ閾値」を判定できる必要がある。標準ツイート容量(加重280)に
  収まる投稿はタイムラインで全文表示されるので、これを既定の折りたたみ閾値として扱う。
  ※厳密な閾値は実装中に実測で確定する(【要実測】)。280は安全側の既定値。

参考: twitter-text の重み設定 (defaultWeight=200, scale=100, 上限280)。
"""

from __future__ import annotations

import re

# 加重1(=重み100)になるコードポイント範囲。これ以外は加重2(=重み200)。
_WEIGHT_1_RANGES: tuple[tuple[int, int], ...] = (
    (0x0000, 0x10FF),  # 基本ラテン〜各種(ラテン拡張/ギリシャ/キリル/ヘブライ/アラビア等)
    (0x2000, 0x200D),  # 一般句読点の一部
    (0x2010, 0x201F),  # ハイフン・引用符類
    (0x2032, 0x2037),  # プライム類
)

#: 標準ツイートの加重上限。X側の上限検証/表示の参考(Latin=1, CJK=2)。
POST_LIMIT_WEIGHTED = 280

#: 加重ベースの折りたたみ閾値(=標準ツイート容量)。
FOLD_THRESHOLD_WEIGHTED = 280

#: 既定の1ツイート上限(字数=コードポイント数)。長文指定が無い限りこの範囲に収める。
#: 「さらに表示」に折りたたまれないよう、日本語の字数で140を上限とする。
POST_LIMIT_CHARS = 140

#: タイムラインで「さらに表示」に折りたたまれない既定の閾値(字数)。
FOLD_THRESHOLD_CHARS = 140


def char_weight(codepoint: int) -> int:
    """1文字の加重(1 または 2)を返す。"""
    for start, end in _WEIGHT_1_RANGES:
        if start <= codepoint <= end:
            return 1
    return 2


def weighted_length(text: str) -> int:
    """Xの加重ルールでの文字数(Latin=1, CJK/絵文字=2)。X側の上限検証や表示の参考に使う。"""
    return sum(char_weight(ord(ch)) for ch in text)


def char_length(text: str) -> int:
    """字数(コードポイント数)。Xの「○字」表示に合わせた既定の計量。"""
    return len(text)


def exceeds_fold(text: str, threshold: int = FOLD_THRESHOLD_CHARS) -> bool:
    """タイムラインで「さらに表示」に折りたたまれる字数かどうか(既定140字)。"""
    return char_length(text) > threshold


# 文末(日本語・英語)の直後で分割するための正規表現。区切り文字は前のセグメントに残す。
_SENTENCE_SPLIT = re.compile(r"(?<=[。．！？!?\n])")


def _hard_split(s: str, limit: int) -> list[str]:
    """1文が上限(字数)を超える場合に、文字境界で強制分割する。"""
    return [s[i : i + limit] for i in range(0, len(s), limit)] if s else []


def split_into_thread(
    text: str, limit: int = POST_LIMIT_CHARS, add_numbering: bool = False
) -> list[str]:
    """長文を上限(字数)以内のセグメント列に分割してスレッド化する。

    既定の上限は140字(長文指定が無いときの1ツイート上限)。文末で優先的に区切り、
    1文が長すぎる場合のみ文字境界で強制分割する。
    add_numbering=True で末尾に (i/n) を付与(番号分の字数は別途要考慮のため既定False)。
    """
    text = text.strip()
    if not text:
        return []

    if char_length(text) <= limit:
        segments = [text]
    else:
        pieces = [p for p in _SENTENCE_SPLIT.split(text) if p]
        segments = []
        cur = ""
        for piece in pieces:
            if char_length(piece) > limit:
                if cur:
                    segments.append(cur)
                    cur = ""
                segments.extend(_hard_split(piece, limit))
                continue
            if char_length(cur + piece) <= limit:
                cur += piece
            else:
                if cur:
                    segments.append(cur)
                cur = piece
        if cur:
            segments.append(cur)
        segments = [s.strip() for s in segments if s.strip()]

    if add_numbering and len(segments) > 1:
        n = len(segments)
        segments = [f"{s} ({i}/{n})" for i, s in enumerate(segments, 1)]
    return segments
