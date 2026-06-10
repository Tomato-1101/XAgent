"""API 従量課金のコスト記録(X API と Claude API の両方)。

X API 単価(調査で確定): 投稿 $0.01/件、読み取り $0.005/件、タイムライン取得 $0.01/件。
Claude API はトークン課金。整形・解析・抽出のたびに使用トークンから実額を計算して積む。
監視/整形コストの可視化のため、読み書き・LLM呼び出しのたびに ApiCostLog を積む。
"""

from __future__ import annotations

from sqlmodel import Session

from .models import ApiCostLog, CostKind

PRICE_USD: dict[CostKind, float] = {
    CostKind.READ: 0.005,
    CostKind.WRITE: 0.01,
    CostKind.TL: 0.01,
}

# Claude のトークン単価(USD / 100万トークン)。claude-opus-4-8 の標準価格(入力$5 / 出力$25)。
# 実課金はサブスクリプション(Claude Code CLI)だが、消費の可視化のため名目額を記録し続ける。
LLM_PRICE_PER_MTOK: dict[str, float] = {"input": 5.0, "output": 25.0}


def log_cost(
    session: Session, kind: CostKind, units: int = 1, note: str | None = None
) -> ApiCostLog:
    """X API のコストを記録(commitは呼び出し側)。"""
    row = ApiCostLog(
        kind=kind, units=units, cost_usd=PRICE_USD[kind] * units, note=note
    )
    session.add(row)
    return row


def llm_cost_usd(input_tokens: int, output_tokens: int) -> float:
    """入出力トークンから Claude API の概算コスト(USD)を計算する。"""
    return (
        input_tokens / 1_000_000 * LLM_PRICE_PER_MTOK["input"]
        + output_tokens / 1_000_000 * LLM_PRICE_PER_MTOK["output"]
    )


def log_llm_cost(
    session: Session,
    input_tokens: int,
    output_tokens: int,
    note: str | None = None,
) -> ApiCostLog:
    """Claude API のコストを記録(commitは呼び出し側)。units=総トークン数。"""
    row = ApiCostLog(
        kind=CostKind.LLM,
        units=input_tokens + output_tokens,
        cost_usd=llm_cost_usd(input_tokens, output_tokens),
        note=note,
    )
    session.add(row)
    return row


def bill_formatter_usage(session: Session, formatter, note: str | None = None) -> ApiCostLog | None:
    """整形器に溜まった Claude トークン使用量を ApiCostLog に記録し、カウンタを0に戻す。

    commit は呼び出し側に任せる(下書き作成と同一トランザクションで永続化する)。
    FakeFormatter 等で使用量属性が無ければ何もしない。
    """
    ti = int(getattr(formatter, "usage_input", 0) or 0)
    to = int(getattr(formatter, "usage_output", 0) or 0)
    if ti == 0 and to == 0:
        return None
    row = log_llm_cost(session, ti, to, note=note)
    formatter.usage_input = 0
    formatter.usage_output = 0
    return row
