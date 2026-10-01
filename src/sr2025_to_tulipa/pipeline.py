from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from sr2025_to_tulipa.capacity_aggregation import (
    CapacitySourceRow,
    aggregate_electricity_capacity,
)
from sr2025_to_tulipa.carrier_profiles import (
    aggregate_electricity_profiles,
    aggregate_hydrogen_profiles,
    aggregate_natural_gas_profiles,
    apply_demand_grouping,
)
from sr2025_to_tulipa.config import (
    ScenarioSeed,
    load_demand_aggregation,
    load_electricity_capacity_aggregation,
    load_gquery_catalogue,
    load_hydrogen_capacity_aggregation,
    load_methane_supply_aggregation,
    load_model_options,
    load_natural_gas_profile_participants,
    load_scenario_registry,
)
from sr2025_to_tulipa.csv_io import write_rows
from sr2025_to_tulipa.data_collection import collect_source_data, write_source_data
from sr2025_to_tulipa.electricity_regionalisation import (
    regionalise_capacity,
    regionalise_demand,
)
from sr2025_to_tulipa.methane_supply_aggregation import (
    MethaneSupplyRow,
    aggregate_methane_supply,
)
from sr2025_to_tulipa.source_validation import (
    SourceValidationError,
    build_scenario_inventory,
    write_scenario_inventory,
)
from sr2025_to_tulipa.tulipa_assets import export_case


@dataclass(frozen=True)
class CollectedPaths:
    inventory: Path
    electricity_capacity: Path
    hydrogen_capacity: Path
    methane_supply: Path
    grouped_electricity_capacity: Path
    grouped_hydrogen_capacity: Path
    grouped_methane_supply: Path
    grouped_demand: Path
    regional_electricity_capacity: Path
    regional_electricity_demand: Path


def collect_scenario(
    scenario_key: str,
    year: int,
    output_root: Path = Path("output"),
    regional_dir: Path = Path("source_data/regionalisation"),
) -> CollectedPaths:
    """Gather and transform all source data required for one scenario case."""
    seed = select_scenario(load_scenario_registry(), scenario_key, year)
    inventory = build_scenario_inventory([seed])
    data_dir = output_root / "data"
    profile_dir = output_root / "profiles"
    regional_output_dir = output_root / "regionalisation"

    inventory_path = data_dir / "scenario_inventory.csv"
    write_scenario_inventory(inventory, inventory_path)

    electricity_rules = load_electricity_capacity_aggregation()
    hydrogen_rules = load_hydrogen_capacity_aggregation()
    methane_rules = load_methane_supply_aggregation()
    electricity_source, hydrogen_source, methane_source = collect_source_data(
        inventory, electricity_rules, hydrogen_rules, methane_rules
    )
    write_source_data(
        electricity_source, hydrogen_source, methane_source, data_dir
    )

    grouped_electricity, _ = aggregate_electricity_capacity(
        [
            CapacitySourceRow(
                row.scenario_key,
                row.year,
                row.scenario_id,
                row.query_key,
                row.value,
                row.unit,
            )
            for row in electricity_source
        ],
        electricity_rules,
    )
    grouped_hydrogen, _ = aggregate_electricity_capacity(
        [
            CapacitySourceRow(
                row.scenario_key,
                row.year,
                row.scenario_id,
                row.query_key,
                row.capacity_mw_hydrogen,
                "MW",
            )
            for row in hydrogen_source
            if row.capacity_mw_hydrogen > 0.0
        ],
        hydrogen_rules,
    )
    grouped_methane, _ = aggregate_methane_supply(
        [
            MethaneSupplyRow(
                row.scenario_key,
                row.year,
                row.route,
                row.annual_supply_twh,
                row.observed_peak_mw,
                row.installed_output_capacity_mw,
            )
            for row in methane_source
        ],
        methane_rules,
    )
    grouped_electricity_path = data_dir / "electricity_capacity_grouped.csv"
    grouped_hydrogen_path = data_dir / "hydrogen_capacity_grouped.csv"
    grouped_methane_path = data_dir / "methane_supply_grouped.csv"
    write_rows(grouped_electricity, grouped_electricity_path)
    write_rows(grouped_hydrogen, grouped_hydrogen_path)
    write_rows(grouped_methane, grouped_methane_path)

    options = load_model_options()
    annual_queries = load_gquery_catalogue()
    electricity_profiles, _, _ = aggregate_electricity_profiles(
        inventory, options, annual_queries
    )
    hydrogen_profiles, _ = aggregate_hydrogen_profiles(inventory, options)
    methane_profiles, _, _ = aggregate_natural_gas_profiles(
        inventory,
        options,
        load_natural_gas_profile_participants(),
        annual_queries,
    )
    grouped_demand = apply_demand_grouping(
        electricity_profiles + hydrogen_profiles + methane_profiles,
        load_demand_aggregation(),
    )
    grouped_demand_path = profile_dir / "demand_hourly.csv"
    write_rows(grouped_demand, grouped_demand_path)

    regionalise_demand(
        grouped_demand_path,
        regional_dir,
        regional_output_dir,
        write_details=False,
    )
    regionalise_capacity(
        grouped_electricity_path,
        regional_dir,
        regional_output_dir,
        write_details=False,
    )

    return CollectedPaths(
        inventory=inventory_path,
        electricity_capacity=data_dir / "electricity_capacity.csv",
        hydrogen_capacity=data_dir / "hydrogen_capacity.csv",
        methane_supply=data_dir / "methane_supply.csv",
        grouped_electricity_capacity=grouped_electricity_path,
        grouped_hydrogen_capacity=grouped_hydrogen_path,
        grouped_methane_supply=grouped_methane_path,
        grouped_demand=grouped_demand_path,
        regional_electricity_capacity=(
            regional_output_dir / "electricity_capacity_by_node.csv"
        ),
        regional_electricity_demand=(
            regional_output_dir / "electricity_demand_node_hourly.csv"
        ),
    )


