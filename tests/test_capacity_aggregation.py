from sr2025_to_tulipa.capacity_aggregation import (
    CapacitySourceRow,
    aggregate_electricity_capacity,
)
from sr2025_to_tulipa.config import CapacityAggregationRule


def _rule(query: str, group: str, included: bool, minimum: float = 0.0) -> CapacityAggregationRule:
    """Create one compact capacity rule for aggregation tests."""
    return CapacityAggregationRule(
        query_key=query,
        asset_group=group,
        fuel="test_fuel",
        technology="test_technology",
        chp=False,
        ccs=False,
        operating_mode="dispatchable",
        included=included,
        reason="test reason",
        minimum_capacity_mw=minimum,
    )


def _row(query: str, value: float) -> CapacitySourceRow:
    """Create one compact native capacity result."""
    return CapacitySourceRow("example", 2040, 1, query, value, "MW")


def test_approved_sources_are_summed_by_asset_group() -> None:
    """Multiple native technologies can form one approved asset group."""
    grouped, excluded = aggregate_electricity_capacity(
        [_row("solar_a", 10.0), _row("solar_b", 20.0)],
        [_rule("solar_a", "solar_pv", True), _rule("solar_b", "solar_pv", True)],
    )

    assert grouped[0].capacity_mw == 30.0
    assert grouped[0].source_count == 2
    assert excluded == []


def test_links_and_small_generators_remain_explicit_exclusions() -> None:
    """Non-assets and capacities below the threshold are not silently lost."""
    grouped, excluded = aggregate_electricity_capacity(
        [_row("link", 1000.0), _row("diesel", 11.0)],
        [_rule("link", "interconnector", False), _rule("diesel", "diesel", True, 50.0)],
    )

    assert grouped == []
    assert len(excluded) == 2