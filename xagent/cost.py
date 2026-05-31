"""X API 従量課金(2026)のコスト記録。

単価(調査で確定): 投稿 $0.01/件、読み取り $0.005/件、タイムライン取得 $0.01/件。
監視コストの可視化のため、読み書きのたびに ApiCostLog を積む。
"""

from __future__ import annotations

from sqlmodel import Session

from .models import ApiCostLog, CostKind

PRICE_USD: dict[CostKind, float] = {
    CostKind.READ: 0.005,
    CostKind.WRITE: 0.01,
    CostKind.TL: 0.01,
}


def log_cost(
    session: Session, kind: CostKind, units: int = 1, note: str | None = None
) -> ApiCostLog:
    """コストを記録(commitは呼び出し側)。"""
    row = ApiCostLog(
        kind=kind, units=units, cost_usd=PRICE_USD[kind] * units, note=note
    )
    session.add(row)
    return row