def build_scenario(
    scenario_key: str,
    year: int,
    output_root: Path = Path("output"),
    regional_dir: Path = Path("source_data/regionalisation"),
    renewable_profile_input: Path | None = None,
    tyndp_root: Path = Path("../TYNDP-26-to-Tulipa"),
) -> Path:
    """Gather source data and export one complete Tulipa scenario case."""
    paths = collect_scenario(scenario_key, year, output_root, regional_dir)
    return export_case(
        scenario_key,
        year,
        paths.regional_electricity_capacity,
        paths.grouped_hydrogen_capacity,
        regional_dir,
        output_root / "tulipa",
        scenario_inventory_input=paths.inventory,
        tyndp_root=tyndp_root,
        hydrogen_capacity_scan_input=paths.hydrogen_capacity,
        methane_supply_input=paths.grouped_methane_supply,
        electricity_demand_input=paths.regional_electricity_demand,
        grouped_demand_input=paths.grouped_demand,
        renewable_profile_input=renewable_profile_input,
    )


def select_scenario(
    scenarios: list[ScenarioSeed], scenario_key: str, year: int
) -> ScenarioSeed:
    """Select one enabled configured scenario-year."""
    matches = [
        scenario
        for scenario in scenarios
        if scenario.enabled
        and scenario.scenario_key == scenario_key
        and scenario.year == year
    ]
    if len(matches) != 1:
        raise SourceValidationError(
            f"No enabled scenario configured for {scenario_key} {year}."
        )
    return matches[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gather SR2025 data and build one Tulipa scenario."
    )
    parser.add_argument("scenario_key")
    parser.add_argument("year", type=int)
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    parser.add_argument(
        "--regional-dir", type=Path, default=Path("source_data/regionalisation")
    )
    parser.add_argument(
        "--renewable-profile-input",
        type=Path,
        help="Exact TYNDP dispatch folder supplying Dutch VRE profiles.",
    )
    parser.add_argument(
        "--tyndp-root", type=Path, default=Path("../TYNDP-26-to-Tulipa")
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="Gather and transform source data without exporting Tulipa tables.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.collect_only:
        paths = collect_scenario(
            args.scenario_key,
            args.year,
            args.output_root,
            args.regional_dir,
        )
        print(f"Collected scenario data in {paths.inventory.parent.parent}.")
        return
    output_dir = build_scenario(
        args.scenario_key,
        args.year,
        args.output_root,
        args.regional_dir,
        args.renewable_profile_input,
        args.tyndp_root,
    )
    print(f"Created Tulipa scenario in {output_dir}.")


if __name__ == "__main__":
    main()
