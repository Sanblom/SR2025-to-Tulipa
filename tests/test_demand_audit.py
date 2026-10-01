from sr2025_to_tulipa.demand_audit import (
    DemandAuditRow,
    preview_aggregation,
    reconcile_demand,
    summarize_sector_shares,
)
from sr2025_to_tulipa.config import DemandAggregationRule


def _row(role: str, sector: str, value: float) -> DemandAuditRow:
    """Create one small demand row for reconciliation tests."""
    return DemandAuditRow(
        scenario_key="example",
        scenario_name="Example",
        year=2030,
        saved_scenario_id=1,
        scenario_id=2,
        carrier="electricity",
        sector=sector,
        role=role,
        query_key=f"query_{sector}",
        present_pj=0.0,
        future_pj=value,
        source_unit="PJ",
        review_status="candidate",
        engine_base_url="https://example.test/api/v3",
        retrieved_at="2026-08-14T00:00:00+00:00",
        response_checksum="checksum",
    )


def test_sector_sum_reconciles_to_control_total() -> None:
    """Matching sector components pass the reconciliation check."""
    rows = [
        _row("control_total", "total", 10.0),
        _row("sector_component", "households", 4.0),
        _row("sector_component", "industry", 6.0),
    ]

    result = reconcile_demand(rows)

    assert len(result) == 1
    assert result[0].difference_pj == 0.0
    assert result[0].status == "pass"


def test_reconciliation_marks_a_difference_for_review() -> None:
    """A missing sector value is visible as a review finding."""
    rows = [
        _row("control_total", "total", 10.0),
        _row("sector_component", "households", 9.0),
    ]

    result = reconcile_demand(rows)

    assert result[0].difference_pj == -1.0
    assert result[0].status == "review"


def test_sector_summary_reports_value_and_share_ranges() -> None:
    """Sector summaries make aggregation significance visible."""
    rows = [
        _row("control_total", "total", 10.0),
        _row("sector_component", "households", 4.0),
    ]

    result = summarize_sector_shares(rows)

    assert result[0].maximum_pj == 4.0
    assert result[0].maximum_share == 0.4
    assert result[0].always_zero is False


def test_aggregation_preview_keeps_excluded_groups_visible() -> None:
    """Excluded energy demand remains visible in the accounting preview."""
    rows = [
        _row("control_total", "total", 10.0),
        _row("sector_component", "households", 8.0),
        _row("sector_component", "energy", 2.0),
    ]
    rules = [
        DemandAggregationRule(
            "electricity", "households", "domestic", True, "configured"
        ),
        DemandAggregationRule(
            "electricity", "energy", "energy_sector", False, "national"
        ),
    ]

    result = preview_aggregation(rows, rules)

    assert sum(row.future_pj for row in result) == 10.0
    assert any(row.demand_group == "energy_sector" and not row.included for row in result)