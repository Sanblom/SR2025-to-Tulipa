import pytest

from sr2025_to_tulipa.config import (
    CapacityAggregationRule,
    MethaneSupplyAggregationRule,
)
from sr2025_to_tulipa.data_collection import (
    electricity_capacity_rows,
    hydrogen_capacity_rows,
    methane_supply_rows,
)
from sr2025_to_tulipa.etm_client import GQueryResponse
from sr2025_to_tulipa.profile_collection import HOURS_PER_YEAR
from sr2025_to_tulipa.source_validation import ScenarioInventoryRow


def _scenario() -> ScenarioInventoryRow:
    return ScenarioInventoryRow(
        "example",
        "Example",
        2040,
        1,
        2,
        "nl2019",
        "2025.01",
        "https://example.test/api/v3",
        "not_exposed",
        "2026-01-01T00:00:00Z",
    )


def _capacity_rule(query_key: str) -> CapacityAggregationRule:
    return CapacityAggregationRule(
        query_key,
        "group",
        "fuel",
        "technology",
        False,
        False,
        "dispatchable",
        True,
        "included",
        0.0,
    )


def _response(values: dict[str, dict[str, object]]) -> GQueryResponse:
    return GQueryResponse(values, "2026-01-01T00:00:00Z", "checksum")


def test_electricity_collection_keeps_nonzero_mw_capacity() -> None:
    rows = electricity_capacity_rows(
        _scenario(),
        [_capacity_rule("active"), _capacity_rule("inactive")],
        _response(
            {
                "active": {"future": 12.5, "unit": "MW"},
                "inactive": {"future": 0.0, "unit": "MW"},
            }
        ),
    )

    assert [(row.query_key, row.value) for row in rows] == [("active", 12.5)]


def test_hydrogen_collection_converts_chart_production_to_twh() -> None:
    rows = hydrogen_capacity_rows(
        _scenario(),
        [_capacity_rule("hydrogen_chart")],
        _response(
            {
                "hydrogen_chart": {
                    "future": {
                        "capacity": 2.5,
                        "production": 3.6e9,
                        "operating_costs": 42.0,
                    },
                    "unit": None,
                }
            }
        ),
    )

    assert rows[0].capacity_mw_hydrogen == 2.5
    assert rows[0].production_twh == 1.0
    assert rows[0].operating_cost_eur_per_mwh == 42.0


def test_methane_collection_combines_curves_and_preserves_storage_capacity() -> None:
    supply_rule = MethaneSupplyAggregationRule(
        "import",
        "import",
        "gas",
        "import",
        True,
        "included",
        "import_energy",
        ("import_curve_a", "import_curve_b"),
        "supply_route",
    )
    storage_rule = MethaneSupplyAggregationRule(
        "storage",
        "storage",
        "gas",
        "storage",
        True,
        "included",
        "storage_capacity",
        ("storage_curve",),
        "storage_withdrawal",
    )
    rows = methane_supply_rows(
        _scenario(),
        [supply_rule, storage_rule],
        _response(
            {
                "import_energy": {"future": 7.2, "unit": "PJ"},
                "import_curve_a": {
                    "future": [2.0] * HOURS_PER_YEAR,
                    "unit": "curve",
                },
                "import_curve_b": {
                    "future": [3.0] * HOURS_PER_YEAR,
                    "unit": "curve",
                },
                "storage_capacity": {"future": 8.0, "unit": "MW"},
                "storage_curve": {
                    "future": [6.0] * HOURS_PER_YEAR,
                    "unit": "curve",
                },
            }
        ),
    )

    assert rows[0].annual_supply_twh == pytest.approx(2.0)
    assert rows[0].observed_peak_mw == 5.0
    assert rows[0].installed_output_capacity_mw == 0.0
    assert rows[1].annual_supply_twh == 0.0
    assert rows[1].observed_peak_mw == 6.0
    assert rows[1].installed_output_capacity_mw == 8.0
