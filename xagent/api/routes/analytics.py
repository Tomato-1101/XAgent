"""Analytics: APIコストの集計と投稿サマリ。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ...models import ApiCostLog, CostKind, Draft, DraftStatus
from ..deps import db_session, require_api_token

router = APIRouter(prefix="/analytics", tags=["analytics"], dependencies=[Depends(require_api_token)])


@router.get("/cost")
def cost(session: Session = Depends(db_session)) -> dict:
    rows = session.exec(select(ApiCostLog)).all()
    by_kind: dict[str, dict] = {}
    total = 0.0
    for r in rows:
        b = by_kind.setdefault(r.kind.value, {"units": 0, "cost_usd": 0.0})
        b["units"] += r.units
        b["cost_usd"] += r.cost_usd
        total += r.cost_usd
    return {"total_usd": round(total, 4), "by_kind": by_kind}


@router.get("/summary")
def summary(session: Session = Depends(db_session)) -> dict:
    counts: dict[str, int] = {}
    for st in DraftStatus:
        n = len(session.exec(select(Draft).where(Draft.status == st)).all())
        counts[st.value] = n
    return {"draft_counts": counts}
