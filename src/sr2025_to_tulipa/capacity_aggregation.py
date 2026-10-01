from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

from sr2025_to_tulipa.config import (
    CapacityAggregationRule,
    load_electricity_capacity_aggregation,
    load_hydrogen_capacity_aggregation,
)
from sr2025_to_tulipa.demand_audit import write_rows
from sr2025_to_tulipa.source_validation import SourceValidationError


@dataclass(frozen=True)
class CapacitySourceRow:
    """Hold one native ETM capacity result for one scenario."""

    scenario_key: str
    year: int
    scenario_id: int
    query_key: str
    value: float
    unit: str


@dataclass(frozen=True)
class CapacityGroupRow:
    """Hold one approved grouped electricity capacity."""

    scenario_key: str
    year: int
    asset_group: str
    fuel: str
    operating_mode: str
    capacity_mw: float
    source_count: int


@dataclass(frozen=True)
class CapacityExclusionRow:
    """Record why one native capacity is not a production asset."""

    scenario_key: str
    year: int
    query_key: str
    capacity_mw: float
    reason: str


def read_capacity_scan(path: Path) -> list[CapacitySourceRow]:
    """Read nonzero electricity capacities from an audit scan."""
    with path.open(encoding="utf-8", newline="") as input_file:
        return [
            CapacitySourceRow(
                scenario_key=row["scenario_key"],
                year=int(row["year"]),
                scenario_id=int(row["scenario_id"]),
                query_key=row["query_key"],
                value=float(row["value"]),
                unit=row["unit"],
            )
            for row in csv.DictReader(input_file)
        ]


def read_hydrogen_capacity_scan(path: Path) -> list[CapacitySourceRow]:
    """Read nonzero hydrogen-output capacities from an audit scan."""
    with path.open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    return [
        CapacitySourceRow(
            scenario_key=row["scenario_key"],
            year=int(row["year"]),
            scenario_id=int(row["scenario_id"]),
            query_key=row["query_key"],
            value=float(row["capacity_mw_hydrogen"]),
            unit="MW",
        )
        for row in rows
        if float(row["capacity_mw_hydrogen"]) > 0.0
    ]


def aggregate_electricity_capacity(
    rows: list[CapacitySourceRow], rules: list[CapacityAggregationRule]
) -> tuple[list[CapacityGroupRow], list[CapacityExclusionRow]]:
    """Apply approved grouping and exclusion rules to native capacities."""
    rule_map = {rule.query_key: rule for rule in rules}
    groups: dict[tuple[str, int, str, str, str], list[float]] = {}
    exclusions: list[CapacityExclusionRow] = []

    for row in rows:
        if row.unit != "MW":
            raise SourceValidationError(f"{row.query_key} returned {row.unit}, expected MW.")
        rule = rule_map.get(row.query_key)
        if rule is None:
            raise SourceValidationError(f"No capacity mapping for {row.query_key}.")
        if not rule.included or row.value < rule.minimum_capacity_mw:
            exclusions.append(
                CapacityExclusionRow(
                    scenario_key=row.scenario_key,
                    year=row.year,
                    query_key=row.query_key,
                    capacity_mw=row.value,
                    reason=rule.reason,
                )
            )
            continue
        key = (
            row.scenario_key,
            row.year,
            rule.asset_group,
            rule.fuel,
            rule.operating_mode,
        )
        groups.setdefault(key, []).append(row.value)

    grouped = [
        CapacityGroupRow(*key, capacity_mw=sum(values), source_count=len(values))
        for key, values in sorted(groups.items())
    ]
    return grouped, exclusions


def main() -> None:
    """Group an all-scenario electricity or hydrogen capacity scan."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument(
        "--carrier", choices=("electricity", "hydrogen"), default="electricity"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
    )
    parser.add_argument("--output-dir", type=Path, default=Path("output/audit"))
    args = parser.parse_args()

    if args.carrier == "electricity":
        input_path = args.input or Path(
            "output/audit/electricity_capacity_all_scenarios.csv"
        )
        rows = read_capacity_scan(input_path)
        rules = load_electricity_capacity_aggregation()
    else:
        input_path = args.input or Path(
            "output/audit/hydrogen_capacity_all_scenarios.csv"
        )
        rows = read_hydrogen_capacity_scan(input_path)
        rules = load_hydrogen_capacity_aggregation()

    grouped, excluded = aggregate_electricity_capacity(rows, rules)
    write_rows(grouped, args.output_dir / f"{args.carrier}_capacity_grouped.csv")
    if excluded:
        write_rows(
            excluded, args.output_dir / f"{args.carrier}_capacity_excluded.csv"
        )
    print(f"Created {len(grouped)} grouped rows and {len(excluded)} exclusions.")


if __name__ == "__main__":
    main()