from sr2025_to_tulipa.config import MethaneSupplyAggregationRule
from sr2025_to_tulipa.methane_supply_aggregation import (
    MethaneSupplyRow,
    aggregate_methane_supply,
)


def _rule(route: str, group: str, included: bool) -> MethaneSupplyAggregationRule:
    """Create one compact methane rule for aggregation tests."""
    return MethaneSupplyAggregationRule(
        route=route,
        supply_group=group,
        resource="test",
        technology="test",
        included=included,
        reason="test reason",
        annual_query_key=f"{route}_annual",
        curve_query_keys=(f"{route}_curve",),
        boundary="supply_route",
    )


def _row(route: str, supply: float, peak: float) -> MethaneSupplyRow:
    """Create one compact methane supply result."""
    return MethaneSupplyRow("example", 2040, route, supply, peak, 0.0)


def test_renewable_routes_form_one_supply_group() -> None:
    """Gasification and upgrading volumes form renewable methane supply."""
    grouped, excluded = aggregate_methane_supply(
        [_row("gasification", 8.0, 900.0), _row("upgrading", 16.0, 1800.0)],
        [
            _rule("gasification", "renewable_methane", True),
            _rule("upgrading", "renewable_methane", True),
        ],
    )

    assert grouped[0].annual_supply_twh == 24.0
    assert grouped[0].summed_route_peak_mw == 2700.0
    assert grouped[0].source_count == 2
    assert excluded == []


def test_industry_transformation_remains_visible_as_excluded() -> None:
    """Excluded transformation supply remains explicit."""
    grouped, excluded = aggregate_methane_supply(
        [_row("industry", 1.0, 100.0)],
        [_rule("industry", "industry_transformation", False)],
    )

    assert grouped == []
    assert excluded[0].annual_supply_twh == 1.0