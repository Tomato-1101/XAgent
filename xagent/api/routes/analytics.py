"""Analytics: APIコストの集計と投稿サマリ。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ...models import ApiCostLog, CostKind, Draft, DraftStatus
from ..deps import db_session, require_api_token

router = APIRouter(prefix="/analytics", tags=["analytics"], dependencies=[Depends(require_api_token)])


def _empty_group() -> dict:
    return {"cost_usd": 0.0, "units": 0, "by_kind": {}}


@router.get("/cost")
def cost(session: Session = Depends(db_session)) -> dict:
    """API コストを X API と Claude API に分けて集計し、合計も返す。"""
    rows = session.exec(select(ApiCostLog)).all()
    by_kind: dict[str, dict] = {}
    x_api = _empty_group()
    claude = _empty_group()
    total = 0.0
    for r in rows:
        b = by_kind.setdefault(r.kind.value, {"units": 0, "cost_usd": 0.0})
        b["units"] += r.units
        b["cost_usd"] += r.cost_usd
        total += r.cost_usd

        group = claude if r.kind == CostKind.LLM else x_api
        group["cost_usd"] += r.cost_usd
        group["units"] += r.units
        gk = group["by_kind"].setdefault(r.kind.value, {"units": 0, "cost_usd": 0.0})
        gk["units"] += r.units
        gk["cost_usd"] += r.cost_usd

    def _round(g: dict) -> dict:
        g["cost_usd"] = round(g["cost_usd"], 4)
        for v in g["by_kind"].values():
            v["cost_usd"] = round(v["cost_usd"], 4)
        return g

    return {
        "total_usd": round(total, 4),
        "by_kind": by_kind,  # 後方互換(全種別フラット)
        "x_api": _round(x_api),
        "claude_api": _round(claude),
    }


@router.get("/summary")
def summary(session: Session = Depends(db_session)) -> dict:
    counts: dict[str, int] = {}
    for st in DraftStatus:
        n = len(session.exec(select(Draft).where(Draft.status == st)).all())
        counts[st.value] = n
    return {"draft_counts": counts}
