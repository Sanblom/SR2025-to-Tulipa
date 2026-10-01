import pytest

from sr2025_to_tulipa.electricity_regionalisation import (
    Municipality,
    _demand_source_reconciliation,
    _industry_demand_sectors,
    _province_to_municipal_driver,
    aggregate_node_weights,
    capacity_weights,
    normalize_weights,
    RegionalReference,
)
from sr2025_to_tulipa.source_validation import SourceValidationError

def test_spatial_weights_are_normalized() -> None:
    weights = normalize_weights({"GM1": 2.0, "GM2": 3.0})

    assert weights == pytest.approx({"GM1": 0.4, "GM2": 0.6})
    assert sum(weights.values()) == 1.0

def test_empty_spatial_driver_is_rejected() -> None:
    with pytest.raises(SourceValidationError):
        normalize_weights({"GM1": 0.0})

def test_municipalities_aggregate_to_electricity_nodes() -> None:
    municipalities = {
        "GM1": Municipality("GM1", "One", "province", "E-A", 1.0),
        "GM2": Municipality("GM2", "Two", "province", "E-A", 1.0),
        "GM3": Municipality("GM3", "Three", "province", "E-B", 1.0),
    }

    nodes = aggregate_node_weights(
        {"GM1": 0.2, "GM2": 0.3, "GM3": 0.5}, municipalities
    )

    assert nodes == pytest.approx({"E-A": 0.5, "E-B": 0.5})

def test_industry_is_distributed_province_first() -> None:
    municipalities = {
        "GM1": Municipality("GM1", "One", "groningen", "E-A", 1.0),
        "GM2": Municipality("GM2", "Two", "groningen", "E-B", 3.0),
        "GM3": Municipality("GM3", "Three", "friesland", "E-C", 2.0),
    }

    driver = _province_to_municipal_driver(
        {
            "groningen": {"Industry": 100.0},
            "friesland": {"Industry": 50.0},
        },
        ("Industry",),
        municipalities,
    )

    assert driver == pytest.approx({"GM1": 25.0, "GM2": 75.0, "GM3": 50.0})

def test_capacity_uses_technology_specific_municipality_driver() -> None:
    reference = RegionalReference(
        reference_year=2030,
        municipalities={
            "GM1": Municipality("GM1", "One", "province", "E-A", 1.0),
            "GM2": Municipality("GM2", "Two", "province", "E-B", 1.0),
        },
        municipality_volume={},
        municipality_capacity={
            "GM1": {"Wind_onshore": 1.0},
            "GM2": {"Wind_onshore": 3.0},
        },
        province_volume={},
        province_capacity={},
    )

    weights = capacity_weights("example", 2030, "wind_onshore", reference)

    assert {row.node: row.allocation_share for row in weights} == pytest.approx(
        {"E-A": 0.25, "E-B": 0.75}
    )
    assert {row.source_method for row in weights} == {
        "municipality_technology_capacity"
    }

def test_capacity_without_spatial_driver_is_rejected() -> None:
    reference = RegionalReference(
        reference_year=2030,
        municipalities={
            "GM1": Municipality("GM1", "One", "province", "E-A", 1.0)
        },
        municipality_volume={},
        municipality_capacity={"GM1": {}},
        province_volume={},
        province_capacity={},
    )

    with pytest.raises(SourceValidationError, match="No spatial capacity driver"):
        capacity_weights("example", 2030, "wind_onshore", reference)

def test_natural_gas_chp_adds_municipal_and_industry_capacity() -> None:
    reference = RegionalReference(
        reference_year=2030,
        municipalities={
            "GM1": Municipality("GM1", "One", "groningen", "E-A", 1.0),
            "GM2": Municipality("GM2", "Two", "groningen", "E-B", 3.0),
        },
        municipality_volume={},
        municipality_capacity={
            "GM1": {"Power_plant_methane_CHP": 50.0, "Agriculture_CHP": 0.0},
            "GM2": {"Power_plant_methane_CHP": 0.0, "Agriculture_CHP": 50.0},
        },
        province_volume={},
        province_capacity={"groningen": {"Industry_methane_CHP": 100.0}},
    )

    weights = capacity_weights("example", 2030, "natural_gas_chp", reference)

    assert {row.node: row.allocation_share for row in weights} == pytest.approx(
        {"E-A": 0.375, "E-B": 0.625}
    )
    assert {row.source_method for row in weights} == {
        "municipality_and_province_industry_capacity"
    }

def test_demand_source_reconciliation_excludes_industry_flexibility() -> None:
    reference = RegionalReference(
        reference_year=2030,
        municipalities={},
        municipality_volume={"GM1": {"Households": 60.0, "Transport_car": 40.0}},
        municipality_capacity={},
        province_volume={
            "province": {
                "Industry_steel": 30.0,
                "Industry_chemicals_PtH": 10.0,
                "Industry_metals_DSR": 5.0,
                "Industry_methane_CHP": 20.0,
            }
        },
        province_capacity={},
    )

    assert _industry_demand_sectors(reference) == ("Industry_steel",)
    reconciliation = _demand_source_reconciliation(
        "example", 2030, reference, modeled_report_total_mwh=130.0
    )

    assert reconciliation.workbook_total_mwh == 130.0
    assert reconciliation.status == "pass"
