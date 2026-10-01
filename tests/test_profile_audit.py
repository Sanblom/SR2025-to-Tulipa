from sr2025_to_tulipa.config import ProfileQuerySpec
from sr2025_to_tulipa.etm_client import GQueryResponse
from sr2025_to_tulipa.profile_audit import (
    HOURS_PER_YEAR,
    MWH_TO_PJ,
    _profile_value_rows,
    assemble_sector_profiles,
    reconcile_profiles,
)
from sr2025_to_tulipa.source_validation import ScenarioInventoryRow


def _scenario() -> ScenarioInventoryRow:
    """Create one small scenario identity for profile tests."""
    return ScenarioInventoryRow(
        scenario_key="example_2030",
        scenario_name="Example",
        year=2030,
        saved_scenario_id=1,
        scenario_id=2,
        area_code="nl2019",
        model_version="2025.01",
        engine_base_url="https://example.test/api/v3",
        saved_updated_at="2026-08-14T00:00:00+00:00",
        scenario_updated_at="2026-08-14T00:00:00+00:00",
    )


def _query(component: str, key: str) -> ProfileQuerySpec:
    """Create one industry hydrogen profile query."""
    return ProfileQuerySpec(
        carrier="hydrogen",
        sector="industry",
        component=component,
        query_key=key,
        expected_unit="curve",
        expected_hours=8760,
        boundary_type="final_demand",
        annual_query_key="final_demand_of_hydrogen_in_industry",
        review_status="candidate",
    )


def test_profile_components_are_added_and_reconciled() -> None:
    """Industry components form one sector curve matching annual demand."""
    queries = [_query("energetic", "curve_a"), _query("non_energetic", "curve_b")]
    response = GQueryResponse(
        values={
            "curve_a": {"unit": "curve", "future": [1.0] * HOURS_PER_YEAR},
            "curve_b": {"unit": "curve", "future": [2.0] * HOURS_PER_YEAR},
            "final_demand_of_hydrogen_in_industry": {
                "unit": "PJ",
                "future": HOURS_PER_YEAR * 3.0 * MWH_TO_PJ,
            },
        },
        retrieved_at="2026-08-14T00:00:00+00:00",
        response_checksum="checksum",
    )

    values = _profile_value_rows(_scenario(), queries, response)
    sectors = assemble_sector_profiles(values)
    reconciliations = reconcile_profiles(_scenario(), queries, sectors, response)

    assert len(values) == 2 * HOURS_PER_YEAR
    assert len(sectors) == HOURS_PER_YEAR
    assert sectors[0].future_mw == 3.0
    assert reconciliations[0].status == "pass"
    assert reconciliations[0].scale_factor_to_annual == 1.0


def test_profile_reconciliation_exposes_mismatched_integral() -> None:
    """A curve with the wrong annual integral remains a review finding."""
    queries = [_query("total", "curve")]
    response = GQueryResponse(
        values={
            "curve": {"unit": "curve", "future": [1.0] * HOURS_PER_YEAR},
            "final_demand_of_hydrogen_in_industry": {
                "unit": "PJ",
                "future": 10.0,
            },
        },
        retrieved_at="2026-08-14T00:00:00+00:00",
        response_checksum="checksum",
    )

    values = _profile_value_rows(_scenario(), queries, response)
    result = reconcile_profiles(
        _scenario(), queries, assemble_sector_profiles(values), response
    )

    assert result[0].status == "review"
    assert result[0].scale_factor_to_annual == 10.0 / (
        HOURS_PER_YEAR * MWH_TO_PJ
    )


def test_inactive_zero_curve_may_omit_etm_unit() -> None:
    """ETM's unitless inactive curves retain a distinct source label."""
    query = _query("inactive", "curve")
    response = GQueryResponse(
        values={"curve": {"unit": None, "future": [0.0] * HOURS_PER_YEAR}},
        retrieved_at="2026-08-14T00:00:00+00:00",
        response_checksum="checksum",
    )

    values = _profile_value_rows(_scenario(), [query], response)

    assert values[0].source_unit == "curve_inactive_zero"