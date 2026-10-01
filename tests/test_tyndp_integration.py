import csv
from pathlib import Path

from sr2025_to_tulipa.tyndp_integration import (
    _copy_optional_tyndp_tables,
    _materialize_optional_tables,
    _transform_sr_rows,
    _tyndp_flow_decisions,
    load_boundary_nodes,
    validate_tyndp_dispatch_source,
)


def test_cached_optional_materialization_preserves_sr_rows(tmp_path: Path) -> None:
    source = tmp_path / "tyndp"
    output = tmp_path / "merged"
    cache = tmp_path / "cache"
    source.mkdir()
    output.mkdir()
    header = "asset,commission_year,profile_name,profile_type\n"
    (source / "assets-profiles.csv").write_text(
        header + "DE00_E_Demand_2030,2030,DE00_E_Demand_2030,demand\n",
        encoding="utf-8",
    )
    (output / "assets-profiles.csv").write_text(
        header + "E-A_E_Demand_2030,2030,SR2025_E-A_E_Demand_2030,demand\n",
        encoding="utf-8",
    )

    _materialize_optional_tables(source, output, cache, 2030, 2030)

    with (output / "assets-profiles.csv").open(
        encoding="utf-8", newline=""
    ) as input_file:
        rows = list(csv.DictReader(input_file))
    assert {row["asset"] for row in rows} == {
        "DE00_E_Demand_2030",
        "E-A_E_Demand_2030",
    }


def test_optional_profile_merge_preserves_sr_and_removes_tyndp_nl(tmp_path: Path) -> None:
    source = tmp_path / "tyndp"
    output = tmp_path / "merged"
    source.mkdir()
    output.mkdir()
    header = "asset,commission_year,profile_name,profile_type\n"
    (source / "assets-profiles.csv").write_text(
        header
        + "NL00_Wind_Onshore,2030,NL00_Wind_Onshore,availability\n"
        + "DE00_Wind_Onshore,2030,DE00_Wind_Onshore,availability\n",
        encoding="utf-8",
    )
    (output / "assets-profiles.csv").write_text(
        header
        + "E-A_wind_onshore_volatile,2025,SR2025_wind_onshore_2025,availability\n",
        encoding="utf-8",
    )

    _copy_optional_tyndp_tables(source, output, 2030, 2025)

    with (output / "assets-profiles.csv").open(
        encoding="utf-8", newline=""
    ) as input_file:
        rows = list(csv.DictReader(input_file))
    assert {row["asset"] for row in rows} == {
        "DE00_Wind_Onshore",
        "E-A_wind_onshore_volatile",
    }
    assert next(row for row in rows if row["asset"] == "DE00_Wind_Onshore")[
        "commission_year"
    ] == "2025"


def test_tyndp_nl_priority_keeps_only_carrier_crossborder_infrastructure() -> None:
    rows = [
        {
            "from_asset": "DE00_E_Demand_2030",
            "to_asset": "NL00_E_Demand_2030",
            "carrier": "electricity",
            "is_transport": "true",
        },
        {
            "from_asset": "DEh2_H_Demand_2030",
            "to_asset": "NLh2_H_Demand_2030",
            "carrier": "hydrogen",
            "is_transport": "true",
        },
        {
            "from_asset": "ens",
            "to_asset": "NLh2_H_Demand_2030",
            "carrier": "hydrogen",
            "is_transport": "true",
        },
        {
            "from_asset": "BE00_E_Demand_2030",
            "to_asset": "DE00_E_Demand_2030",
            "carrier": "electricity",
            "is_transport": "true",
        },
        {
            "from_asset": "NLm_M_Demand_2030",
            "to_asset": "DEm_M_Demand_2030",
            "carrier": "methane",
            "is_transport": "true",
        },
    ]

    retained, decisions, audit = _tyndp_flow_decisions(rows, 2030, 2025)

    assert {(row["from_asset"], row["to_asset"]) for row in retained} == {
        ("DEh2_H_Demand_2025", "NL_H_Demand_2025"),
        ("NL_M_Demand_2025", "DEm_M_Demand_2025"),
        ("BE00_E_Demand_2025", "DE00_E_Demand_2025"),
    }
    assert decisions[("DE00_E_Demand_2030", "NL00_E_Demand_2030")] is None
    assert decisions[("ens", "NLh2_H_Demand_2030")] is None
    assert audit["tyndp_nl_hydrogen_crossborder_kept"] == 1
    assert audit["tyndp_nl_methane_crossborder_kept"] == 1
    assert audit["tyndp_nl_electricity_flows_removed"] == 1


def test_tyndp_dispatch_source_rejects_investable_assets(tmp_path: Path) -> None:
    source = tmp_path / "tyndp"
    source.mkdir()
    (source / "asset-milestone.csv").write_text(
        "asset,investable,milestone_year\nDE00_Gas,true,2040\n",
        encoding="utf-8",
    )

    try:
        validate_tyndp_dispatch_source(source)
    except ValueError as error:
        assert "fixed-capacity dispatch dataset" in str(error)
    else:
        raise AssertionError("Investment-enabled TYNDP input was accepted")


def test_sr_electricity_boundaries_rewire_to_tyndp_country_nodes(tmp_path: Path) -> None:
    config = tmp_path / "boundary.csv"
    with config.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["sr_endpoint", "tyndp_node"])
        writer.writerow(["GER", "DE00"])
    aliases = load_boundary_nodes(config, 2030)
    rows = [{
        "from_asset": "E-HGL_E_Demand_2030",
        "to_asset": "GER_E_Demand_2030",
    }]

    transformed, removed = _transform_sr_rows("flow.csv", rows, aliases)

    assert transformed == [{
        "from_asset": "E-HGL_E_Demand_2030",
        "to_asset": "DE00_E_Demand_2030",
    }]
    assert removed == 0