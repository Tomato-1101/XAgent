"""Claude API コスト記録と、Analytics の X API / Claude API 分離集計のテスト。"""

from __future__ import annotations

from xagent import cost
from xagent.api.routes.analytics import cost as cost_route
from xagent.models import CostKind


def test_llm_cost_usd_computation():
    # claude-opus-4-8: 入力100万tok=$5, 出力100万tok=$25
    assert cost.llm_cost_usd(1_000_000, 0) == 5.0
    assert cost.llm_cost_usd(0, 1_000_000) == 25.0
    assert cost.llm_cost_usd(0, 0) == 0.0


def test_log_llm_cost_records_llm_kind(session):
    row = cost.log_llm_cost(session, 1000, 500, note="test")
    session.commit()
    assert row.kind == CostKind.LLM
    assert row.units == 1500
    assert row.cost_usd > 0


def test_bill_formatter_usage_flushes_and_resets(session):
    class F:
        usage_input = 2000
        usage_output = 1000

    f = F()
    row = cost.bill_formatter_usage(session, f, note="compose")
    session.commit()
    assert row is not None
    assert row.kind == CostKind.LLM
    # フラッシュ後はカウンタが0に戻る(二重課金を防ぐ)
    assert f.usage_input == 0 and f.usage_output == 0
    # 使用量0なら何も記録しない(FakeFormatter等)
    assert cost.bill_formatter_usage(session, f, note="compose") is None


def test_analytics_splits_x_and_claude(session):
    """X API(WRITE/READ/TL) と Claude(LLM) を分け、合計も返す。"""
    cost.log_cost(session, CostKind.WRITE, units=2, note="post")   # X: $0.02
    cost.log_cost(session, CostKind.READ, units=1, note="read")    # X: $0.005
    cost.log_llm_cost(session, 1_000_000, 0, note="format")        # Claude: $5.0
    session.commit()

    res = cost_route(session=session)

    assert res["x_api"]["cost_usd"] == 0.025
    assert res["claude_api"]["cost_usd"] == 5.0
    assert res["total_usd"] == 5.025
    # 内訳のキー
    assert set(res["x_api"]["by_kind"].keys()) == {"write", "read"}
    assert set(res["claude_api"]["by_kind"].keys()) == {"llm"}
    # 後方互換のフラット集計も全種別を含む
    assert set(res["by_kind"].keys()) == {"write", "read", "llm"}


def test_analytics_empty_groups(session):
    res = cost_route(session=session)
    assert res["total_usd"] == 0.0
    assert res["x_api"] == {"cost_usd": 0.0, "units": 0, "by_kind": {}}
    assert res["claude_api"] == {"cost_usd": 0.0, "units": 0, "by_kind": {}}
